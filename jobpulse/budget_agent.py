"""Budget agent — tracks income/spending, categorizes with LLM, updates Notion budget period sheet.

Matches the user's exact Notion "Budget Period Sheet" structure:

  INCOME: Salary, Freelance, Other
  FIXED EXPENSES: Rent/Mortgage, Utilities, Phone/Internet, Subscriptions, Insurance
  VARIABLE SPENDING: Groceries, Eating out, Transport, Shopping, Entertainment, Health, Misc
  SAVINGS + DEBT: Savings, Investments, Credit card/Loan payment
  PERIOD SUMMARY: Total income, Total spending, Total savings, Net

Each period gets its own Notion page (cloned from template structure).
The Notion tables are updated in-place as you log expenses/income.
"""

import re
import json
import sqlite3
from datetime import datetime, timedelta
from jobpulse.config import NOTION_API_KEY, NOTION_PARENT_PAGE_ID, DATA_DIR
from jobpulse import event_logger
from shared.logging_config import get_logger
from shared.db import get_db_conn
from jobpulse.budget_constants import (  # noqa: F401 — re-exported for callers
    BUDGET_PAGE_ID, TABLE_IDS, ROW_IDS, DB_PATH,
    INCOME_CATEGORIES, FIXED_EXPENSE_CATEGORIES,
    VARIABLE_EXPENSE_CATEGORIES, SAVINGS_CATEGORIES, ALL_CATEGORIES,
    PERIOD_DAYS, PERIOD_ANCHOR,
    get_period_start, get_period_end,
)
from jobpulse.budget_nlp import classify_transaction, parse_transaction  # noqa: F401
from jobpulse.budget_notion import (  # noqa: F401
    get_notion_budget_url, _update_table_row, sync_expense_to_notion,
    _update_section_totals, _update_planned_column,
)

logger = get_logger(__name__)

# ── Backward-compat aliases for private-name callers ──
_get_period_start = get_period_start
_get_period_end = get_period_end
_get_week_start = get_period_start


# ── SQLite Storage ──

