"""Tests for form_engine detector."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("JOBPULSE_TEST_MODE", "1")

import pytest


def _mock_element(tag: str, attrs: dict | None = None):
    """Create a mock Playwright ElementHandle."""
    el = MagicMock()
    el.evaluate = AsyncMock(return_value=tag)
    el.get_attribute = AsyncMock(side_effect=lambda name: (attrs or {}).get(name))
    return el


@pytest.mark.asyncio
async def test_detect_native_select():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("select")) == InputType.SELECT_NATIVE


@pytest.mark.asyncio
async def test_detect_multi_select():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("select", {"multiple": ""})) == InputType.MULTI_SELECT


@pytest.mark.asyncio
async def test_detect_radio():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "radio"})) == InputType.RADIO


@pytest.mark.asyncio
async def test_detect_checkbox():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "checkbox"})) == InputType.CHECKBOX


@pytest.mark.asyncio
async def test_detect_date_native():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "date"})) == InputType.DATE_NATIVE


@pytest.mark.asyncio
async def test_detect_file_upload():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "file"})) == InputType.FILE_UPLOAD


@pytest.mark.asyncio
async def test_detect_textarea():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("textarea")) == InputType.TEXTAREA


@pytest.mark.asyncio
async def test_detect_readonly():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "text", "readonly": ""})) == InputType.READONLY


@pytest.mark.asyncio
async def test_detect_disabled():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "text", "disabled": ""})) == InputType.READONLY


@pytest.mark.asyncio
async def test_detect_custom_select_by_role():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("div", {"role": "listbox"})) == InputType.SELECT_CUSTOM


@pytest.mark.asyncio
async def test_detect_combobox():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("div", {"role": "combobox"})) == InputType.SELECT_CUSTOM


@pytest.mark.asyncio
async def test_detect_toggle_switch():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("div", {"role": "switch"})) == InputType.TOGGLE_SWITCH


@pytest.mark.asyncio
async def test_detect_text_input_default():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "text"})) == InputType.TEXT


@pytest.mark.asyncio
async def test_detect_email_as_text():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("input", {"type": "email"})) == InputType.TEXT


@pytest.mark.asyncio
async def test_detect_contenteditable():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("div", {"contenteditable": "true"})) == InputType.RICH_TEXT_EDITOR


@pytest.mark.asyncio
async def test_detect_unknown():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType
    assert await detect_input_type(_mock_element("div")) == InputType.UNKNOWN
