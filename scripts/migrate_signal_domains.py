"""One-time migration: normalize full-URL domain values in optimization.db.signals.

Pre-fix code in jobpulse.post_apply_hook and jobpulse.native_form_filler emitted
LearningSignal rows with `domain` set to the full job URL (e.g.
"https://boards.greenhouse.io/acme/jobs/123") instead of the netloc
("boards.greenhouse.io"). The OptimizationEngine's per-domain aggregation
buckets per row, so per-job signals never reach the per-domain bucket.

The writers were fixed in this session (post_apply_hook + native_form_filler),
but historical rows remain. This script normalizes them in place so per-domain
analytics see unified signal streams going forward.

Usage:
    python scripts/migrate_signal_domains.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _normalize_domain(value: str) -> str:
    if not value:
        return value
    if "://" not in value:
        return value
    parsed = urlparse(value)
    netloc = (parsed.netloc or "").lower().removeprefix("www.")
    return netloc or value


def migrate(db_path: Path, dry_run: bool = False) -> int:
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 0

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT signal_id, source_loop, domain FROM signals "
        "WHERE domain LIKE 'http%' OR domain LIKE 'HTTP%'"
    ).fetchall()

    if not rows:
        print("No full-URL domain rows to normalize.")
        conn.close()
        return 0

    by_loop: dict[str, int] = {}
    updates: list[tuple[str, str]] = []
    for signal_id, source_loop, raw_domain in rows:
        normalized = _normalize_domain(raw_domain)
        if normalized == raw_domain:
            continue
        by_loop[source_loop] = by_loop.get(source_loop, 0) + 1
        updates.append((normalized, signal_id))

    print(f"Found {len(updates)} rows to normalize")
    for loop, count in sorted(by_loop.items(), key=lambda x: -x[1]):
        print(f"  {loop}: {count}")

    if dry_run:
        print("\n--dry-run set — no writes performed")
        for new_domain, signal_id in updates[:5]:
            print(f"  would update signal_id={signal_id} → domain={new_domain!r}")
        conn.close()
        return len(updates)

    cur.executemany(
        "UPDATE signals SET domain = ? WHERE signal_id = ?",
        updates,
    )
    conn.commit()
    conn.close()
    print(f"\nMigrated {len(updates)} rows.")
    return len(updates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing")
    parser.add_argument("--db", default="data/optimization.db",
                        help="Path to optimization.db (default: data/optimization.db)")
    args = parser.parse_args()
    migrate(Path(args.db), dry_run=args.dry_run)
