"""Tests for radio_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


def _mock_radio(label_text: str, checked: bool = False):
    """Create a mock radio element with associated label."""
    el = MagicMock()
    el.click = AsyncMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: {
        "type": "radio",
        "id": f"radio_{label_text.lower().replace(' ', '_')}",
    }.get(name))
    el.is_checked = AsyncMock(return_value=checked)
    el.evaluate = AsyncMock(return_value=label_text)
    el.scroll_into_view_if_needed = AsyncMock()
    return el, label_text


@pytest.mark.asyncio
async def test_fill_radio_exact_match():
    from jobpulse.form_engine.radio_filler import fill_radio_group

    yes_el, _ = _mock_radio("Yes")
    no_el, _ = _mock_radio("No")

    # Mock label elements for each radio
    no_label = MagicMock()
    no_label.text_content = AsyncMock(return_value="No")
    yes_label = MagicMock()
    yes_label.text_content = AsyncMock(return_value="Yes")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[yes_el, no_el])
    # page.query_selector returns label elements for label[for='id'] lookups
    page.query_selector = AsyncMock(side_effect=lambda sel: {
        "label[for='radio_yes']": yes_label,
        "label[for='radio_no']": no_label,
    }.get(sel))

    result = await fill_radio_group(page, "input[name='sponsorship']", "No")
    assert result.success is True
    assert result.value_set == "No"


@pytest.mark.asyncio
async def test_fill_radio_no_matching_option():
    from jobpulse.form_engine.radio_filler import fill_radio_group

    yes_el, _ = _mock_radio("Yes")
    no_el, _ = _mock_radio("No")

    no_label = MagicMock()
    no_label.text_content = AsyncMock(return_value="No")
    yes_label = MagicMock()
    yes_label.text_content = AsyncMock(return_value="Yes")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[yes_el, no_el])
    page.query_selector = AsyncMock(side_effect=lambda sel: {
        "label[for='radio_yes']": yes_label,
        "label[for='radio_no']": no_label,
    }.get(sel))

    result = await fill_radio_group(page, "input[name='test']", "Maybe")
    assert result.success is False
    assert "no matching" in result.error.lower()


@pytest.mark.asyncio
async def test_fill_radio_no_elements():
    from jobpulse.form_engine.radio_filler import fill_radio_group

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[])

    result = await fill_radio_group(page, "input[name='missing']", "Yes")
    assert result.success is False
