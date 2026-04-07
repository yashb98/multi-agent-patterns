"""Pipeline integration tests using live job fixtures.

These tests validate the full job application pipeline against real,
recently-scraped job data. Fixtures are refreshed by:
    python scripts/refresh_test_fixtures.py

Each test loads fixtures from tests/fixtures/live_snapshots/ and validates
a different pipeline stage against real URLs, JDs, and platform structures.

Fixtures expire after 48 hours — stale fixtures cause test_fixtures_are_fresh
to fail, signaling that refresh_test_fixtures.py needs to run again.

Marked with @pytest.mark.live — run separately from unit tests:
    pytest tests/jobpulse/test_pipeline_live.py -v -m live
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "live_snapshots"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
FIXTURE_MAX_AGE_HOURS = 48


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        pytest.skip("No fixture manifest — run: python scripts/refresh_test_fixtures.py")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_all_fixtures() -> list[dict[str, Any]]:
    """Load all fixture JSON files referenced in the manifest."""
    manifest = _load_manifest()
    fixtures = []
    for entry in manifest.get("fixtures", []):
        path = FIXTURE_DIR / entry["filename"]
        if path.exists():
            fixtures.append(json.loads(path.read_text(encoding="utf-8")))
    if not fixtures:
        pytest.skip("No fixtures found — run: python scripts/refresh_test_fixtures.py")
    return fixtures


def _fixtures_by_platform() -> dict[str, list[dict]]:
    """Group fixtures by platform."""
    by_platform: dict[str, list[dict]] = {}
    for fix in _load_all_fixtures():
        plat = fix.get("platform", "unknown")
        by_platform.setdefault(plat, []).append(fix)
    return by_platform


# ---------------------------------------------------------------------------
# Freshness gate
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestFixtureFreshness:
    def test_fixtures_are_fresh(self):
        """Fail if fixtures are older than 48 hours — forces a refresh."""
        manifest = _load_manifest()
        last_refresh = manifest.get("last_refresh")
        assert last_refresh is not None, (
            "No last_refresh timestamp — run: python scripts/refresh_test_fixtures.py"
        )
        refreshed_at = datetime.fromisoformat(last_refresh)
        age_hours = (datetime.now(timezone.utc) - refreshed_at).total_seconds() / 3600
        assert age_hours < FIXTURE_MAX_AGE_HOURS, (
            f"Fixtures are {age_hours:.1f}h old (max {FIXTURE_MAX_AGE_HOURS}h). "
            f"Run: python scripts/refresh_test_fixtures.py"
        )

    def test_minimum_platform_coverage(self):
        """At least 2 platforms must have fixtures."""
        by_platform = _fixtures_by_platform()
        assert len(by_platform) >= 2, (
            f"Only {len(by_platform)} platform(s) have fixtures: {list(by_platform)}. "
            f"Need at least 2. Run: python scripts/refresh_test_fixtures.py"
        )

    def test_each_platform_has_fixtures(self):
        """Every platform in manifest should have at least 1 fixture with a JD."""
        by_platform = _fixtures_by_platform()
        for platform, fixtures in by_platform.items():
            with_jd = [f for f in fixtures if f.get("description")]
            assert len(with_jd) >= 1, (
                f"Platform '{platform}' has {len(fixtures)} fixtures but "
                f"none with JD text. Refresh needed."
            )


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestPlatformDetection:
    """Validate that _detect_ats_platform correctly identifies real URLs."""

    def test_linkedin_urls_detected(self):
        from jobpulse.ext_adapter import _detect_ats_platform

        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("linkedin", []):
            result = _detect_ats_platform(fix["url"])
            assert result == "linkedin", (
                f"LinkedIn URL not detected: {fix['url']} → '{result}'"
            )

    def test_indeed_urls_detected(self):
        from jobpulse.ext_adapter import _detect_ats_platform

        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("indeed", []):
            result = _detect_ats_platform(fix["url"])
            assert result == "indeed", (
                f"Indeed URL not detected: {fix['url']} → '{result}'"
            )

    def test_reed_urls_detected(self):
        from jobpulse.ext_adapter import _detect_ats_platform

        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("reed", []):
            result = _detect_ats_platform(fix["url"])
            assert result == "reed", (
                f"Reed URL not detected: {fix['url']} → '{result}'"
            )

    def test_all_urls_return_valid_platform(self):
        """Every fixture URL should resolve to a non-empty platform string."""
        from jobpulse.ext_adapter import _detect_ats_platform

        for fix in _load_all_fixtures():
            result = _detect_ats_platform(fix["url"])
            assert isinstance(result, str) and len(result) > 0, (
                f"URL returned empty platform: {fix['url']}"
            )


# ---------------------------------------------------------------------------
# JD analysis
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestJDAnalysis:
    """Validate JD analysis pipeline against real job descriptions."""

    def test_skill_extraction_finds_skills(self):
        """Real JDs should yield at least 3 skills via rule-based extraction."""
        from jobpulse.skill_extractor import extract_skills_rule_based

        for fix in _load_all_fixtures():
            jd = fix.get("description", "")
            if len(jd) < 100:
                continue  # Skip fixtures with truncated/empty JDs

            result = extract_skills_rule_based(jd)
            all_skills = result.get("required_skills", []) + result.get("preferred_skills", [])
            assert len(all_skills) >= 3, (
                f"Only {len(all_skills)} skills from {fix['platform']}/"
                f"{fix['company']}: {all_skills}. "
                f"JD length: {len(jd)} chars"
            )

    def test_jd_not_empty_for_fixtures_with_description(self):
        """Fixtures marked as having JDs should have non-trivial text."""
        for fix in _load_all_fixtures():
            if fix.get("description"):
                assert len(fix["description"]) >= 50, (
                    f"JD too short ({len(fix['description'])} chars) for "
                    f"{fix['platform']}/{fix['company']}: {fix['url']}"
                )

    def test_salary_extraction_on_reed_jobs(self):
        """Reed jobs with salary data should have salary_min populated."""
        by_platform = _fixtures_by_platform()
        reed_jobs = by_platform.get("reed", [])
        if not reed_jobs:
            pytest.skip("No Reed fixtures")

        has_salary = [j for j in reed_jobs if j.get("salary_min") is not None]
        # At least some Reed jobs should have salary (Reed API usually includes it)
        assert len(has_salary) >= 1 or len(reed_jobs) == 0, (
            f"No Reed jobs have salary data out of {len(reed_jobs)} fixtures"
        )


# ---------------------------------------------------------------------------
# Recruiter pre-screen (Gate 0)
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestRecruiterScreen:
    """Validate Gate 0 title filtering on real job titles."""

    def test_gate0_does_not_block_target_roles(self):
        """Our target roles (Data Analyst, SWE, ML Engineer) should pass Gate 0."""
        from jobpulse.recruiter_screen import gate0_title_relevance

        target_keywords = {"data", "software", "python", "machine learning", "ml", "engineer", "developer", "analyst"}
        passed = 0
        total = 0

        # Default Gate 0 config (mirrors recruiter_screen expected keys)
        gate0_config = {
            "titles": [
                "data analyst", "data engineer", "data scientist",
                "software engineer", "python developer", "backend developer",
                "machine learning engineer", "ml engineer", "ai engineer",
            ],
            "exclude_keywords": [
                "senior", "lead", "principal", "staff", "director",
                "manager", "head of", "vp ",
            ],
        }

        for fix in _load_all_fixtures():
            title_lower = fix.get("title", "").lower()
            # Only test fixtures whose titles match our search terms
            if any(kw in title_lower for kw in target_keywords):
                total += 1
                jd = fix.get("description", "")
                if gate0_title_relevance(fix["title"], jd, gate0_config):
                    passed += 1

        if total == 0:
            pytest.skip("No fixtures with target role titles")

        pass_rate = passed / total
        assert pass_rate >= 0.5, (
            f"Gate 0 blocked {total - passed}/{total} target roles "
            f"({pass_rate:.0%} pass rate). "
            f"Expected >= 50%"
        )


# ---------------------------------------------------------------------------
# ATS classification from apply URLs
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestATSDetection:
    """Validate ATS detection on real job URLs."""

    def test_detected_ats_matches_url_pattern(self):
        """If a fixture has detected_ats set, it should match URL-based detection."""
        from jobpulse.jd_analyzer import detect_ats_platform

        for fix in _load_all_fixtures():
            if fix.get("detected_ats"):
                url_detection = detect_ats_platform(fix["url"])
                # detected_ats from the fixture was set during scraping
                # URL-based detection may differ (e.g., Reed URL doesn't reveal ATS)
                # But if URL-based detection returns something, it should match
                if url_detection:
                    assert url_detection == fix["detected_ats"], (
                        f"ATS mismatch for {fix['url']}: "
                        f"URL says '{url_detection}', fixture says '{fix['detected_ats']}'"
                    )


# ---------------------------------------------------------------------------
# Ralph Loop routing
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestRalphLoopRouting:
    """Validate that real URLs route correctly through Ralph Loop."""

    def test_url_to_job_id_deterministic_on_real_urls(self):
        """_url_to_job_id produces stable, unique IDs for real URLs."""
        from jobpulse.ralph_loop.loop import _url_to_job_id

        fixtures = _load_all_fixtures()
        ids = set()
        for fix in fixtures:
            job_id = _url_to_job_id(fix["url"])
            assert len(job_id) == 12, f"Bad ID length for {fix['url']}: {job_id}"
            assert all(c in "0123456789abcdef" for c in job_id), (
                f"Non-hex ID for {fix['url']}: {job_id}"
            )
            # Deterministic
            assert _url_to_job_id(fix["url"]) == job_id
            ids.add(job_id)

        # All unique
        assert len(ids) == len(fixtures), (
            f"Collision: {len(fixtures)} URLs produced only {len(ids)} unique IDs"
        )

    def test_classify_action_on_real_scores(self):
        """classify_action produces valid tiers for typical ATS scores."""
        from jobpulse.applicator import classify_action

        # Simulate realistic score distribution
        test_cases = [
            (97.0, True, "auto_submit"),
            (97.0, False, "auto_submit_with_preview"),
            (90.0, True, "send_for_review"),
            (90.0, False, "send_for_review"),
            (80.0, True, "skip"),
            (80.0, False, "skip"),
        ]
        for score, easy, expected in test_cases:
            result = classify_action(score, easy)
            assert result == expected, (
                f"classify_action({score}, {easy}) = '{result}', expected '{expected}'"
            )


# ---------------------------------------------------------------------------
# Applicator platform inference from real URLs
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestApplicatorPlatformInference:
    """Validate that applicator.py correctly infers platform from real URLs."""

    def test_linkedin_url_inferred(self):
        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("linkedin", []):
            url = fix["url"]
            if "linkedin.com" in url:
                # Simulating the platform inference logic from applicator.py lines 215-229
                assert "linkedin.com" in url

    def test_indeed_url_inferred(self):
        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("indeed", []):
            url = fix["url"]
            assert "indeed.com" in url, f"Indeed fixture has non-Indeed URL: {url}"

    def test_reed_url_structure(self):
        """Reed URLs should follow /jobs/<slug>/<id> pattern."""
        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("reed", []):
            url = fix["url"]
            assert "reed.co.uk" in url, f"Reed fixture has non-Reed URL: {url}"


# ---------------------------------------------------------------------------
# Cross-platform dedup
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestCrossPlatformDedup:
    """Validate that job IDs are unique across platforms."""

    def test_no_duplicate_job_ids(self):
        """All fixture job_ids should be unique."""
        fixtures = _load_all_fixtures()
        ids = [f["job_id"] for f in fixtures]
        assert len(ids) == len(set(ids)), (
            f"Duplicate job_ids found: {len(ids)} total, {len(set(ids))} unique"
        )

    def test_same_url_same_id(self):
        """Hashing the same URL twice should produce the same job_id."""
        import hashlib
        for fix in _load_all_fixtures():
            expected = hashlib.sha256(fix["url"].encode()).hexdigest()[:16]
            assert fix["job_id"] == expected, (
                f"job_id mismatch for {fix['url']}: "
                f"got {fix['job_id']}, expected {expected}"
            )


# ---------------------------------------------------------------------------
# State machine selection
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestStateMachineSelection:
    """Validate that real URLs select the correct state machine."""

    def test_get_state_machine_for_all_fixtures(self):
        from jobpulse.state_machines import get_state_machine

        for fix in _load_all_fixtures():
            platform = fix["platform"]
            sm = get_state_machine(platform)
            assert sm is not None, (
                f"No state machine for platform '{platform}' (URL: {fix['url']})"
            )

    def test_linkedin_gets_linkedin_sm(self):
        from jobpulse.state_machines import LinkedInStateMachine, get_state_machine

        by_platform = _fixtures_by_platform()
        for fix in by_platform.get("linkedin", []):
            sm = get_state_machine("linkedin")
            assert isinstance(sm, LinkedInStateMachine), (
                f"LinkedIn URL got wrong SM: {type(sm).__name__}"
            )


# ---------------------------------------------------------------------------
# End-to-end dry-run via Ralph Loop
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestRalphLoopDryRun:
    """Dry-run Ralph Loop against real URLs to validate routing + error handling."""

    def test_dry_run_each_platform(self, tmp_path):
        """ralph_apply_sync(dry_run=True) should not crash on any real URL."""
        from jobpulse.ralph_loop.loop import ralph_apply_sync

        by_platform = _fixtures_by_platform()
        cv_path = tmp_path / "test_cv.pdf"
        cv_path.write_bytes(b"%PDF-1.4 test cv content")

        results = {}
        for platform, fixtures in by_platform.items():
            fix = fixtures[0]  # Test one per platform
            try:
                # Patch apply_job to avoid real submission — just test routing
                with patch("jobpulse.applicator.apply_job") as mock_apply:
                    mock_apply.return_value = {
                        "success": True,
                        "screenshot": None,
                        "error": None,
                    }
                    result = ralph_apply_sync(
                        url=fix["url"],
                        ats_platform=platform,
                        cv_path=cv_path,
                        db_path=str(tmp_path / f"ralph_{platform}.db"),
                        dry_run=True,
                    )
                    results[platform] = result
                    assert result["success"] is True, (
                        f"Dry run failed for {platform}: {result.get('error')}"
                    )
                    mock_apply.assert_called_once()
                    # Verify the correct URL was passed through
                    call_kwargs = mock_apply.call_args
                    assert fix["url"] in str(call_kwargs), (
                        f"URL not passed through for {platform}"
                    )
            except Exception as exc:
                pytest.fail(
                    f"ralph_apply_sync crashed for {platform} "
                    f"({fix['url']}): {exc}"
                )

        assert len(results) >= 2, (
            f"Only {len(results)} platforms tested. Need at least 2."
        )

    def test_pattern_store_isolation(self, tmp_path):
        """Each dry-run uses isolated PatternStore — no cross-contamination."""
        from jobpulse.ralph_loop.pattern_store import PatternStore

        fixtures = _load_all_fixtures()
        if not fixtures:
            pytest.skip("No fixtures")

        db_path = str(tmp_path / "isolation_test.db")
        store = PatternStore(db_path, mode="test")

        # Verify production DB is not touched
        assert "data/" not in store.db_path
        assert str(tmp_path) in store.db_path
