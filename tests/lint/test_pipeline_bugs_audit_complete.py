"""Lint guard: every row in the dead-code + wiring-gap sections of
`docs/audits/pipeline-bugs.md` must have a verdict marker.

The runner table for S5 (`docs/audits/pipeline-bugs-runner.md`) requires
each item be marked WIRE / DELETE / KEEP / DEFERRED. Without this guard,
a future audit can drop a `🔴`/`💀`/`🔌` row into Sections 3-5 without a
disposition and the issue silently never gets resolved.

Verdict markers we accept:
  - `✅ S<n>` — closed in session N (with hash backfill)
  - `⏸ S<n>` — deferred (with reason elsewhere in the row)
  - `🗑 S5 DELETE` — verdict locked, implementation in follow-up session
  - `🔧 S5 WIRE` — verdict locked, wiring lands in follow-up session
  - `📌 S5 KEEP+DOCUMENT` — intentional reserve / cross-referenced
  - `🔌 DEFERRED→S<n>` — covered by another runner-table session

Open markers `🔴 / 💀 / 🔌 ` (without verdict prefix) fail the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


# Open-status icons that, on their own (without a verdict), need a disposition.
_OPEN_ICONS = ("🔴", "💀", "🩹", "📝", "🔌")

# Verdict tokens that, if present anywhere in the row, mean the row is closed
# or scheduled for closure.
_VERDICT_TOKENS = (
    "✅",            # closed in some session
    "⏸ S",          # deferred with session marker
    "🗑 S",          # S5 DELETE verdict
    "🔧 S",          # S5 WIRE verdict
    "📌 S",          # S5 KEEP+DOCUMENT verdict
    "DEFERRED→S",   # cross-session deferral
    "Verdict:",     # any explicit verdict prose
)


def test_pipeline_bugs_audit_rows_have_verdicts() -> None:
    """Section 3 (dead code) and Section 4 (wiring gaps) rows must have a
    verdict. Section 1 (`🔴 Open bugs`) and Section 2 (`🩹 Missing code`)
    rows are exempt because each has its own runner-table session."""
    text = (_root() / "docs/audits/pipeline-bugs.md").read_text(encoding="utf-8")

    # Slice to Sections 3 + 4 + 5 only.
    start = text.find("## Section 3 — 💀 Dead code")
    end = text.find("## Section 6 — 📝 Contract lies")
    assert start != -1 and end != -1, (
        "Section 3-5 anchors not found in pipeline-bugs.md — table layout changed?"
    )
    body = text[start:end]

    open_rows: list[tuple[int, str]] = []
    for i, line in enumerate(body.splitlines(), start=1):
        if not line.startswith("| "):
            continue
        # Skip header / separator rows
        if "| ID " in line or "|---" in line:
            continue
        # Skip rows that don't carry one of the open-status icons.
        if not any(icon in line for icon in _OPEN_ICONS):
            continue
        # Row carries an open icon; require a verdict token elsewhere in the row.
        if any(token in line for token in _VERDICT_TOKENS):
            continue
        open_rows.append((i, line[:160]))

    if not open_rows:
        return

    formatted = "\n".join(f"  L{i}: {snippet}" for i, snippet in open_rows[:10])
    pytest.fail(
        f"\n{len(open_rows)} dead-code / wiring-gap rows in pipeline-bugs.md "
        f"Sections 3-5 are missing a verdict marker:\n\n"
        f"{formatted}\n\n"
        f"Expected one of: ✅ S<n> <hash> (closed), ⏸ S<n> deferred (with "
        f"reason), 🗑 S5 DELETE, 🔧 S5 WIRE, 📌 S5 KEEP+DOCUMENT, "
        f"🔌 DEFERRED→S<n> (covered by another runner-table session). "
        f"Add a verdict in the next /fix-pipeline-bugs session, or surface "
        f"the row to the user via AskUserQuestion."
    )
