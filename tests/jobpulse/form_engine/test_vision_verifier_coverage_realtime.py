"""Tests for S26-follow-up-O-4: coverage_realtime sidecar block."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.form_engine import vision_verifier as vv
from jobpulse.screening_session_state import SessionFillState
from shared import semantic_decisions


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Route writes to tmp; enable verifier; isolate artifact dir."""
    semantic_decisions.set_decisions_db_path(tmp_path / "d.db")
    semantic_decisions.set_test_mode(False)
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "0")
    monkeypatch.setenv("VISION_VERIFIER_SAVE_ARTIFACTS", "1")
    monkeypatch.setenv("VISION_VERIFIER_FALLBACK_MODEL", "none")
    monkeypatch.setenv("VISION_VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("VERIFIED_FILLS_DB_PATH", str(tmp_path / "vf.db"))
    monkeypatch.setattr(vv, "_ARTIFACT_DIR", str(tmp_path / "verifier"))
    vv._FALLBACK_MODEL = "none"
    yield
    semantic_decisions.set_test_mode(None)


def _fake_page() -> MagicMock:
    page = MagicMock()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    locator.is_visible = AsyncMock(return_value=False)
    locator.screenshot = AsyncMock(return_value=b"x")
    sub = MagicMock()
    sub.first = locator
    page.locator = MagicMock(return_value=sub)
    page.screenshot = AsyncMock(return_value=b"fakepng")
    return page


def _vision_response(payload: dict) -> MagicMock:
    r = MagicMock()
    r.choices = [MagicMock()]
    r.choices[0].message = MagicMock()
    r.choices[0].message.content = json.dumps(payload)
    r.usage = MagicMock()
    r.usage.prompt_tokens = 10
    r.usage.completion_tokens = 5
    return r


def _latest_sidecar(tmp_path: Path) -> dict:
    sidecars = sorted((tmp_path / "verifier").glob("*.json"))
    assert sidecars, "expected at least one sidecar JSON written"
    return json.loads(sidecars[-1].read_text())


def test_coverage_realtime_present_in_sidecar(tmp_path):
    """O-4: sidecar payload has a coverage_realtime block when
    session_state is supplied."""
    session = SessionFillState()
    session.record_fill("Email", "y@b.com", field_type="email", verified=True)
    field_metadata = {
        "Email": {"type": "email", "required": True},
        "Country": {"type": "combobox", "required": True},
    }
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            return_value=_vision_response({
                "verdicts": [
                    {"label": "Country", "observed_value": "UK",
                     "matches_claim": True, "contradicts_help_text": False,
                     "reason": "ok"},
                ]
            })
        )
        asyncio.run(vv.verify_form_page(
            _fake_page(),
            {"Email": "y@b.com", "Country": "UK"},
            page_url="https://job-boards.greenhouse.io/x",
            platform="greenhouse",
            field_metadata=field_metadata,
            session_state=session,
        ))
    payload = _latest_sidecar(tmp_path)
    assert payload.get("coverage_realtime") is not None
    cr = payload["coverage_realtime"]
    for key in (
        "filled_verified_at_fill_time",
        "filled_deferred_to_vision",
        "scanner_saw_filler_skipped_required",
        "scanner_saw_filler_skipped_optional",
        "scanner_noise_excluded",
    ):
        assert key in cr, f"missing bucket: {key}"


def test_buckets_sum_equals_scanner_coverage_total(tmp_path):
    """O-4: invariant — sum of all 5 buckets equals scanner_coverage.total."""
    session = SessionFillState()
    session.record_fill("Email", "y@b.com", field_type="email", verified=True)
    field_metadata = {
        "Email": {"type": "email", "required": True},
        "Country": {"type": "combobox", "required": True},
        "Resume": {"type": "file", "required": True},  # noise
    }
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            return_value=_vision_response({
                "verdicts": [
                    {"label": "Country", "observed_value": "UK",
                     "matches_claim": True, "contradicts_help_text": False,
                     "reason": "ok"},
                ]
            })
        )
        asyncio.run(vv.verify_form_page(
            _fake_page(),
            {"Email": "y@b.com", "Country": "UK"},
            page_url="https://job-boards.greenhouse.io/x",
            platform="greenhouse",
            field_metadata=field_metadata,
            session_state=session,
        ))
    payload = _latest_sidecar(tmp_path)
    cr = payload["coverage_realtime"]
    total = (
        len(cr["filled_verified_at_fill_time"])
        + len(cr["filled_deferred_to_vision"])
        + len(cr["scanner_saw_filler_skipped_required"])
        + len(cr["scanner_saw_filler_skipped_optional"])
        + len(cr["scanner_noise_excluded"])
    )
    assert total == payload["scanner_coverage"]["total"], (
        f"bucket-sum invariant violated: {total} != "
        f"{payload['scanner_coverage']['total']}"
    )


def test_verified_field_lands_in_fill_time_bucket(tmp_path):
    """O-4: a session-verified field appears in
    filled_verified_at_fill_time (not in filled_deferred_to_vision)."""
    session = SessionFillState()
    session.record_fill("Email", "y@b.com", field_type="email", verified=True)
    field_metadata = {"Email": {"type": "email", "required": True}}
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            return_value=_vision_response({"verdicts": []})
        )
        asyncio.run(vv.verify_form_page(
            _fake_page(),
            {"Email": "y@b.com"},
            page_url="https://job-boards.greenhouse.io/x",
            platform="greenhouse",
            field_metadata=field_metadata,
            session_state=session,
        ))
    payload = _latest_sidecar(tmp_path)
    cr = payload["coverage_realtime"]
    labels_at_fill_time = {e["label"] for e in cr["filled_verified_at_fill_time"]}
    assert "Email" in labels_at_fill_time
    labels_deferred = {e["label"] for e in cr["filled_deferred_to_vision"]}
    assert "Email" not in labels_deferred


def test_no_session_state_yields_null_coverage_realtime(tmp_path):
    """O-4: legacy callers (no session_state) still work; coverage_realtime is null."""
    with patch.object(vv, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = MagicMock(
            return_value=_vision_response({
                "verdicts": [
                    {"label": "Email", "observed_value": "y@b.com",
                     "matches_claim": True, "contradicts_help_text": False,
                     "reason": "ok"},
                ]
            })
        )
        asyncio.run(vv.verify_form_page(
            _fake_page(),
            {"Email": "y@b.com"},
            page_url="https://x.example/y",
            platform="greenhouse",
        ))
    payload = _latest_sidecar(tmp_path)
    assert payload.get("coverage_realtime") is None
