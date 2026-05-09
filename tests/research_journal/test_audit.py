import sqlite3
import pytest
from pathlib import Path
from research_journal.audit import compute_hallucination_rate, run_weekly_audit


def test_hallucination_rate_basic(tmp_path: Path):
    db = tmp_path / "papers.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE papers (arxiv_id TEXT, summary_long TEXT, verification TEXT, digest_date TEXT);
      INSERT INTO papers VALUES ('a', '...', '{"claims_grounded": true}', '2026-05-05');
      INSERT INTO papers VALUES ('b', '...', '{"claims_grounded": false}', '2026-05-05');
      INSERT INTO papers VALUES ('c', '...', '{"claims_grounded": true}', '2026-05-05');
    """)
    conn.commit()
    conn.close()
    rate = compute_hallucination_rate(db_path=db, days=7)
    assert rate == pytest.approx(1 / 3)


def test_run_weekly_audit_emits_signal(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("research_journal.audit.compute_hallucination_rate", lambda **kw: 0.05)
    monkeypatch.setattr("research_journal.audit.compute_coverage_gap", lambda **kw: 0.10)
    signals = []
    monkeypatch.setattr(
        "research_journal.audit._emit_signal",
        lambda **kw: signals.append(kw),
    )
    monkeypatch.setattr("research_journal.audit._alert_telegram", lambda msg: None)
    run_weekly_audit(db_path=tmp_path / "papers.db")
    assert len(signals) >= 1  # rate=0.05 > threshold=0.02 fires
