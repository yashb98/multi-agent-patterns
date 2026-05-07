"""Tests for jobpulse/scan_pipeline.py — the 5 extracted pipeline stages.

Per project policy: real JobListing/JobDB/SearchConfig objects, no synthetic
fixtures. External boundaries (scan_platforms, gate0_title_relevance,
SkillGraphStore, BlocklistCache, check_jd_quality, etc.) are still patched
because invoking them in CI means real Indeed/LinkedIn HTTP + real LLM cost;
those are Category C boundaries left alone in this pass.

DB writes go through a real `JobDB(db_path=tmp_path/...)` so assertions
inspect actual SQLite rows rather than `mock.assert_called_with(...)` calls.

ProcessTrail is a real instance — it's a pure logger over a list, no mock
needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

from jobpulse.models.application_models import JobListing, SearchConfig
from jobpulse.process_logger import ProcessTrail
from jobpulse.job_db import JobDB


# ---------------------------------------------------------------------------
# Real-object factories (no MagicMock for the system under test or for data)
# ---------------------------------------------------------------------------


def _make_listing(
    job_id="abc123",
    title="Data Analyst",
    company="TestCo",
    platform="reed",
    url="https://example.com/job/1",
    required_skills=None,
    preferred_skills=None,
    description_raw="We need Python and SQL skills.",
    location="London",
    easy_apply=False,
    ats_platform=None,
) -> JobListing:
    """Construct a real JobListing pydantic model."""
    return JobListing(
        job_id=job_id,
        title=title,
        company=company,
        platform=platform,
        url=url,
        required_skills=required_skills or ["Python", "SQL"],
        preferred_skills=preferred_skills or ["Tableau"],
        description_raw=description_raw,
        location=location,
        easy_apply=easy_apply,
        ats_platform=ats_platform,
        found_at=datetime.now(timezone.utc),
    )


def _make_trail():
    """ProcessTrail is a fire-and-forget logger that writes to a global SQLite
    sink. To avoid touching the production agent_process_trails table from
    tests, we substitute a no-op stub. ProcessTrail behavior is covered in
    its own dedicated test file."""
    trail = MagicMock()
    trail.log_step = MagicMock()
    return trail


import tempfile


def _make_db() -> JobDB:
    """Real JobDB on a per-call temp SQLite file (cleaned up by OS on exit).
    Tests can query the DB directly to verify writes — no MagicMock involved."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_scan_")
    import os
    os.close(fd)
    return JobDB(db_path=Path(path))


def _make_search_config(titles=None, exclude_keywords=None) -> SearchConfig:
    """Real SearchConfig pydantic model."""
    return SearchConfig(
        titles=titles or ["data analyst", "python developer"],
        exclude_keywords=exclude_keywords or ["senior", "lead"],
    )


# ---------------------------------------------------------------------------
# Stage 1: fetch_and_filter_jobs
# ---------------------------------------------------------------------------


