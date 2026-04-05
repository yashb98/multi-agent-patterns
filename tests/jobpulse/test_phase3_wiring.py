"""Tests for Phase 3: platform adapter wiring — extension-only mode."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from jobpulse.ats_adapters import get_adapter
from jobpulse.ext_adapter import ExtensionAdapter


def test_get_adapter_returns_extension_adapter():
    """get_adapter always returns ExtensionAdapter in extension-only mode."""
    # Reset singleton
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance
    adapter = get_adapter("greenhouse")
    assert isinstance(adapter, ExtensionAdapter)
    # Cleanup
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance


def test_get_adapter_singleton():
    """Extension adapter is a singleton."""
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance
    a1 = get_adapter("greenhouse")
    a2 = get_adapter("linkedin")
    assert a1 is a2
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance


def test_get_adapter_all_platforms():
    """Extension adapter handles all platforms."""
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance
    for platform in ["greenhouse", "lever", "linkedin", "indeed", "workday", "generic", None]:
        adapter = get_adapter(platform)
        assert isinstance(adapter, ExtensionAdapter)
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance


def test_call_fill_and_submit_sync():
    """_call_fill_and_submit handles sync adapters."""
    from jobpulse.applicator import _call_fill_and_submit

    mock_adapter = MagicMock()
    mock_adapter.fill_and_submit.return_value = {"success": True}

    result = _call_fill_and_submit(mock_adapter, url="http://test.com", cv_path=Path("/cv.pdf"))
    assert result == {"success": True}


def test_call_fill_and_submit_async():
    """_call_fill_and_submit handles async adapters via asyncio.run."""
    from jobpulse.applicator import _call_fill_and_submit

    async def async_fill(**kwargs):
        return {"success": True, "async": True}

    mock_adapter = MagicMock()
    mock_adapter.bridge = None  # prevent MagicMock auto-attr from triggering bridge-loop path
    mock_adapter.fill_and_submit.return_value = async_fill()

    result = _call_fill_and_submit(mock_adapter, url="http://test.com", cv_path=Path("/cv.pdf"))
    assert result["success"] is True
    assert result["async"] is True
