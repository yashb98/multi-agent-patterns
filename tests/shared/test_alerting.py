"""Tests for alerting system."""

from unittest.mock import patch, MagicMock
from shared.alerting import AlertManager, AlertLevel


def test_alert_sends_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "12345")

    with patch("shared.alerting._send_telegram") as mock_send:
        mgr = AlertManager()
        mgr.alert(AlertLevel.CRITICAL, "OpenAI API down", source="circuit_breaker")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "CRITICAL" in msg
        assert "OpenAI API down" in msg


def test_alert_dedup_within_window():
    """Same alert within 5min window should be suppressed."""
    with patch("shared.alerting._send_telegram") as mock_send:
        mgr = AlertManager()
        mgr.alert(AlertLevel.WARNING, "High cost", source="cost_enforcer")
        mgr.alert(AlertLevel.WARNING, "High cost", source="cost_enforcer")
        # Second call suppressed — only 1 actual send
        assert mock_send.call_count == 1


def test_cost_spike_alert(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "12345")

    with patch("shared.alerting._send_telegram") as mock_send:
        mgr = AlertManager()
        mgr.cost_alert(spent=8.50, cap=10.00)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "85%" in msg or "8.50" in msg
