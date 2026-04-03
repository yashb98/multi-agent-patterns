"""Tests for the Gemini Nano bridge integration (Tier 3)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from jobpulse.ext_bridge import ExtensionBridge


@pytest.mark.asyncio
async def test_analyze_field_locally_sends_command():
    """Bridge sends analyze_field command to extension."""
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.analyze_field_locally = AsyncMock(return_value="Yes")

    result = await bridge.analyze_field_locally("Do you have a driving licence?", "radio", [])
    assert result == "Yes"


@pytest.mark.asyncio
async def test_analyze_field_locally_with_options():
    """Bridge passes options to extension."""
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.analyze_field_locally = AsyncMock(return_value="Male")

    result = await bridge.analyze_field_locally(
        "What is your gender?", "select", ["Male", "Female", "Non-binary", "Prefer not to say"]
    )
    assert result == "Male"


@pytest.mark.asyncio
async def test_analyze_field_locally_returns_none_on_unavailable():
    """Returns None when Gemini Nano is not available."""
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.analyze_field_locally = AsyncMock(return_value=None)

    result = await bridge.analyze_field_locally("Any question?", "text", [])
    assert result is None
