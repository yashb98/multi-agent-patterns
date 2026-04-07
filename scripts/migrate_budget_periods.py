#!/usr/bin/env python3
"""One-time migration: recalculate week_start values from 7-day weekly periods
to 28-day periods anchored to 2026-04-02.

Usage:
    python scripts/migrate_budget_periods.py            # dry-run (shows changes)
    python scripts/migrate_budget_periods.py --execute   # commit changes
"""
import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "budget.db"

PERIOD_DAYS = 28
PERIOD_ANCHOR = datetime(2026, 4, 2)


def _get_period_start(date_str: str) -> str:
    """Calculate the 28-day period start for a given date string (YYYY-MM-DD)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    delta_days = (d - PERIOD_ANCHOR).days
    period_num = delta_days // PERIOD_DAYS
    start = PERIOD_ANCHOR + timedelta(days=period_num * PERIOD_DAYS)
    return start.strftime("%Y-%m-%d")


def migrate(execute: bool) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # ── Collect all changes ──────────────────────────────────────────────
    changes: dict[str, list[tuple]] = {}

    # 1. transactions — recalculate week_start from date column
    cur.execute("SELECT id, date, week_start FROM transactions")
    rows = cur.fetchall()
    updates = []
    for row_id, date_val, old_ws in rows:
        new_ws = _get_period_start(date_val)
        if new_ws != old_ws:
            updates.append((new_ws, row_id, old_ws))
    changes["transactions"] = updates

    # 2. work_hours — recalculate week_start from date column
    cur.execute("SELECT id, date, week_start FROM work_hours")
    rows = cur.fetchall()
    updates = []
    for row_id, date_val, old_ws in rows:
        new_ws = _get_period_start(date_val)
        if new_ws != old_ws:
            updates.append((new_ws, row_id, old_ws))
    changes["work_hours"] = updates

    # 3. planned_budgets — recalculate from stored week_start
    cur.execute("SELECT rowid, week_start, category, section FROM planned_budgets")
    rows = cur.fetchall()
    updates = []
    for rowid, old_ws, category, section in rows:
        new_ws = _get_period_start(old_ws)
        if new_ws != old_ws:
            updates.append((new_ws, rowid, old_ws))
    changes["planned_budgets"] = updates

    # 4. category_pages — recalculate from stored week_start
    cur.execute("SELECT rowid, week_start, category FROM category_pages")
    rows = cur.fetchall()
    updates = []
    for rowid, old_ws, category in rows:
        new_ws = _get_period_start(old_ws)
        if new_ws != old_ws:
            updates.append((new_ws, rowid, old_ws))
    changes["category_pages"] = updates

    # ── Print before/after distinct week_start counts ────────────────────
    for table_name in ["transactions", "work_hours", "planned_budgets", "category_pages"]:
        cur.execute(f"SELECT DISTINCT week_start FROM {table_name}")
        old_distinct = sorted([r[0] for r in cur.fetchall()])

        # Simulate new distinct values
        if table_name in ("transactions", "work_hours"):
            date_col = "date"
            cur.execute(f"SELECT DISTINCT {date_col} FROM {table_name}")
            all_dates = [r[0] for r in cur.fetchall()]
            new_distinct = sorted(set(_get_period_start(d) for d in all_dates))
        else:
            cur.execute(f"SELECT DISTINCT week_start FROM {table_name}")
            all_ws = [r[0] for r in cur.fetchall()]
            new_distinct = sorted(set(_get_period_start(ws) for ws in all_ws))

        print(f"\n{'='*60}")
        print(f"  {table_name}")
        print(f"  BEFORE: {len(old_distinct)} distinct week_start values: {old_distinct}")
        print(f"  AFTER:  {len(new_distinct)} distinct week_start values: {new_distinct}")
        print(f"  Rows to update: {len(changes[table_name])}")

        if changes[table_name]:
            for new_ws, row_id, old_ws in changes[table_name][:5]:
                print(f"    row {row_id}: {old_ws} -> {new_ws}")
            if len(changes[table_name]) > 5:
                print(f"    ... and {len(changes[table_name]) - 5} more")

    total = sum(len(v) for v in changes.values())
    print(f"\n{'='*60}")
    print(f"  TOTAL rows to update: {total}")

    if not execute:
        print("\n  DRY RUN — no changes committed. Pass --execute to apply.\n")
        conn.close()
        return

    # ── Apply changes in a transaction ───────────────────────────────────
    print("\n  Applying changes...")
    try:
        cur.execute("BEGIN")

        for new_ws, row_id, _ in changes["transactions"]:
            cur.execute("UPDATE transactions SET week_start = ? WHERE id = ?", (new_ws, row_id))

        for new_ws, row_id, _ in changes["work_hours"]:
            cur.execute("UPDATE work_hours SET week_start = ? WHERE id = ?", (new_ws, row_id))

        for new_ws, rowid, _ in changes["planned_budgets"]:
            cur.execute("UPDATE planned_budgets SET week_start = ? WHERE rowid = ?", (new_ws, rowid))

        for new_ws, rowid, _ in changes["category_pages"]:
            cur.execute("UPDATE category_pages SET week_start = ? WHERE rowid = ?", (new_ws, rowid))

        conn.commit()
        print("  COMMITTED successfully.\n")
    except Exception as e:
        conn.rollback()
        print(f"  ROLLED BACK due to error: {e}\n")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate budget.db week_start to 28-day periods")
    parser.add_argument("--execute", action="store_true", help="Actually commit changes (default: dry-run)")
    args = parser.parse_args()
    migrate(args.execute)
