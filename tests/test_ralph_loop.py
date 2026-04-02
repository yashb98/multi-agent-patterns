"""Tests for the Ralph Loop self-healing job application engine.

All tests use tmp_path for DB isolation — NEVER touches data/*.db.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.ralph_loop.pattern_store import (
    PatternStore,
    FixPattern,
    compute_error_signature,
)
from jobpulse.ralph_loop.diagnoser import (
    infer_step_from_error,
    heuristic_diagnosis,
)
from jobpulse.ralph_loop.loop import (
    build_overrides_from_fixes,
    _merge_fix_into_overrides,
    ralph_apply_sync,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temp DB path — never touches production."""
    return str(tmp_path / "ralph_test.db")


@pytest.fixture
def store(db_path: str) -> PatternStore:
    """Fresh PatternStore with isolated DB."""
    return PatternStore(db_path=db_path)


# ---------------------------------------------------------------------------
# PatternStore Tests
# ---------------------------------------------------------------------------


class TestPatternStore:
    def test_init_creates_tables(self, store: PatternStore) -> None:
        """Verify all 3 tables exist after init."""
        import sqlite3

        conn = sqlite3.connect(store.db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = {t[0] for t in tables}
        assert "fix_patterns" in table_names
        assert "apply_attempts" in table_names
        assert "consolidation_log" in table_names

    def test_save_and_get_fix(self, store: PatternStore) -> None:
        """Round-trip a fix pattern through save and get."""
        fix = store.save_fix(
            platform="linkedin",
            step_name="click_apply_button",
            error_signature="abc123",
            fix_type="selector_override",
            fix_payload={"original_selector": "button.old", "new_selector": "button.new"},
            confidence=0.8,
        )
        assert fix.platform == "linkedin"
        assert fix.fix_type == "selector_override"
        assert fix.confidence == 0.8

        retrieved = store.get_fix("linkedin", "click_apply_button", "abc123")
        assert retrieved is not None
        assert retrieved.id == fix.id
        assert retrieved.payload["new_selector"] == "button.new"

    def test_get_fix_returns_none_for_missing(self, store: PatternStore) -> None:
        assert store.get_fix("missing", "missing", "missing") is None

    def test_upsert_on_conflict(self, store: PatternStore) -> None:
        """Same (platform, step, sig) should upsert, not duplicate."""
        store.save_fix(
            platform="indeed",
            step_name="page_load",
            error_signature="sig1",
            fix_type="wait_adjustment",
            fix_payload={"step": "page_load", "timeout_ms": 5000},
            confidence=0.5,
        )
        store.save_fix(
            platform="indeed",
            step_name="page_load",
            error_signature="sig1",
            fix_type="wait_adjustment",
            fix_payload={"step": "page_load", "timeout_ms": 15000},
            confidence=0.9,
        )
        fixes = store.get_fixes_for_platform("indeed")
        assert len(fixes) == 1
        assert fixes[0].confidence == 0.9
        assert fixes[0].payload["timeout_ms"] == 15000

    def test_invalid_fix_type_raises(self, store: PatternStore) -> None:
        with pytest.raises(ValueError, match="Unknown fix_type"):
            store.save_fix(
                platform="test",
                step_name="test",
                error_signature="test",
                fix_type="invalid_type",
                fix_payload={},
            )

    def test_get_fixes_for_platform_filters(self, store: PatternStore) -> None:
        store.save_fix("linkedin", "step1", "sig1", "selector_override", {"original_selector": "a", "new_selector": "b"})
        store.save_fix("indeed", "step1", "sig1", "selector_override", {"original_selector": "a", "new_selector": "b"})

        linkedin_fixes = store.get_fixes_for_platform("linkedin")
        assert len(linkedin_fixes) == 1
        assert linkedin_fixes[0].platform == "linkedin"

    def test_mark_fixes_applied(self, store: PatternStore) -> None:
        fix = store.save_fix("test", "step", "sig", "wait_adjustment", {"step": "load", "timeout_ms": 5000})
        store.mark_fixes_applied([fix.id])

        retrieved = store.get_fix("test", "step", "sig")
        assert retrieved is not None
        assert retrieved.times_applied == 1

    def test_mark_fixes_successful(self, store: PatternStore) -> None:
        fix = store.save_fix("test", "step", "sig", "wait_adjustment", {"step": "load", "timeout_ms": 5000})
        store.mark_fixes_applied([fix.id])
        store.mark_fixes_successful([fix.id])

        retrieved = store.get_fix("test", "step", "sig")
        assert retrieved is not None
        assert retrieved.times_succeeded == 1
        assert retrieved.success_rate > 0

    def test_record_attempt(self, store: PatternStore) -> None:
        aid = store.record_attempt(
            job_url="https://example.com/job/1",
            platform="greenhouse",
            iteration=1,
            step_name="contact_info",
            outcome="failed",
            error_message="Phone field not found",
            error_signature="abc",
        )
        assert aid is not None

        history = store.get_attempt_history("https://example.com/job/1")
        assert len(history) == 1
        assert history[0]["outcome"] == "failed"
        assert history[0]["step_name"] == "contact_info"

    def test_consolidation_merges_redundant(self, store: PatternStore) -> None:
        """With 10+ fixes, consolidation merges low success_rate duplicates."""
        # Create 10 fixes for the same step with different sigs
        for i in range(10):
            fix = store.save_fix(
                "linkedin", "click_apply_button", f"sig{i}",
                "selector_override",
                {"original_selector": f"btn{i}", "new_selector": f"btn_new{i}"},
                confidence=0.6,
            )
            # Give most of them low success rate
            if i > 0:
                store.mark_fixes_applied([fix.id])

        # Give the first one a high success rate
        first = store.get_fixes_for_platform("linkedin")[0]
        store.mark_fixes_applied([first.id])
        store.mark_fixes_successful([first.id])

        merged = store.consolidate_patterns("linkedin", min_fixes=10)
        assert merged > 0

    def test_consolidation_skips_below_threshold(self, store: PatternStore) -> None:
        store.save_fix("test", "step", "sig1", "selector_override", {"original_selector": "a", "new_selector": "b"})
        merged = store.consolidate_patterns("test", min_fixes=10)
        assert merged == 0


# ---------------------------------------------------------------------------
# Error Signature Tests
# ---------------------------------------------------------------------------


class TestErrorSignature:
    def test_stability(self) -> None:
        """Same error always produces same signature."""
        sig1 = compute_error_signature("linkedin", "click", "Button not found")
        sig2 = compute_error_signature("linkedin", "click", "Button not found")
        assert sig1 == sig2

    def test_strips_timestamps(self) -> None:
        """Timestamps in errors should be stripped for consistent hashing."""
        sig1 = compute_error_signature("test", "step", "Error at 2026-03-31T14:30:00Z")
        sig2 = compute_error_signature("test", "step", "Error at 2026-04-01T09:00:00Z")
        assert sig1 == sig2

    def test_strips_uuids(self) -> None:
        sig1 = compute_error_signature("test", "step", "Job 550e8400-e29b-41d4-a716-446655440000 failed")
        sig2 = compute_error_signature("test", "step", "Job a1b2c3d4-e5f6-7890-abcd-ef1234567890 failed")
        assert sig1 == sig2

    def test_strips_urls(self) -> None:
        sig1 = compute_error_signature("test", "step", "Failed at https://example.com/job/123")
        sig2 = compute_error_signature("test", "step", "Failed at https://other.com/job/999")
        assert sig1 == sig2

    def test_different_errors_different_sigs(self) -> None:
        sig1 = compute_error_signature("test", "step", "Button not found")
        sig2 = compute_error_signature("test", "step", "Timeout waiting for page")
        assert sig1 != sig2

    def test_platform_matters(self) -> None:
        sig1 = compute_error_signature("linkedin", "step", "Error")
        sig2 = compute_error_signature("indeed", "step", "Error")
        assert sig1 != sig2


# ---------------------------------------------------------------------------
# Diagnoser Tests
# ---------------------------------------------------------------------------


class TestStepInference:
    def test_apply_button(self) -> None:
        assert infer_step_from_error("Easy Apply button not found", "linkedin") == "click_apply_button"

    def test_file_upload(self) -> None:
        assert infer_step_from_error("Failed to upload resume file", "greenhouse") == "file_upload"

    def test_contact_info(self) -> None:
        assert infer_step_from_error("Phone number field not visible", "indeed") == "contact_info"

    def test_form_navigation(self) -> None:
        assert infer_step_from_error("Next button not clickable", "linkedin") == "form_navigation"

    def test_final_submit(self) -> None:
        assert infer_step_from_error("Submit application failed", "workday") == "final_submit"

    def test_timeout(self) -> None:
        assert infer_step_from_error("Timeout 30000ms exceeded", "indeed") == "page_load"

    def test_verification(self) -> None:
        assert infer_step_from_error("Cloudflare challenge detected", "linkedin") == "verification_wall"

    def test_unknown(self) -> None:
        assert infer_step_from_error("Something completely unexpected", "test") == "unknown"


class TestHeuristicDiagnosis:
    def test_timeout_maps_to_wait_adjustment(self) -> None:
        result = heuristic_diagnosis("Timeout 30000ms exceeded", "indeed")
        assert result is not None
        assert result["fix_type"] == "wait_adjustment"

    def test_not_found_maps_to_selector_override(self) -> None:
        result = heuristic_diagnosis("Element not found: #submit-btn", "greenhouse")
        assert result is not None
        assert result["fix_type"] == "selector_override"

    def test_not_visible_maps_to_interaction_change(self) -> None:
        result = heuristic_diagnosis("Element not interactable, covered by overlay", "workday")
        assert result is not None
        assert result["fix_type"] == "interaction_change"

    def test_file_maps_to_strategy_switch(self) -> None:
        result = heuristic_diagnosis("File upload input not accepting files", "lever")
        assert result is not None
        assert result["fix_type"] == "strategy_switch"

    def test_unknown_error_returns_none(self) -> None:
        result = heuristic_diagnosis("Completely alien error XYZ", "test")
        assert result is None


# ---------------------------------------------------------------------------
# Overrides Builder Tests
# ---------------------------------------------------------------------------


class TestOverridesBuilder:
    def test_empty_fixes_empty_overrides(self) -> None:
        overrides = build_overrides_from_fixes([])
        assert overrides["selector_overrides"] == {}
        assert overrides["wait_overrides"] == {}
        assert overrides["strategy_overrides"] == {}
        assert overrides["field_remaps"] == {}
        assert overrides["interaction_mods"] == {}

    def test_selector_override(self) -> None:
        fix = FixPattern(
            id="f1", platform="linkedin", step_name="click",
            error_signature="sig", fix_type="selector_override",
            fix_payload=json.dumps({"original_selector": "btn.old", "new_selector": "btn.new"}),
            confidence=0.9, times_applied=0, times_succeeded=0,
            success_rate=0.0, created_at="", last_used_at=None, superseded_by=None,
        )
        overrides = build_overrides_from_fixes([fix])
        assert overrides["selector_overrides"]["btn.old"] == "btn.new"

    def test_wait_adjustment(self) -> None:
        fix = FixPattern(
            id="f2", platform="indeed", step_name="page_load",
            error_signature="sig", fix_type="wait_adjustment",
            fix_payload=json.dumps({"step": "page_load", "timeout_ms": 30000}),
            confidence=0.7, times_applied=0, times_succeeded=0,
            success_rate=0.0, created_at="", last_used_at=None, superseded_by=None,
        )
        overrides = build_overrides_from_fixes([fix])
        assert overrides["wait_overrides"]["page_load"] == 30000

    def test_field_remap(self) -> None:
        fix = FixPattern(
            id="f3", platform="greenhouse", step_name="contact",
            error_signature="sig", fix_type="field_remap",
            fix_payload=json.dumps({"field_label": "Mobile Number", "profile_key": "phone"}),
            confidence=0.8, times_applied=0, times_succeeded=0,
            success_rate=0.0, created_at="", last_used_at=None, superseded_by=None,
        )
        overrides = build_overrides_from_fixes([fix])
        assert overrides["field_remaps"]["Mobile Number"] == "phone"

    def test_merge_fix_into_existing(self) -> None:
        overrides = build_overrides_from_fixes([])
        fix = FixPattern(
            id="f4", platform="test", step_name="step",
            error_signature="sig", fix_type="strategy_switch",
            fix_payload=json.dumps({"step": "file_upload", "new_strategy": "drag_and_drop"}),
            confidence=0.6, times_applied=0, times_succeeded=0,
            success_rate=0.0, created_at="", last_used_at=None, superseded_by=None,
        )
        merged = _merge_fix_into_overrides(overrides, fix)
        assert merged["strategy_overrides"]["file_upload"] == "drag_and_drop"


# ---------------------------------------------------------------------------
# Ralph Loop Integration Tests
# ---------------------------------------------------------------------------


class TestRalphLoop:
    def test_success_on_first_try(self, db_path: str) -> None:
        """When adapter succeeds on first try, no diagnosis is called."""
        mock_result = {"success": True, "screenshot": None, "error": None}

        with patch("jobpulse.applicator.apply_job", return_value=mock_result):
            result = ralph_apply_sync(
                url="https://example.com/job/1",
                ats_platform="greenhouse",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        assert result["success"] is True
        assert result.get("ralph_iterations") == 1

    def test_retry_after_failure_with_heuristic(self, db_path: str) -> None:
        """First call fails with timeout, heuristic diagnoses, second succeeds."""
        fail_result = {"success": False, "screenshot": None, "error": "Timeout 30000ms exceeded"}
        success_result = {"success": True, "screenshot": None, "error": None}

        mock_adapter = MagicMock()
        mock_adapter.fill_and_submit.return_value = success_result

        with patch("jobpulse.applicator.apply_job", return_value=fail_result), \
             patch("jobpulse.applicator.select_adapter", return_value=mock_adapter):
            result = ralph_apply_sync(
                url="https://example.com/job/2",
                ats_platform="indeed",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        assert result["success"] is True
        assert result.get("ralph_iterations") == 2

    def test_max_iterations_respected(self, db_path: str) -> None:
        """Always fails — loop stops at MAX_ITERATIONS."""
        fail_result = {"success": False, "screenshot": None, "error": "Element not found: #submit"}

        mock_adapter = MagicMock()
        mock_adapter.fill_and_submit.return_value = fail_result

        with patch("jobpulse.applicator.apply_job", return_value=fail_result), \
             patch("jobpulse.applicator.select_adapter", return_value=mock_adapter):
            result = ralph_apply_sync(
                url="https://example.com/job/3",
                ats_platform="greenhouse",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        assert result["success"] is False
        assert result.get("ralph_exhausted") is True

    def test_known_pattern_applied_proactively(self, db_path: str) -> None:
        """Pre-seed a fix pattern, verify it's loaded into overrides."""
        store = PatternStore(db_path=db_path)
        store.save_fix(
            platform="greenhouse",
            step_name="click_apply_button",
            error_signature="preseeded",
            fix_type="selector_override",
            fix_payload={"original_selector": "btn.old", "new_selector": "btn.new"},
            confidence=0.9,
        )

        success_result = {"success": True, "screenshot": None, "error": None}

        with patch("jobpulse.applicator.apply_job", return_value=success_result) as mock_apply:
            result = ralph_apply_sync(
                url="https://example.com/job/4",
                ats_platform="greenhouse",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        assert result["success"] is True
        # Verify overrides were passed to apply_job
        call_kwargs = mock_apply.call_args
        overrides = call_kwargs.kwargs.get("overrides") or call_kwargs[1].get("overrides")
        assert overrides is not None
        assert "btn.old" in overrides.get("selector_overrides", {})

    def test_rate_limited_aborts_immediately(self, db_path: str) -> None:
        """Rate limited result should not trigger retries."""
        rate_limited_result = {"success": False, "error": "Daily limit reached", "rate_limited": True}

        with patch("jobpulse.applicator.apply_job", return_value=rate_limited_result):
            result = ralph_apply_sync(
                url="https://example.com/job/5",
                ats_platform="linkedin",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        assert result["success"] is False
        assert result.get("rate_limited") is True

    def test_attempt_history_recorded(self, db_path: str) -> None:
        """Verify attempts are recorded to the database."""
        fail_result = {"success": False, "screenshot": None, "error": "Timeout waiting for page"}
        success_result = {"success": True, "screenshot": None, "error": None}

        mock_adapter = MagicMock()
        mock_adapter.fill_and_submit.return_value = success_result

        with patch("jobpulse.applicator.apply_job", return_value=fail_result), \
             patch("jobpulse.applicator.select_adapter", return_value=mock_adapter):
            ralph_apply_sync(
                url="https://example.com/job/6",
                ats_platform="indeed",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        store = PatternStore(db_path=db_path)
        history = store.get_attempt_history("https://example.com/job/6")
        assert len(history) >= 1


# ---------------------------------------------------------------------------
# PatternStore Source Tracking Tests
# ---------------------------------------------------------------------------


class TestPatternStoreSourceTracking:
    def test_save_fix_defaults_to_production(self, store: PatternStore) -> None:
        """Default source is 'production', confirmed=True, occurrence_count=1."""
        fix = store.save_fix(
            platform="linkedin",
            step_name="click_apply_button",
            error_signature="sig_prod_default",
            fix_type="selector_override",
            fix_payload={"original_selector": "btn.old", "new_selector": "btn.new"},
            confidence=0.8,
        )
        assert fix.source == "production"
        assert fix.confirmed is True
        assert fix.occurrence_count == 1

    def test_save_fix_test_source_unconfirmed(self, store: PatternStore) -> None:
        """First save with source='test' → confirmed=False."""
        fix = store.save_fix(
            platform="linkedin",
            step_name="click_apply_button",
            error_signature="sig_test_unconfirmed",
            fix_type="selector_override",
            fix_payload={"original_selector": "btn.old", "new_selector": "btn.new"},
            confidence=0.6,
            source="test",
        )
        assert fix.source == "test"
        assert fix.confirmed is False
        assert fix.occurrence_count == 1

    def test_save_fix_test_promotes_on_second_occurrence(self, store: PatternStore) -> None:
        """Two saves with source='test' for same key → confirmed=True, count=2."""
        store.save_fix(
            platform="indeed",
            step_name="page_load",
            error_signature="sig_test_promote",
            fix_type="wait_adjustment",
            fix_payload={"step": "page_load", "timeout_ms": 10000},
            confidence=0.5,
            source="test",
        )
        fix2 = store.save_fix(
            platform="indeed",
            step_name="page_load",
            error_signature="sig_test_promote",
            fix_type="wait_adjustment",
            fix_payload={"step": "page_load", "timeout_ms": 10000},
            confidence=0.5,
            source="test",
        )
        assert fix2.confirmed is True
        assert fix2.occurrence_count == 2

    def test_save_fix_production_promotes_test_fix(self, store: PatternStore) -> None:
        """test then production for same key → source='production', confirmed=True."""
        store.save_fix(
            platform="greenhouse",
            step_name="contact_info",
            error_signature="sig_prod_promotes",
            fix_type="field_remap",
            fix_payload={"field_label": "Mobile", "profile_key": "phone"},
            confidence=0.5,
            source="test",
        )
        fix2 = store.save_fix(
            platform="greenhouse",
            step_name="contact_info",
            error_signature="sig_prod_promotes",
            fix_type="field_remap",
            fix_payload={"field_label": "Mobile", "profile_key": "phone"},
            confidence=0.7,
            source="production",
        )
        assert fix2.source == "production"
        assert fix2.confirmed is True

    def test_save_fix_manual_always_confirmed(self, store: PatternStore) -> None:
        """source='manual' → confirmed=True regardless of occurrence count."""
        fix = store.save_fix(
            platform="workday",
            step_name="final_submit",
            error_signature="sig_manual",
            fix_type="interaction_change",
            fix_payload={"action": "js_click"},
            confidence=0.9,
            source="manual",
        )
        assert fix.source == "manual"
        assert fix.confirmed is True

    def test_get_fix_includes_source_fields(self, store: PatternStore) -> None:
        """Retrieved fix includes source, confirmed, and occurrence_count fields."""
        store.save_fix(
            platform="lever",
            step_name="file_upload",
            error_signature="sig_fields",
            fix_type="strategy_switch",
            fix_payload={"step": "file_upload", "new_strategy": "drag_and_drop"},
            confidence=0.75,
            source="production",
        )
        retrieved = store.get_fix("lever", "file_upload", "sig_fields")
        assert retrieved is not None
        assert retrieved.source == "production"
        assert retrieved.confirmed is True
        assert retrieved.occurrence_count == 1

    def test_occurrence_count_increments_on_repeated_save(self, store: PatternStore) -> None:
        """Each save for the same key increments occurrence_count."""
        for i in range(3):
            fix = store.save_fix(
                platform="linkedin",
                step_name="form_navigation",
                error_signature="sig_count",
                fix_type="selector_override",
                fix_payload={"original_selector": "a.old", "new_selector": "a.new"},
                confidence=0.5,
                source="test",
            )
        assert fix.occurrence_count == 3


# ---------------------------------------------------------------------------
# PatternStore Pruning Tests
# ---------------------------------------------------------------------------


class TestPatternStorePruning:
    def test_prune_stale_test_fixes(self, store: PatternStore) -> None:
        """Backdated (15 days old) unconfirmed test fix is deleted by prune."""
        import sqlite3
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=15)
        old_iso = cutoff.isoformat()

        # Insert directly via SQL to backdate created_at
        conn = sqlite3.connect(store.db_path)
        conn.execute(
            """INSERT INTO fix_patterns
               (id, platform, step_name, error_signature, fix_type, fix_payload,
                confidence, times_applied, times_succeeded, success_rate,
                created_at, last_used_at, superseded_by, source, confirmed, occurrence_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "stale_test_fix", "linkedin", "click_apply_button", "sig_stale_test",
                "selector_override", '{"original_selector": "a", "new_selector": "b"}',
                0.5, 0, 0, 0.0, old_iso, None, None,
                "test", 0, 1,
            ),
        )
        conn.commit()
        conn.close()

        deleted = store.prune_stale_test_fixes(max_age_days=14)
        assert deleted == 1

        # Verify it's gone
        retrieved = store.get_fix("linkedin", "click_apply_button", "sig_stale_test")
        assert retrieved is None

    def test_prune_keeps_confirmed_test_fixes(self, store: PatternStore) -> None:
        """Backdated confirmed test fix (occurrence_count=2) is NOT pruned."""
        import sqlite3
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=15)
        old_iso = cutoff.isoformat()

        conn = sqlite3.connect(store.db_path)
        conn.execute(
            """INSERT INTO fix_patterns
               (id, platform, step_name, error_signature, fix_type, fix_payload,
                confidence, times_applied, times_succeeded, success_rate,
                created_at, last_used_at, superseded_by, source, confirmed, occurrence_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "confirmed_test_fix", "indeed", "page_load", "sig_confirmed_test",
                "wait_adjustment", '{"step": "page_load", "timeout_ms": 10000}',
                0.7, 2, 1, 0.5, old_iso, None, None,
                "test", 1, 2,
            ),
        )
        conn.commit()
        conn.close()

        deleted = store.prune_stale_test_fixes(max_age_days=14)
        assert deleted == 0

        # Verify it's still there
        retrieved = store.get_fix("indeed", "page_load", "sig_confirmed_test")
        assert retrieved is not None

    def test_prune_keeps_production_fixes(self, store: PatternStore) -> None:
        """Backdated production fix is NOT pruned (only test fixes are pruned)."""
        import sqlite3
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=15)
        old_iso = cutoff.isoformat()

        conn = sqlite3.connect(store.db_path)
        conn.execute(
            """INSERT INTO fix_patterns
               (id, platform, step_name, error_signature, fix_type, fix_payload,
                confidence, times_applied, times_succeeded, success_rate,
                created_at, last_used_at, superseded_by, source, confirmed, occurrence_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "old_prod_fix", "greenhouse", "contact_info", "sig_old_prod",
                "field_remap", '{"field_label": "Phone", "profile_key": "phone"}',
                0.9, 5, 4, 0.8, old_iso, None, None,
                "production", 1, 1,
            ),
        )
        conn.commit()
        conn.close()

        deleted = store.prune_stale_test_fixes(max_age_days=14)
        assert deleted == 0

        # Verify it's still there
        retrieved = store.get_fix("greenhouse", "contact_info", "sig_old_prod")
        assert retrieved is not None


# ---------------------------------------------------------------------------
# Build Overrides Filtering Tests
# ---------------------------------------------------------------------------


class TestBuildOverridesFiltering:
    def _make_fix(
        self,
        *,
        fix_type: str = "selector_override",
        fix_payload: str | None = None,
        source: str = "production",
        confirmed: bool = True,
        superseded_by: str | None = None,
        occurrence_count: int = 1,
    ) -> FixPattern:
        """Helper to build a FixPattern for filtering tests."""
        if fix_payload is None:
            fix_payload = json.dumps({"original_selector": "btn.old", "new_selector": "btn.new"})
        return FixPattern(
            id="fix_id",
            platform="linkedin",
            step_name="click_apply_button",
            error_signature="sig_filter",
            fix_type=fix_type,
            fix_payload=fix_payload,
            confidence=0.8,
            times_applied=0,
            times_succeeded=0,
            success_rate=0.0,
            created_at="2026-01-01T00:00:00+00:00",
            last_used_at=None,
            superseded_by=superseded_by,
            source=source,
            confirmed=confirmed,
            occurrence_count=occurrence_count,
        )

    def test_skips_unconfirmed_test_fix(self) -> None:
        """FixPattern with source='test', confirmed=False → selector_overrides stays empty."""
        fix = self._make_fix(source="test", confirmed=False)
        overrides = build_overrides_from_fixes([fix])
        assert overrides["selector_overrides"] == {}

    def test_applies_confirmed_test_fix(self) -> None:
        """FixPattern with source='test', confirmed=True, occurrence_count=2 → selector applied."""
        fix = self._make_fix(source="test", confirmed=True, occurrence_count=2)
        overrides = build_overrides_from_fixes([fix])
        assert overrides["selector_overrides"].get("btn.old") == "btn.new"

    def test_applies_production_fix(self) -> None:
        """FixPattern with source='production' → selector applied."""
        fix = self._make_fix(source="production", confirmed=True)
        overrides = build_overrides_from_fixes([fix])
        assert overrides["selector_overrides"].get("btn.old") == "btn.new"

    def test_skips_superseded_fix(self) -> None:
        """FixPattern with superseded_by set → selector NOT applied."""
        fix = self._make_fix(source="production", confirmed=True, superseded_by="winner1")
        overrides = build_overrides_from_fixes([fix])
        assert overrides["selector_overrides"] == {}


# ---------------------------------------------------------------------------
# Dry Run Flag Tests
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    def test_dry_run_passes_to_apply_job(self, db_path: str) -> None:
        """ralph_apply_sync(dry_run=True) passes dry_run=True to apply_job."""
        mock_result = {"success": True, "screenshot": None, "error": None}

        with patch("jobpulse.applicator.apply_job", return_value=mock_result) as mock_apply:
            ralph_apply_sync(
                url="https://example.com/job/dry1",
                ats_platform="greenhouse",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
                dry_run=True,
            )

        call_kwargs = mock_apply.call_args
        passed_dry_run = call_kwargs.kwargs.get("dry_run") if call_kwargs.kwargs else None
        if passed_dry_run is None and call_kwargs.args:
            # positional fallback — dry_run would be after overrides
            passed_dry_run = call_kwargs.args[-1]
        assert passed_dry_run is True, f"Expected dry_run=True, got {passed_dry_run!r}"

    def test_dry_run_false_by_default(self, db_path: str) -> None:
        """ralph_apply_sync without dry_run arg passes dry_run=False to apply_job."""
        mock_result = {"success": True, "screenshot": None, "error": None}

        with patch("jobpulse.applicator.apply_job", return_value=mock_result) as mock_apply:
            ralph_apply_sync(
                url="https://example.com/job/dry2",
                ats_platform="greenhouse",
                cv_path=Path("/tmp/test_cv.pdf"),
                db_path=db_path,
            )

        call_kwargs = mock_apply.call_args
        passed_dry_run = call_kwargs.kwargs.get("dry_run", False) if call_kwargs.kwargs else False
        assert passed_dry_run is False, f"Expected dry_run=False (default), got {passed_dry_run!r}"


# ---------------------------------------------------------------------------
# LinkedIn Dry Run Tests
# ---------------------------------------------------------------------------


class TestLinkedInDryRun:
    def test_dry_run_param_in_signature(self) -> None:
        """LinkedInAdapter.fill_and_submit must have a 'dry_run' parameter."""
        import inspect
        from jobpulse.ats_adapters.linkedin import LinkedInAdapter

        sig = inspect.signature(LinkedInAdapter.fill_and_submit)
        assert "dry_run" in sig.parameters, (
            f"fill_and_submit is missing 'dry_run' parameter. "
            f"Found params: {list(sig.parameters.keys())}"
        )


# ---------------------------------------------------------------------------
# PatternStore Mode Tests
# ---------------------------------------------------------------------------


class TestPatternStoreMode:
    """Tests for PatternStore mode parameter."""

    def test_test_mode_auto_sets_source(self, db_path):
        store = PatternStore(db_path=db_path, mode="test")
        fix = store.save_fix(
            platform="linkedin", step_name="s1", error_signature="e1",
            fix_type="selector_override",
            fix_payload={"original_selector": "a", "new_selector": "b"},
        )
        assert fix.source == "test"
        assert fix.confirmed is False

    def test_production_mode_auto_sets_source(self, db_path):
        store = PatternStore(db_path=db_path, mode="production")
        fix = store.save_fix(
            platform="linkedin", step_name="s1", error_signature="e1",
            fix_type="selector_override",
            fix_payload={"original_selector": "a", "new_selector": "b"},
        )
        assert fix.source == "production"
        assert fix.confirmed is True

    def test_default_mode_is_production(self, db_path):
        store = PatternStore(db_path=db_path)
        assert store.mode == "production"

    def test_explicit_source_overrides_mode(self, db_path):
        store = PatternStore(db_path=db_path, mode="test")
        fix = store.save_fix(
            platform="linkedin", step_name="s1", error_signature="e1",
            fix_type="selector_override",
            fix_payload={"original_selector": "a", "new_selector": "b"},
            source="manual",
        )
        assert fix.source == "manual"
        assert fix.confirmed is True
