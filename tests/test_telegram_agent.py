"""Tests for the Telegram agent (send/receive via curl subprocess)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from jobpulse import telegram_agent


class TestSendMessage:
    def test_successful_send(self):
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"ok": True, "result": {}})
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok123"), \
             patch("jobpulse.telegram_agent.TELEGRAM_CHAT_ID", "chat456"), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            assert telegram_agent.send_message("hello") is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "curl" in args[0]

    def test_missing_token_returns_false(self):
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", ""), \
             patch("jobpulse.telegram_agent.TELEGRAM_CHAT_ID", "chat"):
            assert telegram_agent.send_message("hello") is False

    def test_missing_chat_id_returns_false(self):
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("jobpulse.telegram_agent.TELEGRAM_CHAT_ID", ""):
            assert telegram_agent.send_message("hello") is False

    def test_api_error_returns_false(self):
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"ok": False, "description": "Unauthorized"})
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("jobpulse.telegram_agent.TELEGRAM_CHAT_ID", "chat"), \
             patch("subprocess.run", return_value=mock_result):
            assert telegram_agent.send_message("hello") is False

    def test_subprocess_error_returns_false(self):
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("jobpulse.telegram_agent.TELEGRAM_CHAT_ID", "chat"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("curl", 15)):
            assert telegram_agent.send_message("hello") is False

    def test_custom_chat_id(self):
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"ok": True})
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("jobpulse.telegram_agent.TELEGRAM_CHAT_ID", "default"), \
             patch("subprocess.run", return_value=mock_result):
            telegram_agent.send_message("hello", chat_id="custom789")
            payload = json.loads(subprocess.run.call_args[0][0][-1])
            assert payload["chat_id"] == "custom789"


class TestGetUpdates:
    def test_returns_messages(self):
        updates = [{"update_id": 1, "message": {"text": "hi"}}]
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"ok": True, "result": updates})
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("subprocess.run", return_value=mock_result):
            result = telegram_agent.get_updates(offset=0)
            assert len(result) == 1
            assert result[0]["message"]["text"] == "hi"

    def test_timeout_returns_empty(self):
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("curl", 40)):
            result = telegram_agent.get_updates(long_poll=True)
            assert result == []

    def test_json_error_returns_empty(self):
        mock_result = MagicMock()
        mock_result.stdout = "not json"
        with patch("jobpulse.telegram_agent.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("subprocess.run", return_value=mock_result):
            result = telegram_agent.get_updates()
            assert result == []
