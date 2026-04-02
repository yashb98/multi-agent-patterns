"""PaperStore — SQLite persistence layer for the papers pipeline."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobpulse.papers.models import FactCheckResult, RankedPaper, ReadingStats
from shared.logging_config import get_logger

logger = get_logger(__name__)

# New HF columns added in v2 — auto-migrated on open
_HF_COLUMNS: list[tuple[str, str]] = [
    ("source", "TEXT DEFAULT 'arxiv'"),
    ("hf_upvotes", "INTEGER"),
    ("linked_models", "TEXT"),
    ("linked_datasets", "TEXT"),
    ("model_card_summary", "TEXT"),
    ("fast_score", "REAL DEFAULT 0.0"),
    ("category_tag", "TEXT DEFAULT ''"),
]

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id            TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    authors             TEXT NOT NULL,
    abstract            TEXT NOT NULL,
    categories          TEXT NOT NULL,
    pdf_url             TEXT NOT NULL,
    arxiv_url           TEXT NOT NULL,
    published_at        TEXT NOT NULL,
    source              TEXT DEFAULT 'arxiv',
    hf_upvotes          INTEGER,
    linked_models       TEXT,
    linked_datasets     TEXT,
    model_card_summary  TEXT,
    impact_score        REAL DEFAULT 0.0,
    fast_score          REAL DEFAULT 0.0,
    impact_reason       TEXT DEFAULT '',
    category_tag        TEXT DEFAULT '',
    key_technique       TEXT DEFAULT '',
    practical_takeaway  TEXT DEFAULT '',
    summary             TEXT DEFAULT '',
    status              TEXT DEFAULT 'unread',
    digest_date         TEXT,
    discovered_at       TEXT NOT NULL,
    fact_check_score    REAL,
    fact_check_claims   INTEGER,
    fact_check_verified INTEGER,
    fact_check_issues   TEXT
)
"""