def _get_conn() -> sqlite3.Connection:
    return get_db_conn(DB_PATH)


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            section TEXT NOT NULL,
            type TEXT NOT NULL,
            date TEXT NOT NULL,
            week_start TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS weekly_budgets (
            week_start TEXT PRIMARY KEY,
            notion_page_id TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS planned_budgets (
            week_start TEXT NOT NULL,
            category TEXT NOT NULL,
            section TEXT NOT NULL,
            planned_amount REAL DEFAULT 0,
            PRIMARY KEY (week_start, category)
        );

        CREATE INDEX IF NOT EXISTS idx_txn_week ON transactions(week_start);
        CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
        CREATE INDEX IF NOT EXISTS idx_txn_type ON transactions(type);

        CREATE TABLE IF NOT EXISTS work_hours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hours REAL NOT NULL,
            hourly_rate REAL NOT NULL DEFAULT 13.99,
            total_earned REAL NOT NULL,
            date TEXT NOT NULL,
            week_start TEXT NOT NULL,
            notion_page_id TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_hours_week ON work_hours(week_start);
        CREATE INDEX IF NOT EXISTS idx_hours_date ON work_hours(date);

        CREATE TABLE IF NOT EXISTS recurring_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            section TEXT NOT NULL,
            type TEXT NOT NULL,
            frequency TEXT NOT NULL,
            day_of_month INTEGER,
            day_of_week INTEGER,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            last_logged TEXT
        );
    """)
    conn.commit()
    conn.close()


def add_transaction(amount: float, description: str, category: str,
                    section: str, txn_type: str) -> dict:
    # Validate amount before touching the database
    if not isinstance(amount, (int, float)) or amount <= 0:
        logger.warning("add_transaction: rejected invalid amount: %s", amount)
        return {"error": f"Invalid amount: {amount}. Must be a positive number."}
    if amount > 100_000:
        logger.warning("add_transaction: rejected excessive amount: %s", amount)
        return {"error": f"Amount {amount} exceeds maximum (100,000)."}

    now = datetime.now()
    week_start = _get_week_start(now)
    today = now.strftime("%Y-%m-%d")

    conn = _get_conn()

    # Dedup guard: reject if identical transaction was logged in the last 30 seconds
    # This prevents double-logging from concurrent bot handlers
    recent = conn.execute(
        "SELECT id FROM transactions WHERE amount=? AND description=? AND category=? "
        "AND date=? AND created_at > datetime('now', '-30 seconds')",
        (amount, description, category, today)
    ).fetchone()
    if recent:
        logger.info("Dedup: skipping duplicate transaction (%.2f %s %s)", amount, description, category)
        conn.close()
        return {"id": recent[0], "amount": amount, "description": description,
                "category": category, "section": section, "type": txn_type,
                "date": today, "week_start": week_start, "dedup": True}

    cursor = conn.execute(
        "INSERT INTO transactions (amount, description, category, section, type, date, week_start, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (amount, description, category, section, txn_type, today, week_start, now.isoformat())
    )
    conn.commit()
    conn.close()

    return {"id": cursor.lastrowid, "amount": amount, "description": description,
            "category": category, "section": section, "type": txn_type,
            "date": today, "week_start": week_start}


def set_planned_budget(category: str, section: str, amount: float, week_start: str = None):
    if amount < 0:
        logger.warning("set_planned_budget: rejected negative amount: %s", amount)
        return {"error": f"Budget cannot be negative: {amount}"}
    week_start = week_start or _get_week_start()
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO planned_budgets (week_start, category, section, planned_amount) VALUES (?,?,?,?)",
        (week_start, category, section, amount)
    )
    conn.commit()
    conn.close()


def get_week_summary(week_start: str = None) -> dict:
    week_start = week_start or _get_week_start()
    conn = _get_conn()

    # Get actuals by section and category
    rows = conn.execute(
        "SELECT section, category, type, SUM(amount) as total, COUNT(*) as count "
        "FROM transactions WHERE week_start=? GROUP BY section, category, type ORDER BY section, total DESC",
        (week_start,)
    ).fetchall()

    # Get planned budgets
    planned = conn.execute(
        "SELECT category, section, planned_amount FROM planned_budgets WHERE week_start=?",
        (week_start,)
    ).fetchall()

    # Get totals
    income_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND type='income'",
        (week_start,)
    ).fetchone()[0]

    spending_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND type='expense'",
        (week_start,)
    ).fetchone()[0]

    savings_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND type='savings'",
        (week_start,)
    ).fetchone()[0]

    recent = conn.execute(
        "SELECT amount, description, category, type, date FROM transactions WHERE week_start=? ORDER BY created_at DESC LIMIT 10",
        (week_start,)
    ).fetchall()

    conn.close()

    net = income_total - spending_total - savings_total

    return {
        "week_start": week_start,
        "income_total": income_total,
        "spending_total": spending_total,
        "savings_total": savings_total,
        "net": net,
        "by_category": [dict(r) for r in rows],
        "planned": {r["category"]: r["planned_amount"] for r in planned},
        "recent": [dict(r) for r in recent],
    }


def get_today_spending() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    rows = conn.execute(
        "SELECT amount, description, category, type FROM transactions WHERE date=? ORDER BY created_at DESC",
        (today,)
    ).fetchall()
    total_spent = sum(r["amount"] for r in rows if r["type"] == "expense")
    total_earned = sum(r["amount"] for r in rows if r["type"] == "income")
    conn.close()
    return {"date": today, "total_spent": total_spent, "total_earned": total_earned,
            "items": [dict(r) for r in rows]}


# ── LLM Category Classification ──

def log_transaction(text: str, trigger: str = "telegram_command") -> str:
    """Full pipeline: parse → classify → store → reply."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("budget_agent", trigger)

    # Step 1: Parse
    with trail.step("decision", "Parse transaction text",
                     step_input=text) as s:
        parsed = parse_transaction(text)
        if not parsed:
            s["output"] = "Could not parse"
            s["decision"] = "No amount found in text"
            trail.finalize("Failed: could not parse transaction")
            return ("Couldn't parse that. Try:\n"
                    "  spent 15 on lunch\n"
                    "  £8.50 coffee\n"
                    "  earned 500 freelance\n"
                    "  saved 100 emergency fund")
        s["output"] = f"Amount: £{parsed['amount']:.2f}, Desc: {parsed['description']}, Type: {parsed['type']}"
        s["metadata"] = parsed

    amount = parsed["amount"]
    description = parsed["description"]
    txn_type = parsed["type"]

    # Step 2: Classify
    with trail.step("llm_call", "Classify category",
                     step_input=f"£{amount:.2f} — {description} ({txn_type})") as s:
        section, category = classify_transaction(description, amount, txn_type)
        s["output"] = f"{section} → {category}"
        s["decision"] = f"Classified as {category} in {section}"
        s["metadata"] = {"section": section, "category": category}

    # Step 3: Extract items + store (NLP)
    with trail.step("decision", "Extract items and store",
                     step_input=description) as s:
        from jobpulse.budget_tracker import extract_items_and_store, _get_time_of_day
        extracted = extract_items_and_store(description)
        items = extracted["items"]
        store = extracted["store"]
        time_of_day = _get_time_of_day()
        s["output"] = f"Items: {items}, Store: {store}"

    # Step 4: Store in SQLite (with enhanced fields)
    with trail.step("api_call", "Store in SQLite") as s:
        txn = add_transaction(amount, description, category, section, txn_type)
        # Update with enhanced fields
        conn = _get_conn()
        conn.execute(
            "UPDATE transactions SET items=?, store=?, time_of_day=? WHERE id=?",
            (", ".join(items), store, time_of_day, txn["id"])
        )
        conn.commit()
        conn.close()
        s["output"] = f"Transaction #{txn['id']} stored"

    # Step 5: Create category sub-page + add transaction row FIRST
    # (must happen before sync_expense_to_notion so the link exists)
    category_url = ""
    with trail.step("api_call", "Add to category sub-page",
                     step_input=f"{category}: {', '.join(items)}") as s:
        from jobpulse.budget_tracker import add_transaction_row
        category_url = add_transaction_row(
            category=category, week_start=txn["week_start"],
            amount=amount, date_str=txn["date"],
            description=description, items=items,
            store=store, section=section,
        )
        s["output"] = f"Row added to {category} page"

    # Step 6: Sync to Notion budget sheet (update Actual column + category link)
    with trail.step("api_call", "Sync to Notion budget sheet",
                     step_input=f"{category}: £{amount:.2f}") as s:
        sync_expense_to_notion(txn)
        s["output"] = f"Updated {category} row in Notion"

    # Log to simulation events
    event_logger.log_event(
        event_type="budget_transaction",
        agent_name="budget_agent",
        action=f"logged_{txn_type}",
        content=f"£{amount:.2f} — {description} [{category}]",
        metadata={"amount": amount, "description": description, "category": category,
                  "section": section, "type": txn_type, "items": items, "store": store},
    )

    today = get_today_spending()
    type_emoji = {"income": "💰", "expense": "💸", "savings": "🏦"}
    emoji = type_emoji.get(txn_type, "💸")

    notion_url = get_notion_budget_url(txn["week_start"])
    items_line = f"\n   🛒 Items: {', '.join(items)}" if len(items) > 1 or items[0] != description else ""
    store_line = f"\n   🏪 Store: {store}" if store else ""
    category_link = f"\n📎 {category} detail: {category_url}" if category_url else ""
    budget_link = f"\n📎 Budget: {notion_url}" if notion_url else ""

    reply = (f"{emoji} Logged: £{amount:.2f} — {description}\n"
             f"   Category: {category} ({section})"
             f"{items_line}{store_line}\n"
             f"   Today: spent £{today['total_spent']:.2f} | earned £{today['total_earned']:.2f}"
             f"{category_link}{budget_link}")

    # Check budget alerts after logging
    alerts = check_budget_alerts()
    if alerts:
        reply += "\n\n" + "\n".join(alerts)
        # Also send alerts to the dedicated alert bot
        try:
            from jobpulse.telegram_bots import send_alert
            send_alert("⚠️ BUDGET ALERT\n\n" + "\n".join(alerts))
        except Exception as e:
            logger.warning("Failed to send budget alert to Telegram: %s", e)

    trail.finalize(f"Logged £{amount:.2f} {txn_type} → {category}")
    return reply


