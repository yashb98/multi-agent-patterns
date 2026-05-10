#!/usr/bin/env python3
"""Daily DB-retrieval drop-rate summary.

Reads ``data/db_observability.db`` over a configurable window, computes
per-(db, table) consumed/dropped/unconsumed counts, and:

1. Prints a human-readable table to stdout.
2. When any (db, table) exceeds the drop-rate threshold (default 50 %), emits
   a ``failure`` signal via OptimizationEngine, appends a templated entry to
   ``.claude/mistakes.md``, and (optionally) sends a Telegram alert.

Usage::

    python -m scripts.db_observability_summary
    python -m scripts.db_observability_summary --window-days 1
    python -m scripts.db_observability_summary --threshold 0.6 --no-mistakes

By default the alert path is silent unless ``DB_OBS_ALERT_TELEGRAM=1`` is
set in env, so cron runs that find an issue won't spam during initial
roll-out.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_DIR / "data" / "db_observability.db"
_DEFAULT_MISTAKES = _PROJECT_DIR / ".claude" / "mistakes.md"

_MISTAKES_TEMPLATE = """
## DB drop-rate alert — {db}.{table} ({date})

DB ``{db}.{table}`` returned data that was dropped from {dropped}/{used} consumed lookups
({drop_rate_pct:.1f}% drop rate over the last {window_days} day(s)).

Top drop reason: ``{top_reason}`` ({top_reason_count} occurrences).

**OPRAL investigation prompt:**
- **Observe**: pull a sample of dropped values from
  ``data/db_observability.db`` where ``db_name='{db}' AND table_name='{table}' AND status='dropped'``
- **Plan**: trace which call site produced the lookup and which downstream
  consumer dropped it. Use ``mcp__code-intelligence__callers_of`` on the
  accessor.
- **Reason**: is the data wrong-shape (option_misalignment, validation_failed),
  or is the consumer buggy?
- **Act**: fix the source (rewrite stored row, retrain pattern, replace stale
  default) or the consumer (improve alignment, surface a clearer match).
- **Learn**: emit ``adaptation`` or ``correction`` signal once fixed; verify
  drop rate drops on next daily summary.

Sample dropped rows (max 5):
{sample_rows}

