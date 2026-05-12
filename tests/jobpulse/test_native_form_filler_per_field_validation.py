"""Tests for S26-follow-up-O-2: per-field shift-left validation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jobpulse.native_form_filler import NativeFormFiller


def _mock_locator(input_value: str) -> MagicMock:
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.input_value = AsyncMock(return_value=input_value)
    loc.is_checked = AsyncMock(return_value=False)
    loc.evaluate = AsyncMock(return_value=None)
    loc.get_attribute = AsyncMock(return_value=None)
    return loc


def _bare_filler() -> NativeFormFiller:
    """A NativeFormFiller skeleton bypassing the heavy real __init__.

    We're testing the per-field validation methods in isolation so we
    just need ``_page``, ``_fields_by_label``, ``_session_state``."""
    from jobpulse.screening_session_state import SessionFillState
    filler = NativeFormFiller.__new__(NativeFormFiller)
    filler._page = MagicMock()
    filler._page.url = "https://job-boards.greenhouse.io/x"
    filler._fields_by_label = {}
    filler._session_state = SessionFillState()
    return filler


def test_verify_returns_true_when_dom_matches_claim():
    filler = _bare_filler()
    filler._fields_by_label = {
        "Email": {"type": "email", "locator": _mock_locator("y@b.com")},
    }
    ok = asyncio.run(filler._verify_fill_immediate("Email", "y@b.com"))
    assert ok is True


def test_verify_returns_false_when_dom_mismatches():
    filler = _bare_filler()
    filler._fields_by_label = {
        "Email": {"type": "email", "locator": _mock_locator("wrong@x.com")},
    }
    ok = asyncio.run(filler._verify_fill_immediate("Email", "y@b.com"))
    assert ok is False


def test_verify_returns_none_for_undom_readable_types():
    """combobox / custom_dropdown — DOM can't verify; vision handles end-of-page."""
    filler = _bare_filler()
    loc = _mock_locator("")
    filler._fields_by_label = {
        "Country": {"type": "combobox", "locator": loc},
    }
    ok = asyncio.run(filler._verify_fill_immediate("Country", "United Kingdom"))
    # read_dom_value returns None for combobox; verify returns None.
    assert ok is None


def test_fill_with_validation_passes_first_try():
    """Strike 1 success: dom match → verified_via=dom."""
    filler = _bare_filler()
    filler._fields_by_label = {
        "Email": {"type": "email", "locator": _mock_locator("y@b.com")},
    }
    fill_calls = []

    async def fake_raw(label, value):
        fill_calls.append(value)
        return {"success": True, "value_set": value}

    filler._fill_raw = fake_raw
    result = asyncio.run(filler._fill_with_validation("Email", "y@b.com"))
    assert result["success"] is True
    assert result["verified"] is True
    assert result["verified_via"] == "dom"
    assert len(fill_calls) == 1
    # session_state recorded the verified fill
    assert filler._session_state.was_verified("Email") is True


def test_fill_with_validation_retry_same_then_passes():
    """Strike 2 success: re-fill with same value, then verified."""
    filler = _bare_filler()
    filler._fields_by_label = {
        "Email": {"type": "email", "locator": _mock_locator("y@b.com")},
    }
    fill_calls = []

    async def fake_raw(label, value):
        fill_calls.append(value)
        return {"success": True, "value_set": value}

    verify_results = iter([False, True])

    async def fake_verify(label, value):
        return next(verify_results)

    filler._fill_raw = fake_raw
    filler._verify_fill_immediate = fake_verify
    filler._llm_regen_alternate = AsyncMock(return_value=None)

    result = asyncio.run(filler._fill_with_validation("Email", "y@b.com"))
    assert result["success"] is True
    assert result["verified_via"] == "dom_after_retry"
    assert len(fill_calls) == 2  # initial + retry
    assert filler._llm_regen_alternate.call_count == 0


def test_fill_with_validation_llm_regen_strike_three():
    """Strike 3 success: LLM regen finds an accepted alternate."""
    filler = _bare_filler()
    filler._fields_by_label = {
        "Email": {"type": "email", "locator": _mock_locator("y@b.com")},
    }
    fill_calls = []

    async def fake_raw(label, value):
        fill_calls.append(value)
        return {"success": True, "value_set": value}

    verify_results = iter([False, False, True])

    async def fake_verify(label, value):
        return next(verify_results)

    filler._fill_raw = fake_raw
    filler._verify_fill_immediate = fake_verify
    filler._llm_regen_alternate = AsyncMock(return_value="y.b@example.com")

    result = asyncio.run(filler._fill_with_validation("Email", "y@b.com"))
    assert result["success"] is True
    assert result["verified_via"] == "dom_after_llm_regen"
    assert result["value_set"] == "y.b@example.com"
    assert result.get("original_value_rejected") == "y@b.com"
    assert filler._llm_regen_alternate.call_count == 1


def test_fill_with_validation_three_strikes_bails_to_vision():
    """All three strikes fail → deferred_to_vision."""
    filler = _bare_filler()
    filler._fields_by_label = {
        "Email": {"type": "email", "locator": _mock_locator("y@b.com")},
    }

    async def fake_raw(label, value):
        return {"success": True, "value_set": value}

    async def fake_verify_always_false(label, value):
        return False

    filler._fill_raw = fake_raw
    filler._verify_fill_immediate = fake_verify_always_false
    filler._llm_regen_alternate = AsyncMock(return_value="alt@x.com")

    result = asyncio.run(filler._fill_with_validation("Email", "y@b.com"))
    assert result["success"] is False
    assert result.get("error_class") == "wrong_value_after_retries"
    assert result.get("deferred_to_vision") is True
