"""Budget agent — tracks income/spending, categorizes with LLM, updates Notion weekly budget sheet.

Matches the user's exact Notion "Weekly Budget Sheet" structure:

  INCOME: Salary, Freelance, Other
  FIXED EXPENSES: Rent/Mortgage, Utilities, Phone/Internet, Subscriptions, Insurance
  VARIABLE SPENDING: Groceries, Eating out, Transport, Shopping, Entertainment, Health, Misc
  SAVINGS + DEBT: Savings, Investments, Credit card/Loan payment
  WEEKLY SUMMARY: Total income, Total spending, Total savings, Net

Each week gets its own Notion page (cloned from template structure).
The Notion tables are updated in-place as you log expenses/income.
"""

import re
import json
import sqlite3
from datetime import datetime, timedelta
from jobpulse.config import NOTION_API_KEY, NOTION_PARENT_PAGE_ID, DATA_DIR
from jobpulse.notion_agent import _notion_api

# ── Your existing Notion Weekly Budget Sheet ──
# Page: https://www.notion.so/Weekly-Budget-Sheet-50f750e493694f5e91e4f1680e7192fd
BUDGET_PAGE_ID = "50f750e4-9369-4f5e-91e4-f1680e7192fd"

# Table IDs inside the sheet
TABLE_IDS = {
    "income": "c5d42e98-fdbe-4ded-9628-fcf0d974707b",
    "fixed": "e73bef32-25a6-4331-ad5f-6f4a101e426b",
    "variable": "0d77a285-e62b-4190-9e5e-7e39bb025c3f",
    "savings": "52a95f38-273f-4cb1-a586-71a359c80912",
    "summary": "04eef488-e09a-4cbe-9177-44252f4c06de",
}

# Row IDs for each category (maps category name → row block ID)
# Col 0 = label, Col 1 = Planned, Col 2 = Actual, Col 3 = Notes/Date
ROW_IDS = {
    # Income
    "Salary": "3fba291a-3edf-498c-b5fc-c2445db935c9",
    "Freelance": "a20ee3b5-861d-478d-b01a-1583bc99bab1",
    "Other": "15ca2782-800c-4fef-915c-088ec71e1a2a",
    "Total income": "0d6e9a9e-09d1-454f-a90a-25f3c39bf1b6",
    # Fixed expenses
    "Rent / Mortgage": "d0c830bc-2e09-4d3e-8ea6-8c9bee0d111f",
    "Utilities": "752a0174-e663-42b2-b0c2-401476892ec5",
    "Phone / Internet": "6dd0cce5-7b12-41b3-8c45-b26dcc34f3fe",
    "Subscriptions": "1983caf4-69cd-4a21-9d3f-eb36f1a9cbd4",
    "Insurance": "b1e1ee11-03d9-4ba9-bf2c-8c85f9d06a5e",
    "Total fixed": "345049f7-ba91-4bef-ad79-86ff1420524f",
    # Variable spending
    "Groceries": "abd561c5-6453-49b3-9690-41e1ad6b2f53",
    "Eating out": "908e9fc8-464e-4b19-8393-84f4597beab3",
    "Transport": "69be65a1-0a68-4bb1-95f2-35d73341876d",
    "Shopping": "1b430b3a-992e-4591-a61e-c6aee7ed69f2",
    "Entertainment": "b5c87445-8f5b-4526-bd57-d32f341be02c",
    "Health": "ec7677c1-d00c-4e04-b53a-2aa49ec4a732",
    "Misc": "7ed615cc-98c7-4f0f-a0bf-7c76905a7a09",
    "Total variable": "719e132c-755c-43a4-a7cf-ec8a3534e037",
    # Savings + debt
    "Savings": "5486409b-07dc-47cc-9250-a25ae0552c31",
    "Investments": "21d0a510-24c8-4084-b532-423b7993911d",
    "Credit card / Loan payment": "0f318b42-3fbf-4141-b63b-bbefd2bc1ecf",
    "Total savings + debt": "9dd850ee-6c38-42dd-8b87-82dd59b17310",
    # Summary
    "Total income (summary)": "7a71026f-daca-4387-b34e-1386062a7505",
    "Total spending (fixed + variable)": "9c4df293-1f90-43d8-8724-98b8a9875a7d",
    "Total savings + debt (summary)": "36aec6cb-ac1e-48da-9b78-779007192a9b",
    "Net (income - spending - savings/debt)": "8867602a-9249-49ad-b316-f03321875c02",
}

DB_PATH = DATA_DIR / "budget.db"

# ── Categories matching the Notion sheet exactly ──

