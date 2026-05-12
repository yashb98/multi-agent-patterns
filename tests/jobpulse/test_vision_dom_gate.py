"""Tests for the vision-DOM agreement gate on low-confidence reasoner output."""
from unittest.mock import patch, MagicMock
import pytest
from jobpulse.vision_tier import classify_page_type_from_screenshot


class TestVisionPageTypeClassifier:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("jobpulse.vision_tier.OPENAI_API_KEY", "")
        result = await classify_page_type_from_screenshot(b"fake_png")
        assert result is None

    @pytest.mark.asyncio
    async def test_extracts_page_type_from_response(self, monkeypatch):
        monkeypatch.setattr("jobpulse.vision_tier.OPENAI_API_KEY", "x")
        fake_resp = MagicMock()
        fake_resp.output_text = "login_form"
        fake_client = MagicMock()
        fake_client.responses.create = MagicMock(return_value=fake_resp)
        with patch("jobpulse.vision_tier.get_openai_client", return_value=fake_client):
            with patch("jobpulse.vision_tier.record_openai_usage", create=True):
                result = await classify_page_type_from_screenshot(b"fake_png")
        assert result == "login_form"

    @pytest.mark.asyncio
    async def test_normalizes_invalid_page_type_to_unknown(self, monkeypatch):
        monkeypatch.setattr("jobpulse.vision_tier.OPENAI_API_KEY", "x")
        fake_resp = MagicMock()
        fake_resp.output_text = "rocket_ship"
        fake_client = MagicMock()
        fake_client.responses.create = MagicMock(return_value=fake_resp)
        with patch("jobpulse.vision_tier.get_openai_client", return_value=fake_client):
            with patch("jobpulse.vision_tier.record_openai_usage", create=True):
                result = await classify_page_type_from_screenshot(b"fake_png")
        assert result == "unknown"

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, monkeypatch):
        monkeypatch.setattr("jobpulse.vision_tier.OPENAI_API_KEY", "x")
        fake_client = MagicMock()
        fake_client.responses.create = MagicMock(side_effect=RuntimeError("boom"))
        with patch("jobpulse.vision_tier.get_openai_client", return_value=fake_client):
            result = await classify_page_type_from_screenshot(b"fake_png")
        assert result is None
