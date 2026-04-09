"""Recurring transactions — extracted from budget_agent.py (SRP split).

Manages daily/weekly/monthly recurring transaction rules:
add, process (auto-log due), list, remove, format.
"""
from __future__ import annotations

from datetime import datetime

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ── Helpers (imported from budget_agent at call time to avoid circular import) ──

def _budget():
    """Lazy import of budget_agent to avoid circular dependency."""
    import jobpulse.budget_agent as _ba
    return _ba


def add_recurring(amount: float, description: str, category: str,
                  section: str, txn_type: str, frequency: str,
                  day: int | None = None) -> dict:
    """Add a recurring transaction rule (daily/weekly/monthly)."""
    # Validate frequency
    valid_frequencies = ("daily", "weekly", "monthly")
    if frequency not in valid_frequencies:
        return {"error": f"Invalid frequency '{frequency}'. Must be one of: {', '.join(valid_frequencies)}"}

    now = datetime.now()
    conn = _budget()._get_conn()

    # Check for duplicate rule (same description + frequency)
    existing = conn.execute(
        "SELECT id FROM recurring_transactions WHERE description=? AND frequency=? AND active=1",
        (description, frequency)
    ).fetchone()
    if existing:
        conn.close()
        return {"error": f"Recurring rule already exists for '{description}' ({frequency})"}

    day_of_month = None
    day_of_week = None
    if frequency == "monthly":
        day_of_month = day if day else now.day
    elif frequency == "weekly":
        day_of_week = day if day is not None else now.weekday()

    cursor = conn.execute(
        "INSERT INTO recurring_transactions "
        "(amount, description, category, section, type, frequency, day_of_month, day_of_week, active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,1,?)",
        (amount, description, category, section, txn_type, frequency,
         day_of_month, day_of_week, now.isoformat())
    )
    conn.commit()
    conn.close()

    return {"id": cursor.lastrowid, "amount": amount, "description": description,
            "category": category, "frequency": frequency}


def process_recurring() -> list[dict]:
    """Check all active recurring transactions and log any due today that haven't been logged yet."""
    ba = _budget()
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    conn = ba._get_conn()

    rows = conn.execute(
        "SELECT * FROM recurring_transactions WHERE active=1"
    ).fetchall()

    logged = []
    for row in rows:
        row = dict(row)
        last_logged = row.get("last_logged")

        # Skip if already logged today
        if last_logged == today_str:
            continue

        is_due = False
        freq = row["frequency"]

        if freq == "daily":
            is_due = True
        elif freq == "weekly" and row["day_of_week"] is not None:
            is_due = today.weekday() == row["day_of_week"]
        elif freq == "monthly" and row["day_of_month"] is not None:
            is_due = today.day == row["day_of_month"]

        if is_due:
            txn = ba.add_transaction(
                row["amount"], row["description"],
                row["category"], row["section"], row["type"]
            )
            conn.execute(
                "UPDATE recurring_transactions SET last_logged=? WHERE id=?",
                (today_str, row["id"])
            )
            conn.commit()

            # Sync to Notion
            try:
                ba.sync_expense_to_notion(txn)
            except Exception as e:
                logger.warning("Recurring Notion sync failed: %s", e)

            logged.append(txn)

    conn.close()
    return logged


def list_recurring() -> list[dict]:
    """Return all active recurring transactions."""
    conn = _budget()._get_conn()
    rows = conn.execute(
        "SELECT * FROM recurring_transactions WHERE active=1 ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_recurring(description: str) -> str:
    """Fuzzy match and deactivate a recurring transaction."""
    recurrings = list_recurring()
    if not recurrings:
        return "No active recurring transactions."

    desc_lower = description.lower().strip()
    best_match = None
    best_score = 0

    for r in recurrings:
        r_words = set(r["description"].lower().split())
        q_words = set(desc_lower.split())
        if not q_words:
            continue
        overlap = len(r_words & q_words) / len(q_words)
        if overlap > best_score:
            best_score = overlap
            best_match = r

    if not best_match or best_score < 0.4:
        names = ", ".join(r["description"] for r in recurrings[:5])
        return f"Couldn't match \"{description}\". Active: {names}"

    conn = _budget()._get_conn()
    conn.execute(
        "UPDATE recurring_transactions SET active=0 WHERE id=?",
        (best_match["id"],)
    )
    conn.commit()
    conn.close()
    return f"🛑 Stopped recurring: £{best_match['amount']:.2f} — {best_match['description']} ({best_match['frequency']})"


def format_recurring(items: list[dict]) -> str:
    """Format recurring transactions for display."""
    if not items:
        return "🔄 No active recurring transactions."
    lines = ["🔄 RECURRING TRANSACTIONS:\n"]
    for r in items:
        freq_str = r["frequency"]
        if freq_str == "weekly" and r.get("day_of_week") is not None:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            freq_str = f"weekly ({days[r['day_of_week']]})"
        elif freq_str == "monthly" and r.get("day_of_month") is not None:
            freq_str = f"monthly (day {r['day_of_month']})"
        lines.append(f"  £{r['amount']:.2f} — {r['description']} [{r['category']}] ({freq_str})")
    return "\n".join(lines)
