"""Tests for form_engine gotchas DB."""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os
os.environ.setdefault("JOBPULSE_TEST_MODE", "1")

import pytest


@pytest.fixture
def gotchas_db(tmp_path):
    from jobpulse.form_engine.gotchas import GotchasDB
    return GotchasDB(db_path=str(tmp_path / "gotchas.db"))


def test_store_and_retrieve_gotcha(gotchas_db):
    gotchas_db.store("workday.com", "#country", "native_select_failed", "use_custom_select")
    result = gotchas_db.lookup("workday.com", "#country")
    assert result is not None
    assert result["solution"] == "use_custom_select"
    assert result["times_used"] == 0


def test_lookup_miss_returns_none(gotchas_db):
    result = gotchas_db.lookup("unknown.com", "#field")
    assert result is None


def test_record_usage_increments(gotchas_db):
    gotchas_db.store("lever.co", "#phone", "format_rejected", "prepend_plus44")
    gotchas_db.record_usage("lever.co", "#phone")
    gotchas_db.record_usage("lever.co", "#phone")
    result = gotchas_db.lookup("lever.co", "#phone")
    assert result["times_used"] == 2


def test_lookup_by_domain_pattern(gotchas_db):
    gotchas_db.store("workday.com", "select", "native_select_failed", "use_custom_select")
    results = gotchas_db.lookup_domain("workday.com")
    assert len(results) == 1
    assert results[0]["selector_pattern"] == "select"


def test_store_overwrites_existing(gotchas_db):
    gotchas_db.store("lever.co", "#phone", "old_problem", "old_solution")
    gotchas_db.store("lever.co", "#phone", "new_problem", "new_solution")
    result = gotchas_db.lookup("lever.co", "#phone")
    assert result["solution"] == "new_solution"


def test_get_skip_domains(gotchas_db):
    gotchas_db.store("amazon.jobs", "*", "captcha_always", "skip_manual_review")
    skips = gotchas_db.get_skip_domains()
    assert "amazon.jobs" in skips