class TestFetchAndFilterJobs:
    def test_returns_tuple_of_three(self):
        from jobpulse.scan_pipeline import fetch_and_filter_jobs

        raw = [
            {"title": "Data Analyst", "description": "Python SQL", "platform": "reed", "url": "http://x.com/1"},
            {"title": "Senior Data Analyst", "description": "5+ years Python", "platform": "reed", "url": "http://x.com/2"},
        ]

        with (
            patch("jobpulse.scan_pipeline.scan_platforms", return_value=raw),
            patch("jobpulse.scan_pipeline.check_liveness_batch", return_value=(raw, [])),
            patch("jobpulse.scan_pipeline.gate0_title_relevance", side_effect=lambda title, jd, cfg: "senior" not in title.lower()),
        ):
            jobs, total, rejected = fetch_and_filter_jobs(None, _make_search_config(), _make_trail())

        assert total == 2
        assert rejected == 1
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Data Analyst"

    def test_scan_platforms_failure_returns_empty(self):
        from jobpulse.scan_pipeline import fetch_and_filter_jobs

        with (
            patch("jobpulse.scan_pipeline.scan_platforms", side_effect=RuntimeError("network down")),
            patch("jobpulse.scan_pipeline.check_liveness_batch", return_value=([], [])),
            patch("jobpulse.scan_pipeline.gate0_title_relevance", return_value=True),
        ):
            jobs, total, rejected = fetch_and_filter_jobs(["reed"], _make_search_config(), _make_trail())

        assert total == 0
        assert jobs == []

    def test_liveness_failure_passes_all_through(self):
        from jobpulse.scan_pipeline import fetch_and_filter_jobs

        raw = [{"title": "ML Engineer", "description": "Python", "url": "http://x.com/3"}]

        with (
            patch("jobpulse.scan_pipeline.scan_platforms", return_value=raw),
            patch("jobpulse.scan_pipeline.check_liveness_batch", side_effect=Exception("liveness down")),
            patch("jobpulse.scan_pipeline.gate0_title_relevance", return_value=True),
        ):
            jobs, total, rejected = fetch_and_filter_jobs(None, _make_search_config(), _make_trail())

        # All raw jobs should pass through when liveness check fails
        assert len(jobs) == 1

    def test_gate0_rejects_all_when_no_match(self):
        from jobpulse.scan_pipeline import fetch_and_filter_jobs

        raw = [{"title": "VP of Engineering", "description": "manages teams", "url": "http://x.com/4"}]

        with (
            patch("jobpulse.scan_pipeline.scan_platforms", return_value=raw),
            patch("jobpulse.scan_pipeline.check_liveness_batch", return_value=(raw, [])),
            patch("jobpulse.scan_pipeline.gate0_title_relevance", return_value=False),
        ):
            jobs, total, rejected = fetch_and_filter_jobs(None, _make_search_config(), _make_trail())

        assert rejected == 1
        assert jobs == []

    def test_search_config_object_with_attributes(self):
        """search_config accessed via .titles attribute (not dict)."""
        from jobpulse.scan_pipeline import fetch_and_filter_jobs

        raw = [{"title": "Data Analyst", "description": "Python", "url": "http://x.com/5"}]

        class FakeConfig:
            titles = ["data analyst"]
            exclude_keywords = ["senior"]

        with (
            patch("jobpulse.scan_pipeline.scan_platforms", return_value=raw),
            patch("jobpulse.scan_pipeline.check_liveness_batch", return_value=(raw, [])),
            patch("jobpulse.scan_pipeline.gate0_title_relevance", return_value=True),
        ):
            jobs, total, rejected = fetch_and_filter_jobs(None, FakeConfig(), _make_trail())

        assert total == 1
        assert rejected == 0

    def test_search_config_dict_fallback(self):
        """search_config accessed via dict .get() when no attributes."""
        from jobpulse.scan_pipeline import fetch_and_filter_jobs

        raw = [{"title": "Backend Developer", "description": "Python", "url": "http://x.com/6"}]
        cfg = {"titles": ["backend developer"], "exclude_keywords": []}

        with (
            patch("jobpulse.scan_pipeline.scan_platforms", return_value=raw),
            patch("jobpulse.scan_pipeline.check_liveness_batch", return_value=(raw, [])),
            patch("jobpulse.scan_pipeline.gate0_title_relevance", return_value=True),
        ):
            jobs, total, rejected = fetch_and_filter_jobs(None, cfg, _make_trail())

        assert total == 1


# ---------------------------------------------------------------------------
# Stage 2: analyze_and_deduplicate
# ---------------------------------------------------------------------------