INCOME_CATEGORIES = {
    "salary": "Salary",
    "wage": "Salary",
    "pay": "Salary",
    "paycheck": "Salary",
    "freelance": "Freelance",
    "contract": "Freelance",
    "gig": "Freelance",
    "side hustle": "Freelance",
    "other income": "Other",
    "refund": "Other",
    "gift received": "Other",
    "cashback": "Other",
}

FIXED_EXPENSE_CATEGORIES = {
    "rent": "Rent / Mortgage",
    "mortgage": "Rent / Mortgage",
    "utilities": "Utilities",
    "electricity": "Utilities",
    "water": "Utilities",
    "gas bill": "Utilities",
    "phone": "Phone / Internet",
    "internet": "Phone / Internet",
    "broadband": "Phone / Internet",
    "mobile": "Phone / Internet",
    "subscription": "Subscriptions",
    "netflix": "Subscriptions",
    "spotify": "Subscriptions",
    "gym membership": "Subscriptions",
    "apple": "Subscriptions",
    "amazon prime": "Subscriptions",
    "insurance": "Insurance",
    "car insurance": "Insurance",
    "health insurance": "Insurance",
}

VARIABLE_EXPENSE_CATEGORIES = {
    "groceries": "Groceries",
    "supermarket": "Groceries",
    "tesco": "Groceries",
    "aldi": "Groceries",
    "lidl": "Groceries",
    "sainsbury": "Groceries",
    "eating out": "Eating out",
    "restaurant": "Eating out",
    "takeaway": "Eating out",
    "coffee": "Eating out",
    "lunch": "Eating out",
    "dinner": "Eating out",
    "breakfast": "Eating out",
    "food": "Eating out",
    "uber eats": "Eating out",
    "deliveroo": "Eating out",
    "transport": "Transport",
    "uber": "Transport",
    "taxi": "Transport",
    "bus": "Transport",
    "train": "Transport",
    "fuel": "Transport",
    "petrol": "Transport",
    "parking": "Transport",
    "oyster": "Transport",
    "shopping": "Shopping",
    "clothes": "Shopping",
    "amazon": "Shopping",
    "electronics": "Shopping",
    "shoes": "Shopping",
    "entertainment": "Entertainment",
    "cinema": "Entertainment",
    "movie": "Entertainment",
    "game": "Entertainment",
    "concert": "Entertainment",
    "drinks": "Entertainment",
    "pub": "Entertainment",
    "bar": "Entertainment",
    "health": "Health",
    "pharmacy": "Health",
    "doctor": "Health",
    "dentist": "Health",
    "medicine": "Health",
    "gym": "Health",
    "misc": "Misc",
}

SAVINGS_CATEGORIES = {
    "savings": "Savings",
    "save": "Savings",
    "emergency fund": "Savings",
    "investment": "Investments",
    "invest": "Investments",
    "stocks": "Investments",
    "crypto": "Investments",
    "isa": "Investments",
    "pension": "Investments",
    "credit card": "Credit card / Loan payment",
    "loan": "Credit card / Loan payment",
    "debt": "Credit card / Loan payment",
    "repayment": "Credit card / Loan payment",
}

# Flat lookup: category_name → (section, display_name)
ALL_CATEGORIES = {}
for k, v in INCOME_CATEGORIES.items():
    ALL_CATEGORIES[k] = ("income", v)
for k, v in FIXED_EXPENSE_CATEGORIES.items():
    ALL_CATEGORIES[k] = ("fixed", v)
for k, v in VARIABLE_EXPENSE_CATEGORIES.items():
    ALL_CATEGORIES[k] = ("variable", v)
for k, v in SAVINGS_CATEGORIES.items():
    ALL_CATEGORIES[k] = ("savings", v)


# ── SQLite Storage ──

