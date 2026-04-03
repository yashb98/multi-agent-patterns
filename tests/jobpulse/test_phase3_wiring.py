"""Tests for Phase 3: platform adapter wiring via APPLICATION_ENGINE."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from jobpulse.ats_adapters import get_adapter, _ext_adapter
from jobpulse.ext_adapter import ExtensionAdapter


def test_get_adapter_playwright_mode():
    """Default mode returns Playwright adapter."""
    with patch("jobpulse.config.APPLICATION_ENGINE", "playwright"):
        adapter = get_adapter("greenhouse")
    assert adapter.name == "greenhouse"


def test_get_adapter_extension_mode():
    """Extension mode returns ExtensionAdapter regardless of platform."""
    import jobpulse.ats_adapters as mod

    mod._ext_adapter = None  # Reset singleton
    with patch("jobpulse.config.APPLICATION_ENGINE", "extension"):
        adapter = get_adapter("greenhouse")
    assert isinstance(adapter, ExtensionAdapter)
    mod._ext_adapter = None  # Cleanup


def test_get_adapter_extension_singleton():
    """Extension adapter is a singleton."""
    import jobpulse.ats_adapters as mod

    mod._ext_adapter = None
    with patch("jobpulse.config.APPLICATION_ENGINE", "extension"):
        a1 = get_adapter("greenhouse")
        a2 = get_adapter("linkedin")
    assert a1 is a2
    mod._ext_adapter = None


def test_get_adapter_extension_all_platforms():
    """Extension adapter handles all platforms."""
    import jobpulse.ats_adapters as mod

    mod._ext_adapter = None
    with patch("jobpulse.config.APPLICATION_ENGINE", "extension"):
        for platform in ["greenhouse", "lever", "linkedin", "indeed", "workday", "generic", None]:
            adapter = get_adapter(platform)
            assert isinstance(adapter, ExtensionAdapter)
    mod._ext_adapter = None


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
    mock_adapter.fill_and_submit.return_value = async_fill()

    result = _call_fill_and_submit(mock_adapter, url="http://test.com", cv_path=Path("/cv.pdf"))
    assert result["success"] is True
    assert result["async"] is True
