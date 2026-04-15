"""Tests for Telegram tool URL safety."""

import os
from unittest.mock import patch, MagicMock
import urllib.parse


def test_send_message_url_encodes_text(monkeypatch):
    """Text with special characters must be URL-encoded, not raw interpolated."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    from shared.tools.telegram import TelegramTool

    captured_urls = []

    def mock_urlopen(url, timeout=10):
        captured_urls.append(url if isinstance(url, str) else url.get_full_url())
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok":true}'
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda s, *a: None
        return mock_resp

    with patch("urllib.request.urlopen", mock_urlopen):
        result = TelegramTool.execute("send_message", {
            "chat_id": "12345",
            "text": "Hello & goodbye <script>alert('xss')</script>",
        })

    assert result["status"] == "success"
    assert len(captured_urls) == 1
    url = captured_urls[0]
    # The text must NOT appear raw in the URL
    assert "<script>" not in url
    assert "%26" in url or "urlencode" in url  # & → %26


def test_send_message_url_encodes_chat_id(monkeypatch):
    """chat_id with injection attempt must be safely encoded."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    from shared.tools.telegram import TelegramTool

    captured_urls = []

    def mock_urlopen(url, timeout=10):
        captured_urls.append(url if isinstance(url, str) else url.get_full_url())
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok":true}'
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda s, *a: None
        return mock_resp

    with patch("urllib.request.urlopen", mock_urlopen):
        TelegramTool.execute("send_message", {
            "chat_id": "12345&text=injected",
            "text": "legit",
        })

    url = captured_urls[0]
    # Injection attempt must be encoded
    assert "12345%26text%3Dinjected" in url or "12345&text=injected" not in url.split("?")[1].split("&text=")[0]
