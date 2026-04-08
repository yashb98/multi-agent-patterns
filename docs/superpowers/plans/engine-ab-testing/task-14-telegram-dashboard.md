# Task 14: Telegram Dashboard — `job engine stats/compare/learning`

**Files:**
- Create: `jobpulse/ab_dashboard.py`
- Modify: `jobpulse/dispatcher.py`
- Modify: `jobpulse/swarm_dispatcher.py`
- Modify: `shared/nlp_classifier.py` (add intent examples)

**Why:** Telegram commands to view A/B results, per-platform comparison, and learning curves. Without this, you can't see which engine is winning.

**Dependencies:** Tasks 8, 13 (ABTracker must exist, pipeline must route engine)

---

- [ ] **Step 1: Create ab_dashboard.py**

```python
"""A/B engine dashboard — Telegram command handlers for engine comparison."""
from __future__ import annotations

from jobpulse.tracked_driver import ABTracker
from shared.logging_config import get_logger

logger = get_logger(__name__)


def engine_stats(args: str = "") -> str:
    """Head-to-head engine comparison for last N days."""
    days = 7
    if args.strip().isdigit():
        days = int(args.strip())

    tracker = ABTracker()
    ext = tracker.get_engine_stats("extension", days=days)
    pw = tracker.get_engine_stats("playwright", days=days)

    def _fmt(s: dict) -> str:
        total = s["total_fields"] or 1
        filled = s["fields_filled"]
        verified = s["fields_verified"]
        apps = s["applications"]
        submitted = s["submit_success"]
        fill_pct = f"{filled/total*100:.1f}%" if total > 0 else "N/A"
        verify_pct = f"{verified/max(filled,1)*100:.1f}%" if filled > 0 else "N/A"
        submit_pct = f"{submitted}/{apps}" if apps > 0 else "0/0"
        return f"  Fill: {fill_pct} ({filled}/{total})\n  Verified: {verify_pct}\n  Submit: {submit_pct}"

    return (
        f"Engine A/B Results (last {days} days)\n\n"
        f"Extension ({ext['applications']} apps):\n{_fmt(ext)}\n\n"
        f"Playwright ({pw['applications']} apps):\n{_fmt(pw)}"
    )


def engine_compare(args: str = "") -> str:
    """Per-platform breakdown."""
    tracker = ABTracker()
    platform = args.strip().lower() if args.strip() else None

    import sqlite3
    with sqlite3.connect(tracker.db_path) as conn:
        if platform:
            rows = conn.execute(
                "SELECT engine, action, COUNT(*), SUM(CASE WHEN success THEN 1 ELSE 0 END) "
                "FROM field_events WHERE platform=? GROUP BY engine, action ORDER BY engine, action",
                (platform,),
            ).fetchall()
            lines = [f"Engine comparison for {platform}:\n"]
        else:
            rows = conn.execute(
                "SELECT engine, platform, COUNT(*), SUM(CASE WHEN outcome='submitted' THEN 1 ELSE 0 END) "
                "FROM application_outcomes GROUP BY engine, platform ORDER BY engine, platform",
            ).fetchall()
            lines = ["Per-platform breakdown:\n"]

        for row in rows:
            lines.append(f"  {row[0]} | {row[1]}: {row[3]}/{row[2]}")

    return "\n".join(lines) if lines else "No data yet."


def engine_learning(args: str = "") -> str:
    """Learning curve — fixes accumulated, first-try rate trend."""
    tracker = ABTracker()
    import sqlite3
    with sqlite3.connect(tracker.db_path) as conn:
        rows = conn.execute(
            "SELECT engine, COUNT(*), SUM(CASE WHEN outcome='submitted' THEN 1 ELSE 0 END) "
            "FROM application_outcomes GROUP BY engine",
        ).fetchall()

    lines = ["Engine Learning Summary:\n"]
    for engine, total, success in rows:
        rate = f"{success/total*100:.0f}%" if total > 0 else "N/A"
        lines.append(f"  {engine}: {success}/{total} submitted ({rate})")
    return "\n".join(lines) if len(lines) > 1 else "No data yet."


def engine_reset(args: str = "") -> str:
    """Clear all A/B tracking data."""
    tracker = ABTracker()
    import sqlite3
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute("DELETE FROM field_events")
        conn.execute("DELETE FROM application_outcomes")
        conn.execute("DELETE FROM engine_learning")
        conn.commit()
    return "A/B tracking data cleared."
```

- [ ] **Step 2: Add intents to both dispatchers**

In `jobpulse/dispatcher.py`, add to `AGENT_MAP`:
```python
    "engine_stats": ab_dashboard.engine_stats,
    "engine_compare": ab_dashboard.engine_compare,
    "engine_learning": ab_dashboard.engine_learning,
    "engine_reset": ab_dashboard.engine_reset,
```

Add the import: `from jobpulse import ab_dashboard`

Add intents to `JOB_INTENTS`: `"engine_stats"`, `"engine_compare"`, `"engine_learning"`, `"engine_reset"`

Do the SAME in `jobpulse/swarm_dispatcher.py` (dual dispatcher invariant).

- [ ] **Step 3: Add NLP examples to shared/nlp_classifier.py**

Add to the job-related examples:
```python
    ("job engine stats", "engine_stats"),
    ("engine comparison", "engine_stats"),
    ("engine compare greenhouse", "engine_compare"),
    ("compare engines", "engine_compare"),
    ("engine learning", "engine_learning"),
    ("engine reset", "engine_reset"),
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/ab_dashboard.py jobpulse/dispatcher.py jobpulse/swarm_dispatcher.py shared/nlp_classifier.py
git commit -m "feat: Telegram A/B dashboard — engine stats, compare, learning commands

job engine stats: head-to-head fill/verify/submit rates
job engine compare <platform>: per-platform field-type breakdown
job engine learning: fix accumulation and success rate trends"
```
