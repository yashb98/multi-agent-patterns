"""Tests for extension protocol Pydantic models."""

import pytest
from jobpulse.ext_models import (
    FieldInfo,
    ButtonInfo,
    VerificationWall,
    PageSnapshot,
    ExtCommand,
    ExtResponse,
    FillResult,
    Action,
)


def test_field_info_defaults():
    f = FieldInfo(selector="#name", input_type="text", label="Name")
    assert f.required is False
    assert f.current_value == ""
    assert f.options == []
    assert f.in_shadow_dom is False
    assert f.in_iframe is False
    assert f.iframe_index is None


def test_field_info_full():
    f = FieldInfo(
        selector="select#country",
        input_type="select",
        label="Country",
        required=True,
        options=["UK", "US", "India"],
        in_iframe=True,
        iframe_index=0,
    )
    assert f.input_type == "select"
    assert len(f.options) == 3
    assert f.iframe_index == 0


def test_page_snapshot_from_dict():
    data = {
        "url": "https://boards.greenhouse.io/company/jobs/123",
        "title": "Apply — ML Engineer",
        "fields": [
            {"selector": "#first_name", "input_type": "text", "label": "First Name", "required": True},
        ],
        "buttons": [
            {"selector": "button[type=submit]", "text": "Submit Application", "type": "submit", "enabled": True},
        ],
        "verification_wall": None,
        "page_text_preview": "Apply for ML Engineer at Company...",
        "has_file_inputs": True,
        "iframe_count": 0,
        "timestamp": 1712150400000,
    }
    snap = PageSnapshot(**data)
    assert snap.url.startswith("https://")
    assert len(snap.fields) == 1
    assert snap.fields[0].required is True
    assert snap.has_file_inputs is True
    assert snap.verification_wall is None


def test_page_snapshot_with_verification_wall():
    snap = PageSnapshot(
        url="https://example.com",
        title="Blocked",
        fields=[],
        buttons=[],
        verification_wall=VerificationWall(
            wall_type="cloudflare", confidence=0.95, details="Turnstile detected"
        ),
        page_text_preview="Verify you are human",
        has_file_inputs=False,
        iframe_count=0,
        timestamp=1712150400000,
    )
    assert snap.verification_wall is not None
    assert snap.verification_wall.wall_type == "cloudflare"


def test_ext_command_fill():
    cmd = ExtCommand(
        id="cmd-001",
        action="fill",
        payload={"selector": "#name", "value": "Yash"},
    )
    assert cmd.action == "fill"
    d = cmd.model_dump()
    assert d["id"] == "cmd-001"


def test_ext_response_result():
    resp = ExtResponse(
        id="cmd-001",
        type="result",
        payload={"success": True, "value_set": "Yash"},
    )
    assert resp.type == "result"
    assert resp.payload["success"] is True


def test_fill_result():
    r = FillResult(success=True, value_set="Yash")
    assert r.success is True
    r2 = FillResult(success=False, error="Element not found")
    assert r2.error == "Element not found"


def test_action_model():
    a = Action(type="fill", selector="#name", value="Yash")
    assert a.type == "fill"
    a2 = Action(type="upload", selector="#resume", file_path="/tmp/cv.pdf")
    assert a2.file_path == "/tmp/cv.pdf"
    a3 = Action(type="click", selector="button.submit")
    assert a3.value is None


def test_verification_wall_types():
    for wt in ("cloudflare", "recaptcha", "hcaptcha", "text_challenge", "http_block"):
        w = VerificationWall(wall_type=wt, confidence=0.9, details="test")
        assert w.wall_type == wt


def test_config_extension_vars():
    from jobpulse import config
    assert hasattr(config, "PERPLEXITY_API_KEY")
    assert hasattr(config, "EXT_BRIDGE_HOST")
    assert hasattr(config, "EXT_BRIDGE_PORT")
    assert hasattr(config, "APPLICATION_ENGINE")
    assert config.EXT_BRIDGE_HOST == "localhost"
    assert config.EXT_BRIDGE_PORT == 8765
    assert config.APPLICATION_ENGINE in ("extension", "playwright")


