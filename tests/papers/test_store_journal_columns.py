import sqlite3
from pathlib import Path
from jobpulse.papers.store import PaperStore


def test_journal_columns_created(tmp_path: Path):
    db = tmp_path / "papers.db"
    store = PaperStore(db_path=db)
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(papers)")}
    for required in ("domain_tag", "verification", "summary_long", "rank_reason"):
        assert required in cols, f"missing column {required}"


def test_legacy_db_migrated(tmp_path: Path):
    """A pre-journal DB (only legacy columns) gets the new columns added on open."""
    db = tmp_path / "papers.db"
    legacy = sqlite3.connect(db)
    legacy.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                   "authors TEXT NOT NULL, abstract TEXT NOT NULL, categories TEXT NOT NULL, "
                   "pdf_url TEXT NOT NULL, arxiv_url TEXT NOT NULL, published_at TEXT NOT NULL, "
                   "discovered_at TEXT NOT NULL)")
    legacy.commit()
    legacy.close()
    PaperStore(db_path=db)  # should ALTER on open
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(papers)")}
    assert "domain_tag" in cols
    assert "summary_long" in cols
