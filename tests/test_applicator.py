"""Tests for ATS adapters + applicator tier logic."""
import pytest
from jobpulse.applicator import classify_action, select_adapter, WORK_AUTH


def test_classify_tier_auto_easy():
    assert classify_action(95.0, True) == "auto_submit"


def test_classify_tier_auto_complex():
    assert classify_action(96.0, False) == "auto_submit_with_preview"


def test_classify_tier_review():
    assert classify_action(85.0, True) == "send_for_review"


def test_classify_tier_skip():
    assert classify_action(78.0, False) == "skip"


def test_select_adapter():
    """All platforms route through ExtensionAdapter in extension-only mode."""
    from jobpulse.ext_adapter import ExtensionAdapter
    from jobpulse.ats_adapters import get_adapter

    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance
    for platform in ["greenhouse", "lever", "workday", None, "unknown_ats"]:
        adapter = select_adapter(platform)
        assert isinstance(adapter, ExtensionAdapter), f"Expected ExtensionAdapter for {platform}"
    if hasattr(get_adapter, "_instance"):
        del get_adapter._instance


def test_work_auth_answers():
    assert WORK_AUTH["requires_sponsorship"] is False
    assert "Graduate Visa" in WORK_AUTH["visa_status"]
    assert WORK_AUTH["right_to_work_uk"] is True
