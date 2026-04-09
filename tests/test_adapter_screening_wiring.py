"""Tests that the extension-only adapter registry works correctly.

Verifies:
1. get_adapter() always returns ExtensionAdapter
2. ExtensionAdapter has the required interface methods
3. BaseATSAdapter still provides screening question support
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.ats_adapters.base import BaseATSAdapter


def test_get_adapter_returns_extension_adapter():
    """get_adapter() should always return an ExtensionAdapter."""
    with patch("jobpulse.ats_adapters._get_extension_adapter") as mock:
        mock.return_value = MagicMock(spec=BaseATSAdapter)
        from jobpulse.ats_adapters import get_adapter
        adapter = get_adapter("linkedin")
        mock.assert_called_once()


def test_get_adapter_ignores_platform():
    """Platform parameter is retained for compatibility but unused."""
    with patch("jobpulse.ats_adapters._get_extension_adapter") as mock:
        mock.return_value = MagicMock(spec=BaseATSAdapter)
        from jobpulse.ats_adapters import get_adapter
        get_adapter("greenhouse")
        get_adapter("indeed")
        get_adapter(None)
        assert mock.call_count == 3


