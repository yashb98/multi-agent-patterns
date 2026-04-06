"""Tests for extension-only adapter routing."""

from unittest.mock import patch, MagicMock

import pytest


def test_get_adapter_returns_extension_adapter():
    """get_adapter() always returns ExtensionAdapter regardless of platform."""
    with patch("jobpulse.ats_adapters._get_extension_adapter") as mock:
        mock.return_value = MagicMock()
        mock.return_value.name = "extension"
        from jobpulse.ats_adapters import get_adapter
        adapter = get_adapter("greenhouse")
        assert adapter.name == "extension"


def test_get_adapter_works_without_platform():
    """get_adapter(None) still returns ExtensionAdapter."""
    with patch("jobpulse.ats_adapters._get_extension_adapter") as mock:
        mock.return_value = MagicMock()
        mock.return_value.name = "extension"
        from jobpulse.ats_adapters import get_adapter
        adapter = get_adapter(None)
        assert adapter.name == "extension"


def test_config_application_engine_default():
    """Default APPLICATION_ENGINE is 'extension'."""
    from jobpulse import config
    assert config.APPLICATION_ENGINE == "extension"
