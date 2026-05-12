"""Tests for jobpulse.platform_bypass — direct ATS URL resolution."""
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.platform_bypass import (
    PlatformBypass,
    BypassResult,
    is_aggregator_domain,
    get_platform_bypass,
)


@pytest.fixture
def bypass(tmp_path):
    db = tmp_path / "platform_bypass.db"
    return PlatformBypass(db_path=db)


class TestAggregatorDetection:
    def test_indeed(self):
        assert is_aggregator_domain("https://uk.indeed.com/viewjob?jk=abc123")

    def test_linkedin(self):
        assert is_aggregator_domain("https://www.linkedin.com/jobs/view/123")

    def test_totaljobs(self):
        assert is_aggregator_domain("https://www.totaljobs.com/job/abc")

    def test_reed(self):
        assert is_aggregator_domain("https://www.reed.co.uk/jobs/data-analyst/123")

    def test_glassdoor(self):
        assert is_aggregator_domain("https://www.glassdoor.com/job/123")

    def test_non_aggregator(self):
        assert not is_aggregator_domain("https://boards.greenhouse.io/acme/jobs/123")

    def test_empty(self):
        assert not is_aggregator_domain("")


class TestCache:
    def test_store_and_retrieve(self, bypass):
        bypass._store_cached("Acme Corp", "https://boards.greenhouse.io/acme", "greenhouse", "ats_pattern")
        assert bypass._get_cached("Acme Corp") == "https://boards.greenhouse.io/acme"

    def test_case_insensitive(self, bypass):
        bypass._store_cached("Acme Corp", "https://boards.greenhouse.io/acme", "greenhouse", "test")
        assert bypass._get_cached("acme corp") == "https://boards.greenhouse.io/acme"

    def test_cache_miss(self, bypass):
        assert bypass._get_cached("Unknown Corp") is None

    def test_success_count_increments(self, bypass):
        bypass._store_cached("Acme Corp", "https://boards.greenhouse.io/acme", "greenhouse", "test")
        bypass._get_cached("Acme Corp")
        bypass._get_cached("Acme Corp")
        with sqlite3.connect(bypass._db_path) as conn:
            row = conn.execute("SELECT success_count FROM bypass_cache WHERE company = 'acme corp'").fetchone()
        assert row[0] == 3


class TestResolveDirectUrl:
    @pytest.mark.asyncio
    async def test_cache_hit(self, bypass):
        bypass._store_cached("Acme", "https://boards.greenhouse.io/acme", "greenhouse", "test")
        result = await bypass.resolve_direct_url(
            {"company": "Acme", "title": "Engineer"},
            "https://indeed.com/viewjob?jk=123",
        )
        assert result.resolved
        assert result.direct_url == "https://boards.greenhouse.io/acme"
        assert result.strategy_used == "cache"

    @pytest.mark.asyncio
    async def test_no_company(self, bypass):
        result = await bypass.resolve_direct_url({"company": "", "title": "Dev"}, "https://indeed.com/x")
        assert not result.resolved
        assert "no company" in result.error

    @pytest.mark.asyncio
    async def test_ats_pattern_hit(self, bypass):
        """Updated 2026-05-03: production now uses httpx.get with body
        verification (commit 93faec7). Mock must return a realistic body
        that passes the 3-stage check: size >= 15KB, no catch-all markers
        in H1/title, and the H1/title references the company slug/token."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "<html><head><title>Acme Careers</title></head>"
            "<body><h1>Acme Careers</h1>"
            + ("<p>real careers content</p>" * 1000)  # ~28KB body
            + "</body></html>"
        )
        with patch("httpx.get", return_value=mock_resp):
            result = await bypass.resolve_direct_url(
                {"company": "Acme", "title": "Engineer"},
                "https://indeed.com/viewjob?jk=123",
            )
        assert result.resolved
        assert result.strategy_used == "ats_pattern"

    @pytest.mark.asyncio
    async def test_all_strategies_exhausted(self, bypass):
        with patch("httpx.get", side_effect=Exception("timeout")):
            result = await bypass.resolve_direct_url(
                {"company": "UnknownCorp12345", "title": "Dev"},
                "https://indeed.com/viewjob?jk=123",
                page=None,
            )
        assert not result.resolved
        assert "exhausted" in result.error


class TestLearningSignals:
    def test_emit_does_not_raise(self, bypass):
        bypass._emit_learning_signals(
            "Acme", "https://indeed.com/x", "https://boards.greenhouse.io/acme",
            "ats_pattern", "Engineer",
        )


class TestSingleton:
    def test_get_platform_bypass_returns_same_instance(self):
        import jobpulse.platform_bypass as mod
        mod._instance = None
        a = get_platform_bypass()
        b = get_platform_bypass()
        assert a is b
        mod._instance = None
