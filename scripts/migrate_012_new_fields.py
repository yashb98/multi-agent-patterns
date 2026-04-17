#!/usr/bin/env python3
"""Idempotent migration: add career-ops fields to applications.db.

Run: python scripts/migrate_012_new_fields.py
Safe to run multiple times — checks column existence before ALTER.
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "applications.db"


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def migrate(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path} — skipping migration.")
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    new_columns = {
        "listings": [
            ("ghost_tier", "TEXT"),
            ("archetype", "TEXT"),
            ("archetype_secondary", "TEXT"),
            ("archetype_confidence", "REAL DEFAULT 0.0"),
            ("locale_market", "TEXT"),
            ("locale_language", "TEXT"),
            ("posted_at", "TEXT"),
        ],
        "applications": [
            ("followup_count", "INTEGER DEFAULT 0"),
            ("followup_last_at", "TEXT"),
            ("followup_status", "TEXT DEFAULT 'active'"),
        ],
    }

    changes = 0
    for table, columns in new_columns.items():
        for col_name, col_type in columns:
            if not column_exists(cursor, table, col_name):
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                    print(f"  Added {table}.{col_name} ({col_type})")
                    changes += 1
                except sqlite3.OperationalError as e:
                    if "no such table" in str(e).lower():
                        print(f"  Table '{table}' does not exist — skipping column {col_name}")
                    else:
                        raise
            else:
                print(f"  {table}.{col_name} already exists — skipping")

    conn.commit()
    conn.close()
    print(f"\nMigration complete: {changes} columns added.")


if __name__ == "__main__":
    migrate()