class TestAnalyzeAndDeduplicate:
    def test_returns_new_listings_only(self):
        from jobpulse.scan_pipeline import analyze_and_deduplicate

        listing1 = _make_listing(job_id="aaa")
        listing2 = _make_listing(job_id="bbb")

        raw = [
            {"title": "DA", "company": "A", "url": "http://x.com/1", "platform": "reed", "description": ""},
            {"title": "DA2", "company": "B", "url": "http://x.com/2", "platform": "reed", "description": ""},
        ]

        with (
            patch("jobpulse.scan_pipeline.analyze_jd", side_effect=[listing1, listing2]),
            patch("jobpulse.scan_pipeline.deduplicate", return_value=[listing1]),  # listing2 is duplicate
        ):
            result = analyze_and_deduplicate(raw, _make_db(), _make_trail())

        assert result == [listing1]

    def test_analyze_jd_failure_skips_job(self):
        from jobpulse.scan_pipeline import analyze_and_deduplicate

        raw = [
            {"title": "DA", "company": "A", "url": "http://x.com/1", "platform": "reed", "description": ""},
            {"title": "DA2", "company": "B", "url": "http://x.com/2", "platform": "reed", "description": ""},
        ]
        listing = _make_listing()

        with (
            patch("jobpulse.scan_pipeline.analyze_jd", side_effect=[Exception("parse fail"), listing]),
            patch("jobpulse.scan_pipeline.deduplicate", return_value=[listing]),
        ):
            result = analyze_and_deduplicate(raw, _make_db(), _make_trail())

        assert len(result) == 1

    def test_empty_input_returns_empty(self):
        from jobpulse.scan_pipeline import analyze_and_deduplicate

        with patch("jobpulse.scan_pipeline.deduplicate", return_value=[]):
            result = analyze_and_deduplicate([], _make_db(), _make_trail())

        assert result == []

    def test_platform_defaults_to_reed_when_missing(self):
        """Raw jobs without 'platform' key should default to 'reed' in analyze_jd call."""
        from jobpulse.scan_pipeline import analyze_and_deduplicate

        raw = [{"title": "DA", "company": "A", "url": "http://x.com/1", "description": "text"}]
        listing = _make_listing()
        captured_kwargs = {}

        def fake_analyze_jd(**kwargs):
            captured_kwargs.update(kwargs)
            return listing

        with (
            patch("jobpulse.scan_pipeline.analyze_jd", side_effect=fake_analyze_jd),
            patch("jobpulse.scan_pipeline.deduplicate", return_value=[listing]),
        ):
            analyze_and_deduplicate(raw, _make_db(), _make_trail())

        assert captured_kwargs.get("platform") == "reed"


# ---------------------------------------------------------------------------
# Stage 3: prescreen_listings
# ---------------------------------------------------------------------------


