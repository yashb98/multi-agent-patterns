"""Tests for intent-based locator self-healing.

Real-data oriented: uses real field-shape data from production where possible.
Mocks ONLY the Playwright page (no live browser available in test env).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.form_engine.intent_healing import (
    FieldIntent,
    heal_locator,
    _build_a11y_summary,
)


def _make_page(loc_count_for_selector: dict[str, int]):
    """Build a fake Playwright page where each selector → fixed count."""
    page = MagicMock()

    def make_locator(count: int):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=count)
        return loc

    def page_locator(selector: str):
        return make_locator(loc_count_for_selector.get(selector, 0))

    page.locator = MagicMock(side_effect=page_locator)
    page.get_by_role = MagicMock(
        side_effect=lambda role, name=None, exact=False: make_locator(
            loc_count_for_selector.get(f"role:{role}:{name}", 0)
        )
    )
    page.get_by_label = MagicMock(
        side_effect=lambda label, exact=False: make_locator(
            loc_count_for_selector.get(f"label:{label}", 0)
        )
    )
    return page


class TestPath1StoredSelector:
    @pytest.mark.asyncio
    async def test_stored_selector_returns_locator_when_present(self):
        page = _make_page({"#first-name": 1})
        intent = FieldIntent(label="First Name", role="textbox")
        result = await heal_locator(
            page, stored_selector="#first-name", intent=intent,
        )
        assert result is not None
        page.locator.assert_called_with("#first-name")


class TestPath2RoleFallback:
    @pytest.mark.asyncio
    async def test_role_fallback_when_stored_selector_stale(self):
        # Stored selector returns 0; role-based locator returns 1
        page = _make_page({
            "#stale-id-12345": 0,  # stored selector, stale
            "role:textbox:First Name": 1,  # role-based fallback works
        })
        intent = FieldIntent(label="First Name", role="textbox")
        result = await heal_locator(
            page, stored_selector="#stale-id-12345", intent=intent,
        )
        assert result is not None
        page.get_by_role.assert_called_with("textbox", name="First Name", exact=False)

    @pytest.mark.asyncio
    async def test_label_fallback_when_role_fails(self):
        page = _make_page({
            "#stale": 0,
            "role:textbox:Email": 0,
            "label:Email": 1,
        })
        intent = FieldIntent(label="Email", role="textbox")
        result = await heal_locator(
            page, stored_selector="#stale", intent=intent,
        )
        assert result is not None


class TestPath3LLMResolution:
    @pytest.mark.asyncio
    async def test_llm_resolution_when_role_and_label_both_fail(self):
        page = _make_page({
            "#dead": 0,
            "role:combobox:Country": 0,
            "label:Country": 0,
            "[data-test='country-dropdown']": 1,  # what the LLM returns
        })
        intent = FieldIntent(label="Country", role="combobox", field_type="select")
        snapshot_fields = [
            {"label": "First Name", "role": "textbox", "input_type": "text", "id": "fn"},
            {"label": "Country", "role": "combobox", "input_type": "select", "id": "country"},
        ]
        # Mock the LLM to return a working selector
        with patch(
            "jobpulse.form_engine.intent_healing._call_llm_for_selector",
            return_value="[data-test='country-dropdown']",
        ):
            result = await heal_locator(
                page, stored_selector="#dead",
                intent=intent, snapshot_fields=snapshot_fields,
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_all_three_paths_fail(self):
        page = _make_page({})  # everything returns 0
        intent = FieldIntent(label="Nonexistent", role="textbox")
        with patch(
            "jobpulse.form_engine.intent_healing._call_llm_for_selector",
            return_value=None,
        ):
            result = await heal_locator(
                page, stored_selector=None,
                intent=intent, snapshot_fields=[],
            )
        assert result is None


class TestA11ySummaryBuilder:
    def test_empty_fields_produces_marker(self):
        assert _build_a11y_summary([]) == "(no fields scanned)"

    def test_real_field_shape(self):
        # Real field shape from production form_experience.db serialized field_types
        fields = [
            {"label": "First Name", "role": "textbox", "input_type": "text", "id": "fn"},
            {"label": "Resume", "role": "button", "input_type": "file", "id": "resume"},
        ]
        summary = _build_a11y_summary(fields)
        assert "First Name" in summary
        assert "textbox" in summary
        assert "Resume" in summary
        assert "file" in summary

    def test_truncation_at_limit(self):
        fields = [{"label": f"Field {i}", "role": "textbox"} for i in range(50)]
        summary = _build_a11y_summary(fields, limit=10)
        # Only 10 fields included
        assert summary.count("Field") == 10