class TestFormGroup:
    def test_form_group_creation(self):
        """FormGroup can be created with defaults."""
        from jobpulse.ext_models import FormGroup
        fg = FormGroup(group_selector="fieldset.q1", question="Do you require sponsorship?")
        assert fg.question == "Do you require sponsorship?"
        assert fg.fields == []
        assert fg.is_required is False
        assert fg.is_answered is False

    def test_form_group_with_fields(self):
        """FormGroup can contain FieldInfo objects."""
        from jobpulse.ext_models import FormGroup
        field = FieldInfo(
            selector="#sponsor-yes", input_type="radio", label="Yes",
            group_label="Sponsorship", group_selector="fieldset.q1",
        )
        fg = FormGroup(
            group_selector="fieldset.q1",
            question="Do you require sponsorship?",
            fields=[field],
            is_required=True,
        )
        assert len(fg.fields) == 1
        assert fg.fields[0].group_label == "Sponsorship"


class TestFieldInfoV2:
    def test_field_info_with_context(self):
        """FieldInfo includes v2 context fields."""
        fi = FieldInfo(
            selector="#phone", input_type="tel", label="Phone",
            group_label="Contact Info",
            parent_text="Enter your phone number",
            help_text="Include country code",
            error_text="",
        )
        assert fi.group_label == "Contact Info"
        assert fi.help_text == "Include country code"
        assert fi.error_text == ""

    def test_field_info_v2_defaults(self):
        """v2 fields default to empty strings."""
        fi = FieldInfo(selector="#x", input_type="text", label="X")
        assert fi.group_label == ""
        assert fi.group_selector == ""
        assert fi.parent_text == ""
        assert fi.fieldset_legend == ""
        assert fi.help_text == ""
        assert fi.error_text == ""
        assert fi.aria_describedby == ""


class TestPageSnapshotV2:
    def test_snapshot_with_form_groups(self):
        """PageSnapshot includes form_groups and progress."""
        from jobpulse.ext_models import FormGroup
        snap = PageSnapshot(
            url="https://linkedin.com/easy-apply",
            title="Apply",
            form_groups=[
                FormGroup(group_selector="fieldset", question="Q1"),
            ],
            progress=(2, 5),
            modal_detected=True,
        )
        assert len(snap.form_groups) == 1
        assert snap.progress == (2, 5)
        assert snap.modal_detected is True

    def test_snapshot_v2_defaults(self):
        """v2 snapshot fields default correctly."""
        snap = PageSnapshot(url="https://example.com", title="Test")
        assert snap.form_groups == []
        assert snap.progress is None
        assert snap.modal_detected is False


class TestActionV2Types:
    def test_radio_group_action(self):
        """Action accepts fill_radio_group type."""
        a = Action(type="fill_radio_group", selector="fieldset.q1", value="No")
        assert a.type == "fill_radio_group"

    def test_custom_select_action(self):
        """Action accepts fill_custom_select type."""
        a = Action(type="fill_custom_select", selector="[role='listbox']", value="UK")
        assert a.type == "fill_custom_select"

    def test_autocomplete_action(self):
        """Action accepts fill_autocomplete type."""
        a = Action(type="fill_autocomplete", selector="input.city", value="Dundee")
        assert a.type == "fill_autocomplete"

    def test_date_action(self):
        """Action accepts fill_date type."""
        a = Action(type="fill_date", selector="input[type='date']", value="2026-04-07")
        assert a.type == "fill_date"


class TestExtCommandV2:
    def test_v2_command_actions(self):
        """ExtCommand accepts all v2 action types."""
        from jobpulse.ext_models import ExtCommand
        v2_actions = [
            "fill_radio_group", "fill_custom_select", "fill_autocomplete",
            "fill_tag_input", "fill_date", "scroll_to", "wait_for_selector",
            "get_field_context", "scan_form_groups", "check_consent_boxes",
            "force_click", "rescan_after_fill",
        ]
        for act in v2_actions:
            cmd = ExtCommand(id="test", action=act, payload={})
            assert cmd.action == act