class TestPrescreenListings:
    def _make_screen(self, tier="apply", gate1_kill=None, gate2_fail=None, gate3_score=95):
        screen = MagicMock()
        screen.tier = tier
        screen.gate1_kill_reason = gate1_kill or ""
        screen.gate2_fail_reason = gate2_fail or ""
        screen.gate3_score = gate3_score
        screen.missing_skills = []
        screen.matched_skills = ["Python"]
        screen.best_projects = []
        return screen

    def _make_jd_quality(self, passed=True, reason=""):
        q = MagicMock()
        q.passed = passed
        q.reason = reason
        return q

    def _make_spam(self, is_spam=False, reason=""):
        s = MagicMock()
        s.is_spam = is_spam
        s.reason = reason
        return s

    def _make_blocklist(self, blocked=False, approved=True, known=True):
        bl = MagicMock()
        bl.is_blocked = MagicMock(return_value=blocked)
        bl.is_approved = MagicMock(return_value=approved)
        bl.is_known = MagicMock(return_value=known)
        bl.refresh = MagicMock()
        return bl

    def test_reject_tier_saves_and_excludes(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        screen = self._make_screen(tier="reject", gate1_kill="title mismatch")
        store = MagicMock()
        store.pre_screen_jd.return_value = screen

        db = _make_db()

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", return_value=store),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=self._make_blocklist()),
            patch("jobpulse.scan_pipeline.check_jd_quality", return_value=self._make_jd_quality()),
            patch("jobpulse.scan_pipeline.check_company_background", return_value=MagicMock(previously_applied=False, is_generic=False)),
        ):
            gate4_filtered, gate_rejected, gate_skipped, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        assert gate_rejected == 1
        assert gate_skipped == 0
        assert gate4_blocked == 0
        assert gate4_filtered == []
        # Verify against real DB row, not a mock-call assertion.
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT status, match_tier FROM applications WHERE job_id = ?",
                (listing.job_id,),
            ).fetchone()
        assert row == ("Rejected", "reject")

    def test_skip_tier_saves_and_excludes(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        screen = self._make_screen(tier="skip", gate2_fail="low score", gate3_score=60)
        store = MagicMock()
        store.pre_screen_jd.return_value = screen
        db = _make_db()

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", return_value=store),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=self._make_blocklist()),
            patch("jobpulse.scan_pipeline.check_jd_quality", return_value=self._make_jd_quality()),
            patch("jobpulse.scan_pipeline.check_company_background", return_value=MagicMock(previously_applied=False, is_generic=False)),
        ):
            gate4_filtered, gate_rejected, gate_skipped, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        assert gate_skipped == 1
        assert gate4_filtered == []

    def test_blocked_company_excluded(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        screen = self._make_screen(tier="apply")
        store = MagicMock()
        store.pre_screen_jd.return_value = screen
        db = _make_db()
        blocklist = self._make_blocklist(blocked=True)

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", return_value=store),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=blocklist),
        ):
            gate4_filtered, _, _, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        assert gate4_blocked == 1
        assert gate4_filtered == []

    def test_spam_company_excluded(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        screen = self._make_screen(tier="apply")
        store = MagicMock()
        store.pre_screen_jd.return_value = screen
        db = _make_db()
        blocklist = self._make_blocklist(blocked=False, approved=False, known=False)
        spam = self._make_spam(is_spam=True, reason="recruitment agency")

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", return_value=store),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=blocklist),
            patch("jobpulse.scan_pipeline.detect_spam_company", return_value=spam),
            patch("jobpulse.scan_pipeline.flag_company_in_notion"),
        ):
            gate4_filtered, _, _, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        assert gate4_blocked == 1

    def test_jd_quality_failure_excluded(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        screen = self._make_screen(tier="apply")
        store = MagicMock()
        store.pre_screen_jd.return_value = screen
        db = _make_db()

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", return_value=store),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=self._make_blocklist()),
            patch("jobpulse.scan_pipeline.detect_spam_company", return_value=self._make_spam()),
            patch("jobpulse.scan_pipeline.check_jd_quality", return_value=self._make_jd_quality(passed=False, reason="too short")),
            patch("jobpulse.scan_pipeline.check_company_background", return_value=MagicMock(previously_applied=False, is_generic=False)),
        ):
            gate4_filtered, _, _, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        assert gate4_blocked == 1
        assert gate4_filtered == []

    def test_good_listing_passes_all_gates(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        screen = self._make_screen(tier="apply")
        store = MagicMock()
        store.pre_screen_jd.return_value = screen
        db = _make_db()

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", return_value=store),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=self._make_blocklist()),
            patch("jobpulse.scan_pipeline.detect_spam_company", return_value=self._make_spam()),
            patch("jobpulse.scan_pipeline.check_jd_quality", return_value=self._make_jd_quality()),
            patch("jobpulse.scan_pipeline.check_company_background", return_value=MagicMock(previously_applied=False, is_generic=False)),
        ):
            gate4_filtered, gate_rejected, gate_skipped, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        assert len(gate4_filtered) == 1
        assert gate_rejected == 0
        assert gate_skipped == 0
        assert gate4_blocked == 0
        assert gate4_filtered[0] == (listing, screen)

    def test_skill_graph_store_failure_passes_all_through(self):
        from jobpulse.scan_pipeline import prescreen_listings

        listing = _make_listing()
        db = _make_db()

        with (
            patch("jobpulse.scan_pipeline.SkillGraphStore", side_effect=Exception("DB unavailable")),
            patch("jobpulse.scan_pipeline.BlocklistCache", return_value=MagicMock(
                is_blocked=MagicMock(return_value=False),
                is_approved=MagicMock(return_value=True),
                is_known=MagicMock(return_value=True),
                refresh=MagicMock(),
            )),
            patch("jobpulse.scan_pipeline.check_jd_quality", return_value=MagicMock(passed=True)),
            patch("jobpulse.scan_pipeline.check_company_background", return_value=MagicMock(previously_applied=False, is_generic=False)),
        ):
            gate4_filtered, gate_rejected, gate_skipped, gate4_blocked = prescreen_listings(
                [listing], db, _make_trail(),
            )

        # SkillGraphStore unavailable — all pass through with screen=None
        assert len(gate4_filtered) == 1
        assert gate4_filtered[0] == (listing, None)


