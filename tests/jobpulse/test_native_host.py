# tests/jobpulse/test_native_host.py
"""Tests for Native Messaging host bootstrap."""

import json
import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest


def test_health_check_when_backend_running(monkeypatch):
    """When FastAPI is already running, bootstrap returns ready immediately."""
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.get", return_value=mock_resp):
        from jobpulse.native_host import check_backend_health
        assert check_backend_health() is True


def test_health_check_when_backend_down(monkeypatch):
    """When FastAPI is not running, health check returns False."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        from jobpulse.native_host import check_backend_health
        assert check_backend_health() is False


def test_start_backend_launches_subprocess(monkeypatch):
    """start_backend() launches the FastAPI server as a detached process."""
    mock_popen = MagicMock()
    with patch("subprocess.Popen", return_value=mock_popen) as popen_call:
        from jobpulse.native_host import start_backend
        start_backend()
        popen_call.assert_called_once()
        args = popen_call.call_args
        assert "jobpulse.runner" in str(args)
