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
    assert config.APPLICATION_ENGINE == "extension"
