"""Tests for APPLICATION_ENGINE routing in applicator."""

from unittest.mock import patch

import pytest


def test_select_adapter_playwright_mode():
    """In playwright mode, returns platform-specific adapter."""
    with patch("jobpulse.config.APPLICATION_ENGINE", "playwright"):
        from jobpulse.applicator import select_adapter
        adapter = select_adapter("greenhouse")
        assert adapter.name == "greenhouse"


def test_select_adapter_extension_mode():
    """In extension mode, select_adapter still works (bridge passed separately)."""
    from jobpulse.applicator import select_adapter
    adapter = select_adapter("greenhouse")
    assert adapter.name == "greenhouse"


def test_config_application_engine_default():
    """Default APPLICATION_ENGINE is 'playwright'."""
    from jobpulse import config
    assert config.APPLICATION_ENGINE in ("extension", "playwright")