"""


@dataclass
class Row:
    db_name: str
    table_name: str
    hits: int
    misses: int
    consumed: int
    dropped: int
    unconsumed: int
    pending: int
    top_drop_reason: str
    top_drop_count: int
    sample_dropped: list[dict]

    @property
    def used(self) -> int:
        return self.consumed + self.dropped

    @property
    def drop_rate(self) -> float:
        return (self.dropped / self.used) if self.used > 0 else 0.0


def query_summary(db_path: Path, window_days: int) -> list[Row]:
    if not db_path.exists():
        return []
    cutoff = time.time() - window_days * 86400.0
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        agg = conn.execute(
            """
            SELECT db_name, table_name,
                   SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hits,
                   SUM(CASE WHEN hit = 0 THEN 1 ELSE 0 END) AS misses,
                   SUM(CASE WHEN status = 'consumed' THEN 1 ELSE 0 END) AS consumed,
                   SUM(CASE WHEN status = 'dropped' THEN 1 ELSE 0 END) AS dropped,
                   SUM(CASE WHEN status = 'unconsumed' THEN 1 ELSE 0 END) AS unconsumed,
                   SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending
              FROM lookups
             WHERE ts >= ?
          GROUP BY db_name, table_name
          ORDER BY db_name, table_name
            """,
            (cutoff,),
        ).fetchall()
        rows: list[Row] = []
        for r in agg:
            top_reason_row = conn.execute(
                """
                SELECT drop_reason, COUNT(*) AS c
                  FROM lookups
                 WHERE ts >= ? AND db_name = ? AND table_name = ?
                       AND status = 'dropped' AND drop_reason IS NOT NULL
              GROUP BY drop_reason
              ORDER BY c DESC LIMIT 1
                """,
                (cutoff, r["db_name"], r["table_name"]),
            ).fetchone()
            sample = conn.execute(
                """
                SELECT field_label, intended, actual, drop_reason, value_repr, ts
                  FROM lookups
                 WHERE ts >= ? AND db_name = ? AND table_name = ?
                       AND status = 'dropped'
              ORDER BY ts DESC LIMIT 5
                """,
                (cutoff, r["db_name"], r["table_name"]),
            ).fetchall()
            rows.append(Row(
                db_name=r["db_name"],
                table_name=r["table_name"],
                hits=int(r["hits"] or 0),
                misses=int(r["misses"] or 0),
                consumed=int(r["consumed"] or 0),
                dropped=int(r["dropped"] or 0),
                unconsumed=int(r["unconsumed"] or 0),
                pending=int(r["pending"] or 0),
                top_drop_reason=top_reason_row["drop_reason"] if top_reason_row else "",
                top_drop_count=int(top_reason_row["c"]) if top_reason_row else 0,
                sample_dropped=[dict(s) for s in sample],
            ))
        return rows
    finally:
        conn.close()


def render_table(rows: list[Row]) -> str:
    """Pretty-print the per-(db, table) summary."""

    if not rows:
        return "No observability rows in window."

    lines = [
        f"{'db.table':45s}  {'hits':>5s}  {'miss':>4s}  {'cons':>5s}  "
        f"{'drop':>4s}  {'unc':>4s}  {'pend':>4s}  {'drop%':>6s}  top_reason"
    ]
    lines.append("-" * 110)
    for r in rows:
        name = f"{r.db_name}.{r.table_name}"[:44]
        drop_pct = f"{r.drop_rate * 100:.1f}%" if r.used > 0 else "-"
        lines.append(
            f"{name:45s}  {r.hits:5d}  {r.misses:4d}  {r.consumed:5d}  "
            f"{r.dropped:4d}  {r.unconsumed:4d}  {r.pending:4d}  {drop_pct:>6s}  "
            f"{r.top_drop_reason or '-'}"
        )
    return "\n".join(lines)


def emit_failure_signal(row: Row, threshold: float, window_days: int) -> None:
    """Emit a ``failure`` signal so the optimization aggregator can pattern-match."""

    try:
        from shared.optimization import get_optimization_engine

        engine = get_optimization_engine()
        engine.emit(
            signal_type="failure",
            source_loop="db_observability",
            domain=f"{row.db_name}.{row.table_name}",
            agent_name="db_observability_summary",
            payload={
                "drop_rate": row.drop_rate,
                "consumed": row.consumed,
                "dropped": row.dropped,
                "top_drop_reason": row.top_drop_reason,
                "top_drop_count": row.top_drop_count,
                "threshold": threshold,
                "window_days": window_days,
            },
            severity="warning",
        )
    except Exception as exc:  # pragma: no cover — observability never breaks
        print(f"WARN: optimization signal emit failed: {exc}", file=sys.stderr)


def append_mistakes_entry(
    row: Row, threshold: float, window_days: int, path: Path,
) -> None:
    sample_lines = []
    for s in row.sample_dropped:
        sample_lines.append(
            f"- field={s.get('field_label', '?')!r} intended={s.get('intended', '?')!r}"
            f" actual={s.get('actual', '?')!r} reason={s.get('drop_reason', '?')!r}"
        )
    sample_block = "\n".join(sample_lines) if sample_lines else "(none recorded)"
    entry = _MISTAKES_TEMPLATE.format(
        db=row.db_name,
        table=row.table_name,
        date=time.strftime("%Y-%m-%d"),
        dropped=row.dropped,
        used=row.used,
        drop_rate_pct=row.drop_rate * 100.0,
        window_days=window_days,
        top_reason=row.top_drop_reason or "unknown",
        top_reason_count=row.top_drop_count,
        sample_rows=sample_block,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def telegram_alert(rows: list[Row], threshold: float) -> None:
    if os.environ.get("DB_OBS_ALERT_TELEGRAM") != "1":
        return
    try:
        from shared.alerting import send_alert
    except Exception:
        return
    breached = [r for r in rows if r.used >= 5 and r.drop_rate >= threshold]
    if not breached:
        return
    body_lines = ["DB drop-rate breach:"]
    for r in breached:
        body_lines.append(
            f"• {r.db_name}.{r.table_name}: {r.drop_rate*100:.1f}% "
            f"({r.dropped}/{r.used}) reason={r.top_drop_reason or '?'}"
        )
    try:
        send_alert("\n".join(body_lines))
    except Exception:
        pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-days", type=int, default=7,
                   help="Window over which to compute drop rates.")
    p.add_argument("--threshold", type=float, default=0.5,
                   help="Drop-rate threshold (0-1) above which we alert.")
    p.add_argument("--min-volume", type=int, default=5,
                   help="Minimum used-count before a (db, table) is eligible.")
    p.add_argument("--db-path", type=Path, default=_DEFAULT_DB)
    p.add_argument("--mistakes-path", type=Path, default=_DEFAULT_MISTAKES)
    p.add_argument("--no-mistakes", action="store_true",
                   help="Don't append to .claude/mistakes.md.")
    p.add_argument("--no-signal", action="store_true",
                   help="Don't emit OPRAL signal.")
    p.add_argument("--json", action="store_true",
                   help="Output JSON instead of human table.")
    args = p.parse_args()

    rows = query_summary(args.db_path, args.window_days)

    if args.json:
        out = []
        for r in rows:
            out.append({
                "db_name": r.db_name, "table_name": r.table_name,
                "hits": r.hits, "misses": r.misses,
                "consumed": r.consumed, "dropped": r.dropped,
                "unconsumed": r.unconsumed, "pending": r.pending,
                "drop_rate": r.drop_rate,
                "top_drop_reason": r.top_drop_reason,
            })
        print(json.dumps(out, indent=2))
    else:
        print(render_table(rows))

    breached = [
        r for r in rows
        if r.used >= args.min_volume and r.drop_rate >= args.threshold
    ]

    if breached:
        print(
            f"\n{len(breached)} (db, table) pair(s) breached the "
            f"{args.threshold*100:.0f}% drop-rate threshold:",
            file=sys.stderr,
        )
        for r in breached:
            print(
                f"  - {r.db_name}.{r.table_name}: {r.drop_rate*100:.1f}% "
                f"(reason: {r.top_drop_reason or '?'})",
                file=sys.stderr,
            )
            if not args.no_signal:
                emit_failure_signal(r, args.threshold, args.window_days)
            if not args.no_mistakes:
                append_mistakes_entry(
                    r, args.threshold, args.window_days, args.mistakes_path,
                )
        telegram_alert(rows, args.threshold)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
