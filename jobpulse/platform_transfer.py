"""Cross-domain transfer learning engine.

Computes similarity between ATS domains using 8 learned signals,
selects donors via Thompson Sampling (Beta distributions), and
records transfer outcomes to improve future selections.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TypedDict

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "form_experience.db")

SIGNAL_TYPES = (
    "field_types",
    "page_count",
    "timing_profile",
    "fill_techniques",
    "failure_patterns",
    "correction_rates",
    "navigation_flow",
    "container_selectors",
)


class TransferResult(TypedDict):
    donor_domain: str
    signal_type: str
    similarity: float
    confidence: int
    _transfer: bool


class PlatformTransferEngine:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS platform_similarity (
                    domain_a TEXT NOT NULL,
                    domain_b TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (domain_a, domain_b, signal_type)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transfer_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_domain TEXT NOT NULL,
                    donor_domain TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    alpha REAL NOT NULL DEFAULT 1.0,
                    beta_param REAL NOT NULL DEFAULT 1.0,
                    transfer_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    last_outcome TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (target_domain, donor_domain, signal_type)
                )
            """)