class PaperStore:
    """SQLite-backed store for RankedPaper objects.

    Thread-safe via WAL mode. Auto-migrates legacy schemas when new columns
    are added (e.g., HuggingFace fields).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            from jobpulse.config import DATA_DIR
            db_path = DATA_DIR / "papers.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """Create table and run migrations for any missing columns."""
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            conn.commit()
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add new columns to an existing table without data loss."""
        cursor = conn.execute("PRAGMA table_info(papers)")
        existing = {row["name"] for row in cursor.fetchall()}
        for col_name, col_def in _HF_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {col_def}")
                logger.info("Migration: added column '%s' to papers table", col_name)
        conn.commit()

    def _row_to_ranked_paper(self, row: sqlite3.Row) -> RankedPaper:
        """Convert a DB row into a RankedPaper model."""
        fact_check: FactCheckResult | None = None
        if row["fact_check_score"] is not None:
            issues_raw = row["fact_check_issues"]
            issues: list[str] = json.loads(issues_raw) if issues_raw else []
            fact_check = FactCheckResult(
                score=row["fact_check_score"],
                total_claims=row["fact_check_claims"] or 0,
                verified_count=row["fact_check_verified"] or 0,
                issues=issues,
            )

        linked_models_raw = row["linked_models"]
        linked_datasets_raw = row["linked_datasets"]

        return RankedPaper(
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors=json.loads(row["authors"]),
            abstract=row["abstract"],
            categories=json.loads(row["categories"]),
            pdf_url=row["pdf_url"],
            arxiv_url=row["arxiv_url"],
            published_at=row["published_at"],
            source=row["source"] or "arxiv",
            hf_upvotes=row["hf_upvotes"],
            linked_models=json.loads(linked_models_raw) if linked_models_raw else [],
            linked_datasets=json.loads(linked_datasets_raw) if linked_datasets_raw else [],
            model_card_summary=row["model_card_summary"],
            impact_score=row["impact_score"] or 0.0,
            fast_score=row["fast_score"] or 0.0,
            impact_reason=row["impact_reason"] or "",
            category_tag=row["category_tag"] or "",
            key_technique=row["key_technique"] or "",
            practical_takeaway=row["practical_takeaway"] or "",
            summary=row["summary"] or "",
            fact_check=fact_check,
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def store(self, papers: list[RankedPaper], digest_date: str) -> None:
        """Upsert a list of RankedPaper objects for a given digest date.

        Uses INSERT OR REPLACE so re-running the same digest is idempotent.
        fact_check.score is read from the FactCheckResult model (NOT accuracy_score).
        """
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            for paper in papers:
                fc = paper.fact_check
                conn.execute(
                    """
                    INSERT OR REPLACE INTO papers (
                        arxiv_id, title, authors, abstract, categories,
                        pdf_url, arxiv_url, published_at,
                        source, hf_upvotes, linked_models, linked_datasets,
                        model_card_summary,
                        impact_score, fast_score, impact_reason, category_tag,
                        key_technique, practical_takeaway, summary,
                        status, digest_date, discovered_at,
                        fact_check_score, fact_check_claims,
                        fact_check_verified, fact_check_issues
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        COALESCE((SELECT status FROM papers WHERE arxiv_id = ?), 'unread'),
                        ?, ?,
                        ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        paper.arxiv_id,
                        paper.title,
                        json.dumps(paper.authors),
                        paper.abstract,
                        json.dumps(paper.categories),
                        paper.pdf_url,
                        paper.arxiv_url,
                        paper.published_at,
                        paper.source,
                        paper.hf_upvotes,
                        json.dumps(paper.linked_models),
                        json.dumps(paper.linked_datasets),
                        paper.model_card_summary,
                        paper.impact_score,
                        paper.fast_score,
                        paper.impact_reason,
                        paper.category_tag,
                        paper.key_technique,
                        paper.practical_takeaway,
                        paper.summary,
                        # COALESCE subquery param
                        paper.arxiv_id,
                        digest_date,
                        now,
                        fc.score if fc else None,
                        fc.total_claims if fc else None,
                        fc.verified_count if fc else None,
                        json.dumps(fc.issues) if fc else None,
                    ),
                )
            conn.commit()

    def mark_read(self, arxiv_id: str) -> None:
        """Mark a paper as read. Silently ignores unknown IDs."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE papers SET status = 'read' WHERE arxiv_id = ?",
                (arxiv_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_by_index(self, digest_date: str, index: int) -> RankedPaper | None:
        """Return the Nth paper (1-based) for a given digest date, ordered by impact_score DESC."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM papers
                WHERE digest_date = ?
                ORDER BY impact_score DESC
                LIMIT 1 OFFSET ?
                """,
                (digest_date, index - 1),
            )
            row = cursor.fetchone()
        return self._row_to_ranked_paper(row) if row else None

    def get_by_arxiv_id(self, arxiv_id: str) -> RankedPaper | None:
        """Return a single paper by its arXiv ID."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM papers WHERE arxiv_id = ?",
                (arxiv_id,),
            )
            row = cursor.fetchone()
        return self._row_to_ranked_paper(row) if row else None

    def get_week(self, last_n_days: int = 7) -> list[RankedPaper]:
        """Return all papers discovered in the last N days, ordered by impact_score DESC."""
        cutoff = (datetime.now(UTC) - timedelta(days=last_n_days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM papers
                WHERE discovered_at >= ?
                ORDER BY impact_score DESC
                """,
                (cutoff,),
            )
            rows = cursor.fetchall()
        return [self._row_to_ranked_paper(r) for r in rows]

    def search(self, query: str) -> list[RankedPaper]:
        """Full-text search over title and abstract (case-insensitive LIKE)."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM papers
                WHERE title LIKE ? OR abstract LIKE ?
                ORDER BY impact_score DESC
                """,
                (pattern, pattern),
            )
            rows = cursor.fetchall()
        return [self._row_to_ranked_paper(r) for r in rows]

    def get_stats(self) -> ReadingStats:
        """Return aggregate reading statistics."""
        cutoff_week = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            read = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE status = 'read'"
            ).fetchone()[0]
            this_week = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE discovered_at >= ?",
                (cutoff_week,),
            ).fetchone()[0]
            # with_models: papers that have at least one linked model
            with_models = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE linked_models IS NOT NULL AND linked_models != '[]'"
            ).fetchone()[0]

        return ReadingStats(
            total=total,
            read=read,
            unread=total - read,
            this_week=this_week,
            with_models=with_models,
        )
