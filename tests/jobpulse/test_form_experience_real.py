"""Real-data tests for FormExperienceDB with actual SQLite operations.

No mocks. All assertions verify real DB state via direct queries.
DB isolation via tmp_path per project testing rules.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def db(tmp_path):
    """Fresh FormExperienceDB backed by a real SQLite file in tmp_path."""
    return FormExperienceDB(db_path=str(tmp_path / "form_experience.db"))


# ---------------------------------------------------------------------------
# Recording and retrieval
# ---------------------------------------------------------------------------

class TestRecordFormFillExperience:
    """Record a complete form fill experience and verify all columns persisted."""

    def test_record_stores_all_fields(self, db):
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
        with sqlite3.connect(db._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM form_experience WHERE domain = ?",
                ("boards.greenhouse.io",),
            ).fetchone()

        assert row is not None
        assert row["platform"] == "greenhouse"
        assert row["adapter"] == "extension"
        assert row["pages_filled"] == 3
        assert json.loads(row["field_types"]) == ["text", "select", "upload", "radio"]
        assert json.loads(row["screening_questions"]) == [
            "Do you require sponsorship?",
            "Expected salary?",
        ]
        assert row["time_seconds"] == pytest.approx(42.5)
        assert row["success"] == 1
        assert row["apply_count"] == 1
        assert row["created_at"] != ""
        assert row["updated_at"] != ""

    def test_record_failure(self, db):
        db.record(
            domain="lever.co",
            platform="lever",
            adapter="extension",
            pages_filled=0,
            field_types=[],
            screening_questions=[],
            time_seconds=5.0,
            success=False,
        )
        exp = db.lookup("lever.co")
        assert exp is not None
        assert exp["success"] == 0
        assert exp["apply_count"] == 1


# ---------------------------------------------------------------------------
# Adaptive timing: running average across multiple fills
# ---------------------------------------------------------------------------

class TestAdaptiveTiming:
    """Record 3 fills, verify running average updates correctly."""

    def test_three_sample_running_average(self, db):
        db.store_timing("greenhouse.io", hydration_ms=100, fill_ms=3000, transition_ms=600)
        db.store_timing("greenhouse.io", hydration_ms=200, fill_ms=5000, transition_ms=1000)
        db.store_timing("greenhouse.io", hydration_ms=300, fill_ms=4000, transition_ms=800)

        timing = db.get_timing("greenhouse.io")
        assert timing is not None
        assert timing["sample_count"] == 3

        # Running average uses integer division at each step:
        # After sample 1: h=100, f=3000, t=600
        # After sample 2: h=(100*1+200)//2=150, f=(3000*1+5000)//2=4000, t=(600*1+1000)//2=800
        # After sample 3: h=(150*2+300)//3=200, f=(4000*2+4000)//3=4000, t=(800*2+800)//3=800
        assert timing["avg_hydration_ms"] == 200
        assert timing["avg_fill_ms"] == 4000
        assert timing["avg_transition_ms"] == 800

    def test_single_sample_equals_input(self, db):
        db.store_timing("lever.co", hydration_ms=500, fill_ms=8000, transition_ms=2000)
        timing = db.get_timing("lever.co")
        assert timing["sample_count"] == 1
        assert timing["avg_hydration_ms"] == 500
        assert timing["avg_fill_ms"] == 8000
        assert timing["avg_transition_ms"] == 2000

    def test_timing_returns_none_for_unknown_domain(self, db):
        assert db.get_timing("unknown.example.com") is None


# ---------------------------------------------------------------------------
# Selector learning: record a working selector, retrieve it
# ---------------------------------------------------------------------------

class TestSelectorLearning:
    """Record fill techniques (selectors) and verify retrieval by domain."""

    def test_record_and_retrieve_technique(self, db):
        db.record_fill_technique(
            domain_or_url="boards.greenhouse.io",
            field_label="Country",
            field_type="combobox",
            technique="combobox_prescanned_match",
            value_used="United Kingdom",
            success=True,
        )
        techniques = db.get_fill_techniques("boards.greenhouse.io")
        assert "Country" in techniques
        assert techniques["Country"]["technique"] == "combobox_prescanned_match"
        assert techniques["Country"]["value_used"] == "United Kingdom"
        assert techniques["Country"]["success"] == 1

    def test_successful_technique_updates_apply_count(self, db):
        db.record_fill_technique(
            "greenhouse.io", "Email", "input:text", "direct_fill",
            "test@example.com", success=True,
        )
        db.record_fill_technique(
            "greenhouse.io", "Email", "input:text", "direct_fill",
            "test@example.com", success=True,
        )
        techniques = db.get_fill_techniques("greenhouse.io")
        assert techniques["Email"]["apply_count"] == 2

    def test_get_fill_techniques_only_returns_successful(self, db):
        db.record_fill_technique(
            "lever.co", "Salary", "input:text", "direct_fill",
            "50000", success=False,
        )
        techniques = db.get_fill_techniques("lever.co")
        assert "Salary" not in techniques

    def test_container_selector_store_and_retrieve(self, db):
        db.store_container("greenhouse.io", "#application-form")
        assert db.get_container("greenhouse.io") == "#application-form"

    def test_container_selector_overwrites(self, db):
        db.store_container("greenhouse.io", "#old-form")
        db.store_container("greenhouse.io", "#new-form")
        assert db.get_container("greenhouse.io") == "#new-form"

    def test_container_returns_none_for_unknown(self, db):
        assert db.get_container("nonexistent.example.com") is None

    def test_delete_container(self, db):
        db.store_container("lever.co", ".apply-form")
        db.delete_container("lever.co")
        assert db.get_container("lever.co") is None


# ---------------------------------------------------------------------------
# Success-never-overwritten-by-failure rule
# ---------------------------------------------------------------------------

class TestSuccessNeverOverwrittenByFailure:
    """Record success, then failure -- verify success data preserved."""

    def test_success_preserved_on_failure_record(self, db):
        db.record(
            domain="jobs.lever.co",
            platform="lever",
            adapter="extension",
            pages_filled=3,
            field_types=["text", "select", "upload"],
            screening_questions=["Visa?"],
            time_seconds=45.0,
            success=True,
        )
        db.record(
            domain="jobs.lever.co",
            platform="lever",
            adapter="extension",
            pages_filled=0,
            field_types=[],
            screening_questions=[],
            time_seconds=2.0,
            success=False,
        )
        exp = db.lookup("jobs.lever.co")
        assert exp["success"] == 1, "Failure must not overwrite success"
        assert exp["pages_filled"] == 3, "Original pages_filled preserved"
        assert json.loads(exp["field_types"]) == ["text", "select", "upload"]
        assert exp["time_seconds"] == pytest.approx(45.0)
        assert exp["apply_count"] == 2, "Count incremented even on failure"

    def test_success_preserved_verified_via_raw_sql(self, db):
        """Direct SQL query confirms the invariant at the DB level."""
        db.record("x.example.com", "greenhouse", "ext", 2, ["text"], [], 30.0, True)
        db.record("x.example.com", "greenhouse", "ext", 0, [], [], 1.0, False)
        with sqlite3.connect(db._db_path) as conn:
            row = conn.execute(
                "SELECT success, pages_filled, time_seconds, apply_count "
                "FROM form_experience WHERE domain = 'x.example.com'"
            ).fetchone()
        assert row[0] == 1  # success
        assert row[1] == 2  # pages_filled from success run
        assert row[2] == pytest.approx(30.0)
        assert row[3] == 2  # count incremented

    def test_failure_can_be_overwritten_by_success(self, db):
        """A previous failure CAN be overwritten by a later success."""
        db.record("y.example.com", "lever", "ext", 0, [], [], 1.0, False)
        db.record("y.example.com", "lever", "ext", 4, ["text", "file"], ["Q1?"], 50.0, True)
        exp = db.lookup("y.example.com")
        assert exp["success"] == 1
        assert exp["pages_filled"] == 4
        assert exp["apply_count"] == 2


# ---------------------------------------------------------------------------
# Cross-domain isolation
# ---------------------------------------------------------------------------

class TestCrossDomainIsolation:
    """Verify greenhouse.io data does not leak into lever.co queries."""

    def test_lookup_isolated_per_domain(self, db):
        db.record("greenhouse.io", "greenhouse", "ext", 3,
                  ["text", "select"], ["Visa?"], 40.0, True)
        db.record("lever.co", "lever", "ext", 2,
                  ["text", "upload"], [], 20.0, True)

        gh = db.lookup("greenhouse.io")
        lv = db.lookup("lever.co")
        assert gh["platform"] == "greenhouse"
        assert lv["platform"] == "lever"
        assert json.loads(gh["field_types"]) != json.loads(lv["field_types"])

    def test_timing_isolated_per_domain(self, db):
        db.store_timing("greenhouse.io", hydration_ms=200, fill_ms=5000, transition_ms=1000)
        db.store_timing("lever.co", hydration_ms=100, fill_ms=3000, transition_ms=500)

        gh_timing = db.get_timing("greenhouse.io")
        lv_timing = db.get_timing("lever.co")
        assert gh_timing["avg_fill_ms"] == 5000
        assert lv_timing["avg_fill_ms"] == 3000

    def test_fill_techniques_isolated_per_domain(self, db):
        db.record_fill_technique("greenhouse.io", "Country", "combobox", "prescanned", "UK", True)
        db.record_fill_technique("lever.co", "Location", "text", "direct_fill", "London", True)

        gh = db.get_fill_techniques("greenhouse.io")
        lv = db.get_fill_techniques("lever.co")
        assert "Country" in gh
        assert "Country" not in lv
        assert "Location" in lv
        assert "Location" not in gh

    def test_field_mappings_isolated_per_domain(self, db):
        db.save_field_mappings("greenhouse.io", {"first_name": "first_name"})
        db.save_field_mappings("lever.co", {"full_name": "name"})

        gh = db.get_field_mappings("greenhouse.io")
        lv = db.get_field_mappings("lever.co")
        assert "first_name" in gh
        assert "first_name" not in lv
        assert "full_name" in lv
        assert "full_name" not in gh

    def test_failure_reasons_isolated_per_domain(self, db):
        db.record_failure_reason("greenhouse.io", "greenhouse", "no_field", "Disability")
        db.record_failure_reason("lever.co", "lever", "blocked", "Country")

        gh_failures = db.get_failure_reasons("greenhouse.io")
        lv_failures = db.get_failure_reasons("lever.co")
        assert len(gh_failures) == 1
        assert gh_failures[0]["field_label"] == "Disability"
        assert len(lv_failures) == 1
        assert lv_failures[0]["field_label"] == "Country"

    def test_container_selectors_isolated(self, db):
        db.store_container("greenhouse.io", "#application")
        db.store_container("lever.co", ".lever-form")
        assert db.get_container("greenhouse.io") == "#application"
        assert db.get_container("lever.co") == ".lever-form"


# ---------------------------------------------------------------------------
# Timing defaults per platform
# ---------------------------------------------------------------------------

class TestTimingDefaults:
    """Verify _get_adaptive_page_delay returns correct defaults per platform."""

    def test_workday_default_8s(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        assert _get_adaptive_page_delay("workday", None) == 8.0

    def test_linkedin_default_3s(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        assert _get_adaptive_page_delay("linkedin", None) == 3.0

    def test_greenhouse_default_5s(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        assert _get_adaptive_page_delay("greenhouse", None) == 5.0

    def test_indeed_default_8s(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        assert _get_adaptive_page_delay("indeed", None) == 8.0

    def test_unknown_platform_default_5s(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        assert _get_adaptive_page_delay("unknown_ats", None) == 5.0

    def test_fast_fill_overrides_to_zero(self, monkeypatch):
        monkeypatch.setenv("FAST_FILL", "true")
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        assert _get_adaptive_page_delay("workday", None) == 0.0

    def test_measured_timing_overrides_default(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        timing_data = {"avg_fill_ms": 10000}
        result = _get_adaptive_page_delay("greenhouse", timing_data)
        # max(10000/1000 * 1.1, 3.0) = max(11.0, 3.0) = 11.0
        assert result == pytest.approx(11.0)

    def test_measured_timing_respects_3s_floor(self, monkeypatch):
        monkeypatch.delenv("FAST_FILL", raising=False)
        from jobpulse.native_form_filler import _get_adaptive_page_delay
        timing_data = {"avg_fill_ms": 1000}
        result = _get_adaptive_page_delay("greenhouse", timing_data)
        # max(1000/1000 * 1.1, 3.0) = max(1.1, 3.0) = 3.0
        assert result == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Stale selector cleanup
# ---------------------------------------------------------------------------

class TestStaleSelectorCleanup:
    """When a fill technique fails repeatedly, verify it is marked stale
    (success=0) and excluded from get_fill_techniques results."""

    def test_failed_technique_excluded_from_results(self, db):
        """A technique recorded as success=False is excluded from get_fill_techniques."""
        db.record_fill_technique(
            "greenhouse.io", "Country", "combobox", "prescanned",
            "United Kingdom", success=True,
        )
        # Same domain+field but now fails -- ON CONFLICT overwrites
        db.record_fill_technique(
            "greenhouse.io", "Country", "combobox", "prescanned",
            "UK", success=False,
        )
        techniques = db.get_fill_techniques("greenhouse.io")
        assert "Country" not in techniques, (
            "Failed technique must be excluded from get_fill_techniques (success=1 filter)"
        )

    def test_failed_technique_still_in_raw_db(self, db):
        """The failed record exists in the DB -- it is just filtered by the query."""
        db.record_fill_technique(
            "lever.co", "Location", "text", "direct_fill",
            "London", success=True,
        )
        db.record_fill_technique(
            "lever.co", "Location", "text", "direct_fill",
            "Londn", success=False,
        )
        with sqlite3.connect(db._db_path) as conn:
            row = conn.execute(
                "SELECT success, apply_count FROM fill_techniques "
                "WHERE domain = 'lever.co' AND field_label = 'Location'"
            ).fetchone()
        assert row is not None
        assert row[0] == 0, "Latest write was failure"
        assert row[1] == 2, "Apply count incremented through both writes"

    def test_container_self_healing_via_delete(self, db):
        """Stale container selector can be deleted so re-detection triggers."""
        db.store_container("stale-domain.com", "#old-container")
        assert db.get_container("stale-domain.com") == "#old-container"
        db.delete_container("stale-domain.com")
        assert db.get_container("stale-domain.com") is None


# ---------------------------------------------------------------------------
# Domain normalization
# ---------------------------------------------------------------------------

class TestDomainNormalization:
    """Verify URLs are normalized to plain domains for consistent keying."""

    def test_url_normalized_to_domain(self, db):
        db.record(
            domain="https://boards.greenhouse.io/acme/jobs/123",
            platform="greenhouse",
            adapter="ext",
            pages_filled=2,
            field_types=["text"],
            screening_questions=[],
            time_seconds=30.0,
            success=True,
        )
        exp = db.lookup("boards.greenhouse.io")
        assert exp is not None
        assert exp["platform"] == "greenhouse"

    def test_www_prefix_stripped(self, db):
        db.record(
            domain="www.lever.co",
            platform="lever",
            adapter="ext",
            pages_filled=1,
            field_types=[],
            screening_questions=[],
            time_seconds=10.0,
            success=True,
        )
        exp = db.lookup("lever.co")
        assert exp is not None

    def test_lookup_with_url_finds_domain_record(self, db):
        db.record("example.com", "generic", "ext", 1, [], [], 5.0, True)
        exp = db.lookup("https://www.example.com/apply/42")
        assert exp is not None
        assert exp["domain"] == "example.com"


# ---------------------------------------------------------------------------
# Negative exemplars (PRAXIS)
# ---------------------------------------------------------------------------

class TestNegativeExemplars:
    """Verify negative exemplar storage and retrieval."""

    def test_store_and_retrieve_negative_exemplar(self, db):
        db.store_negative_exemplar(
            domain="greenhouse.io",
            field_label="Country",
            value_tried="UK",
            failure_reason="Option not found in dropdown",
            platform="greenhouse",
        )
        exemplars = db.get_negative_exemplars("greenhouse.io")
        assert len(exemplars) == 1
        assert exemplars[0]["field_label"] == "Country"
        assert exemplars[0]["value_tried"] == "UK"
        assert exemplars[0]["failure_reason"] == "Option not found in dropdown"
        assert exemplars[0]["attempt_count"] == 1

    def test_repeated_failure_increments_attempt_count(self, db):
        db.store_negative_exemplar("lever.co", "Salary", "high", "out of range")
        db.store_negative_exemplar("lever.co", "Salary", "high", "still out of range")
        exemplars = db.get_negative_exemplars("lever.co")
        assert len(exemplars) == 1
        assert exemplars[0]["attempt_count"] == 2
        assert exemplars[0]["failure_reason"] == "still out of range"

    def test_content_hash_cross_domain_lookup(self, db):
        db.store_negative_exemplar(
            "greenhouse.io", "Country", "UK", "not found",
            platform="greenhouse", content_hash="abc123",
        )
        db.store_negative_exemplar(
            "lever.co", "Location", "Londn", "typo",
            platform="lever", content_hash="abc123",
        )
        by_hash = db.get_negative_exemplars_by_hash("abc123")
        assert len(by_hash) == 2
        domains = {e["domain"] for e in by_hash}
        assert domains == {"greenhouse.io", "lever.co"}

    def test_empty_content_hash_returns_nothing(self, db):
        db.store_negative_exemplar("x.com", "f", "v", "r", content_hash="")
        assert db.get_negative_exemplars_by_hash("") == []


# ---------------------------------------------------------------------------
# Platform aggregate
# ---------------------------------------------------------------------------

class TestPlatformAggregate:
    """Verify cross-domain aggregation within a platform."""

    def test_aggregate_across_domains(self, db):
        db.record("gh1.greenhouse.io", "greenhouse", "ext", 2,
                  ["text", "select"], ["Visa?"], 40.0, True)
        db.record("gh2.greenhouse.io", "greenhouse", "ext", 4,
                  ["text", "select", "upload", "radio"], [], 80.0, True)

        agg = db.get_platform_aggregate("greenhouse")
        assert agg is not None
        assert agg["observation_count"] == 2
        assert agg["avg_pages"] == 3.0
        assert agg["avg_time_seconds"] == 60.0
        assert "text" in agg["common_field_types"]
        assert "select" in agg["common_field_types"]

    def test_aggregate_excludes_failures(self, db):
        db.record("ok.lever.co", "lever", "ext", 2, ["text"], [], 30.0, True)
        db.record("bad.lever.co", "lever", "ext", 0, [], [], 1.0, False)

        agg = db.get_platform_aggregate("lever")
        assert agg["observation_count"] == 1
        assert agg["avg_pages"] == 2.0

    def test_aggregate_returns_none_for_unknown_platform(self, db):
        assert db.get_platform_aggregate("nonexistent") is None


# ---------------------------------------------------------------------------
# Scan strategy preferences
# ---------------------------------------------------------------------------

class TestScanStrategyPreferences:
    def test_store_and_retrieve_strategy(self, db):
        db.store_scan_strategy("greenhouse.io", "scoped_cdp", field_count=12)
        strat = db.get_scan_strategy("greenhouse.io")
        assert strat is not None
        assert strat["preferred_strategy"] == "scoped_cdp"
        assert strat["field_count"] == 12
        assert strat["sample_count"] == 1

    def test_strategy_updates_on_repeat(self, db):
        db.store_scan_strategy("lever.co", "full_a11y", field_count=8)
        db.store_scan_strategy("lever.co", "scoped_cdp", field_count=10)
        strat = db.get_scan_strategy("lever.co")
        assert strat["preferred_strategy"] == "scoped_cdp"
        assert strat["field_count"] == 10
        assert strat["sample_count"] == 2


# ---------------------------------------------------------------------------
# Field confidence calibration
# ---------------------------------------------------------------------------

class TestFieldConfidenceCalibration:
    def test_log_and_query_calibration(self, db):
        db.log_field_confidence("greenhouse.io", "Country", 0.9, actual_correct=True)
        db.log_field_confidence("greenhouse.io", "Country", 0.8, actual_correct=True)
        db.log_field_confidence("greenhouse.io", "Country", 0.7, actual_correct=False)

        cal = db.get_confidence_calibration("greenhouse.io")
        assert cal["total"] == 3
        assert cal["correct"] == 2


# ---------------------------------------------------------------------------
# Store with content_hash (PRAXIS variant)
# ---------------------------------------------------------------------------

class TestStoreWithContentHash:
    def test_store_and_lookup_by_content_hash(self, db):
        db.store(
            domain="gh.example.com",
            platform="greenhouse",
            adapter="ext",
            pages_filled=2,
            field_types=["text", "select"],
            screening_questions=[],
            time_seconds=30.0,
            success=True,
            content_hash="sha256_abc",
        )
        result = db.lookup_by_content_hash("sha256_abc", exclude_domain="other.com")
        assert result is not None
        assert result["domain"] == "gh.example.com"

    def test_lookup_by_content_hash_excludes_same_domain(self, db):
        db.store("same.com", "greenhouse", "ext", 1, [], [], 10.0, True, "hash1")
        result = db.lookup_by_content_hash("hash1", exclude_domain="same.com")
        assert result is None

    def test_store_preserves_success_on_failure(self, db):
        """store() has the same success-never-overwritten-by-failure rule as record()."""
        db.store("s.com", "lever", "ext", 3, ["text"], [], 40.0, True, "h1")
        db.store("s.com", "lever", "ext", 0, [], [], 1.0, False, "h1")
        exp = db.lookup("s.com")
        assert exp["success"] == 1
        assert exp["apply_count"] == 2


# ---------------------------------------------------------------------------
# Validate against live DOM
# ---------------------------------------------------------------------------

class TestValidateAgainstLive:
    def test_trusted_when_exact_match(self, db):
        db.record("g.io", "greenhouse", "ext", 2,
                  ["text", "select", "upload"], [], 30.0, True)
        result = db.validate_against_live("g.io", ["text", "select", "upload"])
        assert result["trusted"] is True
        assert result["match_ratio"] == 1.0
        assert result["diverged_fields"] == []

    def test_untrusted_when_diverged(self, db):
        db.record("g.io", "greenhouse", "ext", 2,
                  ["text", "select", "upload"], [], 30.0, True)
        result = db.validate_against_live(
            "g.io", ["textarea", "checkbox", "radio"],
        )
        assert result["trusted"] is False
        assert result["match_ratio"] < 0.8

    def test_page_count_mismatch_untrusts(self, db):
        db.record("g.io", "greenhouse", "ext", 3,
                  ["text", "select"], [], 30.0, True)
        result = db.validate_against_live(
            "g.io", ["text", "select"], live_page_count=8,
        )
        assert result["trusted"] is False

    def test_no_stored_returns_untrusted(self, db):
        result = db.validate_against_live("unknown.com", ["text"])
        assert result["trusted"] is False
        assert result["stored"] is None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_stats_counts(self, db):
        db.record("a.com", "greenhouse", "ext", 1, [], [], 10.0, True)
        db.record("b.com", "lever", "ext", 2, [], [], 15.0, False)
        db.record_failure_reason("b.com", "lever", "no_field", "Country")
        db.record_failure_reason("b.com", "lever", "blocked", "Email")

        stats = db.get_stats()
        assert stats["total_domains"] == 2
        assert stats["successful_domains"] == 1
        assert stats["recorded_failures"] == 2
