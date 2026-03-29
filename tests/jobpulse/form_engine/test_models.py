"""Tests for form_engine models."""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

os.environ.setdefault("JOBPULSE_TEST_MODE", "1")


def test_input_type_enum_has_all_types():
    from jobpulse.form_engine.models import InputType

    expected = {
        "text", "textarea", "select_native", "select_custom",
        "radio", "checkbox", "date_native", "date_custom",
        "search_autocomplete", "file_upload", "multi_select",
        "tag_input", "toggle_switch", "rich_text_editor",
        "readonly", "unknown",
    }
    actual = {t.value for t in InputType}
    assert actual == expected


def test_fill_result_success():
    from jobpulse.form_engine.models import FillResult

    r = FillResult(success=True, selector="#email", value_attempted="a@b.com", value_set="a@b.com")
    assert r.success is True
    assert r.error is None


def test_fill_result_failure():
    from jobpulse.form_engine.models import FillResult

    r = FillResult(success=False, selector="#name", value_attempted="Yash", error="element not found")
    assert r.success is False
    assert r.value_set is None


def test_fill_result_skipped():
    from jobpulse.form_engine.models import FillResult

    r = FillResult(success=True, selector="#readonly", value_attempted="", skipped=True)
    assert r.skipped is True


def test_field_info_basic():
    from jobpulse.form_engine.models import FieldInfo, InputType

    f = FieldInfo(
        selector="#email",
        input_type=InputType.TEXT,
        label="Email Address",
        required=True,
        current_value="",
    )
    assert f.input_type == InputType.TEXT
    assert f.required is True
