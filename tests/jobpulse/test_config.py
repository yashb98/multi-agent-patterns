"""Tests for jobpulse config defaults."""

import importlib
import os
import unittest.mock


def test_auto_submit_defaults_to_false(monkeypatch):
    """CRITICAL: auto_submit must default to false per docs and safety policy.

    Patches load_dotenv to a no-op so code-level defaults are tested,
    not values overridden by the .env file.
    """
    monkeypatch.delenv("JOB_AUTOPILOT_AUTO_SUBMIT", raising=False)
    monkeypatch.delenv("JOB_AUTOPILOT_MAX_DAILY", raising=False)
    with unittest.mock.patch("dotenv.load_dotenv"):
        import jobpulse.config as cfg
        importlib.reload(cfg)
    assert cfg.JOB_AUTOPILOT_AUTO_SUBMIT is False
    assert cfg.JOB_AUTOPILOT_MAX_DAILY == 10


def test_auto_submit_respects_env_true(monkeypatch):
    monkeypatch.setenv("JOB_AUTOPILOT_AUTO_SUBMIT", "true")
    monkeypatch.setenv("JOB_AUTOPILOT_MAX_DAILY", "25")
    with unittest.mock.patch("dotenv.load_dotenv"):
        import jobpulse.config as cfg
        importlib.reload(cfg)
    assert cfg.JOB_AUTOPILOT_AUTO_SUBMIT is True
    assert cfg.JOB_AUTOPILOT_MAX_DAILY == 25
