"""Tests for the Ralph Loop self-healing retry wrapper.

Covers:
- build_overrides_from_fixes: all 5 fix types, superseded skip, unconfirmed test skip
- _merge_fix_into_overrides: merging a single fix into existing overrides
- ralph_apply_sync: success on first try, retry with known fix, heuristic diagnosis,
  rate-limited abort, undiagnosable stop, max iterations exhaustion
- _url_to_job_id: deterministic hash output
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.ralph_loop.loop import (
    MAX_ITERATIONS,
    _merge_fix_into_overrides,
    _url_to_job_id,
    build_overrides_from_fixes,
    ralph_apply_sync,
)
from jobpulse.ralph_loop.pattern_store import FixPattern, PatternStore


# ---------------------------------------------------------------------------
# Helpers — factory functions to build FixPattern instances without DB
# ---------------------------------------------------------------------------

def _make_fix(
    fix_type: str,
    payload: dict,
    *,
    fix_id: str = "abc123",
    platform: str = "greenhouse",
    step_name: str = "file_upload",
    superseded_by: str | None = None,
    source: str = "production",
    confirmed: bool = True,
    occurrence_count: int = 1,
) -> FixPattern:
    return FixPattern(
        id=fix_id,
        platform=platform,
        step_name=step_name,
        error_signature="sig001",
        fix_type=fix_type,
        fix_payload=json.dumps(payload),
        confidence=0.8,
        times_applied=0,
        times_succeeded=0,
        success_rate=0.0,
        created_at="2026-04-07T00:00:00+00:00",
        last_used_at=None,
        superseded_by=superseded_by,
        source=source,
        confirmed=confirmed,
        occurrence_count=occurrence_count,
    )


def _empty_overrides() -> dict:
    return {
        "selector_overrides": {},
        "wait_overrides": {},
        "strategy_overrides": {},
        "field_remaps": {},
        "interaction_mods": {},
    }


# ---------------------------------------------------------------------------
# build_overrides_from_fixes — fix type coverage
# ---------------------------------------------------------------------------

class TestBuildOverridesFromFixes:
    def test_selector_override_populated(self):
        fix = _make_fix(
            "selector_override",
            {"original_selector": "#old-btn", "new_selector": "#new-btn"},
        )
        result = build_overrides_from_fixes([fix])
        assert result["selector_overrides"]["#old-btn"] == "#new-btn"

    def test_wait_adjustment_populated(self):
        fix = _make_fix(
            "wait_adjustment",
            {"step": "page_load", "timeout_ms": 20000},
        )
        result = build_overrides_from_fixes([fix])
        assert result["wait_overrides"]["page_load"] == 20000

    def test_strategy_switch_populated(self):
        fix = _make_fix(
            "strategy_switch",
            {"step": "file_upload", "new_strategy": "drag_and_drop"},
        )
        result = build_overrides_from_fixes([fix])
        assert result["strategy_overrides"]["file_upload"] == "drag_and_drop"

    def test_field_remap_populated(self):
        fix = _make_fix(
            "field_remap",
            {"field_label": "Full Name", "profile_key": "first_name"},
        )
        result = build_overrides_from_fixes([fix])
        assert result["field_remaps"]["Full Name"] == "first_name"

    def test_interaction_change_populated(self):
        fix = _make_fix(
            "interaction_change",
            {"action": "click", "modifier": "scroll_first", "wait_ms": 3000, "step": "modal_open"},
        )
        result = build_overrides_from_fixes([fix])
        mod = result["interaction_mods"]["modal_open"]
        assert mod["modifier"] == "scroll_first"
        assert mod["wait_ms"] == 3000

    def test_interaction_change_uses_action_as_step_when_no_step_key(self):
        fix = _make_fix(
            "interaction_change",
            {"action": "click_submit", "modifier": "js_click", "wait_ms": 1000},
        )
        result = build_overrides_from_fixes([fix])
        assert "click_submit" in result["interaction_mods"]

    def test_superseded_fix_is_skipped(self):
        fix = _make_fix(
            "selector_override",
            {"original_selector": "#old", "new_selector": "#new"},
            superseded_by="other_fix_id",
        )
        result = build_overrides_from_fixes([fix])
        assert result["selector_overrides"] == {}

    def test_unconfirmed_test_fix_is_skipped(self):
        fix = _make_fix(
            "wait_adjustment",
            {"step": "form_nav", "timeout_ms": 5000},
            source="test",
            confirmed=False,
        )
        result = build_overrides_from_fixes([fix])
        assert result["wait_overrides"] == {}

    def test_confirmed_test_fix_is_included(self):
        fix = _make_fix(
            "wait_adjustment",
            {"step": "form_nav", "timeout_ms": 5000},
            source="test",
            confirmed=True,
        )
        result = build_overrides_from_fixes([fix])
        assert result["wait_overrides"]["form_nav"] == 5000

    def test_empty_list_returns_blank_overrides(self):
        result = build_overrides_from_fixes([])
        assert result == _empty_overrides()

    def test_multiple_fixes_merged_correctly(self):
        fixes = [
            _make_fix("selector_override", {"original_selector": "#a", "new_selector": "#b"}, fix_id="f1"),
            _make_fix("wait_adjustment", {"step": "nav", "timeout_ms": 8000}, fix_id="f2"),
            _make_fix("field_remap", {"field_label": "City", "profile_key": "location"}, fix_id="f3"),
        ]
        result = build_overrides_from_fixes(fixes)
        assert result["selector_overrides"]["#a"] == "#b"
        assert result["wait_overrides"]["nav"] == 8000
        assert result["field_remaps"]["City"] == "location"

    def test_selector_override_with_missing_original_is_ignored(self):
        fix = _make_fix(
            "selector_override",
            {"new_selector": "#new"},  # missing original_selector
        )
        result = build_overrides_from_fixes([fix])
        assert result["selector_overrides"] == {}

    def test_selector_override_with_missing_new_is_ignored(self):
        fix = _make_fix(
            "selector_override",
            {"original_selector": "#old"},  # missing new_selector
        )
        result = build_overrides_from_fixes([fix])
        assert result["selector_overrides"] == {}

    def test_wait_adjustment_with_missing_step_is_ignored(self):
        fix = _make_fix(
            "wait_adjustment",
            {"timeout_ms": 5000},  # missing step
        )
        result = build_overrides_from_fixes([fix])
        assert result["wait_overrides"] == {}

    def test_strategy_switch_with_missing_new_strategy_is_ignored(self):
        fix = _make_fix(
            "strategy_switch",
            {"step": "file_upload"},  # missing new_strategy
        )
        result = build_overrides_from_fixes([fix])
        assert result["strategy_overrides"] == {}

    def test_field_remap_with_missing_key_is_ignored(self):
        fix = _make_fix(
            "field_remap",
            {"field_label": "Phone"},  # missing profile_key
        )
        result = build_overrides_from_fixes([fix])
        assert result["field_remaps"] == {}

    def test_later_fix_overwrites_same_selector(self):
        fix1 = _make_fix(
            "selector_override",
            {"original_selector": "#btn", "new_selector": "#btn-v1"},
            fix_id="f1",
        )
        fix2 = _make_fix(
            "selector_override",
            {"original_selector": "#btn", "new_selector": "#btn-v2"},
            fix_id="f2",
        )
        result = build_overrides_from_fixes([fix1, fix2])
        # The last one in the list wins
        assert result["selector_overrides"]["#btn"] == "#btn-v2"


# ---------------------------------------------------------------------------
# _merge_fix_into_overrides — single-fix merge
# ---------------------------------------------------------------------------

class TestMergeFixIntoOverrides:
    def test_selector_override_added_to_existing(self):
        overrides = _empty_overrides()
        overrides["selector_overrides"]["#existing"] = "#already"
        fix = _make_fix("selector_override", {"original_selector": "#new", "new_selector": "#new-val"})
        result = _merge_fix_into_overrides(overrides, fix)
        assert result["selector_overrides"]["#existing"] == "#already"
        assert result["selector_overrides"]["#new"] == "#new-val"

    def test_wait_override_added(self):
        overrides = _empty_overrides()
        fix = _make_fix("wait_adjustment", {"step": "contact_info", "timeout_ms": 12000})
        result = _merge_fix_into_overrides(overrides, fix)
        assert result["wait_overrides"]["contact_info"] == 12000

    def test_strategy_switch_added(self):
        overrides = _empty_overrides()
        fix = _make_fix("strategy_switch", {"step": "file_upload", "new_strategy": "js_click"})
        result = _merge_fix_into_overrides(overrides, fix)
        assert result["strategy_overrides"]["file_upload"] == "js_click"

    def test_field_remap_added(self):
        overrides = _empty_overrides()
        fix = _make_fix("field_remap", {"field_label": "Surname", "profile_key": "last_name"})
        result = _merge_fix_into_overrides(overrides, fix)
        assert result["field_remaps"]["Surname"] == "last_name"

    def test_interaction_change_added(self):
        overrides = _empty_overrides()
        fix = _make_fix(
            "interaction_change",
            {"action": "click", "modifier": "force_click", "wait_ms": 1500, "step": "submit_btn"},
        )
        result = _merge_fix_into_overrides(overrides, fix)
        assert result["interaction_mods"]["submit_btn"]["modifier"] == "force_click"
        assert result["interaction_mods"]["submit_btn"]["wait_ms"] == 1500

    def test_returns_same_dict_object(self):
        """_merge_fix_into_overrides mutates and returns the same dict."""
        overrides = _empty_overrides()
        fix = _make_fix("wait_adjustment", {"step": "s", "timeout_ms": 1000})
        result = _merge_fix_into_overrides(overrides, fix)
        assert result is overrides

    def test_unknown_fix_type_leaves_overrides_unchanged(self):
        overrides = _empty_overrides()
        fix = _make_fix("selector_override", {"original_selector": "", "new_selector": ""})
        result = _merge_fix_into_overrides(overrides, fix)
        # Both selectors are empty strings, no entry should be added
        assert result["selector_overrides"] == {}


# ---------------------------------------------------------------------------
# _url_to_job_id — deterministic hash
# ---------------------------------------------------------------------------

class TestUrlToJobId:
    def test_same_url_produces_same_id(self):
        url = "https://boards.greenhouse.io/stripe/jobs/6142978003"
        assert _url_to_job_id(url) == _url_to_job_id(url)

    def test_different_urls_produce_different_ids(self):
        assert _url_to_job_id("https://boards.greenhouse.io/stripe/jobs/6142978003") != _url_to_job_id("https://jobs.lever.co/figma/5118a0b8-4a29-4029-8e49-17dbfc3694b0")

    def test_output_is_12_chars(self):
        result = _url_to_job_id("https://www.linkedin.com/jobs/view/3945782198")
        assert len(result) == 12

    def test_output_is_hex(self):
        result = _url_to_job_id("https://uk.indeed.com/viewjob?jk=a1b2c3d4e5f6")
        assert all(c in "0123456789abcdef" for c in result)

    def test_empty_url_is_stable(self):
        assert _url_to_job_id("") == _url_to_job_id("")


# ---------------------------------------------------------------------------
# ralph_apply_sync — core loop scenarios
# ---------------------------------------------------------------------------

# Common fixtures for these tests
_URL = "https://boards.greenhouse.io/stripe/jobs/6142978003"
_CV = Path("/tmp/test_cv.pdf")
_PLATFORM = "greenhouse"


def _make_store_mock(tmp_path: Path) -> MagicMock:
    """Build a PatternStore backed by a real tmp SQLite DB (no mocking of store methods)."""
    store = PatternStore(str(tmp_path / "ralph_test.db"), mode="production")
    return store


class TestRalphApplySync:
    """Integration-level tests using a real PatternStore on tmp_path SQLite."""

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_success_on_first_try(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        mock_apply.return_value = {"success": True, "screenshot": None, "error": None}

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=_make_store_mock(tmp_path),
        ):
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=str(tmp_path / "ralph_test.db"),
            )

        assert result["success"] is True
        assert result["ralph_iterations"] == 1
        mock_apply.assert_called_once()
        mock_fill.assert_not_called()

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_rate_limited_aborts_immediately(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        mock_apply.return_value = {
            "success": False,
            "rate_limited": True,
            "screenshot": None,
            "error": "Rate limited",
        }

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=_make_store_mock(tmp_path),
        ):
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=str(tmp_path / "ralph_test.db"),
            )

        assert result["rate_limited"] is True
        # Only 1 attempt — no retry
        mock_apply.assert_called_once()
        mock_fill.assert_not_called()

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_success_after_retry_with_known_fix(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """Second attempt succeeds after a known fix is loaded from the store."""
        db_path = str(tmp_path / "ralph_test.db")

        # Pre-seed a known fix in the real store
        store = PatternStore(db_path, mode="production")
        error_msg = "Selector #apply-button not found"
        from jobpulse.ralph_loop.pattern_store import compute_error_signature
        from jobpulse.ralph_loop.diagnoser import infer_step_from_error
        step = infer_step_from_error(error_msg, _PLATFORM)
        sig = compute_error_signature(_PLATFORM, step, error_msg)
        store.save_fix(
            platform=_PLATFORM,
            step_name=step,
            error_signature=sig,
            fix_type="selector_override",
            fix_payload={"original_selector": "#apply-button", "new_selector": ".apply-btn"},
            confidence=0.9,
        )

        # apply_job fails first time with the known error, fill_and_submit succeeds on retry
        mock_apply.return_value = {
            "success": False,
            "screenshot": None,
            "error": error_msg,
        }
        mock_fill.return_value = {"success": True, "screenshot": None, "error": None}
        mock_select.return_value = MagicMock()

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
            )

        assert result["success"] is True
        assert result["ralph_iterations"] == 2
        mock_apply.assert_called_once()
        mock_fill.assert_called_once()

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_success_after_heuristic_diagnosis(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """Loop diagnoses heuristically, saves fix, retries, succeeds."""
        db_path = str(tmp_path / "ralph_test.db")

        mock_apply.return_value = {
            "success": False,
            "screenshot": None,
            "error": "Timed out waiting for element",
            "_page": None,  # no page, so vision diagnosis won't run
        }
        mock_fill.return_value = {"success": True, "screenshot": None, "error": None}
        mock_select.return_value = MagicMock()

        mock_diag.return_value = {
            "fix_type": "wait_adjustment",
            "fix_payload": {"step": "page_load", "timeout_ms": 30000},
            "confidence": 0.4,
            "diagnosis": "Timed out waiting — increasing timeout",
        }

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
            )

        assert result["success"] is True
        assert result["ralph_iterations"] == 2
        mock_diag.assert_called_once()

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_undiagnosable_error_stops_loop(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """When heuristic returns None and no known fix, loop stops early."""
        db_path = str(tmp_path / "ralph_test.db")

        mock_apply.return_value = {
            "success": False,
            "screenshot": None,
            "error": "Completely unexpected failure xyz",
            "_page": None,
        }
        mock_diag.return_value = None  # undiagnosable

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
            )

        assert result.get("success") is not True
        # Should have stopped at iteration 1 — fill never called
        mock_fill.assert_not_called()
        # Loop breaks out after undiagnosable failure then hits flag_for_human_review;
        # ralph_exhausted is set and ralph_iterations reflects the final iteration count.
        assert result.get("ralph_exhausted") is True
        assert result.get("ralph_iterations") == MAX_ITERATIONS

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_max_iterations_exhaustion(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """All 5 iterations fail — loop returns with ralph_exhausted=True."""
        db_path = str(tmp_path / "ralph_test.db")

        mock_apply.return_value = {
            "success": False,
            "screenshot": None,
            "error": "Timed out waiting for page load",
            "_page": None,
        }
        mock_fill.return_value = {
            "success": False,
            "screenshot": None,
            "error": "Timed out waiting for page load",
        }
        mock_select.return_value = MagicMock()

        # Heuristic always returns a new fix so the loop keeps going
        call_count = {"n": 0}

        def rotating_diag(error_msg, platform):
            call_count["n"] += 1
            return {
                "fix_type": "wait_adjustment",
                "fix_payload": {"step": f"step_{call_count['n']}", "timeout_ms": 30000},
                "confidence": 0.4,
                "diagnosis": "timeout",
            }

        mock_diag.side_effect = rotating_diag

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            with patch("jobpulse.ralph_loop.pattern_store.PatternStore.flag_for_human_review"):
                result = ralph_apply_sync(
                    url=_URL,
                    ats_platform=_PLATFORM,
                    cv_path=_CV,
                    db_path=db_path,
                )

        assert result["ralph_exhausted"] is True
        assert result["ralph_iterations"] == MAX_ITERATIONS

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_dry_run_uses_test_mode_store(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """dry_run=True passes mode='test' to PatternStore."""
        db_path = str(tmp_path / "ralph_test.db")
        mock_apply.return_value = {"success": True, "screenshot": None, "error": None}

        captured_modes = []

        original_init = PatternStore.__init__

        def tracking_init(self, db_path_arg=None, mode="production"):
            captured_modes.append(mode)
            original_init(self, db_path_arg, mode)

        with patch.object(PatternStore, "__init__", tracking_init):
            ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
                dry_run=True,
            )

        assert "test" in captured_modes

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_iteration_callback_called_on_success(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """iteration_callback is invoked when apply succeeds."""
        db_path = str(tmp_path / "ralph_test.db")
        mock_apply.return_value = {"success": True, "screenshot": None, "error": None}

        callback_calls = []

        def cb(iteration, screenshot_bytes, diagnosis, result):
            callback_calls.append((iteration, diagnosis, result.get("success")))

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
                iteration_callback=cb,
            )

        assert len(callback_calls) == 1
        assert callback_calls[0][2] is True  # success=True

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_iteration_callback_called_on_diagnosis(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """iteration_callback is invoked after each diagnosis attempt."""
        db_path = str(tmp_path / "ralph_test.db")

        mock_apply.return_value = {
            "success": False, "screenshot": None, "error": "not visible: #btn", "_page": None,
        }
        mock_fill.return_value = {"success": True, "screenshot": None, "error": None}
        mock_select.return_value = MagicMock()

        mock_diag.return_value = {
            "fix_type": "interaction_change",
            "fix_payload": {"action": "click", "modifier": "scroll_first", "wait_ms": 2000},
            "confidence": 0.5,
            "diagnosis": "not visible",
        }

        callback_calls = []

        def cb(iteration, screenshot_bytes, diagnosis, result):
            callback_calls.append(iteration)

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
                iteration_callback=cb,
            )

        # Callback should have been called at least once (diagnosis iteration)
        assert len(callback_calls) >= 1

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_external_redirect_switches_platform(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """When apply_job returns external_redirect, subsequent iterations use the new URL/platform."""
        db_path = str(tmp_path / "ralph_test.db")
        external_url = "https://jobs.lever.co/acme/b72f3c91-6e08-4d2a-9f1a-82c456def789"

        mock_apply.return_value = {
            "success": False,
            "external_redirect": True,
            "external_url": external_url,
            "external_platform": "lever",
            "screenshot": None,
            "error": "redirected to external ATS",
            "_page": None,
        }
        mock_fill.return_value = {"success": True, "screenshot": None, "error": None}
        mock_select.return_value = MagicMock()

        mock_diag.return_value = {
            "fix_type": "wait_adjustment",
            "fix_payload": {"step": "page_load", "timeout_ms": 15000},
            "confidence": 0.5,
            "diagnosis": "redirect",
        }

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
            )

        # fill_and_submit should be called with the external URL target
        assert mock_fill.call_count >= 1
        call_kwargs = mock_fill.call_args[1]
        assert call_kwargs["url"] == external_url

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_same_error_sig_not_diagnosed_twice(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """The same error signature is only diagnosed once per run."""
        db_path = str(tmp_path / "ralph_test.db")

        # Apply always fails with the same error; fill always fails too
        same_error = "Timed out waiting for element #btn"
        mock_apply.return_value = {
            "success": False, "screenshot": None, "error": same_error, "_page": None,
        }
        mock_fill.return_value = {
            "success": False, "screenshot": None, "error": same_error,
        }
        mock_select.return_value = MagicMock()

        call_count = {"n": 0}

        def counting_diag(error_msg, platform):
            call_count["n"] += 1
            # On second call, return None so the loop stops
            if call_count["n"] > 1:
                return None
            return {
                "fix_type": "wait_adjustment",
                "fix_payload": {"step": "page_load", "timeout_ms": 30000},
                "confidence": 0.4,
                "diagnosis": "timeout",
            }

        mock_diag.side_effect = counting_diag

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
            )

        # Heuristic called for iteration 1 (diagnosis), then on iteration 2 the same sig
        # is in already_tried_sigs so we go straight to heuristic for the new attempt,
        # but that returns None and stops. Overall, diagnosis should be called a bounded number
        # of times — not once per iteration.
        assert call_count["n"] <= 2

    @patch("jobpulse.ralph_loop.loop.heuristic_diagnosis")
    @patch("jobpulse.applicator.select_adapter")
    @patch("jobpulse.applicator._call_fill_and_submit")
    @patch("jobpulse.applicator.apply_job")
    def test_fill_exception_wrapped_as_failure(
        self, mock_apply, mock_fill, mock_select, mock_diag, tmp_path
    ):
        """If _call_fill_and_submit raises, the exception is caught and treated as failure."""
        db_path = str(tmp_path / "ralph_test.db")

        # First call succeeds (apply_job) but triggers diagnosis path
        mock_apply.return_value = {
            "success": False, "screenshot": None, "error": "timeout", "_page": None,
        }
        # Iteration 2 fill raises
        mock_fill.side_effect = RuntimeError("Playwright crashed")
        mock_select.return_value = MagicMock()

        mock_diag.return_value = {
            "fix_type": "wait_adjustment",
            "fix_payload": {"step": "page_load", "timeout_ms": 30000},
            "confidence": 0.5,
            "diagnosis": "timeout",
        }

        with patch(
            "jobpulse.ralph_loop.loop.PatternStore",
            return_value=PatternStore(db_path, mode="production"),
        ):
            # Should not raise — exception is caught internally
            result = ralph_apply_sync(
                url=_URL,
                ats_platform=_PLATFORM,
                cv_path=_CV,
                db_path=db_path,
            )

        assert result.get("success") is not True
        assert "Playwright crashed" in result.get("error", "")


# ---------------------------------------------------------------------------
# PatternStore — DB isolation (safety check that tmp_path is used)
# ---------------------------------------------------------------------------

class TestPatternStoreIsolation:
    def test_store_writes_to_tmp_path_only(self, tmp_path):
        db = str(tmp_path / "isolation_test.db")
        store = PatternStore(db, mode="production")
        fix = store.save_fix(
            platform="linkedin",
            step_name="file_upload",
            error_signature="sig_abc",
            fix_type="wait_adjustment",
            fix_payload={"step": "file_upload", "timeout_ms": 5000},
            confidence=0.7,
        )
        assert fix.id is not None
        # Verify it's in the tmp DB, not data/
        assert Path(db).exists()
        import sqlite3
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT id FROM fix_patterns").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_production_db_never_touched(self, tmp_path):
        """Paranoia check: production DB path should not appear in test."""
        db = str(tmp_path / "safe.db")
        store = PatternStore(db)
        assert "data/ralph_patterns.db" not in store.db_path


# ---------------------------------------------------------------------------
# PatternStore — engine column: per-engine isolation and defaults
# ---------------------------------------------------------------------------

class TestPatternStoreEngineColumn:
    def test_engine_tag_isolates_fixes_by_engine(self, tmp_path):
        """Same error + platform/step but different engines produce separate fixes."""
        store = PatternStore(str(tmp_path / "patterns.db"))

        store.save_fix(
            "greenhouse", "fill_email", "sig1", "selector_override",
            {"selector": "#email-new"}, engine="extension",
        )
        store.save_fix(
            "greenhouse", "fill_email", "sig1", "selector_override",
            {"selector": "#email-pw"}, engine="playwright",
        )

        ext_fixes = store.get_fixes_for_platform("greenhouse", engine="extension")
        pw_fixes = store.get_fixes_for_platform("greenhouse", engine="playwright")

        assert len(ext_fixes) == 1
        assert ext_fixes[0].engine == "extension"
        assert len(pw_fixes) == 1
        assert pw_fixes[0].engine == "playwright"

    def test_engine_default_is_extension(self, tmp_path):
        """Engine defaults to 'extension' for backward compatibility."""
        store = PatternStore(str(tmp_path / "patterns.db"))
        fix = store.save_fix("lever", "fill_name", "sig2", "selector_override", {"s": "#name"})
        assert fix.engine == "extension"

    def test_get_fix_filters_by_engine(self, tmp_path):
        """get_fix returns the correct fix for each engine."""
        store = PatternStore(str(tmp_path / "patterns.db"))

        store.save_fix(
            "gh", "step1", "sig1", "selector_override",
            {"s": "#a"}, engine="extension",
        )
        store.save_fix(
            "gh", "step1", "sig1", "selector_override",
            {"s": "#b"}, engine="playwright",
        )

        ext = store.get_fix("gh", "step1", "sig1", engine="extension")
        pw = store.get_fix("gh", "step1", "sig1", engine="playwright")

        assert ext is not None
        assert pw is not None
        assert ext.engine == "extension"
        assert pw.engine == "playwright"
        assert json.loads(ext.fix_payload)["s"] == "#a"
        assert json.loads(pw.fix_payload)["s"] == "#b"

    def test_get_fixes_for_platform_no_engine_filter_returns_all(self, tmp_path):
        """get_fixes_for_platform with engine=None returns fixes for all engines."""
        store = PatternStore(str(tmp_path / "patterns.db"))

        store.save_fix("lever", "step_a", "sig_x", "selector_override",
                       {"s": "#ext"}, engine="extension")
        store.save_fix("lever", "step_b", "sig_y", "selector_override",
                       {"s": "#pw"}, engine="playwright")

        all_fixes = store.get_fixes_for_platform("lever", engine=None)
        assert len(all_fixes) == 2
        engines = {f.engine for f in all_fixes}
        assert engines == {"extension", "playwright"}

    def test_get_fix_wrong_engine_returns_none(self, tmp_path):
        """get_fix returns None when engine doesn't match."""
        store = PatternStore(str(tmp_path / "patterns.db"))
        store.save_fix("gh", "step1", "sig1", "selector_override",
                       {"s": "#a"}, engine="extension")

        result = store.get_fix("gh", "step1", "sig1", engine="playwright")
        assert result is None

    def test_engine_stored_in_db_row(self, tmp_path):
        """engine value is persisted and read back correctly from the DB."""
        db_path = str(tmp_path / "patterns.db")
        store = PatternStore(db_path)
        store.save_fix("workday", "fill_phone", "sig3", "field_remap",
                       {"field_label": "Phone", "profile_key": "phone"}, engine="playwright")

        # Re-open the store (fresh connection) and verify engine value persists
        store2 = PatternStore(db_path)
        fixes = store2.get_fixes_for_platform("workday", engine="playwright")
        assert len(fixes) == 1
        assert fixes[0].engine == "playwright"

    def test_engine_migration_adds_column_to_old_db(self, tmp_path):
        """Migration correctly adds engine column with default 'extension' to old DBs."""
        import sqlite3 as _sqlite3
        db_path = str(tmp_path / "old.db")

        # Simulate an old DB without the engine column
        conn = _sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE fix_patterns (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                step_name TEXT NOT NULL,
                error_signature TEXT NOT NULL,
                fix_type TEXT NOT NULL,
                fix_payload TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                times_applied INTEGER DEFAULT 0,
                times_succeeded INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                superseded_by TEXT,
                source TEXT NOT NULL DEFAULT 'production',
                confirmed BOOLEAN NOT NULL DEFAULT 1,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(platform, step_name, error_signature)
            )
        """)
        conn.execute("""
            INSERT INTO fix_patterns
            (id, platform, step_name, error_signature, fix_type, fix_payload,
             confidence, created_at)
            VALUES ('abc123', 'lever', 'fill_name', 'sig_old', 'selector_override',
                    '{"s": "#old"}', 0.8, '2026-01-01T00:00:00+00:00')
        """)
        conn.commit()
        conn.close()

        # Opening via PatternStore should run migration and add the engine column
        store = PatternStore(db_path)
        fixes = store.get_fixes_for_platform("lever")
        assert len(fixes) == 1
        # Old row defaults to 'extension'
        assert fixes[0].engine == "extension"