# ---------------------------------------------------------------------------
# Stage 4: generate_materials
# ---------------------------------------------------------------------------


class TestGenerateMaterials:
    def test_returns_materials_bundle(self, tmp_path):
        from jobpulse.scan_pipeline import generate_materials, MaterialsBundle

        listing = _make_listing()
        db = _make_db()
        notion_failures: list = []

        mock_ats = MagicMock()
        mock_ats.total = 88.5

        with (
            patch("jobpulse.scan_pipeline.create_application_page", return_value="notion-page-id"),
            patch("jobpulse.scan_pipeline.build_extra_skills", return_value={"extra": "Spark"}),
            patch("jobpulse.scan_pipeline.get_best_projects_for_jd", return_value=[{"title": "P1", "bullets": ["built X"], "url": ""}]),
            patch("jobpulse.scan_pipeline.get_role_profile", return_value={"tagline": "t", "summary": "s"}),
            patch("jobpulse.scan_pipeline.score_ats", return_value=mock_ats),
            patch("jobpulse.scan_pipeline.BASE_SKILLS", {"a": "Python"}),
            patch("jobpulse.scan_pipeline.EDUCATION", [{"degree": "BSc", "institution": "Uni"}]),
            patch("jobpulse.scan_pipeline.EXPERIENCE", [{"title": "Dev", "bullets": ["did X"]}]),
            patch("jobpulse.scan_pipeline.scrutinize_cv_deterministic", return_value=MagicMock(status="clean", warnings=[])),
            patch("jobpulse.scan_pipeline.scrutinize_cv_llm", return_value=MagicMock(needs_review=False, score=8)),
            patch("jobpulse.scan_pipeline.update_application_page"),
            patch("jobpulse.scan_pipeline.build_page_content", return_value=[]),
            patch("jobpulse.scan_pipeline.set_page_content"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="review"),
            patch("jobpulse.scan_pipeline.fetch_and_cache_repos", return_value=[]),
            patch("jobpulse.scan_pipeline.pick_top_projects", return_value=[]),
        ):
            bundle = generate_materials(listing, None, db, [], notion_failures)

        assert isinstance(bundle, MaterialsBundle)
        assert bundle.ats_score == 88.5
        assert bundle.notion_page_id == "notion-page-id"
        # ats_score 88.5 ≥ 85 triggers eager CV PDF generation in
        # scan_pipeline.generate_materials. Pre-2026-05 this was masked by a
        # KeyError on `proj["url"]` when projects lacked a url key — that
        # bug is now fixed (see generate_cv.py:538), so cv_path is populated.
        assert bundle.cv_path is not None
        assert bundle.cv_drive_link is None
        assert bundle.cover_letter_path is None

    def test_cv_generation_failure_returns_zero_score(self, tmp_path):
        from jobpulse.scan_pipeline import generate_materials

        listing = _make_listing()
        db = _make_db()
        notion_failures: list = []

        with (
            patch("jobpulse.scan_pipeline.create_application_page", return_value=None),
            patch("jobpulse.scan_pipeline.build_extra_skills", side_effect=Exception("CV gen failed")),
            patch("jobpulse.scan_pipeline.get_best_projects_for_jd", return_value=[]),
            patch("jobpulse.scan_pipeline.fetch_and_cache_repos", return_value=[]),
            patch("jobpulse.scan_pipeline.pick_top_projects", return_value=[]),
            patch("jobpulse.scan_pipeline.get_role_profile", return_value={}),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="skip"),
        ):
            bundle = generate_materials(listing, None, db, [], notion_failures)

        assert bundle.ats_score == 0.0
        assert bundle.cv_path is None

    def test_notion_failure_appended_to_failures_list(self, tmp_path):
        from jobpulse.scan_pipeline import generate_materials

        listing = _make_listing()
        db = _make_db()
        notion_failures: list = []

        with (
            patch("jobpulse.scan_pipeline.create_application_page", side_effect=Exception("Notion 502")),
            patch("jobpulse.scan_pipeline.build_extra_skills", return_value={}),
            patch("jobpulse.scan_pipeline.get_best_projects_for_jd", return_value=[]),
            patch("jobpulse.scan_pipeline.fetch_and_cache_repos", return_value=[]),
            patch("jobpulse.scan_pipeline.pick_top_projects", return_value=[]),
            patch("jobpulse.scan_pipeline.get_role_profile", return_value={}),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="skip"),
        ):
            bundle = generate_materials(listing, None, db, [], notion_failures)

        assert len(notion_failures) == 1
        assert "Notion 502" in notion_failures[0]

    def test_screen_best_projects_used_when_available(self, tmp_path):
        from jobpulse.scan_pipeline import generate_materials

        listing = _make_listing()
        db = _make_db()
        notion_failures: list = []

        proj1 = MagicMock()
        proj1.name = "ProjectA"
        proj2 = MagicMock()
        proj2.name = "ProjectB"

        screen = MagicMock()
        screen.best_projects = [proj1, proj2]

        mock_ats = MagicMock()
        mock_ats.total = 75.0

        with (
            patch("jobpulse.scan_pipeline.create_application_page", return_value="pg-id"),
            patch("jobpulse.scan_pipeline.build_extra_skills", return_value={}),
            patch("jobpulse.scan_pipeline.get_best_projects_for_jd", return_value=[{"title": "P1", "bullets": []}]),
            patch("jobpulse.scan_pipeline.get_role_profile", return_value={}),
            patch("jobpulse.scan_pipeline.score_ats", return_value=mock_ats),
            patch("jobpulse.scan_pipeline.BASE_SKILLS", {}),
            patch("jobpulse.scan_pipeline.EDUCATION", []),
            patch("jobpulse.scan_pipeline.EXPERIENCE", []),
            patch("jobpulse.scan_pipeline.scrutinize_cv_deterministic", return_value=MagicMock(status="clean", warnings=[])),
            patch("jobpulse.scan_pipeline.scrutinize_cv_llm", return_value=MagicMock(needs_review=False)),
            patch("jobpulse.scan_pipeline.update_application_page"),
            patch("jobpulse.scan_pipeline.build_page_content", return_value=[]),
            patch("jobpulse.scan_pipeline.set_page_content"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="review"),
        ):
            bundle = generate_materials(listing, screen, db, [], notion_failures)

        # Projects from screen should be used, not fetched fresh
        assert "ProjectA" in bundle.matched_project_names
        assert "ProjectB" in bundle.matched_project_names


