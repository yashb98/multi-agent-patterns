"""Tests for file_filler."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_file_upload_basic(tmp_path):
    from jobpulse.form_engine.file_filler import fill_file_upload

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_text("fake pdf")

    page = MagicMock()
    el = MagicMock()
    el.set_input_files = AsyncMock()
    el.get_attribute = AsyncMock(return_value=None)
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_file_upload(page, "input[type='file']", cv_file)
    assert result.success is True
    el.set_input_files.assert_called_once()
    call_arg = el.set_input_files.call_args[0][0]
    assert call_arg["name"] == "cv.pdf"
    assert call_arg["mimeType"] == "application/pdf"
    assert call_arg["buffer"] == b"fake pdf"


@pytest.mark.asyncio
async def test_fill_file_upload_file_not_exists():
    from jobpulse.form_engine.file_filler import fill_file_upload

    page = MagicMock()

    result = await fill_file_upload(page, "input[type='file']", Path("/nonexistent.pdf"))
    assert result.success is False
    assert "does not exist" in result.error


@pytest.mark.asyncio
async def test_fill_file_upload_element_not_found(tmp_path):
    from jobpulse.form_engine.file_filler import fill_file_upload

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_text("fake pdf")

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_file_upload(page, "input[type='file']", cv_file)
    assert result.success is False


@pytest.mark.asyncio
async def test_fill_file_upload_checks_accept(tmp_path):
    from jobpulse.form_engine.file_filler import fill_file_upload

    cv_file = tmp_path / "cv.txt"
    cv_file.write_text("plain text")

    page = MagicMock()
    el = MagicMock()
    el.set_input_files = AsyncMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: ".pdf,.docx" if name == "accept" else None)
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_file_upload(page, "input[type='file']", cv_file)
    assert result.success is False
    assert "type" in result.error.lower()
