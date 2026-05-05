"""extract_visible_questions walks the page, finds question-shaped text."""
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_extracts_questions_in_document_order():
    from jobpulse.form_engine.semantic_scanner import extract_visible_questions

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"text": "Complete your profile", "y": 100, "dom_path": ""},
        {"text": "First Name", "y": 150, "dom_path": ""},
        {"text": "What is your country of residence", "y": 250, "dom_path": ""},
        {"text": "Do you require visa sponsorship in the UK?", "y": 320, "dom_path": ""},
        {"text": "What is your notice period?", "y": 380, "dom_path": ""},
        {"text": "Are you happy to work remotely all the time?", "y": 450, "dom_path": ""},
        {"text": "£85,500 - £118,000 Per year", "y": 480, "dom_path": ""},
        {"text": "Apply now", "y": 600, "dom_path": ""},
    ])

    qs = await extract_visible_questions(page)
    texts = [q.text for q in qs]
    assert "Do you require visa sponsorship in the UK?" in texts
    assert "What is your notice period?" in texts
    assert "Are you happy to work remotely all the time?" in texts
    assert "Apply now" not in texts
    assert "£85,500 - £118,000 Per year" not in texts
    visa_idx = texts.index("Do you require visa sponsorship in the UK?")
    notice_idx = texts.index("What is your notice period?")
    assert visa_idx < notice_idx


@pytest.mark.asyncio
async def test_recognizes_question_starters_without_question_mark():
    from jobpulse.form_engine.semantic_scanner import extract_visible_questions

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"text": "Tell us about your experience", "y": 200, "dom_path": ""},
        {"text": "What is your full name", "y": 300, "dom_path": ""},
        {"text": "Submit application", "y": 400, "dom_path": ""},
        {"text": "Click here to upload", "y": 450, "dom_path": ""},
    ])
    qs = await extract_visible_questions(page)
    texts = [q.text for q in qs]
    assert "Tell us about your experience" in texts
    assert "What is your full name" in texts
    assert "Submit application" not in texts
    assert "Click here to upload" not in texts


@pytest.mark.asyncio
async def test_filters_out_pure_field_labels_without_context():
    """Bare 'First Name' is a field label, not a standalone question — let
    the existing label-based scanners handle it."""
    from jobpulse.form_engine.semantic_scanner import extract_visible_questions

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"text": "First Name", "y": 100, "dom_path": ""},
        {"text": "Email Address", "y": 150, "dom_path": ""},
    ])
    qs = await extract_visible_questions(page)
    assert qs == []
