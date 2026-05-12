"""Tests proving ScanLearningEngine is wired to job_scanners and optimization."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jobpulse.scan_learning import ScanLearningEngine
from jobpulse.job_scanners import handle_block, SessionSignals
from shared.optimization._engine import OptimizationEngine


# ── Shared kwargs for record_event ──────────────────────────────────

def _event_kwargs(*, outcome: str = "success", wall_type: str | None = None) -> dict:
    return dict(
        platform="linkedin",
        requests_in_session=5,
        avg_delay=3.2,
        session_age_seconds=120.0,
        user_agent_hash="abc12345",
        was_fresh_session=True,
        used_vpn=False,
        simulated_mouse=False,
        referrer_chain="direct",
        search_query="python developer",
        pages_before_block=5,
        browser_fingerprint="fp12345",
        waited_for_page_load=True,
        page_load_time_ms=1200,
        outcome=outcome,
        wall_type=wall_type,
    )


# ── Test A: record_event creates a row in scan_events ────────────

def test_record_event_creates_scan_event(tmp_path):
    db = str(tmp_path / "scan_learning.db")
    engine = ScanLearningEngine(db_path=db)

    event_id = engine.record_event(**_event_kwargs(outcome="success"))

    assert event_id  # non-empty string

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM scan_events WHERE id = ?", (event_id,)
        ).fetchone()

    assert row is not None
    # column order: id, platform, timestamp, time_of_day_bucket, ...
    assert row[0] == event_id
    assert row[1] == "linkedin"
    # outcome is column index 17
    assert row[17] == "success"


# ── Test B: blocked event emits optimization signal ──────────────

def test_blocked_event_emits_optimization_signal(tmp_path):
    scan_db = str(tmp_path / "scan_learning.db")
    opt_db = str(tmp_path / "optimization.db")

    opt_engine = OptimizationEngine(db_path=opt_db)

    with patch("shared.optimization._engine._shared_engine", opt_engine), \
         patch("shared.optimization.get_optimization_engine", return_value=opt_engine):
        # Patch the import path used inside scan_learning.py
        with patch(
            "jobpulse.scan_learning.get_optimization_engine",
            create=True,
        ) as mock_getter:
            # Since scan_learning.py does `from shared.optimization import get_optimization_engine`
            # at call-time inside record_event, we need to patch at the source
            pass

        # Actually, scan_learning.py does a local import:
        #   from shared.optimization import get_optimization_engine
        # So we patch shared.optimization.get_optimization_engine directly
        with patch(
            "shared.optimization.get_optimization_engine",
            return_value=opt_engine,
        ):
            scan_engine = ScanLearningEngine(db_path=scan_db)
            event_id = scan_engine.record_event(
                **_event_kwargs(outcome="blocked", wall_type="cloudflare")
            )

    # Verify the signal was written to optimization.db signals table
    with sqlite3.connect(opt_db) as conn:
        rows = conn.execute(
            "SELECT signal_type, source_loop, domain, agent_name, severity, session_id "
            "FROM signals WHERE source_loop = 'scan_learning'"
        ).fetchall()

    assert len(rows) >= 1
    sig = rows[0]
    assert sig[0] == "failure"        # signal_type
    assert sig[1] == "scan_learning"  # source_loop
    assert sig[2] == "linkedin"       # domain
    assert sig[3] == "scanner"        # agent_name
    assert sig[4] == "critical"       # severity
    assert sig[5] == event_id         # session_id


# ── Test C: handle_block records cooldown ────────────────────────

def test_handle_block_records_cooldown(tmp_path):
    scan_db = str(tmp_path / "scan_learning.db")
    opt_db = str(tmp_path / "optimization.db")
    engine = ScanLearningEngine(db_path=scan_db)

    # Build a SessionSignals for the test
    signals = SessionSignals(platform="indeed", user_agent="TestAgent/1.0")
    signals.record_request()  # so requests_count > 0

    # wall needs .wall_type attribute
    wall = SimpleNamespace(wall_type="turnstile")

    # Patch optimization engine to avoid touching production DB.
    # handle_block calls record_event (which emits optimization signal)
    # and update_learned_rules (which may also emit).
    opt_engine = OptimizationEngine(db_path=opt_db)
    with patch(
        "shared.optimization.get_optimization_engine",
        return_value=opt_engine,
    ):
        handle_block(engine, "indeed", wall, signals)

    # Verify cooldown was written
    with sqlite3.connect(scan_db) as conn:
        row = conn.execute(
            "SELECT platform, consecutive_blocks, last_wall_type FROM cooldowns WHERE platform = ?",
            ("indeed",),
        ).fetchone()

    assert row is not None
    assert row[0] == "indeed"
    assert row[1] == 1                # first block
    assert row[2] == "turnstile"

    # Also verify the scan_events row exists with outcome=blocked
    with sqlite3.connect(scan_db) as conn:
        event_row = conn.execute(
            "SELECT outcome, wall_type FROM scan_events WHERE platform = 'indeed'"
        ).fetchone()

    assert event_row is not None
    assert event_row[0] == "blocked"
    assert event_row[1] == "turnstile"


# ── Test D: scan_linkedin must not record_success when zero results ──
# Regression for S9 audit M-A: scan_linkedin previously called record_success
# unconditionally even when every page returned 429 / empty HTML, which would
# reset the cooldown after a blocked session.

class _LinkedInEmptyResp:
    status_code = 200
    text = "<html></html>"
    headers: dict = {}


class _LinkedInEmptyClient:
    def __init__(self, *_, **__) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, _url):
        return _LinkedInEmptyResp()


class _LinkedInLiveResp:
    status_code = 200
    headers: dict = {}
    # one card with required selectors so the parser yields a result
    text = (
        '<html><body>'
        '<div class="base-search-card">'
        '<h3 class="base-search-card__title">Engineer</h3>'
        '<h4 class="base-search-card__subtitle">Acme</h4>'
        '<span class="job-search-card__location">London</span>'
        '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/1">x</a>'
        '</div></body></html>'
    )


class _LinkedInLiveClient:
    def __init__(self, *_, **__) -> None:
        self._first = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, _url):
        # First call returns the search page with one card; detail fetches and
        # subsequent paginated calls return empty HTML so we exit the loop.
        if self._first:
            self._first = False
            return _LinkedInLiveResp()
        return _LinkedInEmptyResp()


def test_scan_linkedin_skips_record_success_on_zero_results(monkeypatch, tmp_path):
    """When scan_linkedin returns 0 jobs, record_success must NOT be called."""
    import httpx
    from unittest.mock import MagicMock
    from jobpulse.job_scanners import linkedin as linkedin_mod
    from jobpulse.scan_learning import ScanLearningEngine
    from jobpulse.models.application_models import SearchConfig

    monkeypatch.setattr(httpx, "Client", _LinkedInEmptyClient)
    monkeypatch.setattr(linkedin_mod.httpx, "Client", _LinkedInEmptyClient)

    fake_record_success = MagicMock()
    monkeypatch.setattr(linkedin_mod, "record_success", fake_record_success)

    # Avoid hitting the real scan_learning DB
    fake_engine = MagicMock(spec=ScanLearningEngine)
    fake_engine.get_adaptive_params.return_value = {"cooldown_active": False, "max_requests": 5}
    monkeypatch.setattr(linkedin_mod, "ScanLearningEngine", lambda: fake_engine)

    config = SearchConfig(
        titles=["Software Engineer"], location="London", include_remote=False, salary_min=0
    )
    results = linkedin_mod.scan_linkedin(config)

    assert results == []
    assert fake_record_success.call_count == 0


def test_scan_linkedin_records_success_when_results_non_empty(monkeypatch):
    """Sanity check: the M-A guard does not break the normal happy path."""
    import httpx
    from unittest.mock import MagicMock
    from jobpulse.job_scanners import linkedin as linkedin_mod
    from jobpulse.scan_learning import ScanLearningEngine
    from jobpulse.models.application_models import SearchConfig

    monkeypatch.setattr(httpx, "Client", _LinkedInLiveClient)
    monkeypatch.setattr(linkedin_mod.httpx, "Client", _LinkedInLiveClient)

    # Skip detail fetch sleeps to keep the test fast
    monkeypatch.setattr(linkedin_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(linkedin_mod.random, "uniform", lambda *_: 0)

    fake_record_success = MagicMock()
    monkeypatch.setattr(linkedin_mod, "record_success", fake_record_success)

    fake_engine = MagicMock(spec=ScanLearningEngine)
    fake_engine.get_adaptive_params.return_value = {"cooldown_active": False, "max_requests": 5}
    monkeypatch.setattr(linkedin_mod, "ScanLearningEngine", lambda: fake_engine)

    config = SearchConfig(
        titles=["Software Engineer"], location="London", include_remote=False, salary_min=0
    )
    results = linkedin_mod.scan_linkedin(config)

    assert len(results) >= 1
    assert fake_record_success.call_count == 1
