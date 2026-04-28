"""One-time migration: V1 ats_answer_cache → V2 screening_semantic_cache.

Reads all entries from the old exact-match cache in applications.db,
embeds the question text, and inserts into the V2 semantic cache with
confidence=0.7. Skips generic/test entries.

Usage:
    python scripts/migrate_v1_screening_cache.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobpulse.job_db import JobDB
from jobpulse.screening_semantic_cache import ScreeningSemanticCache

SKIP_QUESTIONS = {
    "question", "question 0", "question 1", "question 2",
    "email", "name", "phone", "address",
}


def migrate(dry_run: bool = False) -> None:
    db = JobDB()
    v1_entries = db.get_all_cached_answers()
    db.close()

    cache = ScreeningSemanticCache()
    migrated = 0
    skipped = 0

    for question, answer in v1_entries.items():
        q_norm = question.strip().lower()
        if q_norm in SKIP_QUESTIONS or len(q_norm) < 10:
            skipped += 1
            continue

        if dry_run:
            print(f"  [DRY RUN] Would migrate: '{question[:60]}' → '{answer[:40]}'")
            migrated += 1
            continue

        cache.cache(
            question=question,
            intent="unknown",
            answer=answer,
            confidence=0.7,
        )
        migrated += 1

    print(f"\nMigration complete: {migrated} migrated, {skipped} skipped")
    if dry_run:
        print("(Dry run — no changes written)")


if __name__ == "__main__":
    is_dry_run = "--dry-run" in sys.argv
    migrate(dry_run=is_dry_run)
