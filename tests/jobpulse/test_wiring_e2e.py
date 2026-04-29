"""End-to-end wiring test: post_apply_hook -> all downstream systems fire.

Proves that post_apply_hook() triggers real DB writes to:
1. form_experience.db (FormExperienceDB)
2. optimization.db (OptimizationEngine learning_actions table)
3. navigation_learning.db (NavigationLearner sequences table)

External services (Drive, Notion, strategy_reflector) are patched out.
All DB writes verified via direct SQLite queries on tmp_path databases.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from shared.optimization._engine import OptimizationEngine


@pytest.fixture
def wiring_dbs(tmp_path):
    """Create all DB paths that post_apply_hook touches, return dict."""
    return {
        "form_experience": str(tmp_path / "form_experience.db"),
        "optimization": str(tmp_path / "optimization.db"),
        "navigation": str(tmp_path / "navigation_learning.db"),
    }


def _make_result(success=True):
    """Minimal result dict mimicking adapter.fill_and_submit() return."""
    return {
        "success": success,
        "pages_filled": 2,
        "field_types": ["text", "select", "file"],
        "screening_questions": ["Salary expectation: 35000"],
        "time_seconds": 12.5,
        "agent_fill_stats": {
            "fields_attempted": 5,
            "fields_filled": 4,
            "fields_failed": 1,
            "failed_labels": ["Cover Letter"],
            "llm_fallback_count": 1,
        },
        "navigation_steps": [
            {"action": "click", "selector": "#apply-btn"},
            {"action": "fill", "selector": "#name", "value": "test"},
        ],
    }


def _make_job_context(job_id="test_job_001"):
    """Minimal job_context dict matching post_apply_hook expectations."""
    return {
        "job_id": job_id,
        "company": "TestCorp",
        "title": "Data Analyst",
        "url": "https://boards.greenhouse.io/testcorp/jobs/123",
        "platform": "greenhouse",
        "ats_platform": "greenhouse",
        "notion_page_id": None,
        "cv_path": None,
        "cover_letter_path": None,
        "match_tier": "M1",
        "ats_score": 85,
        "matched_projects": ["project_a", "project_b"],
    }


def _patch_externals():
    """Return a list of context managers patching out Drive, Notion, JobDB, strategy_reflector."""
    return [
        patch("jobpulse.post_apply_hook.upload_cv", return_value=None),
        patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None),
        patch("jobpulse.post_apply_hook.find_application_page", return_value=None),
        patch("jobpulse.post_apply_hook.update_application_page"),
        patch("jobpulse.post_apply_hook.JobDB", return_value=MagicMock()),
        patch("jobpulse.strategy_reflector.reflect_on_application", return_value=MagicMock(
            heuristics="[]", fields_total=5, fields_pattern=3,
            fields_llm=1, fields_corrected=1,
        )),
    ]


class TestPostApplyHookWiring:
    """Verify post_apply_hook writes to all downstream databases."""

    def test_writes_form_experience(self, wiring_dbs):
        """post_apply_hook must write at least 1 row to form_experience table."""
        from jobpulse.post_apply_hook import post_apply_hook

        opt_engine = OptimizationEngine(db_path=wiring_dbs["optimization"])

        patches = _patch_externals() + [
            patch("shared.optimization.get_optimization_engine", return_value=opt_engine),
            patch("shared.optimization._engine.get_optimization_engine", return_value=opt_engine),
            patch("shared.optimization._engine._shared_engine", opt_engine),
        ]

        for p in patches:
            p.start()
        try:
            post_apply_hook(
                result=_make_result(),
                job_context=_make_job_context(),
                form_exp_db_path=wiring_dbs["form_experience"],
            )
        finally:
            for p in patches:
                p.stop()

        conn = sqlite3.connect(wiring_dbs["form_experience"])
        rows = conn.execute("SELECT COUNT(*) FROM form_experience").fetchone()[0]
        conn.close()
        assert rows >= 1, "post_apply_hook must write at least 1 row to form_experience"

    def test_emits_optimization_learning_action(self, wiring_dbs):
        """post_apply_hook must create at least 1 learning_action (before/after pair)."""
        from jobpulse.post_apply_hook import post_apply_hook

        opt_engine = OptimizationEngine(db_path=wiring_dbs["optimization"])

        patches = _patch_externals() + [
            patch("shared.optimization.get_optimization_engine", return_value=opt_engine),
            patch("shared.optimization._engine.get_optimization_engine", return_value=opt_engine),
            patch("shared.optimization._engine._shared_engine", opt_engine),
        ]

        for p in patches:
            p.start()
        try:
            post_apply_hook(
                result=_make_result(),
                job_context=_make_job_context(),
                form_exp_db_path=wiring_dbs["form_experience"],
            )
        finally:
            for p in patches:
                p.stop()

        conn = sqlite3.connect(wiring_dbs["optimization"])
        conn.row_factory = sqlite3.Row
        actions = conn.execute("SELECT COUNT(*) as cnt FROM learning_actions").fetchone()["cnt"]
        conn.close()
        assert actions >= 1, "post_apply_hook must create at least 1 learning_action"

    def test_records_navigation_sequence(self, wiring_dbs):
        """post_apply_hook must save at least 1 navigation sequence."""
        from jobpulse.post_apply_hook import post_apply_hook

        opt_engine = OptimizationEngine(db_path=wiring_dbs["optimization"])

        patches = _patch_externals() + [
            patch("shared.optimization.get_optimization_engine", return_value=opt_engine),
            patch("shared.optimization._engine.get_optimization_engine", return_value=opt_engine),
            patch("shared.optimization._engine._shared_engine", opt_engine),
            patch("jobpulse.navigation_learner._DEFAULT_DB", wiring_dbs["navigation"]),
        ]

        for p in patches:
            p.start()
        try:
            post_apply_hook(
                result=_make_result(),
                job_context=_make_job_context(),
                form_exp_db_path=wiring_dbs["form_experience"],
            )
        finally:
            for p in patches:
                p.stop()

        conn = sqlite3.connect(wiring_dbs["navigation"])
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT COUNT(*) as cnt FROM sequences").fetchone()["cnt"]
        conn.close()
        assert rows >= 1, "post_apply_hook must save at least 1 navigation sequence"
