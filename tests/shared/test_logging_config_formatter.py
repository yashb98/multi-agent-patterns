"""Tests for structured logging formatter behavior."""

from __future__ import annotations

import logging

from shared.logging_config import (
    StructuredFormatter,
    clear_trajectory_id,
    set_run_id,
    set_trajectory_id,
)


def _make_record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logging",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_structured_formatter_ignores_formatter_injected_fields():
    set_run_id("run_test_logging")
    clear_trajectory_id()

    formatter = StructuredFormatter("%(message)s%(structured)s")
    record = _make_record("plain")
    # Simulate a previous handler mutating the record.
    record.message = "plain"
    record.asctime = "2026-04-22 23:59:59"

    output = formatter.format(record)
    assert output == "plain"


def test_structured_formatter_appends_true_extra_fields():
    set_run_id("run_test_logging")
    clear_trajectory_id()

    formatter = StructuredFormatter("%(message)s%(structured)s")
    record = _make_record("with extras")
    record.phase = "phase2"
    record.step = 3

    output = formatter.format(record)
    assert output.startswith("with extras | ")
    assert '"phase": "phase2"' in output
    assert '"step": 3' in output


def test_structured_formatter_includes_trajectory_in_format_fields():
    set_run_id("run_test_logging")
    set_trajectory_id("traj_phase4")

    formatter = StructuredFormatter("[%(run_id)s][%(trajectory_id)s] %(message)s%(structured)s")
    record = _make_record("phase4")

    output = formatter.format(record)
    assert output.startswith("[run_test_logging][traj_phase4] phase4")

    clear_trajectory_id()