def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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
    """)
    conn.commit()
    conn.close()


def _get_week_start(date: datetime = None) -> str:
    date = date or datetime.now()
    monday = date - timedelta(days=date.weekday())
    return monday.strftime("%Y-%m-%d")


def get_notion_budget_url(week_start: str = None) -> str:
    """Get the Notion URL for the budget sheet."""
    page_id = BUDGET_PAGE_ID.replace("-", "")
    return f"https://www.notion.so/{page_id}"


def _update_table_row(row_id: str, col2_value: str, col3_value: str = None):
    """Update a table row's Actual (col 2) and optionally Notes (col 3).

    IMPORTANT: We must read the existing row first to preserve col 0 (category name)
    and col 1 (planned amount). Notion's PATCH replaces ALL cells — sending []
    for a cell erases it.
    """
    # Read current row to preserve col 0 and col 1
    current = _notion_api("GET", f"/blocks/{row_id}")
    existing_cells = current.get("table_row", {}).get("cells", [[], [], [], []])

    # Keep col 0 and col 1 as-is, update col 2 (actual), optionally col 3 (notes/date)
    cells = [
        existing_cells[0] if len(existing_cells) > 0 else [],  # preserve category name
        existing_cells[1] if len(existing_cells) > 1 else [],  # preserve planned amount
        [{"type": "text", "text": {"content": col2_value}}],   # update actual
        [{"type": "text", "text": {"content": col3_value}}] if col3_value is not None
            else (existing_cells[3] if len(existing_cells) > 3 else []),  # preserve or update notes
    ]
    _notion_api("PATCH", f"/blocks/{row_id}", {
        "table_row": {"cells": cells}
    })


def sync_expense_to_notion(txn: dict):
    """Update the Actual column in the correct row of your existing budget sheet."""
    category = txn["category"]
    row_id = ROW_IDS.get(category)
    if not row_id:
        print(f"[Budget] No row ID for category: {category}")
        return

    # Get current total for this category from SQLite
    conn = _get_conn()
    total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE week_start=? AND category=?",
        (txn["week_start"], category)
    ).fetchone()[0]
    conn.close()

    # Update the Actual column with the running total, date in notes
    _update_table_row(row_id, f"£{total:.2f}", f"Last: {txn['description']} ({txn['date']})")

    # Also update the section total row
    _update_section_totals(txn["week_start"])


def _update_section_totals(week_start: str = None):
    """Recalculate and update all Total rows and the Summary table."""
    week_start = week_start or _get_week_start()
    conn = _get_conn()

    # Section totals
    income_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND type='income'",
        (week_start,)
    ).fetchone()[0]

    fixed_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND section='fixed'",
        (week_start,)
    ).fetchone()[0]

    variable_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND section='variable'",
        (week_start,)
    ).fetchone()[0]

    savings_total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE week_start=? AND type='savings'",
        (week_start,)
    ).fetchone()[0]

    conn.close()

    spending_total = fixed_total + variable_total
    net = income_total - spending_total - savings_total

    # Update Total rows
    _update_table_row(ROW_IDS["Total income"], f"£{income_total:.2f}")
    _update_table_row(ROW_IDS["Total fixed"], f"£{fixed_total:.2f}")
    _update_table_row(ROW_IDS["Total variable"], f"£{variable_total:.2f}")
    _update_table_row(ROW_IDS["Total savings + debt"], f"£{savings_total:.2f}")

    # Update Summary table
    _update_table_row(ROW_IDS["Total income (summary)"], f"£{income_total:.2f}")
    _update_table_row(ROW_IDS["Total spending (fixed + variable)"], f"£{spending_total:.2f}")
    _update_table_row(ROW_IDS["Total savings + debt (summary)"], f"£{savings_total:.2f}")

    # Net with difference
    _update_table_row(
        ROW_IDS["Net (income - spending - savings/debt)"],
        f"£{net:.2f}",
        "✅ Positive" if net >= 0 else "⚠️ Negative"
    )


def add_transaction(amount: float, description: str, category: str,
                    section: str, txn_type: str) -> dict:
    now = datetime.now()
    week_start = _get_week_start(now)

    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO transactions (amount, description, category, section, type, date, week_start, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (amount, description, category, section, txn_type, now.strftime("%Y-%m-%d"), week_start, now.isoformat())
    )
    conn.commit()
    conn.close()

    return {"id": cursor.lastrowid, "amount": amount, "description": description,
            "category": category, "section": section, "type": txn_type,
            "date": now.strftime("%Y-%m-%d"), "week_start": week_start}


def set_planned_budget(category: str, section: str, amount: float, week_start: str = None):
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

def classify_transaction(description: str, amount: float, txn_type: str = "expense") -> tuple[str, str]:
    """Classify into (section, category). First tries keyword match, then LLM."""

    desc_lower = description.lower()

    # Keyword match first (free)
    for keyword, (section, category) in ALL_CATEGORIES.items():
        if keyword in desc_lower:
            if txn_type == "income" and section == "income":
                return section, category
            elif txn_type == "expense" and section in ("fixed", "variable"):
                return section, category
            elif txn_type == "savings" and section == "savings":
                return section, category
            elif txn_type == "expense":
                return section, category

    # LLM fallback
    try:
        from openai import OpenAI
        from jobpulse.config import OPENAI_API_KEY

        categories_list = """
