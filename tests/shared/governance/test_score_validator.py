import math
import pytest


class TestClampScore:
    def test_within_bounds_unchanged(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(7.5) == 7.5

    def test_above_max_clamped(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(999.0) == 10.0

    def test_below_min_clamped(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(-5.0) == 0.0

    def test_nan_returns_fallback(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(float("nan")) == 5.0

    def test_inf_clamped_to_max(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(float("inf")) == 10.0

    def test_neg_inf_clamped_to_min(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(float("-inf")) == 0.0

    def test_boundary_exact_zero(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(0.0) == 0.0

    def test_boundary_exact_ten(self):
        from shared.governance._score_validator import clamp_score
        assert clamp_score(10.0) == 10.0


class TestValidateReview:
    def test_valid_review_passes(self):
        from shared.governance._score_validator import validate_review
        result = validate_review({
            "overall_score": 8.5,
            "accuracy_score": 9.0,
            "passed": True,
            "improvements_needed": [],
        })
        assert result.overall_score == 8.5
        assert result.accuracy_score == 9.0
        assert result.anomalies == []

    def test_out_of_bounds_score_clamped_and_flagged(self):
        from shared.governance._score_validator import validate_review
        result = validate_review({"overall_score": 999, "accuracy_score": -2})
        assert result.overall_score == 10.0
        assert result.accuracy_score == 0.0
        assert len(result.anomalies) >= 2

    def test_string_score_fallback(self):
        from shared.governance._score_validator import validate_review
        result = validate_review({"overall_score": "ten"})
        assert result.overall_score == 5.0
        assert any("parse" in a.lower() for a in result.anomalies)

    def test_empty_review_defaults(self):
        from shared.governance._score_validator import validate_review
        result = validate_review({})
        assert result.overall_score == 5.0
        assert result.accuracy_score == 0.0
        assert len(result.anomalies) >= 1

    def test_original_raw_preserved(self):
        from shared.governance._score_validator import validate_review
        raw = {"overall_score": 999, "custom_field": "test"}
        result = validate_review(raw)
        assert result.original_raw == raw

    def test_nan_score_detected(self):
        from shared.governance._score_validator import validate_review
        result = validate_review({"overall_score": float("nan")})
        assert result.overall_score == 5.0
        assert any("nan" in a.lower() for a in result.anomalies)


class TestAnomalyTracking:
    def test_reset_anomaly_counter(self):
        from shared.governance._score_validator import reset_anomaly_counter, get_anomaly_count
        reset_anomaly_counter()
        assert get_anomaly_count() == 0

    def test_anomalies_increment_counter(self):
        from shared.governance._score_validator import validate_review, reset_anomaly_counter, get_anomaly_count
        reset_anomaly_counter()
        validate_review({"overall_score": 999})
        assert get_anomaly_count() >= 1


class TestReviewerIntegration:
    def test_reviewer_output_uses_validate_review(self):
        """Verify that the reviewer node returns clamped scores."""
        from shared.governance._score_validator import validate_review
        raw = {"overall_score": 999, "accuracy_score": -5, "passed": True}
        result = validate_review(raw)
        assert result.overall_score == 10.0
        assert result.accuracy_score == 0.0
