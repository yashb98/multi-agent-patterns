"""vision_augment_scan: vision LLM finds DOM-missed fields."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_vision_augment_returns_fields_not_already_in_existing():
    from jobpulse.form_engine.vision_gate import vision_augment_scan, _CACHE

    _CACHE.clear()
    page = MagicMock()
    page.url = "https://welovealfa.com/.../complete-profile"
    page.screenshot = AsyncMock(return_value=b"fake-png-bytes")
    page.title = AsyncMock(return_value="Software Engineer (Data) at Revolut")

    existing_fields = [
        {"label": "Enter your first name", "type": "text"},
        {"label": "Enter email address", "type": "text"},
    ]

    fake_llm_response = {
        "missing_fields": [
            {"label": "Do you require visa sponsorship in the United Kingdom?",
             "type": "select", "options": ["Yes", "No"]},
            {"label": "What is your notice period?",
             "type": "select", "options": ["Immediately", "1 month", "3 months"]},
        ]
    }

    with patch("jobpulse.form_engine.vision_gate._call_vision_llm",
               AsyncMock(return_value=fake_llm_response)):
        result = await vision_augment_scan(page, existing_fields)

    assert len(result) == 2
    assert all(f.get("vision_only") is True for f in result)
    assert result[0]["label"] == "Do you require visa sponsorship in the United Kingdom?"
    assert result[1]["type"] == "select"


@pytest.mark.asyncio
async def test_vision_augment_caches_by_content_hash():
    from jobpulse.form_engine.vision_gate import vision_augment_scan, _CACHE

    _CACHE.clear()
    page = MagicMock()
    page.url = "https://example.com/apply"
    page.screenshot = AsyncMock(return_value=b"identical-bytes")
    page.title = AsyncMock(return_value="Form")

    fake_response = {"missing_fields": [{"label": "X", "type": "text"}]}
    mock_llm = AsyncMock(return_value=fake_response)

    with patch("jobpulse.form_engine.vision_gate._call_vision_llm", mock_llm):
        await vision_augment_scan(page, [])
        await vision_augment_scan(page, [])

    assert mock_llm.call_count == 1


@pytest.mark.asyncio
async def test_vision_augment_returns_empty_on_llm_error():
    from jobpulse.form_engine.vision_gate import vision_augment_scan, _CACHE

    _CACHE.clear()
    page = MagicMock()
    page.url = "https://example.com/apply"
    page.screenshot = AsyncMock(return_value=b"bytes")
    page.title = AsyncMock(return_value="Form")

    with patch("jobpulse.form_engine.vision_gate._call_vision_llm",
               AsyncMock(side_effect=Exception("vision API down"))):
        result = await vision_augment_scan(page, [])
    assert result == []
