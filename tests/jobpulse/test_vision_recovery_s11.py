"""Tests for vision recovery — Audit 2026-05-10 / Slice S11 / TP-21.

Vision recovery was POSTing to `https://api.moonshot.ai/v1/responses` and
getting 404 because Moonshot doesn't implement OpenAI's `/v1/responses`
endpoint. The fix pins vision to OpenAI's `api.openai.com/v1` (bypasses
the Kimi mandate which covers chat completions only) and uses the
multi-provider-compatible `chat.completions.create()` API. Skips cleanly
when no `OPENAI_API_KEY` is configured.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def test_get_openai_vision_client_returns_client_with_openai_key(monkeypatch):
    """When OPENAI_API_KEY is set, returns an OpenAI client pinned to
    api.openai.com/v1 (bypassing any OPENAI_BASE_URL that might point at
    Moonshot under the Kimi mandate)."""
    from shared.agents import get_openai_vision_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.moonshot.ai/v1")  # red herring

    client = get_openai_vision_client()
    assert client is not None
    # Must be pinned to OpenAI, not whatever OPENAI_BASE_URL says
    assert "api.openai.com" in str(client.base_url)
    assert "moonshot" not in str(client.base_url)


def test_get_openai_vision_client_returns_none_without_key(monkeypatch):
    """When no OPENAI_API_KEY, returns None — caller must handle the
    skip cleanly. Per S11 design, vision is OpenAI-only in this codebase."""
    from shared.agents import get_openai_vision_client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_openai_vision_client() is None


@pytest.mark.asyncio
async def test_vision_recovery_skips_cleanly_without_openai_key(monkeypatch):
    """When OPENAI_API_KEY is not set, recover_failed_fields_with_vision
    must return ({}, 0) without calling any LLM endpoint. No exceptions,
    no 404s, no incremented call count."""
    from jobpulse.form_engine.field_mapper import recover_failed_fields_with_vision

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Mock screenshot to avoid Playwright interaction
    async def fake_screenshot(page):
        return b"fake_png_bytes"

    page = MagicMock()
    failed_fields = [
        {
            "field": {"label": "Test Field", "type": "text"},
            "attempted_value": "abc",
            "result": {"actual_value": "", "options_seen": []},
        },
    ]

    with patch("jobpulse.form_engine.field_mapper._screenshot_form_area",
               side_effect=fake_screenshot):
        recovered, calls = await recover_failed_fields_with_vision(
            page, failed_fields, profile={}, custom_answers={}, platform="test",
        )

    assert recovered == {}
    assert calls == 0


@pytest.mark.asyncio
async def test_vision_recovery_uses_chat_completions_not_responses(monkeypatch):
    """The fix replaces `client.responses.create()` with
    `client.chat.completions.create()` — multimodal API that works on
    OpenAI's actual endpoint. No call to `/v1/responses` should be made."""
    from jobpulse.form_engine import field_mapper

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Mock screenshot
    async def fake_screenshot(page):
        return b"fake_png_bytes"

    # Mock vision client — record which API method gets called
    method_calls: list[str] = []
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(
        content='{"Test Field": "corrected_value"}'
    ))]

    mock_chat = MagicMock()
    mock_chat.completions.create = MagicMock(
        side_effect=lambda **kwargs: (
            method_calls.append("chat.completions.create"),
            mock_response,
        )[1]
    )

    mock_responses = MagicMock()
    mock_responses.create = MagicMock(
        side_effect=lambda **kwargs: (
            method_calls.append("responses.create"),
            mock_response,
        )[1]
    )

    mock_client = MagicMock()
    mock_client.chat = mock_chat
    mock_client.responses = mock_responses

    page = MagicMock()
    failed_fields = [
        {
            "field": {"label": "Test Field", "type": "text"},
            "attempted_value": "abc",
            "result": {"actual_value": "", "options_seen": []},
        },
    ]

    with patch.object(field_mapper, "_screenshot_form_area", side_effect=fake_screenshot), \
         patch("shared.agents.get_openai_vision_client", return_value=mock_client):
        recovered, calls = await field_mapper.recover_failed_fields_with_vision(
            page, failed_fields, profile={}, custom_answers={}, platform="test",
        )

    # Must use chat.completions.create, NOT responses.create
    assert "chat.completions.create" in method_calls, \
        f"Expected chat.completions.create, got: {method_calls}"
    assert "responses.create" not in method_calls, \
        f"Should NOT use responses.create (Moonshot 404), got: {method_calls}"
    assert recovered == {"Test Field": "corrected_value"}
    assert calls == 1


@pytest.mark.asyncio
async def test_vision_recovery_passes_image_url_content_type(monkeypatch):
    """The chat.completions request must include an `image_url` content
    item — that's how multimodal vision is encoded in the chat completions
    API (vs the older `input_image` shape used by the responses API)."""
    from jobpulse.form_engine import field_mapper

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def fake_screenshot(page):
        return b"PNG_BYTES_HERE"

    captured_kwargs: dict = {}
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{}'))]

    def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    mock_client = MagicMock()
    mock_client.chat.completions.create = fake_create

    with patch.object(field_mapper, "_screenshot_form_area", side_effect=fake_screenshot), \
         patch("shared.agents.get_openai_vision_client", return_value=mock_client):
        await field_mapper.recover_failed_fields_with_vision(
            MagicMock(),
            [{"field": {"label": "L", "type": "text"},
              "attempted_value": "x",
              "result": {"actual_value": "", "options_seen": []}}],
            profile={}, custom_answers={}, platform="t",
        )

    # Verify the messages payload structure matches OpenAI's chat completions
    # multimodal format: list of content items with `type` keys.
    messages = captured_kwargs.get("messages", [])
    assert messages, f"No messages in kwargs: {captured_kwargs}"
    user_msg = messages[0]
    content = user_msg.get("content", [])
    types = [c.get("type") for c in content if isinstance(c, dict)]
    assert "text" in types, f"Missing text content: {types}"
    assert "image_url" in types, f"Missing image_url content: {types}"
    # No legacy `input_image` from the responses API
    assert "input_image" not in types, f"Legacy input_image leaked: {types}"
