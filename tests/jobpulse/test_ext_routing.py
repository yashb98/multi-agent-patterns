"""Tests for APPLICATION_ENGINE routing in applicator."""

from unittest.mock import patch

import pytest


def test_select_adapter_returns_extension():
    """select_adapter returns ExtensionAdapter in extension-only mode."""
    from jobpulse.applicator import select_adapter
    from jobpulse.ext_adapter import ExtensionAdapter
    from jobpulse.ats_adapters import get_adapter

    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance
    adapter = select_adapter("greenhouse")
    assert isinstance(adapter, ExtensionAdapter)
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance


def test_config_application_engine_default():
    """Default APPLICATION_ENGINE is 'extension'."""
    from jobpulse import config
    assert config.APPLICATION_ENGINE == "extension"
