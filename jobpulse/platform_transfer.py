"""Cross-domain transfer learning engine.

Computes similarity between ATS domains using 8 learned signals,
selects donors via Thompson Sampling (Beta distributions), and
records transfer outcomes to improve future selections.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TypedDict

from shared.logging_config import get_logger
from shared.optimization import get_optimization_engine

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

    # ------------------------------------------------------------------
    # Similarity metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _cosine_similarity(vec_a: dict[str, int | float], vec_b: dict[str, int | float]) -> float:
        if not vec_a or not vec_b:
            return 0.0
        keys = set(vec_a) | set(vec_b)
        dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in keys)
        mag_a = sum(v ** 2 for v in vec_a.values()) ** 0.5
        mag_b = sum(v ** 2 for v in vec_b.values()) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _jaccard_index(set_a: set, set_b: set) -> float:
        if not set_a and not set_b:
            return 0.0
        union = set_a | set_b
        return len(set_a & set_b) / len(union)

    @staticmethod
    def _normalized_page_diff(pages_a: int, pages_b: int) -> float:
        if pages_a == 0 and pages_b == 0:
            return 0.0
        return 1.0 - abs(pages_a - pages_b) / max(pages_a, pages_b)

    @staticmethod
    def _normalized_levenshtein(seq_a: list[str], seq_b: list[str]) -> float:
        if not seq_a and not seq_b:
            return 0.0
        n, m = len(seq_a), len(seq_b)
        dp = list(range(m + 1))
        for i in range(1, n + 1):
            prev, dp[0] = dp[0], i
            for j in range(1, m + 1):
                cost = 0 if seq_a[i - 1] == seq_b[j - 1] else 1
                prev, dp[j] = dp[j], min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
        distance = dp[m]
        max_len = max(n, m)
        return 1.0 - distance / max_len

    @staticmethod
    def _token_overlap(selector_a: str, selector_b: str) -> float:
        import re
        tokens_a = set(re.split(r"[\s.#\[\]=>\+~,()]+", selector_a)) - {""}
        tokens_b = set(re.split(r"[\s.#\[\]=>\+~,()]+", selector_b)) - {""}
        if not tokens_a and not tokens_b:
            return 0.0
        union = tokens_a | tokens_b
        return len(tokens_a & tokens_b) / len(union)
