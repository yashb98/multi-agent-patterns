"""Tests for PRAXIS procedural memory — cross-domain generalization."""
from __future__ import annotations

import pytest

from jobpulse.form_experience_db import FormExperienceDB
from jobpulse.navigation_learner import NavigationLearner


class TestContentHashStorage:
    def test_store_with_content_hash(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="company-a.com",
            platform="greenhouse",
            adapter="playwright",
            pages_filled=2,
            field_types={"text": 5, "file": 1},
            screening_questions=["Are you authorized?"],
            time_seconds=45.0,
            success=True,
            content_hash="abc123def456789a",
        )
        exp = db.lookup("https://company-a.com/apply")
        assert exp is not None
        assert exp["content_hash"] == "abc123def456789a"

    def test_store_without_content_hash_defaults_empty(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="example.com",
            platform="generic",
            adapter="playwright",
            pages_filled=1,
            field_types={"text": 3},
            screening_questions=[],
            time_seconds=20.0,
            success=True,
        )
        exp = db.lookup("https://example.com/apply")
        assert exp is not None
        assert exp.get("content_hash", "") == ""

    def test_cross_domain_lookup_by_content_hash(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="alpha.com", platform="greenhouse", adapter="playwright",
            pages_filled=2, field_types={"text": 5},
            screening_questions=[], time_seconds=30.0, success=True,
            content_hash="shared_hash_1234",
        )
        result = db.lookup_by_content_hash("shared_hash_1234", exclude_domain="beta.com")
        assert result is not None
        assert result["domain"] == "alpha.com"
        assert result["platform"] == "greenhouse"

    def test_cross_domain_excludes_self(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="only.com", platform="lever", adapter="playwright",
            pages_filled=1, field_types={"text": 2},
            screening_questions=[], time_seconds=15.0, success=True,
            content_hash="unique_hash",
        )
        result = db.lookup_by_content_hash("unique_hash", exclude_domain="only.com")
        assert result is None


class TestNegativeExemplars:
    def test_store_negative_exemplar(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_negative_exemplar(
            domain="workday.com",
            field_label="Salary",
            value_tried="negotiate",
            failure_reason="validation_error",
            platform="workday",
            content_hash="wday_hash_123456",
        )
        negatives = db.get_negative_exemplars("workday.com")
        assert len(negatives) == 1
        assert negatives[0]["field_label"] == "Salary"
        assert negatives[0]["value_tried"] == "negotiate"

    def test_cross_domain_negative_exemplars(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_negative_exemplar(
            domain="alpha.com", field_label="Visa", value_tried="N/A",
            failure_reason="wrong_value", platform="greenhouse",
            content_hash="shared_hash",
        )
        negatives = db.get_negative_exemplars_by_hash("shared_hash")
        assert len(negatives) == 1
        assert negatives[0]["domain"] == "alpha.com"

    def test_negative_exemplar_deduplication(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        for _ in range(3):
            db.store_negative_exemplar(
                domain="dup.com", field_label="X", value_tried="bad",
                failure_reason="wrong", platform="generic",
                content_hash="dup_hash",
            )
        negatives = db.get_negative_exemplars("dup.com")
        assert len(negatives) == 1
        assert negatives[0]["attempt_count"] == 3


class TestNavigationLearnerContentHash:
    def test_save_with_content_hash(self, tmp_path):
        nl = NavigationLearner(db_path=str(tmp_path / "nav.db"))
        nl._transfer_db_path = str(tmp_path / "transfer.db")
        steps = [{"action": "click", "selector": "#apply"}]
        nl.save_sequence("company-a.com", steps, success=True,
                         platform="greenhouse", content_hash="nav_hash_1234")
        result = nl.get_sequence("company-a.com")
        assert result == steps

    def test_cross_domain_nav_fallback(self, tmp_path):
        nl = NavigationLearner(db_path=str(tmp_path / "nav.db"))
        nl._transfer_db_path = str(tmp_path / "transfer.db")
        steps = [{"action": "click", "selector": "#apply-btn"}]
        nl.save_sequence("alpha.com", steps, success=True,
                         platform="greenhouse", content_hash="shared_nav_hash")
        result = nl.get_sequence_by_content_hash(
            "shared_nav_hash", exclude_domain="beta.com",
        )
        assert result == steps

    def test_failed_sequence_stored_with_hash(self, tmp_path):
        nl = NavigationLearner(db_path=str(tmp_path / "nav.db"))
        nl._transfer_db_path = str(tmp_path / "transfer.db")
        fail_steps = [{"action": "click", "selector": "#wrong"}]
        nl.save_sequence("fail.com", fail_steps, success=False,
                         platform="lever", content_hash="fail_hash")
        assert nl.get_sequence("fail.com") is None
        result = nl.get_failed_sequences("fail.com")
        assert len(result) == 1
