"""Wiring test: DOM-fast-path to fill_form is gated on domain prior success.

Live regression on 2026-05-04: pls-solicitors.co.uk landing page
(`/jobs/graduate-...`) has visible form-like elements (search bar,
filter dropdowns) so the DOM classifier mis-labelled it as
APPLICATION_FORM at confidence ≥0.8. The navigator's _phase_plan
DOM-fast-path then skipped PageReasoner and went straight to fill,
which timed out trying to scroll to a stale field locator.

This fix gates the fast path on FormExperienceDB.lookup(domain).success
— trusted domains keep the speed; untrusted/new domains fall through
to PageReasoner regardless of DOM confidence.

The fix is dynamic — no hardcoded domain allow-lists. Trust accrues
automatically from successful applications.
"""
from __future__ import annotations
import sqlite3
from unittest.mock import patch, MagicMock

import pytest


def test_unknown_domain_returns_false(monkeypatch, tmp_path):
    """A domain with no FormExperienceDB record → not trusted."""
    from jobpulse import form_experience_db as fe_mod
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    monkeypatch.setattr(fe_mod, "_DEFAULT_DB", str(tmp_path / "fe.db"), raising=False)
    assert FormNavigator._domain_has_prior_success("https://pls-solicitors.co.uk/jobs/foo") is False


def test_domain_with_failed_record_returns_false(monkeypatch, tmp_path):
    """A domain that's been seen but never SUCCESSFULLY filled → not trusted."""
    from jobpulse import form_experience_db as fe_mod
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    db_path = str(tmp_path / "fe.db")
    monkeypatch.setattr(fe_mod, "_DEFAULT_DB", db_path, raising=False)

    fe = fe_mod.FormExperienceDB(db_path=db_path)
    fe.record(
        domain="pls-solicitors.co.uk",
        platform="generic",
        adapter="extension",
        pages_filled=0,
        field_types=[],
        screening_questions=[],
        time_seconds=10.0,
        success=False,
    )
    assert FormNavigator._domain_has_prior_success("https://pls-solicitors.co.uk/jobs/foo") is False


def test_domain_with_successful_record_returns_true(monkeypatch, tmp_path):
    """A domain we've successfully filled before → trusted."""
    from jobpulse import form_experience_db as fe_mod
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    db_path = str(tmp_path / "fe.db")
    monkeypatch.setattr(fe_mod, "_DEFAULT_DB", db_path, raising=False)

    fe = fe_mod.FormExperienceDB(db_path=db_path)
    fe.record(
        domain="boards.greenhouse.io",
        platform="greenhouse",
        adapter="playwright",
        pages_filled=2,
        field_types=["text", "select"],
        screening_questions=[],
        time_seconds=120.0,
        success=True,
    )
    assert FormNavigator._domain_has_prior_success("https://boards.greenhouse.io/contentful/jobs/123") is True


def test_invalid_url_returns_false():
    """Malformed URL → safely return False (trust nothing)."""
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    assert FormNavigator._domain_has_prior_success("") is False
    assert FormNavigator._domain_has_prior_success("not-a-url") is False
