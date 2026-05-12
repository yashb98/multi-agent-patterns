"""Scan-loop block-event wiring tests — pipeline-bugs S9 (M-9.B, M-9.C, M-9.D).

Pre-fix:
- M-9.B: ``scan_indeed`` has no ``ScanLearningEngine`` wiring whatsoever
  (no ``can_scan_now``, no ``record_success``, no ``handle_block``). The
  highest-block-rate platform empirically.
- M-9.C: ``scan_reed`` only consults ``can_scan_now``. It never emits
  ``record_success`` or ``handle_block`` on terminal 429.
- M-9.D: ``handle_block`` insists on ``wall.wall_type`` — incompatible with
  httpx-based scanners that have plain string codes (``http_429``).

These tests fail pre-fix and pass once the three scanner files emit the
right events to ``scan_learning.db``. SQLite isolation via tmp_path
mirrors ``tests/jobpulse/test_scan_learning_wiring.py`` — never touches
``data/scan_learning.db``.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from jobpulse.job_scanners import (
    SessionSignals,
    handle_block,
    record_success,
)
from jobpulse.models.application_models import SearchConfig
from jobpulse.scan_learning import ScanLearningEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(tmp_path):
    return ScanLearningEngine(db_path=str(tmp_path / "scan_learning.db"))


def _events(engine: ScanLearningEngine, platform: str) -> list[dict]:
    """Return all scan_events rows for a platform as dicts."""
    conn = sqlite3.connect(engine.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM scan_events WHERE platform = ? ORDER BY timestamp",
        (platform,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# M-9.D — handle_block accepts string wall_type
# ---------------------------------------------------------------------------


class TestHandleBlockShape:
    """Bug pattern: ``handle_block`` insists on a wall object with
    ``.wall_type`` so httpx scanners can't call it. Post-fix accepts a plain
    string too."""

    def test_handle_block_accepts_string_wall_type(self, tmp_path):
        engine = _engine(tmp_path)
        signals = SessionSignals("indeed", "test-ua")
        signals.last_query = "data engineer"
        signals.record_request()

        # Pre-fix this would AttributeError on `wall.wall_type`.
        handle_block(engine, "indeed", "http_429", signals)

        rows = _events(engine, "indeed")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "blocked"
        assert rows[0]["wall_type"] == "http_429"

    def test_handle_block_still_accepts_wall_object(self, tmp_path):
        """Backwards compatibility — LinkedIn passes ``VerificationWall``."""
        engine = _engine(tmp_path)
        signals = SessionSignals("linkedin", "test-ua")
        signals.last_query = "swe"
        signals.record_request()

        wall = SimpleNamespace(wall_type="cloudflare_turnstile")
        handle_block(engine, "linkedin", wall, signals)

        rows = _events(engine, "linkedin")
        assert len(rows) == 1
        assert rows[0]["wall_type"] == "cloudflare_turnstile"


# ---------------------------------------------------------------------------
# M-9.B — scan_indeed wiring
# ---------------------------------------------------------------------------


class TestIndeedWiring:
    """Pre-fix scan_indeed had no ScanLearningEngine wiring at all."""

    def test_scan_indeed_records_success_on_results(self, tmp_path):
        from jobpulse.job_scanners import indeed as indeed_mod

        # Fake JobSpy DataFrame: 2 listings, no exception.
        fake_df = MagicMock()
        fake_df.iterrows.return_value = iter([
            (0, MagicMock(to_dict=lambda: {
                "title": "Senior Data Engineer", "company": "TestCo",
                "location": "London", "description": "...",
                "job_url": "https://www.indeed.com/viewjob?jk=abc1",
                "job_url_direct": "", "date_posted": "2026-05-08",
            })),
        ])
        # `len(results)` is used in the log line — DataFrame supports it.
        fake_df.__len__ = MagicMock(return_value=1)

        engine = _engine(tmp_path)
        with patch.object(indeed_mod, "ScanLearningEngine", return_value=engine), \
             patch.object(indeed_mod, "scrape_jobs", return_value=fake_df):
            results = indeed_mod.scan_indeed(
                search_terms=["data engineer"],
                location="London",
                max_results=5,
            )

        assert len(results) == 1
        rows = _events(engine, "indeed")
        success_rows = [r for r in rows if r["outcome"] == "success"]
        assert len(success_rows) == 1, (
            f"Expected one success row in scan_learning.db; got {rows}. "
            "M-9.B: scan_indeed had no record_success wiring."
        )

    def test_scan_indeed_records_block_on_jobspy_block_exception(self, tmp_path):
        from jobpulse.job_scanners import indeed as indeed_mod

        engine = _engine(tmp_path)
        # JobSpy raises with a block-shaped message.
        def _raise_block(**_kw):
            raise RuntimeError("Indeed returned 403: forbidden / captcha required")

        with patch.object(indeed_mod, "ScanLearningEngine", return_value=engine), \
             patch.object(indeed_mod, "scrape_jobs", side_effect=_raise_block):
            results = indeed_mod.scan_indeed(
                search_terms=["data engineer"],
                location="London",
                max_results=5,
            )

        assert results == []
        rows = _events(engine, "indeed")
        block_rows = [r for r in rows if r["outcome"] == "blocked"]
        assert len(block_rows) == 1, (
            f"Expected one block row in scan_learning.db; got {rows}. "
            "M-9.B: scan_indeed had no handle_block wiring."
        )
        assert block_rows[0]["wall_type"] == "jobspy_exception"

    def test_scan_indeed_skips_when_cooldown_active(self, tmp_path):
        from jobpulse.job_scanners import indeed as indeed_mod

        engine = _engine(tmp_path)
        engine.start_cooldown("indeed", "http_429")

        with patch.object(indeed_mod, "ScanLearningEngine", return_value=engine), \
             patch.object(indeed_mod, "scrape_jobs") as mock_scrape:
            results = indeed_mod.scan_indeed(
                search_terms=["x"], location="London", max_results=5,
            )
        assert results == []
        mock_scrape.assert_not_called()


# ---------------------------------------------------------------------------
# M-9.C — scan_reed wiring
# ---------------------------------------------------------------------------


class TestReedWiring:
    """Pre-fix scan_reed had can_scan_now but no record_success / handle_block."""

    def _config(self) -> SearchConfig:
        return SearchConfig(
            titles=["Data Engineer"],
            location="London",
            salary_min=30000.0,
        )

    def test_scan_reed_records_success_on_200(self, tmp_path, monkeypatch):
        from jobpulse.job_scanners import reed as reed_mod

        engine = _engine(tmp_path)
        monkeypatch.setattr(reed_mod, "REED_API_KEY", "fake-key-for-test")

        # Mock httpx.Client to return a 200 with one job, then empty page.
        def _mock_handler(request: httpx.Request) -> httpx.Response:
            if "/jobs/" in str(request.url):
                # Detail enrichment fetch
                return httpx.Response(
                    200, json={"jobDescription": "Long detailed description text"},
                )
            return httpx.Response(
                200, json={"results": [{
                    "jobTitle": "Data Engineer", "employerName": "Acme",
                    "jobUrl": "https://www.reed.co.uk/jobs/123",
                    "jobId": 123, "locationName": "London",
                    "minimumSalary": 50000, "maximumSalary": 70000,
                    "jobDescription": "Short description",
                    "date": "2026-05-08T10:00:00Z",
                }]},
            )

        # Patch httpx.Client to use our MockTransport
        original_client = httpx.Client

        def _make_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_mock_handler)
            kwargs.pop("timeout", None)  # avoid duplicate
            return original_client(timeout=20, transport=kwargs["transport"])

        with patch.object(reed_mod, "ScanLearningEngine", return_value=engine), \
             patch.object(reed_mod.httpx, "Client", side_effect=_make_client):
            results = reed_mod.scan_reed(self._config())

        assert len(results) >= 1
        rows = _events(engine, "reed")
        success_rows = [r for r in rows if r["outcome"] == "success"]
        assert len(success_rows) == 1, (
            f"Expected one success row in scan_learning.db; got {rows}. "
            "M-9.C: scan_reed had no record_success wiring."
        )

    def test_scan_reed_records_block_on_terminal_429(self, tmp_path, monkeypatch):
        from jobpulse.job_scanners import reed as reed_mod

        engine = _engine(tmp_path)
        monkeypatch.setattr(reed_mod, "REED_API_KEY", "fake-key-for-test")
        # Skip retry sleeps
        monkeypatch.setattr(reed_mod.time, "sleep", lambda *_a, **_k: None)

        # Always return 429 — exhausts retries, triggers terminal block.
        def _always_429(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        original_client = httpx.Client

        def _make_client(*args, **kwargs):
            return original_client(timeout=20, transport=httpx.MockTransport(_always_429))

        with patch.object(reed_mod, "ScanLearningEngine", return_value=engine), \
             patch.object(reed_mod.httpx, "Client", side_effect=_make_client):
            results = reed_mod.scan_reed(self._config())

        assert results == []
        rows = _events(engine, "reed")
        block_rows = [r for r in rows if r["outcome"] == "blocked"]
        assert len(block_rows) == 1, (
            f"Expected one block row in scan_learning.db; got {rows}. "
            "M-9.C: scan_reed had no handle_block wiring."
        )
        assert block_rows[0]["wall_type"] == "http_429"

    def test_scan_reed_skips_record_success_when_no_results(
        self, tmp_path, monkeypatch,
    ):
        """Backwards compat: 0 results == no success row (matches LinkedIn's
        existing guard at scan_linkedin.py:252-258)."""
        from jobpulse.job_scanners import reed as reed_mod

        engine = _engine(tmp_path)
        monkeypatch.setattr(reed_mod, "REED_API_KEY", "fake-key-for-test")

        def _empty(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        original_client = httpx.Client

        def _make_client(*args, **kwargs):
            return original_client(timeout=20, transport=httpx.MockTransport(_empty))

        with patch.object(reed_mod, "ScanLearningEngine", return_value=engine), \
             patch.object(reed_mod.httpx, "Client", side_effect=_make_client):
            results = reed_mod.scan_reed(self._config())

        assert results == []
        rows = _events(engine, "reed")
        # Neither success nor block — empty result is not a block, just dry.
        assert not [r for r in rows if r["outcome"] == "success"]
        assert not [r for r in rows if r["outcome"] == "blocked"]
