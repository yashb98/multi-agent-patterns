"""Tests for ApplicationOrchestrator static helpers.

Per project policy: no mocks. End-to-end navigation/login/fill behavior is
covered by real Playwright runs in `tests/jobpulse/integration/test_pipeline_live.py`
and the `*_real.py` test suites. The mock-driven tests that previously lived
here exercised AsyncMock(bridge) — they no longer match the 5-phase navigation
pipeline (2026-04 rewrite) and were producing false-positives. Removed
2026-05-03 in favor of real-Playwright integration coverage.

Static helpers (`_extract_domain`, `_find_apply_button`, `_find_signup_link`,
`_as_dict`, `_to_page_snapshot`) are pure functions over dicts/Pydantic models
and are testable directly with real data structures.
"""

from __future__ import annotations

import pytest

from jobpulse.application_orchestrator import ApplicationOrchestrator
from jobpulse.form_models import (
    ButtonInfo,
    FieldInfo,
    PageSnapshot,
    PageType,
    VerificationWall,
)


# =========================================================================
# Real PageSnapshot construction (no mocks — actual Pydantic model)
# =========================================================================


def _real_snapshot(
    url="https://boards.greenhouse.io/acme/jobs/4567890",
    title="Test",
    fields=None,
    buttons=None,
    wall=None,
    text="",
    has_files=False,
):
    return PageSnapshot(
        url=url,
        title=title,
        fields=fields or [],
        buttons=buttons or [],
        verification_wall=wall,
        page_text_preview=text,
        has_file_inputs=has_files,
        iframe_count=0,
        timestamp=1000,
    )


# =========================================================================
# Static helpers
# =========================================================================


class TestStaticHelpers:
    def test_extract_domain_standard(self):
        assert ApplicationOrchestrator._extract_domain("https://www.greenhouse.io/apply") == "greenhouse.io"

    def test_extract_domain_no_www(self):
        assert ApplicationOrchestrator._extract_domain("https://lever.co/jobs/1") == "lever.co"

    def test_extract_domain_empty_url(self):
        assert ApplicationOrchestrator._extract_domain("") == ""

    def test_extract_domain_no_scheme(self):
        """URL without scheme — urlparse puts everything in path."""
        result = ApplicationOrchestrator._extract_domain("example.com")
        assert result == "example.com"

    def test_find_apply_button_matches(self):
        snap = {
            "buttons": [
                {"selector": "#about", "text": "About", "enabled": True},
                {"selector": "#apply", "text": "Apply Now", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_apply_button(snap)
        assert btn is not None
        assert btn["selector"] == "#apply"

    def test_find_apply_button_disabled(self):
        snap = {
            "buttons": [
                {"selector": "#apply", "text": "Apply Now", "enabled": False},
            ],
        }
        btn = ApplicationOrchestrator._find_apply_button(snap)
        assert btn is None

    def test_find_apply_button_no_match(self):
        snap = {
            "buttons": [
                {"selector": "#login", "text": "Sign In", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_apply_button(snap)
        assert btn is None

    def test_find_signup_link(self):
        snap = {
            "buttons": [
                {"selector": "#signup", "text": "Create Account", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_signup_link(snap)
        assert btn is not None

    def test_find_signup_link_dont_have(self):
        snap = {
            "buttons": [
                {"selector": "#signup", "text": "Don't have an account?", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_signup_link(snap)
        assert btn is not None

    def test_as_dict_pydantic_model(self):
        snap = _real_snapshot(url="https://boards.greenhouse.io/deepmind/jobs/5551234567")
        result = ApplicationOrchestrator._as_dict(snap)
        assert isinstance(result, dict)
        assert result["url"] == "https://boards.greenhouse.io/deepmind/jobs/5551234567"

    def test_as_dict_already_dict(self):
        d = {"url": "https://jobs.lever.co/anthropic/a1b2c3d4-e5f6-7890-abcd-ef0123456789"}
        result = ApplicationOrchestrator._as_dict(d)
        assert result is d

    def test_to_page_snapshot_from_dict(self):
        raw = {
            "url": "https://www.linkedin.com/jobs/view/3945782198",
            "title": "Test",
            "fields": [
                {"selector": "#q", "input_type": "text", "label": "Name"},
            ],
            "buttons": [
                {"selector": "#btn", "text": "Next", "type": "button", "enabled": True},
            ],
            "verification_wall": None,
            "page_text_preview": "Hello",
            "has_file_inputs": False,
        }
        snap = ApplicationOrchestrator._to_page_snapshot(raw)
        assert isinstance(snap, PageSnapshot)
        assert len(snap.fields) == 1
        assert len(snap.buttons) == 1

    def test_to_page_snapshot_malformed_field_skipped(self):
        """Malformed field dict is silently skipped."""
        raw = {
            "url": "",
            "title": "",
            "fields": [
                {"bad_key": "no selector"},
                {"selector": "#ok", "input_type": "text", "label": "OK"},
            ],
            "buttons": [],
            "verification_wall": None,
            "page_text_preview": "",
            "has_file_inputs": False,
        }
        snap = ApplicationOrchestrator._to_page_snapshot(raw)
        assert len(snap.fields) == 1

    def test_to_page_snapshot_malformed_button_skipped(self):
        raw = {
            "url": "",
            "title": "",
            "fields": [],
            "buttons": [
                {"bad": True},
                {"selector": "#ok", "text": "OK"},
            ],
            "verification_wall": None,
            "page_text_preview": "",
            "has_file_inputs": False,
        }
        snap = ApplicationOrchestrator._to_page_snapshot(raw)
        assert len(snap.buttons) == 1
