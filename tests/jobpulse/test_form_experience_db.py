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


class TestValidateAgainstLive:
    def test_trusted_when_fields_match(self, db):
        db.record(domain="g.io", platform="greenhouse", adapter="ext",
                  pages_filled=2, field_types=["text", "select", "upload"],
                  screening_questions=[], time_seconds=30.0, success=True)
        result = db.validate_against_live(
            "g.io", ["text", "select", "upload"],
        )
        assert result["trusted"] is True
        assert result["match_ratio"] == 1.0
        assert result["diverged_fields"] == []

    def test_untrusted_when_fields_diverge(self, db):
        db.record(domain="g.io", platform="greenhouse", adapter="ext",
                  pages_filled=2, field_types=["text", "select", "upload"],
                  screening_questions=[], time_seconds=30.0, success=True)
        result = db.validate_against_live(
            "g.io", ["text", "checkbox", "radio", "textarea"],
        )
        assert result["trusted"] is False
        assert result["match_ratio"] < 0.8
        assert len(result["diverged_fields"]) > 0

    def test_trusted_with_partial_overlap_above_threshold(self, db):
        db.record(domain="g.io", platform="greenhouse", adapter="ext",
                  pages_filled=2, field_types=["text", "select", "upload", "radio"],
                  screening_questions=[], time_seconds=30.0, success=True)
        result = db.validate_against_live(
            "g.io", ["text", "select", "upload", "checkbox"],
        )
        assert result["match_ratio"] == 3 / 5
        assert result["trusted"] is False

    def test_no_stored_experience(self, db):
        result = db.validate_against_live("unknown.com", ["text"])
        assert result["trusted"] is False
        assert result["stored"] is None

    def test_page_count_mismatch_untrusts(self, db):
        db.record(domain="g.io", platform="greenhouse", adapter="ext",
                  pages_filled=3, field_types=["text", "select"],
                  screening_questions=[], time_seconds=30.0, success=True)
        result = db.validate_against_live(
            "g.io", ["text", "select"], live_page_count=6,
        )
        assert result["trusted"] is False

    def test_page_count_close_still_trusted(self, db):
        db.record(domain="g.io", platform="greenhouse", adapter="ext",
                  pages_filled=3, field_types=["text", "select"],
                  screening_questions=[], time_seconds=30.0, success=True)
        result = db.validate_against_live(
            "g.io", ["text", "select"], live_page_count=4,
        )
        assert result["trusted"] is True

    def test_custom_threshold(self, db):
        db.record(domain="g.io", platform="greenhouse", adapter="ext",
                  pages_filled=2, field_types=["text", "select", "upload"],
                  screening_questions=[], time_seconds=30.0, success=True)
        result = db.validate_against_live(
            "g.io", ["text", "select"],
            match_threshold=0.5,
        )
        assert result["trusted"] is True
