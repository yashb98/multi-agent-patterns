"""Tests for S11 redesign: Kimi vision recovery via chat.completions.

Audit 2026-05-10 / Slice S11 (redesign) / TP-21.

Confirms the field_mapper vision sites switched from
`client.responses.create()` (OpenAI Responses API — Moonshot 404s) to
`client.chat.completions.create()` with a multimodal `image_url` content
type, and that the model is the Moonshot vision model.

Live verification (real Moonshot call): scripts/audit_s11_kimi_vision_live.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.form_engine import field_mapper


def _vision_response(text: str = '{"name": "Yash"}') -> MagicMock:
    """Mock a chat.completions response shape (post-redesign)."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    response.usage = MagicMock(prompt_tokens=100, completion_tokens=10)
    response.model = field_mapper._VISION_MODEL
    return response


class TestVisionRecoveryUsesChatCompletions:
    """Vision recovery must call chat.completions.create, not responses.create."""

    def test_vision_recovery_uses_chat_completions_api(self):
        """recover_failed_fields_with_vision must call chat.completions, not
        responses (which Moonshot 404s on)."""
        import asyncio

        async def run():
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _vision_response()
            mock_page = MagicMock()
            mock_page.screenshot.return_value = b"fake-png-bytes"

            async def fake_screenshot(page):
                return b"fake-png-bytes"

            with patch.object(field_mapper, "get_openai_client", return_value=mock_client), \
                 patch.object(field_mapper, "_screenshot_form_area", fake_screenshot), \
                 patch("jobpulse.applicator.PROFILE", {"name": "Yash"}):
                await field_mapper.recover_failed_fields_with_vision(
                    page=mock_page,
                    failed_fields=[{
                        "field": {"label": "Name", "type": "text"},
                        "attempted_value": "Yash",
                        "result": {"actual_value": "", "options_seen": []},
                    }],
                    profile={"name": "Yash"},
                    custom_answers={},
                    platform="generic",
                )
            assert mock_client.chat.completions.create.called
            assert not mock_client.responses.create.called, (
                "responses.create() must NOT be called — Moonshot doesn't "
                "implement /v1/responses (TP-21 root cause)"
            )

        asyncio.run(run())

    def test_vision_recovery_uses_moonshot_vision_model(self):
        """The model passed to chat.completions must be the Moonshot
        vision model, not gpt-4.1-mini (Moonshot rejects gpt-* names)."""
        import asyncio

        async def run():
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _vision_response()
            mock_page = MagicMock()

            async def fake_screenshot(page):
                return b"fake-png-bytes"

            with patch.object(field_mapper, "get_openai_client", return_value=mock_client), \
                 patch.object(field_mapper, "_screenshot_form_area", fake_screenshot), \
                 patch("jobpulse.applicator.PROFILE", {}):
                await field_mapper.recover_failed_fields_with_vision(
                    page=mock_page,
                    failed_fields=[{
                        "field": {"label": "Name", "type": "text"},
                        "attempted_value": "Yash",
                        "result": {"actual_value": "", "options_seen": []},
                    }],
                    profile={},
                    custom_answers={},
                    platform="generic",
                )
            kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert kwargs["model"] == field_mapper._VISION_MODEL
            assert kwargs["model"].startswith("moonshot-"), (
                f"Vision model must be a Moonshot model, got: {kwargs['model']}"
            )

        asyncio.run(run())

    def test_vision_recovery_uses_multimodal_image_url_content(self):
        """The content payload must be a list with a {type: image_url,
        image_url: {url: ...}} entry — that's the multimodal shape both
        OpenAI and Moonshot accept (input_text/input_image is Responses-only)."""
        import asyncio

        async def run():
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _vision_response()
            mock_page = MagicMock()

            async def fake_screenshot(page):
                return b"fake-png-bytes"

            with patch.object(field_mapper, "get_openai_client", return_value=mock_client), \
                 patch.object(field_mapper, "_screenshot_form_area", fake_screenshot), \
                 patch("jobpulse.applicator.PROFILE", {}):
                await field_mapper.recover_failed_fields_with_vision(
                    page=mock_page,
                    failed_fields=[{
                        "field": {"label": "Name", "type": "text"},
                        "attempted_value": "Yash",
                        "result": {"actual_value": "", "options_seen": []},
                    }],
                    profile={},
                    custom_answers={},
                    platform="generic",
                )
            kwargs = mock_client.chat.completions.create.call_args.kwargs
            messages = kwargs["messages"]
            assert len(messages) == 1
            content = messages[0]["content"]
            kinds = [c["type"] for c in content]
            assert "image_url" in kinds, f"missing image_url in content: {kinds}"
            image_entry = next(c for c in content if c["type"] == "image_url")
            assert "image_url" in image_entry, "image_url entry missing nested image_url dict"
            assert image_entry["image_url"]["url"].startswith("data:image/png;base64,")
            # input_text / input_image are OpenAI Responses-only — Moonshot rejects them.
            assert "input_text" not in kinds, "input_text is Responses-only, breaks Moonshot"
            assert "input_image" not in kinds, "input_image is Responses-only, breaks Moonshot"

        asyncio.run(run())


class TestVisionModelOverridable:
    """The vision model is env-overridable so a future Moonshot model name
    swap doesn't require a code change."""

    def test_vision_model_default_is_moonshot_vision(self):
        assert field_mapper._VISION_MODEL.startswith("moonshot-")
        assert "vision" in field_mapper._VISION_MODEL

    def test_vision_model_env_override(self, monkeypatch):
        """VISION_MODEL env var overrides the default. Re-import to pick up."""
        import importlib
        monkeypatch.setenv("VISION_MODEL", "moonshot-v1-128k-vision-preview")
        importlib.reload(field_mapper)
        try:
            assert field_mapper._VISION_MODEL == "moonshot-v1-128k-vision-preview"
        finally:
            monkeypatch.delenv("VISION_MODEL", raising=False)
            importlib.reload(field_mapper)
