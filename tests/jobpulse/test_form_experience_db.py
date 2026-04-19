"""Tests for FormExperienceDB — per-domain form experience storage."""
import json

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def db(tmp_path):
    return FormExperienceDB(db_path=str(tmp_path / "form_exp.db"))


def test_record_and_lookup(db):
    db.record(
        domain="boards.greenhouse.io",
        platform="greenhouse",
        adapter="extension",
        pages_filled=3,
        field_types=["text", "select", "upload", "radio"],
        screening_questions=["Do you require sponsorship?", "Expected salary?"],
        time_seconds=42.5,
        success=True,
    )
    exp = db.lookup("boards.greenhouse.io")
    assert exp is not None
    assert exp["platform"] == "greenhouse"
    assert exp["adapter"] == "extension"
    assert exp["pages_filled"] == 3
    assert json.loads(exp["field_types"]) == ["text", "select", "upload", "radio"]
    assert json.loads(exp["screening_questions"]) == [
        "Do you require sponsorship?", "Expected salary?"
    ]
    assert exp["time_seconds"] == pytest.approx(42.5)
    assert exp["success"] == 1
    assert exp["apply_count"] == 1


def test_repeat_updates_count(db):
    db.record(domain="jobs.lever.co", platform="lever", adapter="extension",
              pages_filled=2, field_types=["text"], screening_questions=[],
              time_seconds=20.0, success=True)
    db.record(domain="jobs.lever.co", platform="lever", adapter="extension",
              pages_filled=2, field_types=["text", "select"], screening_questions=["Salary?"],
              time_seconds=18.0, success=True)
    exp = db.lookup("jobs.lever.co")
    assert exp["apply_count"] == 2
    assert json.loads(exp["field_types"]) == ["text", "select"]
    assert exp["time_seconds"] == pytest.approx(18.0)


def test_lookup_missing_returns_none(db):
    assert db.lookup("nonexistent.com") is None


def test_get_stats(db):
    db.record(domain="a.com", platform="greenhouse", adapter="extension",
              pages_filled=1, field_types=[], screening_questions=[],
              time_seconds=10.0, success=True)
    db.record(domain="b.com", platform="lever", adapter="extension",
              pages_filled=2, field_types=[], screening_questions=[],
              time_seconds=15.0, success=False)
    stats = db.get_stats()
    assert stats["total_domains"] == 2
    assert stats["successful_domains"] == 1


def test_failed_record_does_not_overwrite_success(db):
    db.record(domain="x.com", platform="greenhouse", adapter="extension",
              pages_filled=3, field_types=["text"], screening_questions=[],
              time_seconds=30.0, success=True)
    db.record(domain="x.com", platform="greenhouse", adapter="extension",
              pages_filled=0, field_types=[], screening_questions=[],
              time_seconds=5.0, success=False)
    exp = db.lookup("x.com")
    assert exp["success"] == 1
    assert exp["pages_filled"] == 3
    assert exp["apply_count"] == 2
