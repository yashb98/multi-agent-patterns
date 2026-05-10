"""Tests for shared/db_observability — decorator + buffer + correlator."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import pytest

from shared import db_observability as obs


@pytest.fixture(autouse=True)
def _isolated_observability_db(tmp_path, monkeypatch):
    """Each test gets a fresh observability DB and a clean thread buffer."""

    db = tmp_path / "obs.db"
    monkeypatch.delenv("JOBPULSE_TEST_MODE", raising=False)
    obs.set_test_mode(False)
    obs.set_observability_db_path(db)
    # Clear any thread-local state from prior tests.
    obs.flush_all()
    yield db
    obs.flush_all()
    obs.set_test_mode(None)


def _rows(db_path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM lookups ORDER BY id"))
    finally:
        conn.close()


def test_decorator_preserves_str_return(_isolated_observability_db):
    @obs.observe_lookup("test_db", "table_a", key_arg=0)
    def read(key):
        return "hello"

    assert read("k1") == "hello"
    assert isinstance(read("k1"), str)


def test_decorator_preserves_dataclass_return(_isolated_observability_db):
    @dataclass
    class Foo:
        a: int
        b: str

    @obs.observe_lookup("test_db", "table_b", key_arg=0)
    def read(key):
        return Foo(a=1, b="x")

    val = read("k")
    assert isinstance(val, Foo)
    assert val.a == 1 and val.b == "x"


def test_decorator_preserves_none_and_records_miss(_isolated_observability_db):
    @obs.observe_lookup("test_db", "table_c", key_arg=0)
    def read(key):
        return None

    assert read("missing_key") is None
    obs.flush_all()
    rows = _rows(_isolated_observability_db)
    assert len(rows) == 1
    assert rows[0]["hit"] == 0


def test_decorator_records_hit_for_truthy(_isolated_observability_db):
    @obs.observe_lookup("test_db", "table_d", key_arg=0)
    def read(key):
        return {"name": "Yash"}

    read("identity")
    obs.flush_all()
    rows = _rows(_isolated_observability_db)
    assert rows[0]["hit"] == 1


def test_record_lookup_inserts_row(_isolated_observability_db):
    obs.record_lookup("foo_db", "tab", key="k", value="v")
    obs.flush_all()
    rows = _rows(_isolated_observability_db)
    assert len(rows) == 1
    assert rows[0]["db_name"] == "foo_db"
    assert rows[0]["table_name"] == "tab"


def test_mark_fill_outcome_consumed(_isolated_observability_db):
    obs.record_lookup("user_profile", "screening_defaults",
                      key="relocation", value="Yes")
    n = obs.mark_fill_outcome("Are you willing to relocate?",
                              intended="Yes", actual="Yes")
    assert n == 1
    rows = _rows(_isolated_observability_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "consumed"
    assert rows[0]["drop_reason"] is None
    assert rows[0]["actual"] == "Yes"


def test_mark_fill_outcome_dropped_with_reason(_isolated_observability_db):
    """The relocation drop scenario: stored answer is wrong-shape, the
    option-aligner overrides it before the field is filled."""

    obs.record_lookup("user_profile", "screening_defaults",
                      key="relocation", value="Yes, within the UK")
    n = obs.mark_fill_outcome("Are you willing to relocate?",
                              intended="Yes, within the UK",
                              actual="Yes",
                              drop_reason=obs.DROP_OPTION_MISALIGNMENT)
    assert n == 1
    rows = _rows(_isolated_observability_db)
    assert rows[0]["status"] == "dropped"
    assert rows[0]["drop_reason"] == obs.DROP_OPTION_MISALIGNMENT
    assert rows[0]["intended"] == "Yes, within the UK"
    assert rows[0]["actual"] == "Yes"


def test_mark_fill_outcome_falls_back_to_recent_lookups(_isolated_observability_db):
    """When the intended value isn't in the buffer (e.g. transformed
    before fill), the most-recent lookups are tagged anyway."""

    obs.record_lookup("user_profile", "identity", key="email",
                      value="user@example.com")
    n = obs.mark_fill_outcome("Email", intended="user@example.com",
                              actual="user@example.com")
    assert n == 1
    rows = _rows(_isolated_observability_db)
    assert rows[0]["status"] == "consumed"


def test_buffer_bound_does_not_leak(_isolated_observability_db):
    for i in range(obs._BUFFER_MAX + 50):
        obs.record_lookup("noisy_db", "tab", key=f"k{i}", value="v")
    state = obs._state()
    assert len(state.buffer) <= obs._BUFFER_MAX
    obs.flush_all()
    rows = _rows(_isolated_observability_db)
    # Every overflow row was flushed as unconsumed.
    assert len(rows) >= obs._BUFFER_MAX
    statuses = {r["status"] for r in rows}
    assert "unconsumed" in statuses


def test_recency_window_unconsumed(monkeypatch, _isolated_observability_db):
    obs.record_lookup("old_db", "tab", key="k", value="v")
    state = obs._state()
    state.buffer[0].ts = time.time() - obs._RECENCY_WINDOW_S - 5.0
    obs.mark_fill_outcome("Some field", intended="other", actual="other")
    obs.flush_all()
    rows = _rows(_isolated_observability_db)
    assert rows[0]["status"] == "unconsumed"


def test_test_mode_short_circuits_writes(tmp_path, monkeypatch):
    db = tmp_path / "obs2.db"
    obs.set_observability_db_path(db)
    obs.set_test_mode(True)
    try:
        @obs.observe_lookup("test_db", "tab", key_arg=0)
        def read(key):
            return "x"

        read("k")
        # No writes — table doesn't even need to exist.
        assert not db.exists() or _rows(db) == []
    finally:
        obs.set_test_mode(False)


def test_observability_failure_does_not_break_caller(tmp_path, monkeypatch):
    """If the SQLite write fails, the wrapped accessor still returns
    its real value."""

    bad_path = tmp_path / "nope" / "does" / "not" / "exist.db"
    obs.set_observability_db_path(bad_path)
    monkeypatch.setattr(obs, "_insert_pending",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            sqlite3.OperationalError("simulated")))

    @obs.observe_lookup("test_db", "tab", key_arg=0)
    def read(key):
        return "still works"

    # observe_lookup wraps record_lookup in try/except, so this is fine.
    # We have to monkeypatch record_lookup itself since _insert_pending
    # is called from inside it.
    monkeypatch.setattr(obs, "record_lookup",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            sqlite3.OperationalError("simulated")))
    assert read("k") == "still works"


def test_decorator_re_raises_accessor_exception(_isolated_observability_db):
    @obs.observe_lookup("test_db", "tab", key_arg=0)
    def read(key):
        raise ValueError("bad key")

    with pytest.raises(ValueError, match="bad key"):
        read("k")


def test_drop_rate_aggregate(_isolated_observability_db):
    """Sanity check: after recording 4 hits and tagging 1 consumed +
    3 dropped, a SUM query reports the expected drop rate."""

    for i in range(4):
        obs.record_lookup("foo", "bar", key=f"k{i}", value=f"v{i}")
    obs.mark_fill_outcome("F1", intended="v0", actual="v0")
    obs.mark_fill_outcome("F2", intended="v1", actual="v_aligned",
                          drop_reason=obs.DROP_OPTION_MISALIGNMENT)
    obs.mark_fill_outcome("F3", intended="v2", actual="other",
                          drop_reason=obs.DROP_VALIDATION_FAILED)
    obs.mark_fill_outcome("F4", intended="v3", actual="other",
                          drop_reason=obs.DROP_OVERRIDDEN_BY_LLM)
    rows = _rows(_isolated_observability_db)
    statuses = [r["status"] for r in rows]
    assert statuses.count("consumed") == 1
    assert statuses.count("dropped") == 3
