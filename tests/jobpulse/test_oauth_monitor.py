"""Tests for Google OAuth health monitoring."""
import json
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestOAuthMonitor:
    """Test OAuth health check and alerting."""

    def test_healthy_token(self, tmp_path):
        """Token with all scopes and valid expiry returns healthy."""
        from jobpulse.oauth_monitor import check_oauth_health

        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "test",
            "refresh_token": "test_refresh",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/calendar.readonly",
                "https://www.googleapis.com/auth/drive.file",
            ],
            "expiry": "2026-12-31T00:00:00Z",
        }))

        result = check_oauth_health(token_path=token_file)
        assert result["status"] == "healthy"
        assert result["missing_scopes"] == []

    def test_missing_scopes(self, tmp_path):
        """Token missing scopes returns scope_mismatch."""
        from jobpulse.oauth_monitor import check_oauth_health

        token_file = tmp_path / "google_token.json"
        token_file.write_text(json.dumps({
            "token": "test",
            "refresh_token": "test_refresh",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
            "expiry": "2026-12-31T00:00:00Z",
        }))

        result = check_oauth_health(token_path=token_file)
        assert result["status"] == "scope_mismatch"
        assert len(result["missing_scopes"]) == 3

    def test_missing_token_file(self, tmp_path):
        """Missing token file returns missing status."""
        from jobpulse.oauth_monitor import check_oauth_health

        result = check_oauth_health(token_path=tmp_path / "nonexistent.json")
        assert result["status"] == "missing"

    def test_alert_message_for_scope_mismatch(self):
        """Alert message includes re-auth command."""
        from jobpulse.oauth_monitor import format_alert

        health = {
            "status": "scope_mismatch",
            "missing_scopes": ["gmail.modify", "drive.file"],
        }
        msg = format_alert(health)
        assert "setup_integrations.py" in msg
        assert "gmail.modify" in msg

    def test_no_alert_when_healthy(self):
        """Healthy token produces no alert."""
        from jobpulse.oauth_monitor import format_alert

        health = {"status": "healthy", "missing_scopes": []}
        msg = format_alert(health)
        assert msg is None
