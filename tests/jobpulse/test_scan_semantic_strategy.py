"""scan_semantic combines extract + match + classify into a strategy."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_semantic_returns_field_dicts_for_each_question():
    from jobpulse.form_engine.semantic_scanner import scan_semantic, Question

    page = MagicMock()
    page.url = "https://welovealfa.com/.../apply"

    fake_questions = [
        Question(text="Do you require visa sponsorship?", y=300, dom_path=""),
        Question(text="What is your notice period?", y=400, dom_path=""),
    ]
    fake_widgets = [
        {"matched": True, "selector": "#visa", "tag": "BUTTON",
         "role": "button", "aria_haspopup": "listbox",
         "ancestor_classes": "", "y": 360, "distance_px": 60,
         "match_kind": "proximity"},
        {"matched": True, "selector": "#notice", "tag": "SELECT",
         "role": "", "aria_haspopup": "", "ancestor_classes": "",
         "y": 460, "distance_px": 60, "match_kind": "proximity"},
    ]

    with patch("jobpulse.form_engine.semantic_scanner.extract_visible_questions",
               AsyncMock(return_value=fake_questions)), \
         patch("jobpulse.form_engine.semantic_scanner.match_question_to_widget",
               AsyncMock(side_effect=fake_widgets)):
        out = await scan_semantic(page)

    assert len(out) == 2
    assert out[0]["label"] == "Do you require visa sponsorship?"
    assert out[0]["type"] == "combobox"
    assert out[0]["selector"] == "#visa"
    assert out[0]["semantic_match"] is True

    assert out[1]["label"] == "What is your notice period?"
    assert out[1]["type"] == "select"


@pytest.mark.asyncio
async def test_scan_semantic_drops_unmatched_questions():
    from jobpulse.form_engine.semantic_scanner import scan_semantic, Question

    page = MagicMock()
    page.url = "https://example.com/apply"

    with patch("jobpulse.form_engine.semantic_scanner.extract_visible_questions",
               AsyncMock(return_value=[Question(text="Q1?", y=100, dom_path="")])), \
         patch("jobpulse.form_engine.semantic_scanner.match_question_to_widget",
               AsyncMock(return_value=None)):
        out = await scan_semantic(page)

    assert out == []
