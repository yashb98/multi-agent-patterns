"""Tests for scripts/db_observability_summary.py.

The summary script aggregates per-(db, table) lookup outcomes, prints a
table, and on threshold breach emits a signal + appends a mistakes.md
entry. We exercise both the happy path (no breach) and the breach path.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "db_observability_summary.py"


@pytest.fixture
def summary_module():
    spec = importlib.util.spec_from_file_location("db_obs_summary", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_obs_summary"] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop("db_obs_summary", None)


@pytest.fixture
def populated_db(tmp_path):
    db = tmp_path / "obs.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE lookups (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            db_name       TEXT NOT NULL,
            table_name    TEXT NOT NULL,
            key_hash      TEXT NOT NULL,
            hit           INTEGER NOT NULL,
            value_repr    TEXT,
            latency_ms    REAL,
            ts            REAL NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            drop_reason   TEXT,
            field_label   TEXT,
            intended      TEXT,
            actual        TEXT,
            consumed_ts   REAL
        );
        """
    )
    now = time.time()

    def insert(db_name, table, status, drop_reason=None, field_label=None,
               intended=None, actual=None, value_repr="'val'"):
        conn.execute(
            """INSERT INTO lookups
                (db_name, table_name, key_hash, hit, value_repr,
                 latency_ms, ts, status, drop_reason, field_label,
                 intended, actual, consumed_ts)
                VALUES (?, ?, 'h', 1, ?, 1.0, ?, ?, ?, ?, ?, ?, ?)""",
            (db_name, table, value_repr, now, status, drop_reason,
             field_label, intended, actual, now),
        )

    # Healthy DB: 5 consumed, 0 dropped (drop rate 0 %)
    for _ in range(5):
        insert("good_db", "good_table", "consumed", field_label="OK",
               intended="x", actual="x")

    # Bad DB: 2 consumed, 8 dropped → drop rate 80 %
    for _ in range(2):
        insert("user_profile", "screening_defaults", "consumed",
               field_label="Other", intended="No", actual="No")
    for _ in range(8):
        insert("user_profile", "screening_defaults", "dropped",
               drop_reason="option_misalignment",
               field_label="Are you willing to relocate?",
               intended="Yes, within the UK", actual="Yes",
               value_repr="'Yes, within the UK'")

    # Sparse DB: only 1 dropped (below min_volume) → not eligible
    insert("rare_db", "rare_table", "dropped", drop_reason="x")

    conn.commit()
    conn.close()
    return db


def test_query_summary_aggregates_correctly(summary_module, populated_db):
    rows = summary_module.query_summary(populated_db, window_days=7)
    by_pair = {(r.db_name, r.table_name): r for r in rows}
    bad = by_pair[("user_profile", "screening_defaults")]
    assert bad.dropped == 8
    assert bad.consumed == 2
    assert bad.used == 10
    assert pytest.approx(bad.drop_rate, abs=1e-9) == 0.8
    assert bad.top_drop_reason == "option_misalignment"
    assert bad.top_drop_count == 8
    assert len(bad.sample_dropped) == 5

    good = by_pair[("good_db", "good_table")]
    assert good.dropped == 0
    assert good.drop_rate == 0.0


def test_breach_emits_signal_and_writes_mistakes(
    populated_db, tmp_path, monkeypatch, summary_module,
):
    mistakes_path = tmp_path / "mistakes.md"
    captured = []

    class FakeEngine:
        def emit(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(
        "shared.optimization.get_optimization_engine",
        lambda: FakeEngine(),
    )
    monkeypatch.setenv("DB_OBS_ALERT_TELEGRAM", "0")

    rc = _run_main(
        summary_module,
        ["--db-path", str(populated_db),
         "--mistakes-path", str(mistakes_path),
         "--threshold", "0.5",
         "--min-volume", "5",
         "--window-days", "7"],
        monkeypatch,
    )

    assert rc == 1, "Expected non-zero exit when threshold is breached"

    assert len(captured) == 1, "Expected one failure signal for the breached DB"
    sig = captured[0]
    assert sig["signal_type"] == "failure"
    assert sig["domain"] == "user_profile.screening_defaults"
    assert sig["payload"]["top_drop_reason"] == "option_misalignment"

    text = mistakes_path.read_text(encoding="utf-8")
    assert "user_profile.screening_defaults" in text
    assert "option_misalignment" in text
    assert "Yes, within the UK" in text


def test_no_breach_no_alert(populated_db, tmp_path, monkeypatch, summary_module):
    mistakes_path = tmp_path / "mistakes.md"
    captured = []

    class FakeEngine:
        def emit(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(
        "shared.optimization.get_optimization_engine",
        lambda: FakeEngine(),
    )

    rc = _run_main(
        summary_module,
        ["--db-path", str(populated_db),
         "--mistakes-path", str(mistakes_path),
         "--threshold", "0.95",
         "--min-volume", "5",
         "--window-days", "7"],
        monkeypatch,
    )
    assert rc == 0
    assert captured == []
    assert not mistakes_path.exists()


def test_no_signal_flag_suppresses_emit(
    populated_db, tmp_path, monkeypatch, summary_module,
):
    mistakes_path = tmp_path / "mistakes.md"
    captured = []

    class FakeEngine:
        def emit(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(
        "shared.optimization.get_optimization_engine",
        lambda: FakeEngine(),
    )

    rc = _run_main(
        summary_module,
        ["--db-path", str(populated_db),
         "--mistakes-path", str(mistakes_path),
         "--threshold", "0.5",
         "--min-volume", "5",
         "--no-signal", "--no-mistakes"],
        monkeypatch,
    )
    assert rc == 1
    assert captured == []
    assert not mistakes_path.exists()


def _run_main(mod, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["db_observability_summary"] + argv)
    return mod.main()
