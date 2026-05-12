"""Wiring test for the `isolate_optimization_db` autouse fixture.

S6 T-1 / S10 T-10.1: tests were leaking `cognitive_outcomes` rows with
`agent_name='test_agent'` into `data/optimization.db` because
`get_optimization_engine()` returned a cached production-path singleton.

This test exercises both halves of the fix:

  1. **Positive**: a real `record_cognitive_outcome(...)` call inside a
     test writes to the tmp DB the fixture sets up. (If the fixture
     failed, the call would either error out or silently no-op.)

  2. **Negative**: the production `data/optimization.db` row count for
     `agent_name='test_agent'` does not change across the test.

The two assertions together prove both that the fixture works AND that
the production DB is protected.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from shared.optimization import (
    OptimizationEngine,
    get_optimization_engine,
    reset_optimization_engine,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _production_db_path() -> Path:
    """Return the production `data/optimization.db` path, irrespective of
    whatever the autouse fixture has set `OPTIMIZATION_DB` to."""
    return _root() / "data" / "optimization.db"


def _count_test_agent_rows(db: Path) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(str(db)) as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM cognitive_outcomes WHERE agent_name = 'test_agent'"
        )
        return int(cur.fetchone()[0])


def test_optimization_engine_writes_go_to_tmp_db_not_production() -> None:
    prod_db = _production_db_path()
    prod_before = _count_test_agent_rows(prod_db)

    # The autouse fixture has set OPTIMIZATION_DB to a tmp path.
    tmp_db = Path(os.environ["OPTIMIZATION_DB"])
    assert tmp_db != prod_db, (
        f"OPTIMIZATION_DB env var ({tmp_db}) should not point at the "
        f"production DB ({prod_db}); the autouse fixture is not active."
    )

    # Force a real write through the production code path.
    engine = get_optimization_engine()
    assert isinstance(engine, OptimizationEngine)
    engine._tracker.record_cognitive_outcome(
        domain="test_domain",
        agent_name="test_agent",
        level=0,
        success=True,
        escalated=False,
    )

    # Positive: the tmp DB now has the row.
    tmp_count = _count_test_agent_rows(tmp_db)
    assert tmp_count == 1, (
        f"Expected 1 `agent_name='test_agent'` row in tmp DB ({tmp_db}); "
        f"found {tmp_count}. The fixture redirected the env var but the "
        f"engine still wrote elsewhere."
    )

    # Negative: production DB row count is unchanged.
    prod_after = _count_test_agent_rows(prod_db)
    assert prod_after == prod_before, (
        f"Production `data/optimization.db` test_agent row count changed "
        f"from {prod_before} to {prod_after} during this test. The fixture "
        f"didn't isolate the singleton; this is the S6 T-1 / S10 T-10.1 "
        f"leak the audit flagged."
    )


def test_reset_optimization_engine_drops_singleton() -> None:
    """`reset_optimization_engine()` must clear the cache so the next call
    rebuilds with the current environment."""
    from shared.optimization import _engine as _opt_engine

    first = get_optimization_engine()
    assert _opt_engine._shared_engine is first

    reset_optimization_engine()
    assert _opt_engine._shared_engine is None

    second = get_optimization_engine()
    assert second is not first, (
        "After `reset_optimization_engine()`, the next call should rebuild a "
        "fresh engine. Got back the same instance — reset is a no-op."
    )


def test_optimization_db_env_var_is_honoured(tmp_path, monkeypatch) -> None:
    """`_default_db_path()` must read `OPTIMIZATION_DB`, not silently
    return the hardcoded `data/optimization.db` path."""
    from shared.optimization._engine import _default_db_path

    custom = tmp_path / "custom_opt.db"
    monkeypatch.setenv("OPTIMIZATION_DB", str(custom))
    assert _default_db_path() == str(custom), (
        f"_default_db_path() returned {_default_db_path()!r}, ignoring "
        f"OPTIMIZATION_DB={custom!r}. The env-var override is broken."
    )
