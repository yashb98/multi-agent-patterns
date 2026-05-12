"""Verified-fill cache — skip re-filling fields that already verified OK.

Vision verification has confirmed certain (domain, label, value) tuples
match what the form renders. There's no point re-issuing the fill action
on a subsequent dry-run if the DOM still shows the cached value. This
cache lets the filler short-circuit those fills.

Writes happen at the verifier — only on ``tier_reached == "passed"`` (the
strong-evidence verdict tier). ``mismatch_detected`` invalidates any
existing row for the label so the next run re-attempts the fill.

Reads happen at the filler — before issuing the fill, ``_fill_by_label``
consults the cache; if there's a hit AND the DOM still shows the cached
value, the fill is skipped entirely.

TODO: page_hash. The current key ``(domain, label_norm, verified_value)``
collides across pages on the same domain that share a label (e.g. a
multi-page Workday with "First name" on both an account-creation and a
profile page). Cross-page collision is acceptable for v1 — the filler's
DOM re-check on lookup catches drift — but a ``page_hash`` column would
remove the false-positive risk entirely.
"""

from __future__ import annotations

import os
import sqlite3
import time

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "verified_fills.db")
_DEFAULT_TTL_DAYS = 30


def _normalize_label(label: str) -> str:
    """Stripped-required, lowercased label — the cache lookup key."""
    if not label:
        return ""
    # Mirror ``_field_crop._strip_required_marker`` without the regex
    # import here; cheap string-level fallback that also strips trailing
    # whitespace and asterisks.
    stripped = label.strip()
    while stripped.endswith(("*", "(required)", "(Required)", "required")):
        if stripped.endswith("*"):
            stripped = stripped[:-1].rstrip()
        elif stripped.endswith(("(required)", "(Required)")):
            stripped = stripped[:-len("(required)")].rstrip()
        elif stripped.endswith("required"):
            stripped = stripped[:-len("required")].rstrip()
    return stripped.lower()


class VerifiedFillsDB:
    """SQLite-backed cache of fills that have already verified OK."""

    def __init__(self, db_path: str | None = None) -> None:
        # Resolve order: explicit arg → env override (tests) → default.
        # The env hook keeps the verifier's helper (which constructs
        # ``VerifiedFillsDB()`` with no arg) testable without monkey-
        # patching internals.
        self.db_path = (
            db_path
            or os.environ.get("VERIFIED_FILLS_DB_PATH")
            or _DEFAULT_DB_PATH
        )
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS verified_fills (
                    domain         TEXT NOT NULL,
                    label_norm     TEXT NOT NULL,
                    field_type     TEXT NOT NULL,
                    verified_value TEXT NOT NULL,
                    ts             INTEGER NOT NULL,
                    method         TEXT NOT NULL,
                    PRIMARY KEY (domain, label_norm, verified_value)
                )"""
            )
            conn.commit()

    def lookup(
        self, domain: str, label: str, value: str,
    ) -> dict | None:
        """Return the cached row for this (domain, label, value) or None.

        Returns a dict with ``field_type``, ``verified_value``, ``ts``,
        ``method`` on hit. Caller is responsible for the subsequent DOM
        re-check before trusting the hit.
        """
        if not domain or not label or value is None:
            return None
        label_norm = _normalize_label(label)
        verified_value = str(value).strip()
        if not verified_value:
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT field_type, verified_value, ts, method"
                    " FROM verified_fills"
                    " WHERE domain = ? AND label_norm = ?"
                    " AND verified_value = ?",
                    (domain, label_norm, verified_value),
                ).fetchone()
                if row is None:
                    return None
                return dict(row)
        except sqlite3.Error as exc:
            logger.debug("verified_fills.lookup failed: %s", exc)
            return None

    def record(
        self,
        domain: str,
        label: str,
        field_type: str,
        verified_value: str,
        method: str,
    ) -> None:
        """Upsert a verified-fill row.

        Callers: invoke only when the verifier produced ``tier_reached ==
        "passed"`` for this field. Mismatches must use ``invalidate``,
        not ``record``.
        """
        if not domain or not label or verified_value is None:
            return
        verified_value = str(verified_value).strip()
        if not verified_value:
            return
        label_norm = _normalize_label(label)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO verified_fills
                       (domain, label_norm, field_type, verified_value, ts, method)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT (domain, label_norm, verified_value)
                       DO UPDATE SET
                         field_type = excluded.field_type,
                         ts         = excluded.ts,
                         method     = excluded.method""",
                    (
                        domain, label_norm, field_type or "",
                        verified_value, int(time.time()), method or "",
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.debug("verified_fills.record failed: %s", exc)

    def invalidate(self, domain: str, label: str) -> None:
        """Drop every cached row for a label on this domain.

        Called when the verifier produces ``mismatch_detected`` — the
        cache's promise (rendered value matches) is invalid for that
        label.
        """
        if not domain or not label:
            return
        label_norm = _normalize_label(label)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM verified_fills"
                    " WHERE domain = ? AND label_norm = ?",
                    (domain, label_norm),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.debug("verified_fills.invalidate failed: %s", exc)

    def prune(self, ttl_days: int = _DEFAULT_TTL_DAYS) -> int:
        """Drop rows older than ``ttl_days`` days. Returns deleted count.

        Not wired into the daemon's hourly tick — a TTL-only cache with
        infrequent pruning is fine for weeks (rows are bounded by the
        product of distinct domains × distinct labels × distinct values
        actually verified). Exposed so an operator can run it ad hoc.
        """
        cutoff = int(time.time()) - ttl_days * 86400
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "DELETE FROM verified_fills WHERE ts < ?", (cutoff,),
                )
                conn.commit()
                return cur.rowcount or 0
        except sqlite3.Error as exc:
            logger.debug("verified_fills.prune failed: %s", exc)
            return 0
