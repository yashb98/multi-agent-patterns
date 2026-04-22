"""Tests for DraftSession._capture_final_mapping_async.

Phase 0, item 3 regression: draft_applicator used to call
``confirm_application(agent_mapping=X, final_mapping=X)`` with the same
dict, so ``CorrectionCapture.record_corrections`` always saw an empty
diff and the correction→RL feedback loop was silently dead.

These tests pin down the new capture logic:

- reads live page values for every visible field type
- labels match the fill-time accessibility logic
- checkbox → "true"/"false"; radio → checked option label
- when the user edits a value in Chrome, `final_mapping[label]` reflects
  the edit while `agent_mapping[label]` keeps the agent's original — so
  the diff is non-empty and correction capture actually fires
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


_radio_labels: dict[int, str] = {}


def _fake_locator(kind: str, **kwargs):
    """Build a mock Playwright locator with the attrs the capture reads."""
    loc = AsyncMock()
    if kind == "text":
        loc.input_value = AsyncMock(return_value=kwargs.get("value", ""))
    elif kind == "textarea":
        loc.input_value = AsyncMock(return_value=kwargs.get("value", ""))
    elif kind == "select":
        loc.input_value = AsyncMock(return_value=kwargs.get("value", ""))
        loc.evaluate = AsyncMock(return_value="select")
    elif kind == "combobox":
        loc.input_value = AsyncMock(return_value=kwargs.get("value", ""))
        loc.evaluate = AsyncMock(return_value="input")
    elif kind == "checkbox":
        loc.is_checked = AsyncMock(return_value=kwargs.get("checked", False))
    elif kind == "radio":
        loc.is_checked = AsyncMock(return_value=kwargs.get("checked", False))
        _radio_labels[id(loc)] = kwargs.get("label", "")
    elif kind == "radiogroup":
        radios = kwargs.get("radios", [])
        radio_group = AsyncMock()
        radio_group.all = AsyncMock(return_value=radios)
        loc.get_by_role = MagicMock(return_value=radio_group)
    return loc


def _wire_page(page, *, textboxes=(), comboboxes=(), radiogroups=(),
               checkboxes=(), textareas=()):
    """Wire a mock page so it returns the configured locators per role."""
    role_map = {
        "textbox": textboxes,
        "combobox": comboboxes,
        "radiogroup": radiogroups,
        "checkbox": checkboxes,
    }

    def get_by_role(role):
        group = AsyncMock()
        group.all = AsyncMock(return_value=list(role_map.get(role, [])))
        return group

    page.get_by_role = MagicMock(side_effect=get_by_role)

    def locator(selector):
        group = AsyncMock()
        if selector == "textarea:visible":
            group.all = AsyncMock(return_value=list(textareas))
        else:
            group.all = AsyncMock(return_value=[])
        return group

    page.locator = MagicMock(side_effect=locator)


class _FakeFiller:
    """Stand-in for NativeFormFiller: only `_get_accessible_name` is used."""

    def __init__(self, labels: dict[int, str]):
        self._labels = labels

    async def _get_accessible_name(self, loc) -> str:
        # Radios register via _radio_labels since AsyncMock auto-creates
        # attributes and we can't rely on `hasattr` to dispatch.
        if id(loc) in _radio_labels:
            return _radio_labels[id(loc)]
        return self._labels.get(id(loc), "")


def _make_session():
    """Minimal DraftSession with enough attrs to call _capture_final_mapping."""
    from jobpulse.draft_applicator import DraftSession

    session = DraftSession.__new__(DraftSession)
    session._agent_mapping = {}
    session._final_mapping = {}
    return session


# ─── basic capture per field type ──────────────────────────────

@pytest.mark.asyncio
async def test_capture_reads_text_inputs():
    from jobpulse.draft_applicator import DraftSession  # noqa: F401 (ensure import)

    session = _make_session()
    page = MagicMock()
    session._page = page

    loc = _fake_locator("text", value="ada@example.com")
    _wire_page(page, textboxes=[loc])
    filler = _FakeFiller({id(loc): "Email Address"})

    out = await session._capture_final_mapping_async(filler)

    assert out == {"Email Address": "ada@example.com"}


@pytest.mark.asyncio
async def test_capture_reads_checkbox_state():
    session = _make_session()
    page = MagicMock()
    session._page = page

    cb_on = _fake_locator("checkbox", checked=True)
    cb_off = _fake_locator("checkbox", checked=False)
    _wire_page(page, checkboxes=[cb_on, cb_off])
    filler = _FakeFiller({
        id(cb_on): "Relocate",
        id(cb_off): "Sponsorship",
    })

    out = await session._capture_final_mapping_async(filler)

    assert out == {"Relocate": "true", "Sponsorship": "false"}


@pytest.mark.asyncio
async def test_capture_reads_radio_group_selected_option():
    session = _make_session()
    page = MagicMock()
    session._page = page

    r_yes = _fake_locator("radio", checked=False, label="Yes")
    r_no = _fake_locator("radio", checked=True, label="No")
    rg = _fake_locator("radiogroup", radios=[r_yes, r_no])
    _wire_page(page, radiogroups=[rg])
    filler = _FakeFiller({id(rg): "Authorized to work?"})

    out = await session._capture_final_mapping_async(filler)

    assert out == {"Authorized to work?": "No"}


@pytest.mark.asyncio
async def test_capture_reads_textarea():
    session = _make_session()
    page = MagicMock()
    session._page = page

    ta = _fake_locator("textarea", value="Dear team, ...")
    _wire_page(page, textareas=[ta])
    filler = _FakeFiller({id(ta): "Cover letter"})

    out = await session._capture_final_mapping_async(filler)

    assert out == {"Cover letter": "Dear team, ..."}


@pytest.mark.asyncio
async def test_capture_distinguishes_native_select_from_combobox():
    session = _make_session()
    page = MagicMock()
    session._page = page

    native = _fake_locator("select", value="US")
    react_cb = _fake_locator("combobox", value="Senior")
    _wire_page(page, comboboxes=[native, react_cb])
    filler = _FakeFiller({id(native): "Country", id(react_cb): "Seniority"})

    out = await session._capture_final_mapping_async(filler)

    assert out == {"Country": "US", "Seniority": "Senior"}


# ─── label-less fields are dropped ────────────────────────────

@pytest.mark.asyncio
async def test_capture_skips_fields_with_empty_labels():
    session = _make_session()
    page = MagicMock()
    session._page = page

    loc = _fake_locator("text", value="spam")
    _wire_page(page, textboxes=[loc])
    filler = _FakeFiller({id(loc): ""})

    out = await session._capture_final_mapping_async(filler)

    assert out == {}


# ─── error resilience ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_per_field_error_does_not_poison_others():
    session = _make_session()
    session._agent_mapping = {"Email": "fallback@example.com"}
    page = MagicMock()
    session._page = page

    broken = AsyncMock()
    broken.input_value = AsyncMock(side_effect=RuntimeError("CDP died"))
    good = _fake_locator("text", value="ada@example.com")
    _wire_page(page, textboxes=[broken, good])
    filler = _FakeFiller({id(broken): "Phone", id(good): "Email"})

    out = await session._capture_final_mapping_async(filler)

    assert out == {"Email": "ada@example.com"}


@pytest.mark.asyncio
async def test_capture_page_crash_falls_back_to_agent_mapping():
    session = _make_session()
    session._agent_mapping = {"Email": "agent@example.com"}
    session._page = None  # simulates driver already closed

    out = await session._capture_final_mapping_async(_FakeFiller({}))

    assert out == {"Email": "agent@example.com"}


# ─── the critical property: correction capture is no longer dead ─

@pytest.mark.asyncio
async def test_user_edit_produces_nonempty_diff_vs_agent_mapping():
    """This is the whole point of Phase 0 item 3: when the user edits a
    field value in Chrome after the agent filled it, the captured
    final_mapping must differ from agent_mapping so CorrectionCapture
    records a learnable delta."""
    session = _make_session()
    session._agent_mapping = {
        "Email": "agent@example.com",
        "Phone": "+44 0000",
    }
    page = MagicMock()
    session._page = page

    email = _fake_locator("text", value="real.user@example.com")  # user edited
    phone = _fake_locator("text", value="+44 0000")               # unchanged
    _wire_page(page, textboxes=[email, phone])
    filler = _FakeFiller({id(email): "Email", id(phone): "Phone"})

    final = await session._capture_final_mapping_async(filler)

    diff = {
        k: (session._agent_mapping.get(k), v)
        for k, v in final.items()
        if session._agent_mapping.get(k) != v
    }
    assert diff == {"Email": ("agent@example.com", "real.user@example.com")}


@pytest.mark.asyncio
async def test_no_user_edit_yields_empty_diff():
    """Contrast with the above: if nothing was edited, the diff must be
    empty — we don't fabricate corrections."""
    session = _make_session()
    session._agent_mapping = {"Email": "ada@example.com"}
    page = MagicMock()
    session._page = page

    loc = _fake_locator("text", value="ada@example.com")
    _wire_page(page, textboxes=[loc])
    filler = _FakeFiller({id(loc): "Email"})

    final = await session._capture_final_mapping_async(filler)

    assert final == {"Email": "ada@example.com"}
    assert final == session._agent_mapping
