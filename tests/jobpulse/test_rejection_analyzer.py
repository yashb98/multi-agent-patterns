"""Tests for rejection_analyzer — outcome/blocker classifiers and analysis functions."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from jobpulse.rejection_analyzer import (
    classify_outcome,
    classify_blocker,
    compute_funnel,
    compute_score_by_outcome,
    compute_blocker_frequency,
    generate_recommendations,
    generate_full_report,
)


# ---------------------------------------------------------------------------
# TestClassifyOutcome
# ---------------------------------------------------------------------------

class TestClassifyOutcome:
    def test_positive_interview(self):
        assert classify_outcome("interview") == "positive"

    def test_positive_offer(self):
        assert classify_outcome("offer") == "positive"

    def test_positive_responded(self):
        assert classify_outcome("responded") == "positive"

    def test_positive_case_insensitive(self):
        assert classify_outcome("Interview") == "positive"

    def test_negative_rejected(self):
        assert classify_outcome("rejected") == "negative"

    def test_negative_discarded(self):
        assert classify_outcome("discarded") == "negative"

    def test_self_filtered_skipped(self):
        assert classify_outcome("skipped") == "self_filtered"

    def test_self_filtered_blocked(self):
        assert classify_outcome("blocked") == "self_filtered"

    def test_pending_found(self):
        assert classify_outcome("found") == "pending"

    def test_pending_applied(self):
        assert classify_outcome("applied") == "pending"

    def test_pending_unknown(self):
        assert classify_outcome("something_else") == "pending"


# ---------------------------------------------------------------------------
# TestClassifyBlocker
# ---------------------------------------------------------------------------

class TestClassifyBlocker:
    def test_geo_visa(self):
        assert classify_blocker("requires visa sponsorship") == "geo-restriction"

    def test_geo_us_only(self):
        assert classify_blocker("US.only applicants") == "geo-restriction"

    def test_geo_right_to_work(self):
        assert classify_blocker("must have right to work in UK") == "geo-restriction"

    def test_seniority_staff_engineer(self):
        assert classify_blocker("role is staff engineer level") == "seniority-mismatch"

    def test_seniority_director(self):
        assert classify_blocker("Director of Engineering") == "seniority-mismatch"

    def test_seniority_vp(self):
        assert classify_blocker("VP of Product") == "seniority-mismatch"

    def test_onsite_requirement(self):
        assert classify_blocker("must be onsite 5 days") == "onsite-requirement"

    def test_onsite_hybrid(self):
        assert classify_blocker("hybrid working model required") == "onsite-requirement"

    def test_onsite_relocate(self):
        assert classify_blocker("must relocate to London") == "onsite-requirement"

    def test_stack_java(self):
        assert classify_blocker("requires java expertise") == "stack-mismatch"

    def test_stack_cpp(self):
        assert classify_blocker("C++ experience required") == "stack-mismatch"

    def test_stack_ruby(self):
        assert classify_blocker("Ruby on Rails preferred") == "stack-mismatch"

    def test_stack_flutter(self):
        assert classify_blocker("flutter mobile experience") == "stack-mismatch"

    def test_other_default(self):
        assert classify_blocker("overqualified candidate") == "other"

    def test_other_empty(self):
        assert classify_blocker("") == "other"


# ---------------------------------------------------------------------------
# TestComputeFunnel
# ---------------------------------------------------------------------------

class TestComputeFunnel:
    def test_basic_funnel(self):
        apps = [
            {"status": "Found"},
            {"status": "Found"},
            {"status": "Applied"},
            {"status": "Rejected"},
            {"status": "Interview"},
        ]
        funnel = compute_funnel(apps)
        assert funnel["Found"] == 2
        assert funnel["Applied"] == 1
        assert funnel["Rejected"] == 1
        assert funnel["Interview"] == 1

    def test_empty(self):
        assert compute_funnel([]) == {}

    def test_missing_status(self):
        apps = [{"status": "Applied"}, {}]
        funnel = compute_funnel(apps)
        assert funnel["Applied"] == 1
        assert funnel["unknown"] == 1


# ---------------------------------------------------------------------------
# TestScoreByOutcome
# ---------------------------------------------------------------------------

class TestScoreByOutcome:
    def test_groups_scores_correctly(self):
        apps = [
            {"status": "interview", "score": 85},
            {"status": "offer", "score": 90},
            {"status": "rejected", "score": 55},
            {"status": "rejected", "score": 60},
        ]
        result = compute_score_by_outcome(apps)

        assert "positive" in result
        assert result["positive"]["count"] == 2
        assert result["positive"]["avg"] == pytest.approx(87.5)
        assert result["positive"]["min"] == 85.0
        assert result["positive"]["max"] == 90.0

        assert "negative" in result
        assert result["negative"]["count"] == 2
        assert result["negative"]["avg"] == pytest.approx(57.5)

    def test_skips_missing_scores(self):
        apps = [
            {"status": "interview"},
            {"status": "rejected", "score": 50},
        ]
        result = compute_score_by_outcome(apps)
        assert "positive" not in result
        assert result["negative"]["count"] == 1

    def test_empty(self):
        assert compute_score_by_outcome([]) == {}


# ---------------------------------------------------------------------------
# TestRecommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def _make_geo_apps(self, n: int) -> list[dict]:
        """Return n blocked apps with geo-restriction reasons."""
        return [{"status": "blocked", "block_reason": "requires visa sponsorship"}] * n

    def test_geo_blocker_recommendation(self):
        # 3 geo-blocked out of 10 total blocked = 30% → should trigger
        apps = self._make_geo_apps(3) + [
            {"status": "blocked", "block_reason": "overqualified"},
        ] * 7
        recs = generate_recommendations(apps)
        actions = [r["action"] for r in recs]
        assert any("location" in a.lower() for a in actions)

    def test_no_recommendation_below_threshold(self):
        # 1 geo-blocked out of 10 = 10% → below 20% threshold, no geo rec
        apps = self._make_geo_apps(1) + [
            {"status": "blocked", "block_reason": "overqualified"},
        ] * 9
        recs = generate_recommendations(apps)
        actions = [r["action"] for r in recs]
        assert not any("location" in a.lower() for a in actions)

    def test_stack_mismatch_recommendation(self):
        apps = (
            [{"status": "blocked", "block_reason": "requires java expertise"}] * 3
            + [{"status": "blocked", "block_reason": "overqualified"}] * 7
        )
        # 3/10 = 30% → above 15%
        recs = generate_recommendations(apps)
        actions = [r["action"] for r in recs]
        assert any("stack" in a.lower() or "tech" in a.lower() for a in actions)

    def test_max_recs_respected(self):
        apps = (
            [{"status": "blocked", "block_reason": "requires visa sponsorship"}] * 4
            + [{"status": "blocked", "block_reason": "requires java expertise"}] * 4
            + [{"status": "blocked", "block_reason": "must be onsite"}] * 2
        )
        recs = generate_recommendations(apps, max_recs=2)
        assert len(recs) <= 2

    def test_empty_applications(self):
        assert generate_recommendations([]) == []


# ---------------------------------------------------------------------------
# TestGenerateFullReport
# ---------------------------------------------------------------------------

class TestGenerateFullReport:
    def test_report_structure(self):
        apps = [
            {"status": "interview", "score": 85, "block_reason": ""},
            {"status": "rejected", "score": 55, "block_reason": "requires visa"},
            {"status": "applied", "score": 70, "block_reason": ""},
        ]
        report = generate_full_report(apps)
        assert "total" in report
        assert report["total"] == 3
        assert "funnel" in report
        assert "outcome_counts" in report
        assert "score_by_outcome" in report
        assert "blocker_frequency" in report
        assert "recommendations" in report

    def test_outcome_counts_sum(self):
        apps = [
            {"status": "offer"},
            {"status": "rejected"},
            {"status": "skipped"},
            {"status": "applied"},
        ]
        report = generate_full_report(apps)
        total = sum(report["outcome_counts"].values())
        assert total == 4