def set_budget(text: str) -> str:
    """Parse and set a planned budget. E.g. 'set budget groceries 50'"""
    match = re.search(r"(\d+(?:\.\d{1,2})?)", text)
    if not match:
        return "Include an amount. E.g.: set budget groceries 50"

    amount = float(match.group(1))
    desc = re.sub(r"\d+(\.\d{1,2})?", "", text).strip()
    desc = re.sub(r"^(set\s+)?budget\s+", "", desc, flags=re.IGNORECASE).strip()

    if not desc:
        return "Which category? E.g.: set budget groceries 50"

    section, category = classify_transaction(desc, amount, "expense")

    # Validate that the resolved category is a known budget category
    known_categories = {c for _, c in ALL_CATEGORIES.values()}
    if category not in known_categories:
        return (
            f"Unknown category '{category}'. Known categories:\n"
            + ", ".join(sorted(known_categories))
        )

    result = set_planned_budget(category, section, amount)
    if isinstance(result, dict) and "error" in result:
        return result["error"]

    # Update the Planned (col 1) column in Notion
    row_id = ROW_IDS.get(category)
    if row_id:
        _update_planned_column(row_id, amount)

    notion_url = get_notion_budget_url()
    link_line = f"\n📎 {notion_url}" if notion_url else ""
    return f"📋 Budget set: {category} = £{amount:.2f}/period{link_line}"


