"""Wiring test: fill_and_advance is terminal so file uploads run.

Live regression on Revolut welovealfa.com 2026-05-05: the apply page is
just `Upload CV` + drop zone + Continue (no scanned form fields). The
reasoner correctly classified it as application_form with action
`fill_and_advance`, but `fill_and_advance` was NOT in TERMINAL_ACTIONS,
so the navigator handled it inline via NavigationActionExecutor — which
only executes the reasoner's pre-planned `field_fills` (text-only). The
plan was empty (no text fields), the executor did nothing, the verifier
saw no change → reflection loop → wait_human → abort. CV never reached
upload_files() because that lives inside NativeFormFiller.fill_form()
which only runs when navigator hands back a TERMINAL action with
page_type=APPLICATION_FORM.

Fix: treat fill_and_advance the same as fill_form — both mean "this is
an application form, hand off to NativeFormFiller pipeline which scans,
fills text, AND uploads files".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_fill_and_advance_is_terminal():
    """fill_and_advance must terminate the navigator loop so the
    orchestrator's NativeFormFiller takes over."""
    from jobpulse.application_orchestrator_pkg._navigator import TERMINAL_ACTIONS
    assert "fill_and_advance" in TERMINAL_ACTIONS
    assert "fill_form" in TERMINAL_ACTIONS  # regression check


def test_make_result_maps_fill_and_advance_to_application_form():
    """When fill_and_advance terminates, _make_result must return
    page_type=APPLICATION_FORM so the orchestrator dispatches to
    NativeFormFiller (which calls upload_files)."""
    from jobpulse.application_orchestrator_pkg._navigator import (
        FormNavigator,
    )
    from jobpulse.form_models import PageType

    ctx = SimpleNamespace(
        planned_action=SimpleNamespace(action="fill_and_advance",
                                       page_type="application_form",
                                       page_understanding=""),
        snapshot={"url": "https://welovealfa.com/.../apply/upload-cv"},
    )
    result = FormNavigator._make_result(ctx)
    assert result["page_type"] == PageType.APPLICATION_FORM
    assert result["snapshot"] is ctx.snapshot
    # Regression: not marked expired
    assert "expired" not in result


def test_make_result_still_handles_done_and_other():
    from jobpulse.application_orchestrator_pkg._navigator import (
        FormNavigator,
    )
    from jobpulse.form_models import PageType

    done_ctx = SimpleNamespace(
        planned_action=SimpleNamespace(action="done",
                                       page_type="confirmation",
                                       page_understanding=""),
        snapshot={"url": "https://example.com/applied"},
    )
    assert FormNavigator._make_result(done_ctx)["page_type"] == PageType.CONFIRMATION

    other_ctx = SimpleNamespace(
        planned_action=SimpleNamespace(action="abort",
                                       page_type="unknown",
                                       page_understanding=""),
        snapshot={},
    )
    assert FormNavigator._make_result(other_ctx)["page_type"] == PageType.UNKNOWN
