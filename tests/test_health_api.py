"""Tests for the health API endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from jobpulse.health_api import health_router


@pytest.fixture()
def client():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


class TestGetStatus:
    def test_returns_daemon_health_and_platforms(self, client):
        with patch("jobpulse.healthcheck.check_daemon_health", return_value={"alive": True}), \
             patch("jobpulse.config.TELEGRAM_BOT_TOKEN", "tok"), \
             patch("jobpulse.config.SLACK_BOT_TOKEN", ""), \
             patch("jobpulse.config.DISCORD_BOT_TOKEN", ""):
            resp = client.get("/api/health/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daemon"] == {"alive": True}
        assert "telegram" in data["platforms"]
        assert "slack" not in data["platforms"]

    def test_no_platforms_when_tokens_empty(self, client):
        with patch("jobpulse.healthcheck.check_daemon_health", return_value={}), \
             patch("jobpulse.config.TELEGRAM_BOT_TOKEN", ""), \
             patch("jobpulse.config.SLACK_BOT_TOKEN", ""), \
             patch("jobpulse.config.DISCORD_BOT_TOKEN", ""):
            resp = client.get("/api/health/status")
        assert resp.json()["platforms"] == []


class TestGetErrors:
    def test_returns_errors(self, client):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            {"run_id": "r1", "agent_name": "gmail", "step_name": "fetch",
             "step_output": "timeout", "created_at": "2026-04-09"}
        ]
        with patch("jobpulse.process_logger._get_conn", return_value=mock_conn):
            resp = client.get("/api/health/errors?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()["errors"]) == 1

    def test_handles_db_error(self, client):
        with patch("jobpulse.process_logger._get_conn", side_effect=RuntimeError("db locked")):
            resp = client.get("/api/health/errors")
        assert resp.status_code == 200
        assert resp.json()["errors"] == []
        assert "error" in resp.json()


class TestGetAgentHealth:
    def test_returns_stats(self, client):
        with patch("jobpulse.process_logger.get_agent_stats",
                    return_value=[{"agent": "gmail", "success_rate": 0.95}]):
            resp = client.get("/api/health/agents")
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 1

    def test_handles_error(self, client):
        with patch("jobpulse.process_logger.get_agent_stats", side_effect=RuntimeError("fail")):
            resp = client.get("/api/health/agents")
        assert resp.json()["agents"] == []


class TestGetRateLimits:
    def test_returns_limits(self, client):
        with patch("shared.rate_monitor.get_current_limits",
                    return_value=[{"api": "openai", "remaining": 100}]):
            resp = client.get("/api/health/rate-limits")
        assert resp.status_code == 200
        assert len(resp.json()["limits"]) == 1

    def test_handles_error(self, client):
        with patch("shared.rate_monitor.get_current_limits", side_effect=ImportError("no module")):
            resp = client.get("/api/health/rate-limits")
        assert resp.json()["limits"] == []
