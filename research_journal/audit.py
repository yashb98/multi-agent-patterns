"""Weekly quality audit — hallucination rate + coverage gap vs HF Daily Papers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)


def compute_hallucination_rate(db_path: Path | None = None, days: int = 7) -> float:
    """Fraction of papers from the last `days` whose verification.claims_grounded == False."""
    if db_path is None:
        from shared.paths import DATA_DIR
        db_path = DATA_DIR / "papers.db"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT verification FROM papers WHERE digest_date >= ? AND verification != ''",
            (cutoff,),
        ).fetchall()
    if not rows:
        return 0.0
    failed = 0
    for (raw,) in rows:
        try:
            v = json.loads(raw)
            if not v.get("claims_grounded", True):
                failed += 1
        except json.JSONDecodeError:
            continue
    return failed / len(rows)


def compute_coverage_gap(db_path: Path | None = None, days: int = 7) -> float:
    """Fraction of HF Daily Papers' top picks NOT present in our journal in the last `days`."""
    if db_path is None:
        from shared.paths import DATA_DIR
        db_path = DATA_DIR / "papers.db"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        ours = {r[0] for r in conn.execute(
            "SELECT arxiv_id FROM papers WHERE digest_date >= ?", (cutoff,)
        ).fetchall()}
    try:
        from jobpulse.papers.fetcher import PaperFetcher
        import asyncio
        fetcher = PaperFetcher()
        hf = asyncio.run(fetcher._fetch_huggingface())
    except Exception as exc:
        logger.warning("HF Daily Papers fetch failed in audit: %s", exc)
        return 0.0
    hf_ids = {p.arxiv_id for p in hf}
    if not hf_ids:
        return 0.0
    return len(hf_ids - ours) / len(hf_ids)


def run_weekly_audit(db_path: Path | None = None) -> dict:
    """Run both quality metrics and emit signals / alerts when thresholds are breached.

    Hallucination rate > 2%  → ``failure`` signal + Telegram alert.
    Coverage gap > 30%       → ``adaptation`` signal (classifier drift).
    """
    rate = compute_hallucination_rate(db_path=db_path)
    gap = compute_coverage_gap(db_path=db_path)
    summary = {"hallucination_rate": rate, "coverage_gap": gap}
    logger.info("journal weekly audit: %s", summary)

    if rate > 0.02:
        _emit_signal(
            signal_type="failure",
            domain="journal_summary",
            metric="hallucination_rate",
            value=rate,
            threshold=0.02,
        )
        _alert_telegram(f"Journal hallucination rate {rate:.2%} > 2%")

    if gap > 0.30:
        _emit_signal(
            signal_type="adaptation",
            domain="journal_classifier",
            metric="coverage_gap",
            value=gap,
            threshold=0.30,
        )

    return summary


def _emit_signal(
    signal_type: str,
    domain: str,
    metric: str,
    value: float,
    threshold: float,
) -> None:
    try:
        from shared.optimization import get_optimization_engine
        engine = get_optimization_engine()
        engine.emit(
            signal_type=signal_type,
            source_loop="journal_audit",
            domain=domain,
            payload={"metric": metric, "value": value, "threshold": threshold},
        )
    except Exception as exc:
        logger.warning("optimization signal emit failed: %s", exc)


def _alert_telegram(msg: str) -> None:
    try:
        from jobpulse.telegram_bots import send_research
        send_research(msg)
    except Exception as exc:
        logger.warning("telegram alert failed: %s", exc)