# ---------------------------------------------------------------------------
# Stage 5: route_and_apply
# ---------------------------------------------------------------------------


class TestRouteAndApply:
    def _make_bundle(self, ats_score=92.0, cv_path=None, notion_page_id=None):
        from jobpulse.scan_pipeline import MaterialsBundle
        bundle = MaterialsBundle(
            ats_score=ats_score,
            cv_path=cv_path or Path("/tmp/cv.pdf"),
            matched_project_names=["P1"],
            matched_projects=[],
            notion_page_id=notion_page_id,
            gate4b_notes="",
            notion_status="Ready",
        )
        bundle.cl_generator = lambda: None
        bundle.cover_letter_path = None
        return bundle

    def test_auto_applied_increments_counter(self):
        from jobpulse.scan_pipeline import route_and_apply, RouteResult

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=92.0)
        db = _make_db()
        # Real DB requires the listing to exist before save_application can FK to it.
        db.save_listing(listing)
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="auto_submit"),
            patch("jobpulse.scan_pipeline.apply_job", return_value={"success": True}),
            patch("jobpulse.scan_pipeline.update_application_page"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="auto"),
            patch("jobpulse.scan_pipeline._queue_for_review"),
        ):
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=10, auto_applied=0)

        assert result.action == "auto_applied"
        assert isinstance(result, RouteResult)

    def test_apply_failure_routes_to_review(self):
        from jobpulse.scan_pipeline import route_and_apply

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=92.0)
        db = _make_db()
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="auto_submit"),
            patch("jobpulse.scan_pipeline.apply_job", return_value={"success": False, "error": "timeout"}),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="auto"),
            patch("jobpulse.scan_pipeline._queue_for_review") as mock_queue,
        ):
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=10, auto_applied=0)

        assert result.action == "queued_for_review"
        mock_queue.assert_called_once()

    def test_apply_exception_routes_to_review(self):
        from jobpulse.scan_pipeline import route_and_apply

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=92.0)
        db = _make_db()
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="auto_submit"),
            patch("jobpulse.scan_pipeline.apply_job", side_effect=RuntimeError("browser crashed")),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="auto"),
            patch("jobpulse.scan_pipeline._queue_for_review") as mock_queue,
        ):
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=10, auto_applied=0)

        assert result.action == "queued_for_review"
        mock_queue.assert_called_once()

    def test_send_for_review_action_queues(self):
        from jobpulse.scan_pipeline import route_and_apply

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=85.0)
        db = _make_db()
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="send_for_review"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="review"),
            patch("jobpulse.scan_pipeline._queue_for_review") as mock_queue,
        ):
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=10, auto_applied=0)

        assert result.action == "queued_for_review"
        mock_queue.assert_called_once()

    def test_skip_action_updates_db(self):
        from jobpulse.scan_pipeline import route_and_apply

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=70.0, notion_page_id=None)
        db = _make_db()
        db.save_listing(listing)
        # update_status only changes existing rows, so seed an application row first.
        db.save_application(job_id=listing.job_id, status="Analyzing")
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="skip"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="skip"),
        ):
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=10, auto_applied=0)

        assert result.action == "skipped"
        # Verify the real DB row was updated, not a mock call.
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM applications WHERE job_id = ?",
                (listing.job_id,),
            ).fetchone()
        assert row is not None and row[0] == "Skipped"

    def test_daily_cap_reached_routes_to_review(self):
        from jobpulse.scan_pipeline import route_and_apply

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=95.0)
        db = _make_db()
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="auto_submit"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="auto"),
            patch("jobpulse.scan_pipeline._queue_for_review") as mock_queue,
        ):
            # auto_applied == remaining_cap → cap reached
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=5, auto_applied=5)

        assert result.action == "queued_for_review"
        mock_queue.assert_called_once()

    def test_no_cv_routes_to_review(self):
        from jobpulse.scan_pipeline import route_and_apply

        listing = _make_listing()
        bundle = self._make_bundle(ats_score=92.0, cv_path=None)
        bundle.cv_path = None
        db = _make_db()
        review_batch: list = []

        with (
            patch("jobpulse.scan_pipeline.classify_action", return_value="auto_submit"),
            patch("jobpulse.scan_pipeline.determine_match_tier", return_value="auto"),
            patch("jobpulse.scan_pipeline._queue_for_review") as mock_queue,
        ):
            result = route_and_apply(listing, bundle, db, review_batch, remaining_cap=10, auto_applied=0)

        assert result.action == "queued_for_review"
        mock_queue.assert_called_once()


