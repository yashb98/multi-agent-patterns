"""Tests for the vision tier (screenshot → GPT-4o-mini)."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from jobpulse.vision_tier import analyze_field_screenshot, _build_vision_prompt


def test_build_vision_prompt():
    prompt = _build_vision_prompt("What is your gender?", "select")
    assert "What is your gender?" in prompt
    assert "select" in prompt


def test_build_vision_prompt_textarea():
    prompt = _build_vision_prompt("Why do you want this role?", "textarea")
    assert "Why do you want this role?" in prompt


@pytest.mark.asyncio
async def test_analyze_returns_answer_on_success():
    """Vision tier returns parsed answer from LLM."""
    mock_response = MagicMock()
    mock_response.output_text = "Male"

    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_response

    with patch("jobpulse.vision_tier.OPENAI_API_KEY", "test-key"), \
         patch("jobpulse.vision_tier.get_openai_client", return_value=mock_client):
        result = await analyze_field_screenshot(
            "What is your gender?",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            "select",
        )
    assert result == "Male"


@pytest.mark.asyncio
async def test_analyze_returns_none_on_error():
    """Vision tier returns None when API fails."""
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = Exception("API down")

    with patch("jobpulse.vision_tier.OPENAI_API_KEY", "test-key"), \
         patch("jobpulse.vision_tier.get_openai_client", return_value=mock_client):
        result = await analyze_field_screenshot(
            "What is your gender?",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            "select",
        )
    assert result is None


@pytest.mark.asyncio
async def test_analyze_no_api_key_returns_none():
    """Vision tier returns None when no API key configured."""
    with patch("jobpulse.vision_tier.OPENAI_API_KEY", ""):
        result = await analyze_field_screenshot(
            "What is your gender?",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            "select",
        )
    assert result is None
