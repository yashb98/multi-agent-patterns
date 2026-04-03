"""Tests for ExtensionAdapter orchestrator wiring."""

from unittest.mock import MagicMock
from jobpulse.ext_adapter import ExtensionAdapter


def test_ext_adapter_has_fill_and_submit():
    adapter = ExtensionAdapter.__new__(ExtensionAdapter)
    assert hasattr(adapter, "fill_and_submit")


def test_ext_adapter_creates_with_bridge():
    bridge = MagicMock()
    adapter = ExtensionAdapter(bridge)
    assert adapter.bridge is bridge