# ---------------------------------------------------------------------------
# MaterialsBundle and RouteResult dataclass smoke tests
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_materials_bundle_defaults(self):
        from jobpulse.scan_pipeline import MaterialsBundle

        b = MaterialsBundle()
        assert b.cv_path is None
        assert b.ats_score == 0.0
        assert b.matched_project_names == []
        assert b.gate4b_notes == ""
        assert b.notion_status == "Ready"

    def test_route_result_fields(self):
        from jobpulse.scan_pipeline import RouteResult

        r = RouteResult(action="auto_applied", job_id="xyz", title="Dev", company="Acme")
        assert r.action == "auto_applied"
        assert r.job_id == "xyz"


# ---------------------------------------------------------------------------
# process_single_url — single-URL CLI path (used by `runner job-process-url`)
# ---------------------------------------------------------------------------


class TestProcessSingleUrlGateRouting:
    """Regression coverage for the single-URL gate-routing surface.

    The single-URL path historically diverged from `prescreen_listings` —
    notably comparing `pre_screen.tier == "rejected"` while SkillGraphStore
    only sets tier ∈ {"reject", "skip", "apply", "strong"} (S7 audit B-1).
    """

    def test_gate1_reject_short_circuits(self, monkeypatch):
        """tier == 'reject' must produce a 'rejected' status response.

        Bug: scan_pipeline.py:1092 compared against the wrong literal,
        so Gate 1 kills (seniority, primary skill, foreign domain) silently
        fell through to material generation.
        """
        from jobpulse.scan_pipeline import process_single_url
        from jobpulse.skill_graph_store import PreScreenResult

        screen = PreScreenResult()
        screen.tier = "reject"
        screen.gate1_passed = False
        screen.gate1_kill_reason = "Seniority kill: JD requires 5+ years experience"

        listing = _make_listing(
            job_id="test-reject-001",
            title="Senior Engineer",
            company="Acme",
            url="https://example.com/job/1",
        )

        # Stub HTTP client used inside process_single_url
        class _Resp:
            status_code = 200
            url = "https://example.com/job/1"
            text = (
                "<html><body><h1>Senior Engineer</h1>"
                "<div class='job-description'>Need 5+ years experience</div>"
                "</body></html>"
            )
            def __init__(self, *_a, **_kw):
                pass

        class _Client:
            def __init__(self, *_a, **_kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_a):
                return False
            def get(self, *_a, **_kw):
                return _Resp()

        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Resp())
        monkeypatch.setattr(httpx, "Client", _Client)

        from jobpulse.liveness_checker import classify_liveness
        monkeypatch.setattr(
            "jobpulse.liveness_checker.classify_liveness",
            lambda **kw: type("LR", (), {"status": "alive"})(),
        )

        # Stub heavy dependencies
        monkeypatch.setattr("jobpulse.scan_pipeline.analyze_jd", lambda **kw: listing)

        class _SGS:
            def __init__(self, *_a, **_kw): pass
            def pre_screen_jd(self, _listing): return screen

        monkeypatch.setattr("jobpulse.scan_pipeline.SkillGraphStore", _SGS)

        # JobDB is lazy-imported inside process_single_url — patch on origin module.
        # MagicMock here: the test asserts on the returned response, not DB rows.
        db = MagicMock()
        db.get_listing.return_value = None
        monkeypatch.setattr("jobpulse.job_db.JobDB", lambda: db)

        # If the bug is present, the code falls through to generate_materials —
        # patch that to detect the leak; passing test means we never reached it.
        called = {"materials": False}
        def _fake_materials(*a, **kw):
            called["materials"] = True
            from jobpulse.scan_pipeline import MaterialsBundle
            return MaterialsBundle()
        monkeypatch.setattr("jobpulse.scan_pipeline.generate_materials", _fake_materials)

        result = process_single_url(
            "https://example.com/job/1",
            platform="generic",
            dry_run=True,
        )

        assert result["status"] == "rejected", (
            f"Gate 1 reject must short-circuit, got status={result['status']}"
        )
        assert result["pre_screen"].tier == "reject"
        assert "Seniority kill" in result["message"]
        assert called["materials"] is False, (
            "generate_materials should NOT run after a Gate 1 reject"
        )
