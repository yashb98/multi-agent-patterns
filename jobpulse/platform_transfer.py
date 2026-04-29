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

    # ------------------------------------------------------------------
    # Data loaders
    # ------------------------------------------------------------------

    def _load_form_experience_data(self) -> dict[str, dict]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM form_experience WHERE success = 1").fetchall()
        return {r["domain"]: dict(r) for r in rows}

    def _load_timing_data(self) -> dict[str, dict]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM page_timings").fetchall()
        return {r["domain"]: dict(r) for r in rows}

    def _load_container_data(self) -> dict[str, str]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT domain, selector FROM container_selectors").fetchall()
        return {r[0]: r[1] for r in rows}

    def _load_fill_techniques(self) -> dict[str, set[str]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT domain, technique FROM fill_techniques WHERE success = 1").fetchall()
        result: dict[str, set[str]] = {}
        for domain, technique in rows:
            result.setdefault(domain, set()).add(technique)
        return result

    def _load_failure_data(self) -> dict[str, dict[str, int]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT domain, failure_type, COUNT(*) as cnt FROM form_failure_reasons GROUP BY domain, failure_type"
            ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for domain, ftype, cnt in rows:
            result.setdefault(domain, {})[ftype] = cnt
        return result

    def _load_correction_data(self) -> dict[str, dict[str, int]]:
        corrections_db = str(DATA_DIR / "field_corrections.db")
        try:
            with sqlite3.connect(corrections_db) as conn:
                rows = conn.execute(
                    "SELECT domain, field_label, COUNT(*) as cnt FROM field_corrections GROUP BY domain, field_label"
                ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return {}
        result: dict[str, dict[str, int]] = {}
        for domain, label, cnt in rows:
            result.setdefault(domain, {})[label] = cnt
        return result

    def _load_navigation_data(self) -> dict[str, list[str]]:
        nav_db = str(DATA_DIR / "navigation_learning.db")
        try:
            with sqlite3.connect(nav_db) as conn:
                rows = conn.execute("SELECT domain, steps FROM sequences WHERE success = 1").fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return {}
        result: dict[str, list[str]] = {}
        for domain, steps_json in rows:
            try:
                steps = json.loads(steps_json)
                result[domain] = [s.get("action", "") for s in steps]
            except (json.JSONDecodeError, AttributeError):
                pass
        return result

    # ------------------------------------------------------------------
    # Similarity matrix recomputation
    # ------------------------------------------------------------------

    def recompute_similarity_matrix(self, trigger_domain: str) -> int:
        fe_data = self._load_form_experience_data()
        timing_data = self._load_timing_data()
        container_data = self._load_container_data()
        technique_data = self._load_fill_techniques()
        failure_data = self._load_failure_data()
        correction_data = self._load_correction_data()
        nav_data = self._load_navigation_data()

        all_domains = set(fe_data.keys())
        if trigger_domain not in all_domains:
            return 0

        now = datetime.now(UTC).isoformat()
        written = 0

        with sqlite3.connect(self._db_path) as conn:
            for other_domain in all_domains:
                if other_domain == trigger_domain:
                    continue
                pairs = self._compute_pair_signals(
                    trigger_domain, other_domain,
                    fe_data, timing_data, container_data,
                    technique_data, failure_data, correction_data, nav_data,
                )
                for signal_type, similarity, sample_count in pairs:
                    if sample_count < 2:
                        continue
                    # Store both directions
                    conn.execute(
                        """INSERT INTO platform_similarity
                           (domain_a, domain_b, signal_type, similarity, sample_count, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(domain_a, domain_b, signal_type) DO UPDATE SET
                               similarity = excluded.similarity, sample_count = excluded.sample_count, updated_at = excluded.updated_at""",
                        (trigger_domain, other_domain, signal_type, similarity, sample_count, now),
                    )
                    conn.execute(
                        """INSERT INTO platform_similarity
                           (domain_a, domain_b, signal_type, similarity, sample_count, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(domain_a, domain_b, signal_type) DO UPDATE SET
                               similarity = excluded.similarity, sample_count = excluded.sample_count, updated_at = excluded.updated_at""",
                        (other_domain, trigger_domain, signal_type, similarity, sample_count, now),
                    )
                    written += 2

        logger.info("transfer: recomputed similarity for %s — %d rows across %d peers", trigger_domain, written, len(all_domains) - 1)
        return written

    def _compute_pair_signals(self, domain_a, domain_b, fe_data, timing_data, container_data, technique_data, failure_data, correction_data, nav_data) -> list[tuple[str, float, int]]:
        results: list[tuple[str, float, int]] = []
        fe_a, fe_b = fe_data.get(domain_a), fe_data.get(domain_b)

        if fe_a and fe_b:
            def _parse_field_types(ft_raw) -> dict[str, int]:
                from collections import Counter
                if isinstance(ft_raw, str):
                    ft_list = json.loads(ft_raw)
                else:
                    ft_list = ft_raw
                return dict(Counter(ft_list))
            ft_a = _parse_field_types(fe_a.get("field_types", "[]"))
            ft_b = _parse_field_types(fe_b.get("field_types", "[]"))
            count = (fe_a.get("apply_count", 1) or 1) + (fe_b.get("apply_count", 1) or 1)
            results.append(("field_types", self._cosine_similarity(ft_a, ft_b), count))

            pages_a = fe_a.get("pages_filled", 0) or 0
            pages_b = fe_b.get("pages_filled", 0) or 0
            if pages_a > 0 or pages_b > 0:
                results.append(("page_count", self._normalized_page_diff(pages_a, pages_b), count))

        t_a, t_b = timing_data.get(domain_a), timing_data.get(domain_b)
        if t_a and t_b:
            vec_a = {"hydration": t_a["avg_hydration_ms"], "fill": t_a["avg_fill_ms"], "transition": t_a["avg_transition_ms"]}
            vec_b = {"hydration": t_b["avg_hydration_ms"], "fill": t_b["avg_fill_ms"], "transition": t_b["avg_transition_ms"]}
            samples = (t_a.get("sample_count", 1) or 1) + (t_b.get("sample_count", 1) or 1)
            results.append(("timing_profile", self._cosine_similarity(vec_a, vec_b), samples))

        tech_a, tech_b = technique_data.get(domain_a), technique_data.get(domain_b)
        if tech_a and tech_b:
            results.append(("fill_techniques", self._jaccard_index(tech_a, tech_b), len(tech_a) + len(tech_b)))

        fail_a, fail_b = failure_data.get(domain_a), failure_data.get(domain_b)
        if fail_a and fail_b:
            results.append(("failure_patterns", self._cosine_similarity(fail_a, fail_b), sum(fail_a.values()) + sum(fail_b.values())))

        corr_a, corr_b = correction_data.get(domain_a), correction_data.get(domain_b)
        if corr_a and corr_b:
            results.append(("correction_rates", self._cosine_similarity(corr_a, corr_b), sum(corr_a.values()) + sum(corr_b.values())))

        nav_a, nav_b = nav_data.get(domain_a), nav_data.get(domain_b)
        if nav_a and nav_b:
            results.append(("navigation_flow", self._normalized_levenshtein(nav_a, nav_b), len(nav_a) + len(nav_b)))

        cont_a, cont_b = container_data.get(domain_a), container_data.get(domain_b)
        if cont_a and cont_b:
            results.append(("container_selectors", self._token_overlap(cont_a, cont_b), 2))

        return results