INCOME: Salary, Freelance, Other
FIXED EXPENSES: Rent / Mortgage, Utilities, Phone / Internet, Subscriptions, Insurance
VARIABLE: Groceries, Eating out, Transport, Shopping, Entertainment, Health, Misc
SAVINGS: Savings, Investments, Credit card / Loan payment"""

        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Classify this {txn_type} into one category:
{categories_list}

Transaction: £{amount:.2f} — "{description}"

Respond with ONLY: section|category
Example: variable|Eating out
Example: income|Salary
Example: fixed|Subscriptions"""}],
            max_tokens=15, temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        parts = raw.split("|")
        if len(parts) == 2:
            return parts[0].strip().lower(), parts[1].strip()
    except Exception as e:
        print(f"[Budget] LLM classify failed: {e}")

    # Default
    if txn_type == "income":
        return "income", "Other"
    elif txn_type == "savings":
        return "savings", "Savings"
    return "variable", "Misc"


# ── Spend/Earn Parsing ──

def parse_transaction(text: str) -> dict | None:
    """Parse natural language into {amount, description, type}.

    Handles:
      "spent 15 on lunch" → expense
      "earned 500 freelance" → income
      "saved 100" → savings
      "£8.50 coffee" → expense
      "income 2000 salary" → income
    """
    text = text.strip()

    # Detect type from keywords
    txn_type = "expense"
    if re.match(r"^(earned|income|received|got paid|salary|freelance)", text, re.IGNORECASE):
        txn_type = "income"
        text = re.sub(r"^(earned|income|received|got paid)\s+", "", text, flags=re.IGNORECASE)
    elif re.match(r"^(saved|saving|invest|debt|loan|repay|credit card)", text, re.IGNORECASE):
        txn_type = "savings"
        text = re.sub(r"^(saved|saving)\s+", "", text, flags=re.IGNORECASE)
    else:
        text = re.sub(r"^(spent|spend|paid|bought|got)\s+", "", text, flags=re.IGNORECASE)

    # Extract amount
    match = re.search(r"[£$€]?\s*(\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None

    amount = float(match.group(1))
    if amount <= 0 or amount > 100000:
        return None

    # Extract description
    start = match.start()
    if start > 0 and text[start - 1] in "£$€":
        start -= 1
    desc = text[:start] + " " + text[match.end():]
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = re.sub(r"^(on|for|at|to)\s+", "", desc, flags=re.IGNORECASE)
    desc = desc.strip() or "Unspecified"

    return {"amount": amount, "description": desc, "type": txn_type}


def log_transaction(text: str) -> str:
    """Full pipeline: parse → classify → store → reply."""
    parsed = parse_transaction(text)
    if not parsed:
        return ("Couldn't parse that. Try:\n"
                "  spent 15 on lunch\n"
                "  £8.50 coffee\n"
                "  earned 500 freelance\n"
                "  saved 100 emergency fund")

    amount = parsed["amount"]
    description = parsed["description"]
    txn_type = parsed["type"]

    section, category = classify_transaction(description, amount, txn_type)
    txn = add_transaction(amount, description, category, section, txn_type)

    # Sync to Notion
    sync_expense_to_notion(txn)

    today = get_today_spending()
    type_emoji = {"income": "💰", "expense": "💸", "savings": "🏦"}
    emoji = type_emoji.get(txn_type, "💸")

    notion_url = get_notion_budget_url(txn["week_start"])
    link_line = f"\n\n📎 {notion_url}" if notion_url else ""

    return (f"{emoji} Logged: £{amount:.2f} — {description}\n"
            f"   Category: {category} ({section})\n"
            f"   Today: spent £{today['total_spent']:.2f} | earned £{today['total_earned']:.2f}"
            f"{link_line}")


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
    set_planned_budget(category, section, amount)

    # Update the Planned (col 1) column in Notion
    row_id = ROW_IDS.get(category)
    if row_id:
        _update_planned_column(row_id, amount)

    notion_url = get_notion_budget_url()
    link_line = f"\n📎 {notion_url}" if notion_url else ""
    return f"📋 Budget set: {category} = £{amount:.2f}/week{link_line}"


def _update_planned_column(row_id: str, amount: float):
    """Update the Planned (col 1) column of a row, preserving all other columns."""
    current = _notion_api("GET", f"/blocks/{row_id}")
    existing_cells = current.get("table_row", {}).get("cells", [[], [], [], []])

    cells = [
        existing_cells[0] if len(existing_cells) > 0 else [],  # preserve category name
        [{"type": "text", "text": {"content": f"£{amount:.2f}"}}],  # update planned
        existing_cells[2] if len(existing_cells) > 2 else [],  # preserve actual
        existing_cells[3] if len(existing_cells) > 3 else [],  # preserve notes
    ]
    _notion_api("PATCH", f"/blocks/{row_id}", {
        "table_row": {"cells": cells}
    })


# ── Formatting ──

def format_week_summary(summary: dict) -> str:
    if not summary["by_category"] and summary["income_total"] == 0:
        return "💰 No transactions logged this week yet."

    lines = [f"💰 WEEKLY BUDGET (since {summary['week_start']}):\n"]

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


init_db()
