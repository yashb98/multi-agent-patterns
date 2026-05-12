"""Tests for JobDB.record_gate_decision and gate_effectiveness table.

Verifies the low-level writer mechanics: INSERT, ON CONFLICT upsert,
and read-back helpers. Uses tmp_path so production data/*.db is never touched.
"""

import pytest

from jobpulse.job_db import JobDB


@pytest.fixture
def jdb(tmp_path):
    return JobDB(db_path=tmp_path / "applications.db")


def test_record_creates_row(jdb):
    """First call inserts a row with count=1."""
    jdb.record_gate_decision("g1", "pass", "OK")
    rows = jdb.get_gate_effectiveness("g1")
    assert len(rows) == 1
    assert rows[0]["gate_name"] == "g1"
    assert rows[0]["decision"] == "pass"
    assert rows[0]["count"] == 1


def test_record_increments_on_conflict(jdb):
    """Repeated calls for the same (gate, decision, outcome) increment count."""
    for _ in range(3):
        jdb.record_gate_decision("g1", "pass", "OK")
    rows = jdb.get_gate_effectiveness("g1")
    assert rows[0]["count"] == 3


def test_different_decisions_tracked_separately(jdb):
    """Distinct decisions for the same gate produce separate rows."""
    jdb.record_gate_decision("jd_quality", "pass", "OK")
    jdb.record_gate_decision("jd_quality", "fail", "Too short")
    rows = jdb.get_gate_effectiveness("jd_quality")
    decisions = {r["decision"] for r in rows}
    assert decisions == {"pass", "fail"}


def test_get_all_gate_effectiveness_groups_by_gate(jdb):
    """get_all_gate_effectiveness returns rows grouped by gate_name."""
    jdb.record_gate_decision("gate_a", "pass", "OK")
    jdb.record_gate_decision("gate_b", "fail", "Bad")
    result = jdb.get_all_gate_effectiveness()
    assert "gate_a" in result
    assert "gate_b" in result
    assert result["gate_a"][0]["decision"] == "pass"
    assert result["gate_b"][0]["decision"] == "fail"


def test_record_does_not_raise_on_long_strings(jdb):
    """Writer accepts long reason strings without truncation errors."""
    jdb.record_gate_decision("jd_quality", "fail", "x" * 500)
    rows = jdb.get_gate_effectiveness("jd_quality")
    assert rows[0]["final_outcome"] == "x" * 500
