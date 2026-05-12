"""Live integration test — requires real API keys.

Run with:  pytest tests/research_journal/test_pipeline_live.py -v -m live

Requires env vars: TOGETHER_API_KEY, GITHUB_TOKEN, NOTION_API_KEY (optional — Notion call is mocked).
"""
import sqlite3
from pathlib import Path
import pytest


@pytest.mark.live
@pytest.mark.asyncio
async def test_daily_journal_live_5_papers(tmp_path: Path, monkeypatch):
    """Runs the real daily_journal against live arXiv. Caps to 5 papers via classify_domain stub."""
    from research_journal.pipeline import JournalPipeline

    db = tmp_path / "papers.db"
    pipeline = JournalPipeline(db_path=db)

    # Cap volume by short-circuiting domain classifier to "core" for first 5 only
    cnt = {"n": 0}
    real_classify = __import__("research_journal.domain_filter", fromlist=["classify_domain"]).classify_domain
    def capped(p):
        if cnt["n"] >= 5:
            return ("out", 0.0, "capped for live test")
        cnt["n"] += 1
        return real_classify(p)
    monkeypatch.setattr("research_journal.pipeline.classify_domain", capped)

    # Don't actually post to Notion / Telegram in live test
    monkeypatch.setattr("research_journal.pipeline.publish_journal_to_notion", lambda **kw: ["fake-id"])
    monkeypatch.setattr("research_journal.pipeline.send_telegram_message", lambda msg: None)

    result = await pipeline.daily_journal(target_volume_max=5)

    # Assertions: DB rows exist with non-empty summary_long + verification
    rows = sqlite3.connect(db).execute(
        "SELECT arxiv_id, summary_long, verification, domain_tag FROM papers WHERE summary_long != ''"
    ).fetchall()
    assert len(rows) >= 1
    for arxiv_id, summary, verification, domain_tag in rows:
        assert "## TL;DR" in summary
        assert "## Method" in summary
        assert verification.startswith("{")  # JSON
        assert domain_tag in ("core", "tangent")