# ── Formatting ──

def format_week_summary(summary: dict) -> str:
    if not summary["by_category"] and summary["income_total"] == 0:
        return "💰 No transactions logged this period yet."

    period_end = _get_period_end(summary["week_start"])
    lines = [f"💰 BUDGET PERIOD ({summary['week_start']} to {period_end}):\n"]

    # Income
    income_items = [c for c in summary["by_category"] if c["type"] == "income"]
    if income_items:
        lines.append("  📥 INCOME:")
        for c in income_items:
            lines.append(f"    {c['category']}: £{c['total']:.2f}")
        lines.append(f"    Total: £{summary['income_total']:.2f}\n")

    # Expenses
    expense_items = [c for c in summary["by_category"] if c["type"] == "expense"]
    if expense_items:
        lines.append("  📤 SPENDING:")
        for c in expense_items:
            planned = summary["planned"].get(c["category"])
            budget_str = f" / £{planned:.0f}" if planned else ""
            lines.append(f"    {c['category']}: £{c['total']:.2f}{budget_str}")
        lines.append(f"    Total: £{summary['spending_total']:.2f}\n")

    # Savings
    savings_items = [c for c in summary["by_category"] if c["type"] == "savings"]
    if savings_items:
        lines.append("  🏦 SAVINGS + DEBT:")
        for c in savings_items:
            lines.append(f"    {c['category']}: £{c['total']:.2f}")
        lines.append(f"    Total: £{summary['savings_total']:.2f}\n")

    # Net
    lines.append(f"  📊 NET: £{summary['net']:.2f}")

    # Recent
    if summary["recent"]:
        lines.append(f"\n  Recent:")
        for item in summary["recent"][:5]:
            emoji = "📥" if item["type"] == "income" else "📤" if item["type"] == "expense" else "🏦"
            lines.append(f"    {emoji} £{item['amount']:.2f} — {item['description']}")

    # Notion link
    notion_url = get_notion_budget_url(summary["week_start"])
    if notion_url:
        lines.append(f"\n📎 View in Notion: {notion_url}")

    return "\n".join(lines)


def format_today(data: dict) -> str:
    if not data["items"]:
        return "💰 No transactions today."

    lines = [f"💰 TODAY ({data['date']}): spent £{data['total_spent']:.2f} | earned £{data['total_earned']:.2f}\n"]
    for item in data["items"]:
        emoji = "📥" if item["type"] == "income" else "📤" if item["type"] == "expense" else "🏦"
        lines.append(f"  {emoji} £{item['amount']:.2f} — {item['description']} [{item['category']}]")
    return "\n".join(lines)


# ── Recurring Expenses (delegated to budget_recurring.py) ──
from jobpulse.budget_recurring import (  # noqa: F401 — re-exported for callers
    add_recurring, process_recurring, list_recurring, remove_recurring, format_recurring,
)


# ── Budget Alerts ──

