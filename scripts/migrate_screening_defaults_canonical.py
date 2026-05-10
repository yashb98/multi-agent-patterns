#!/usr/bin/env python3
"""One-off cleanup: rewrite wrong-shape ``screening_defaults`` rows.

Two values were stored with prose qualifiers that don't fit closed-set
Yes/No form options:

- ``relocation``: ``"Yes, within the UK"`` — typed as free text; on a
  Yes/No combobox the form's autocomplete would either pick whatever
  matched first or reject. Per the user's relocation memory ("Always
  happy to relocate"), the canonical answer is ``"Yes"``.
- ``commuting``: ``"Yes, willing to commute to any UK office"`` — same
  shape mismatch; canonical is ``"Yes"``.

The structural fix lives in ``_align_screening_to_options`` (it routes
stored answers through the OptionAligner before fill), but until that
aligner is taught the prose mappings, the cached drops keep the cleanup
of these specific rows worthwhile.

Run from repo root::

    python scripts/migrate_screening_defaults_canonical.py
    python scripts/migrate_screening_defaults_canonical.py --dry-run

Idempotent — re-runs are no-ops once the rows are canonical.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from shared.profile_store import get_profile_store  # noqa: E402

# (key, expected_old_value, canonical_new_value, rationale)
_FIXES: list[tuple[str, str, str, str]] = [
    (
        "relocation",
        "Yes, within the UK",
        "Yes",
        "user memory: always happy to relocate; closed-set Yes/No form fields",
    ),
    (
        "commuting",
        "Yes, willing to commute to any UK office",
        "Yes",
        "user memory: always happy to relocate; closed-set Yes/No form fields",
    ),
]

_BACKUP_DIR = REPO / "data" / "migrations"


def _backup(rows: list[tuple[str, str]]) -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _BACKUP_DIR / f"screening_defaults_canonical_{ts}.json"
    path.write_text(
        json.dumps([{"key": k, "old_value": v} for k, v in rows], indent=2),
        encoding="utf-8",
    )
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without modifying the DB.")
    args = p.parse_args()

    ps = get_profile_store()
    changes: list[tuple[str, str]] = []
    skipped: list[tuple[str, str, str]] = []

    for key, expected_old, new_val, _ in _FIXES:
        current = ps.screening_default(key)
        if current == new_val:
            print(f"  {key}: already canonical ({current!r}) — skipping")
            continue
        if current and current != expected_old:
            print(f"  {key}: unexpected value {current!r} "
                  f"(expected {expected_old!r}) — skipping for review")
            skipped.append((key, current, expected_old))
            continue
        if not current:
            print(f"  {key}: empty (no row) — skipping")
            continue
        print(f"  {key}: {current!r} → {new_val!r}")
        changes.append((key, current))

    if args.dry_run:
        print("\nDry run — no DB writes.")
        return 0

    if changes:
        backup_path = _backup(changes)
        print(f"\nBacked up {len(changes)} row(s) to {backup_path}")
        for key, _ in changes:
            new_val = next(nv for k, _, nv, _ in _FIXES if k == key)
            ps.set_screening_default(key, new_val)
        print(f"Applied {len(changes)} canonical-value updates.")

    if skipped:
        print(f"\n{len(skipped)} row(s) had unexpected values — left untouched. "
              "Inspect manually.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
