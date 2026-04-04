"""Tests for the ExtensionAdapter that wraps bridge + state machine."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.ext_adapter import ExtensionAdapter
from jobpulse.ext_bridge import ExtensionBridge
from jobpulse.ext_models import (
    PageSnapshot, FieldInfo, ButtonInfo, VerificationWall, FillResult,
)


def _snap(url="", fields=None, buttons=None, wall=None, text="", has_files=False):
    return PageSnapshot(
        url=url, title="Test", fields=fields or [], buttons=buttons or [],
        verification_wall=wall, page_text_preview=text,
        has_file_inputs=has_files, iframe_count=0, timestamp=1000,
    )


@pytest.fixture
def mock_bridge():
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.connected = True
    return bridge


@pytest.fixture
def adapter(mock_bridge):
    return ExtensionAdapter(bridge=mock_bridge)


@pytest.mark.asyncio
async def test_adapter_detect_always_false(adapter):
    """ExtensionAdapter.detect() always returns False — routing is by config, not URL."""
    assert adapter.detect("https://anything.com") is False


@pytest.mark.asyncio
async def test_fill_and_submit_greenhouse_happy_path(adapter, mock_bridge, tmp_path):
    """Greenhouse single-page: orchestrator navigates → detects form → fills → confirms."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")

    # Application form with contact fields
    snap_form = _snap(
        url="https://boards.greenhouse.io/company/jobs/1",
        fields=[
            FieldInfo(selector="#first_name", input_type="text", label="First Name"),
            FieldInfo(selector="#last_name", input_type="text", label="Last Name"),
            FieldInfo(selector="#email", input_type="email", label="Email"),
        ],
        has_files=True,
    )
    snap_confirm = _snap(
        url="https://boards.greenhouse.io/company/jobs/1",
        text="Thank you for applying! Your application has been received.",
    )

    # Orchestrator flow: get_snapshot after navigate, after cookie dismiss,
    # then navigation loop detects APPLICATION_FORM, enters _fill_application loop
    mock_bridge.get_snapshot.side_effect = [
        snap_form,   # after navigate
        snap_form,   # after cookie dismiss
        snap_form,   # after nav-loop cookie dismiss
        snap_confirm, snap_confirm, snap_confirm, snap_confirm,
    ]
    mock_bridge.fill.return_value = FillResult(success=True, value_set="filled")
    mock_bridge.upload.return_value = True
    mock_bridge.screenshot.return_value = b"screenshot"

    profile = {"first_name": "Yash", "last_name": "B", "email": "yash@test.com"}

    result = await adapter.fill_and_submit(
        url="https://boards.greenhouse.io/company/jobs/1",
        cv_path=cv,
        cover_letter_path=None,
        profile=profile,
        custom_answers={},
        dry_run=True,
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_and_submit_verification_wall(adapter, mock_bridge, tmp_path):
    """Verification wall stops the application."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")

    snap_wall = _snap(
        url="https://boards.greenhouse.io/company/jobs/1",
        wall=VerificationWall(wall_type="cloudflare", confidence=0.95),
    )
    mock_bridge.get_snapshot.return_value = snap_wall

    result = await adapter.fill_and_submit(
        url="https://boards.greenhouse.io/company/jobs/1",
        cv_path=cv,
        cover_letter_path=None,
        profile={},
        custom_answers={},
    )
    assert result["success"] is False
    assert "CAPTCHA" in result.get("error", "")


@pytest.mark.asyncio
async def test_fill_and_submit_max_iterations_safety(adapter, mock_bridge, tmp_path):
    """Safety cap prevents infinite loops — orchestrator detects stuck pages."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")

    # Application form that never progresses — same page every time
    snap_stuck = _snap(
        url="https://boards.greenhouse.io/company/jobs/1",
        fields=[FieldInfo(selector="#q1", input_type="text", label="First Name")],
        has_files=True,
        text="x" * 800,  # needs enough text for stuck detection (chars 200-700)
    )
    mock_bridge.get_snapshot.return_value = snap_stuck
    mock_bridge.fill.return_value = FillResult(success=True, value_set="answer")
    mock_bridge.screenshot.return_value = b"screenshot"

    result = await adapter.fill_and_submit(
        url="https://boards.greenhouse.io/company/jobs/1",
        cv_path=cv,
        cover_letter_path=None,
        profile={},
        custom_answers={},
    )
    # Should eventually bail out via stuck detection or page exhaustion
    assert result["success"] is False