def check_budget_alerts() -> list[str]:
    """For each category with a planned budget, check if actual >= 80% of planned.
    Returns alert messages."""
    week_start = _get_week_start()
    conn = _get_conn()

    planned_rows = conn.execute(
        "SELECT category, section, planned_amount FROM planned_budgets WHERE week_start=? AND planned_amount > 0",
        (week_start,)
    ).fetchall()

    alerts = []
    today = datetime.now()
    # Calculate days left in 28-day period
    period_start_dt = datetime.strptime(week_start, "%Y-%m-%d")
    days_elapsed = (today - period_start_dt).days
    days_left = max(0, PERIOD_DAYS - 1 - days_elapsed)

    for row in planned_rows:
        category = row["category"]
        planned = row["planned_amount"]

        actual = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE week_start=? AND category=?",
            (week_start, category)
        ).fetchone()[0]

        if planned > 0 and actual >= planned * 0.8:
            pct = int((actual / planned) * 100)
            alerts.append(
                f"⚠️ {category}: £{actual:.0f}/£{planned:.0f} ({pct}%) — {days_left} day{'s' if days_left != 1 else ''} left in period"
            )

    # Historical pace alerts (compare to last period's spending by this day in the period)
    last_period = (period_start_dt - timedelta(days=PERIOD_DAYS)).strftime("%Y-%m-%d")
    cutoff_date = (datetime.strptime(last_period, "%Y-%m-%d") + timedelta(days=days_elapsed)).strftime("%Y-%m-%d")

    for row in planned_rows:
        category = row["category"]
        planned = row["planned_amount"]
        actual = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE week_start=? AND category=?",
            (week_start, category)
        ).fetchone()[0]

        # What was spent by this day in the last period?
        last_week_by_now = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE week_start=? AND category=? AND date <= ?",
            (last_period, category, cutoff_date)
        ).fetchone()[0]

        if last_week_by_now > 0 and actual > last_week_by_now * 1.5:
            # Spending 50%+ more than usual pace
            pct_over = int(((actual - last_week_by_now) / last_week_by_now) * 100)
            alert = f"📈 {category}: £{actual:.0f} so far (was £{last_week_by_now:.0f} by day {days_elapsed} last period, +{pct_over}%)"
            if alert not in [a for a in alerts]:  # avoid duplicates with threshold alerts
                alerts.append(alert)

    conn.close()
    return alerts


# ── Undo Last Transaction ──

def undo_last_transaction(pick: int = None) -> str:
    """Show last 5 transactions for selection, or delete a specific one by number.

    - undo         → shows last 5 with numbers
    - undo 3       → deletes transaction #3 from the list
    """
    conn = _get_conn()
    recent = conn.execute(
        "SELECT * FROM transactions ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    conn.close()

    if not recent:
        return "No transactions to undo."

    recent = [dict(r) for r in recent]

    # If no pick specified, show the list
    if pick is None:
        lines = ["↩️ UNDO — Select a transaction to remove:\n"]
        for i, txn in enumerate(recent, 1):
            emoji = "📥" if txn["type"] == "income" else "📤" if txn["type"] == "expense" else "🏦"
            lines.append(f"  {i}. {emoji} £{txn['amount']:.2f} — {txn['description']} [{txn['category']}] ({txn['date']})")
        lines.append("\nReply: undo 1, undo 2, or undo 1,3 for multiple")
        notion_url = get_notion_budget_url()
        lines.append(f"\n📎 {notion_url}")
        return "\n".join(lines)

    # Pick specified — delete that transaction
    if pick < 1 or pick > len(recent):
        return f"Invalid number. Choose 1-{len(recent)}."

    target = recent[pick - 1]

    conn = _get_conn()
    # Verify transaction still exists (guards against double-undo)
    exists = conn.execute("SELECT id FROM transactions WHERE id=?", (target["id"],)).fetchone()
    if not exists:
        conn.close()
        return "Transaction already deleted."
    conn.execute("DELETE FROM transactions WHERE id=?", (target["id"],))
    conn.commit()
    conn.close()

    # Recalculate Notion totals
    try:
        week_start = target["week_start"]
        category = target["category"]
        row_id = ROW_IDS.get(category)
        if row_id:
            conn2 = _get_conn()
            new_total = conn2.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE week_start=? AND category=?",
                (week_start, category)
            ).fetchone()[0]
            conn2.close()
            _update_table_row(row_id, f"£{new_total:.2f}")
        _update_section_totals(week_start)
    except Exception as e:
        logger.warning("Undo Notion sync failed: %s", e)

    notion_url = get_notion_budget_url(target["week_start"])
    return (f"✅ Removed: £{target['amount']:.2f} — {target['description']} "
            f"[{target['category']}] ({target['date']})\n\n"
            f"Notion budget sheet updated.\n📎 {notion_url}")


# ── Work Hours / Salary Tracking (delegated to budget_salary.py) ──
from jobpulse.budget_salary import (  # noqa: F401 — re-exported for callers
    HOURLY_RATE, TAX_RATE, SAVINGS_RATE, WORD_TO_NUM,
    _get_salary_week_start, _get_or_create_salary_page,
    _add_row_to_salary_page, _parse_date_from_text, _words_to_numbers,
    log_hours, confirm_savings_transfer, get_hours_summary,
    _rebuild_notion_timesheet, undo_hours,
)


init_db()
