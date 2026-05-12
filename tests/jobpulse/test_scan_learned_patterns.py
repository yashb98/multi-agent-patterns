"""Scanner consults GotchasDB.widget_patterns for the current domain."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_learned_patterns_returns_known_widgets():
    from jobpulse.form_engine.field_scanner import _scan_learned_patterns

    page = MagicMock()
    page.url = "https://welovealfa.com/.../apply"
    page.locator = MagicMock(return_value=MagicMock(
        first=MagicMock(
            count=AsyncMock(return_value=1),
            is_visible=AsyncMock(return_value=True),
            evaluate=AsyncMock(return_value=""),
        ),
    ))

    fake_patterns = [
        {"label": "Do you require visa sponsorship?",
         "selector": "div[data-q='visa'] button",
         "widget_type": "custom_select",
         "ancestor_classes": "", "aria_label": "", "fix_count": 3},
    ]
    with patch("jobpulse.form_engine.gotchas.GotchasDB.get_widget_patterns",
               return_value=fake_patterns):
        out = await _scan_learned_patterns(page)

    assert len(out) == 1
    assert out[0]["label"] == "Do you require visa sponsorship?"
    assert out[0]["type"] == "custom_select"
    assert out[0].get("locator") is not None


@pytest.mark.asyncio
async def test_scan_learned_patterns_skips_when_selector_not_on_page():
    from jobpulse.form_engine.field_scanner import _scan_learned_patterns

    page = MagicMock()
    page.url = "https://welovealfa.com/.../apply"
    page.locator = MagicMock(return_value=MagicMock(
        first=MagicMock(count=AsyncMock(return_value=0)),
    ))

    fake_patterns = [
        {"label": "Stale field", "selector": "#missing",
         "widget_type": "text", "ancestor_classes": "",
         "aria_label": "", "fix_count": 1},
    ]
    with patch("jobpulse.form_engine.gotchas.GotchasDB.get_widget_patterns",
               return_value=fake_patterns):
        out = await _scan_learned_patterns(page)

    assert out == []


@pytest.mark.asyncio
async def test_scan_learned_patterns_returns_empty_for_unknown_domain():
    from jobpulse.form_engine.field_scanner import _scan_learned_patterns

    page = MagicMock()
    page.url = "https://brand-new-domain.test/apply"
    with patch("jobpulse.form_engine.gotchas.GotchasDB.get_widget_patterns",
               return_value=[]):
        out = await _scan_learned_patterns(page)
    assert out == []
