"""Tests for per-field confidence scoring (AUQ System 1/2)."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from jobpulse.form_engine.confidence_scorer import FieldMapping, ConfidenceScorer


class TestFieldMapping:
    def test_high_confidence_mapping(self):
        fm = FieldMapping(label="First Name", value="Yash", confidence=1.0, source="deterministic")
        assert fm.is_confident
        assert fm.confidence == 1.0

    def test_low_confidence_mapping(self):
        fm = FieldMapping(label="Preferred pronouns", value="He/Him", confidence=0.6, source="llm")
        assert not fm.is_confident
        assert fm.confidence == 0.6

    def test_confidence_threshold_boundary(self):
        at_threshold = FieldMapping(label="x", value="y", confidence=0.9, source="llm")
        assert at_threshold.is_confident
        below = FieldMapping(label="x", value="y", confidence=0.89, source="llm")
        assert not below.is_confident


class TestConfidenceScorer:
    def test_deterministic_mapping_gets_full_confidence(self):
        scorer = ConfidenceScorer()
        mappings = {"First Name": "Yash", "Email": "test@example.com"}
        source = "deterministic"
        scored = scorer.score_mappings(mappings, source=source)
        assert all(fm.confidence == 1.0 for fm in scored)
        assert all(fm.source == "deterministic" for fm in scored)

    def test_cached_mapping_gets_high_confidence(self):
        scorer = ConfidenceScorer()
        scored = scorer.score_mappings({"City": "London"}, source="cached")
        assert scored[0].confidence == 0.95

    def test_llm_mapping_gets_heuristic_confidence(self):
        scorer = ConfidenceScorer()
        fields = [
            {"label": "First Name", "type": "text", "options": []},
        ]
        scored = scorer.score_mappings(
            {"First Name": "Yash"}, source="llm", fields=fields,
        )
        assert scored[0].confidence >= 0.85

    def test_llm_screening_field_gets_lower_confidence(self):
        scorer = ConfidenceScorer()
        fields = [
            {"label": "Are you authorized?", "type": "radio", "options": ["Yes", "No"]},
        ]
        scored = scorer.score_mappings(
            {"Are you authorized?": "Yes"}, source="llm", fields=fields,
        )
        assert scored[0].confidence < 0.9

    def test_empty_mappings_returns_empty(self):
        scorer = ConfidenceScorer()
        assert scorer.score_mappings({}, source="deterministic") == []


class TestBestOfNConsensus:
    def test_unanimous_consensus(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"Salary": "35000"}',
            '{"Salary": "35000"}',
            '{"Salary": "35000"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["Salary"])
        assert result["Salary"] == "35000"

    def test_majority_consensus(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"Notice": "1 month"}',
            '{"Notice": "1 month"}',
            '{"Notice": "2 weeks"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["Notice"])
        assert result["Notice"] == "1 month"

    def test_no_consensus_returns_first(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"X": "a"}',
            '{"X": "b"}',
            '{"X": "c"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["X"])
        assert result["X"] == "a"

    def test_malformed_candidate_skipped(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"Y": "good"}',
            'not json',
            '{"Y": "good"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["Y"])
        assert result["Y"] == "good"

    @patch("jobpulse.form_engine.confidence_scorer.parallel_grpo_candidates")
    def test_escalate_calls_grpo(self, mock_grpo):
        mock_grpo.return_value = ['{"Q": "A"}', '{"Q": "A"}', '{"Q": "A"}']
        scorer = ConfidenceScorer()
        low_conf = [
            FieldMapping(label="Q", value="B", confidence=0.5, source="llm"),
        ]
        fields = [{"label": "Q", "type": "radio", "options": ["A", "B"]}]
        result = scorer.escalate_low_confidence(
            low_confidence_mappings=low_conf,
            fields=fields,
            profile={"name": "Test"},
            custom_answers={},
            platform="greenhouse",
        )
        assert mock_grpo.called
        assert "Q" in result


class TestConfidenceTracking:
    def test_log_and_retrieve_confidence(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.log_field_confidence(
            domain="greenhouse.io",
            field_label="Salary",
            predicted_confidence=0.7,
            actual_correct=True,
        )
        db.log_field_confidence(
            domain="greenhouse.io",
            field_label="Salary",
            predicted_confidence=0.8,
            actual_correct=False,
        )
        stats = db.get_confidence_calibration("greenhouse.io")
        assert stats["total"] == 2
        assert stats["correct"] == 1

    def test_calibration_empty_domain(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        stats = db.get_confidence_calibration("unknown.com")
        assert stats["total"] == 0
