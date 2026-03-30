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
from jobpulse import event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)

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
    "apple music": "Subscriptions",
    "apple tv": "Subscriptions",
    "apple one": "Subscriptions",
    "icloud": "Subscriptions",
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
    "haircut": "Health",
    "barber": "Health",
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


def _get_week_start(date: datetime = None) -> str:
    date = date or datetime.now()
    monday = date - timedelta(days=date.weekday())
    return monday.strftime("%Y-%m-%d")


def get_notion_budget_url(week_start: str = None) -> str:
    """Get the Notion URL for the budget sheet."""
    page_id = BUDGET_PAGE_ID.replace("-", "")
    return f"https://www.notion.so/{page_id}"


def _update_table_row(row_id: str, col2_value: str, col3_value: str = None, col0_link: str = None):
    """Update a table row's Actual (col 2) and optionally Notes (col 3).

    If col0_link is provided, the category name (col 0) becomes a clickable link
    to the category's detail sub-page in Notion.

    IMPORTANT: We must read the existing row first to preserve col 0 (category name)
    and col 1 (planned amount). Notion's PATCH replaces ALL cells — sending []
    for a cell erases it.
    """
    # Read current row to preserve col 0 and col 1
    current = _notion_api("GET", f"/blocks/{row_id}")
    existing_cells = current.get("table_row", {}).get("cells", [[], [], [], []])

    # If we have a link, make col 0 (category name) clickable
    if col0_link and existing_cells and existing_cells[0]:
        # Get the original category name text
        orig_text = "".join(t.get("plain_text", "") for t in existing_cells[0])
        if orig_text:
            col0_cell = [{"type": "text", "text": {"content": orig_text, "link": {"url": col0_link}}}]
        else:
            col0_cell = existing_cells[0]
    else:
        col0_cell = existing_cells[0] if len(existing_cells) > 0 else []

    cells = [
        col0_cell,
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

    # Get the category sub-page link
    from jobpulse.budget_tracker import get_category_page_url
    sub_url = get_category_page_url(category, txn["week_start"])

    # For Salary, also check for the timesheet page
    if category == "Salary" and not sub_url:
        conn2 = _get_conn()
        from jobpulse.budget_agent import _get_salary_week_start
        salary_week = _get_salary_week_start(datetime.strptime(txn["date"], "%Y-%m-%d"))
        ts_row = conn2.execute(
            "SELECT notion_page_id FROM work_hours WHERE week_start=? AND notion_page_id != '' LIMIT 1",
            (salary_week,)
        ).fetchone()
        conn2.close()
        if ts_row:
            sub_url = f"https://www.notion.so/{ts_row['notion_page_id'].replace('-', '')}"

    notes = f"Last: {txn['description']} ({txn['date']})"

    # Update the Actual column + make category name a clickable link to sub-page
    _update_table_row(row_id, f"£{total:.2f}", notes, col0_link=sub_url)

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

def classify_transaction(description: str, amount: float, txn_type: str = "expense") -> tuple[str, str]:
    """Classify into (section, category) with reason tracking.

    4-stage pipeline:
    1. Store→category inference first (Tesco → Groceries, overrides generic keywords)
    2. Multi-word phrase match (longest match wins, word-boundary safe)
    3. Single-word keyword match (word-boundary safe)
    4. LLM fallback
    """
    import re

    desc_lower = description.lower()

    # Stage 1: Store→category inference FIRST (store is strongest signal)
    # If someone says "drinks from tesco", Tesco = Groceries beats "drinks" = Entertainment
    store_category_map = {
        # Grocery stores → Groceries
        "tesco": ("variable", "Groceries"), "aldi": ("variable", "Groceries"),
        "lidl": ("variable", "Groceries"), "sainsbury": ("variable", "Groceries"),
        "asda": ("variable", "Groceries"), "morrisons": ("variable", "Groceries"),
        "waitrose": ("variable", "Groceries"), "co-op": ("variable", "Groceries"),
        "iceland": ("variable", "Groceries"), "m&s food": ("variable", "Groceries"),
        # Eating out stores → Eating out
        "pret": ("variable", "Eating out"), "costa": ("variable", "Eating out"),
        "starbucks": ("variable", "Eating out"), "greggs": ("variable", "Eating out"),
        "mcdonald": ("variable", "Eating out"), "kfc": ("variable", "Eating out"),
        "nando": ("variable", "Eating out"), "subway": ("variable", "Eating out"),
        "wagamama": ("variable", "Eating out"), "wetherspoon": ("variable", "Eating out"),
        "domino": ("variable", "Eating out"), "pizza hut": ("variable", "Eating out"),
        # Health stores → Health
        "boots": ("variable", "Health"), "superdrug": ("variable", "Health"),
        "holland and barrett": ("variable", "Health"),
        # Shopping stores → Shopping
        "argos": ("variable", "Shopping"), "jd sports": ("variable", "Shopping"),
        "primark": ("variable", "Shopping"), "tk maxx": ("variable", "Shopping"),
        "next": ("variable", "Shopping"), "asos": ("variable", "Shopping"),
        "zara": ("variable", "Shopping"),
    }
    for store, (section, category) in store_category_map.items():
        if re.search(rf"\b{re.escape(store)}\b", desc_lower):
            if txn_type == "expense":
                return section, category

    # Stage 2: Keyword match (longest-first, word-boundary safe)
    sorted_keywords = sorted(ALL_CATEGORIES.keys(), key=len, reverse=True)

    for keyword in sorted_keywords:
        section, category = ALL_CATEGORIES[keyword]
        if re.search(rf"\b{re.escape(keyword)}\b", desc_lower):
            if txn_type == "income" and section == "income":
                return section, category
            elif txn_type == "expense" and section in ("fixed", "variable"):
                return section, category
            elif txn_type == "savings" and section == "savings":
                return section, category
            elif txn_type == "expense":
                return section, category

    # Stage 3: LLM fallback
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
            llm_section = parts[0].strip().lower()
            llm_category = parts[1].strip()
            # Validate LLM response against real categories
            valid_categories = set()
            for _, (s, c) in ALL_CATEGORIES.items():
                valid_categories.add(c)
            if llm_category in valid_categories:
                return llm_section, llm_category
            # Try fuzzy match (LLM might say "Eating Out" vs "Eating out")
            for vc in valid_categories:
                if vc.lower() == llm_category.lower():
                    return llm_section, vc
            logger.warning("LLM returned invalid category: %s", llm_category)
    except Exception as e:
        logger.warning("LLM classify failed: %s", e)

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

    # Extract amount — supports: 15, 15.99, .50, £1,000, $1,000.50
    # First strip commas from numbers like 1,000
    text_clean = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    match = re.search(r"[£$€]?\s*(\d*\.?\d+)", text_clean)
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
        except Exception:
            pass

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


# ── Recurring Expenses ──

def add_recurring(amount: float, description: str, category: str,
                  section: str, txn_type: str, frequency: str,
                  day: int | None = None) -> dict:
    """Add a recurring transaction rule (daily/weekly/monthly)."""
    # Validate frequency
    valid_frequencies = ("daily", "weekly", "monthly")
    if frequency not in valid_frequencies:
        return {"error": f"Invalid frequency '{frequency}'. Must be one of: {', '.join(valid_frequencies)}"}

    now = datetime.now()
    conn = _get_conn()

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
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    conn = _get_conn()

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
            txn = add_transaction(
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
                sync_expense_to_notion(txn)
            except Exception as e:
                logger.warning("Recurring Notion sync failed: %s", e)

            logged.append(txn)

    conn.close()
    return logged


def list_recurring() -> list[dict]:
    """Return all active recurring transactions."""
    conn = _get_conn()
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

    conn = _get_conn()
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
    # Calculate days left in week (Mon=0 .. Sun=6)
    days_left = 6 - today.weekday()
    if days_left < 0:
        days_left = 0

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
                f"⚠️ {category}: £{actual:.0f}/£{planned:.0f} ({pct}%) — {days_left} day{'s' if days_left != 1 else ''} left in the week"
            )

    # Historical pace alerts (compare to last week's spending by this day)
    day_of_week = today.weekday()  # 0=Mon .. 6=Sun
    last_week = (datetime.strptime(week_start, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")

    for row in planned_rows:
        category = row["category"]
        planned = row["planned_amount"]
        actual = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE week_start=? AND category=?",
            (week_start, category)
        ).fetchone()[0]

        # What was spent by this day last week?
        last_week_by_now = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE week_start=? AND category=? AND CAST(strftime('%w', date) AS INTEGER) <= ?",
            (last_week, category, day_of_week)
        ).fetchone()[0]

        if last_week_by_now > 0 and actual > last_week_by_now * 1.5:
            # Spending 50%+ more than usual pace
            pct_over = int(((actual - last_week_by_now) / last_week_by_now) * 100)
            alert = f"📈 {category}: £{actual:.0f} so far (was £{last_week_by_now:.0f} by this day last week, +{pct_over}%)"
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


# ── Work Hours / Salary Tracking ──

import os
HOURLY_RATE = float(os.getenv("HOURLY_RATE", "13.99"))
TAX_RATE = 0.20   # UK basic rate income tax (no NI)
SAVINGS_RATE = 0.30  # 30% of after-tax goes to savings


def _get_salary_week_start(date: datetime = None) -> str:
    """Get the Sunday that starts this working week (Sunday–Saturday)."""
    date = date or datetime.now()
    # weekday(): Mon=0 ... Sun=6. We want Sunday as start.
    days_since_sunday = (date.weekday() + 1) % 7
    sunday = date - timedelta(days=days_since_sunday)
    return sunday.strftime("%Y-%m-%d")


def _get_or_create_salary_page(week_start: str) -> str:
    """Get or create a Notion sub-page for this week's salary timesheet."""
    # Check if we already have a page for this week
    conn = _get_conn()
    row = conn.execute(
        "SELECT DISTINCT notion_page_id FROM work_hours WHERE week_start=? AND notion_page_id != ''",
        (week_start,)
    ).fetchone()
    conn.close()

    if row and row["notion_page_id"]:
        return row["notion_page_id"]

    # Create a new Notion page for this week's timesheet
    parent_id = BUDGET_PAGE_ID
    week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    title = f"Salary Timesheet — {week_start} to {week_end}"

    data = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]},
        },
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": f"⏱️ Work Hours — Week of {week_start}"}}]
            }},
            {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"text": {"content": f"Hourly rate: £{HOURLY_RATE:.2f}/hr"}}]
            }},
            {"object": "block", "type": "divider", "divider": {}},
            # Table: Hours Worked | Hour Rate | Date | Total
            {"object": "block", "type": "table", "table": {
                "table_width": 4,
                "has_column_header": True,
                "has_row_header": False,
                "children": [
                    {"object": "block", "type": "table_row", "table_row": {
                        "cells": [
                            [{"type": "text", "text": {"content": "Hours Worked"}}],
                            [{"type": "text", "text": {"content": "Hour Rate"}}],
                            [{"type": "text", "text": {"content": "Date"}}],
                            [{"type": "text", "text": {"content": "Total"}}],
                        ]
                    }},
                ]
            }},
        ]
    }

    result = _notion_api("POST", "/pages", data)
    page_id = result.get("id", "")
    if page_id:
        logger.info("Created salary timesheet page: %s", page_id)
    return page_id


def _add_row_to_salary_page(page_id: str, hours: float, rate: float, date_str: str, total: float):
    """Append a row to the timesheet table."""
    if not page_id:
        return

    # Find the table block
    blocks = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")
    table_id = None
    for block in blocks.get("results", []):
        if block.get("type") == "table":
            table_id = block["id"]
            break

    if not table_id:
        logger.warning("No table found in salary page %s", page_id)
        return

    # First remove old TOTAL row if exists
    table_rows = _notion_api("GET", f"/blocks/{table_id}/children?page_size=100")
    for row in table_rows.get("results", []):
        cells = row.get("table_row", {}).get("cells", [])
        if cells and cells[0]:
            text = "".join(t.get("plain_text", "") for t in cells[0])
            if "TOTAL" in text.upper():
                _notion_api("DELETE", f"/blocks/{row['id']}")

    # Add the data row
    _notion_api("PATCH", f"/blocks/{table_id}/children", {
        "children": [
            {"object": "block", "type": "table_row", "table_row": {
                "cells": [
                    [{"type": "text", "text": {"content": f"{hours:.1f}"}}],
                    [{"type": "text", "text": {"content": f"£{rate:.2f}"}}],
                    [{"type": "text", "text": {"content": date_str}}],
                    [{"type": "text", "text": {"content": f"£{total:.2f}"}}],
                ]
            }},
        ]
    })

    # Add updated TOTAL row (use Sunday-based week to match work_hours table)
    conn = _get_conn()
    salary_week = _get_salary_week_start(datetime.strptime(date_str, "%Y-%m-%d"))
    totals = conn.execute(
        "SELECT SUM(hours) as h, SUM(total_earned) as e FROM work_hours WHERE week_start=?",
        (salary_week,)
    ).fetchone()
    conn.close()

    total_h = totals['h'] or 0
    total_e = totals['e'] or 0
    total_tax = round(total_e * TAX_RATE, 2)
    total_after_tax = round(total_e - total_tax, 2)
    total_savings = round(total_after_tax * SAVINGS_RATE, 2)

    _notion_api("PATCH", f"/blocks/{table_id}/children", {
        "children": [
            {"object": "block", "type": "table_row", "table_row": {
                "cells": [
                    [{"type": "text", "text": {"content": f"TOTAL: {total_h:.1f}h"}}],
                    [{"type": "text", "text": {"content": f"£{rate:.2f}/hr"}}],
                    [{"type": "text", "text": {"content": f"Week of {salary_week}"}}],
                    [{"type": "text", "text": {"content": f"£{total_e:.2f} gross | £{total_after_tax:.2f} net | Save £{total_savings:.2f}"}}],
                ]
            }},
        ]
    })


def _parse_date_from_text(text: str) -> tuple[str, datetime]:
    """Extract a date from text. Returns (cleaned_text, target_date).

    Supports: "yesterday", "monday", "tuesday", ..., "march 25", "25/03", "2026-03-25"
    If no date found, returns today.
    """
    now = datetime.now()
    text_lower = text.lower()

    # "yesterday"
    m = re.search(r"\byesterday\b", text_lower)
    if m:
        cleaned = text[:m.start()] + text[m.end():]
        return re.sub(r"\s+", " ", cleaned).strip(), now - timedelta(days=1)

    # Day names: "monday", "tuesday", etc. (most recent past occurrence)
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(day_names):
        m = re.search(rf"\b(on\s+)?{day}\b", text_lower)
        if m:
            current_day = now.weekday()
            days_ago = (current_day - i) % 7
            # days_ago == 0 means today IS that day — treat as today, not last week
            cleaned = text[:m.start()] + text[m.end():]
            return re.sub(r"\s+", " ", cleaned).strip(), now - timedelta(days=days_ago)

    # "march 25" or "mar 25"
    month_names = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month_pattern = "|".join(month_names.keys())
    m = re.search(rf"\b(on\s+)?({month_pattern})\s+(\d{{1,2}})\b", text_lower)
    if m:
        month = month_names[m.group(2)]
        day = int(m.group(3))
        try:
            target = datetime(now.year, month, day)
            if target > now:
                target = datetime(now.year - 1, month, day)
            cleaned = text[:m.start()] + text[m.end():]
            return re.sub(r"\s+", " ", cleaned).strip(), target
        except ValueError:
            pass

    # "25/03" or "25-03"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        try:
            target = datetime(now.year, month, day)
            if target > now:
                target = datetime(now.year - 1, month, day)
            cleaned = text[:m.start()] + text[m.end():]
            return re.sub(r"\s+", " ", cleaned).strip(), target
        except ValueError:
            pass

    # ISO: "2026-03-25"
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        try:
            target = datetime.strptime(m.group(1), "%Y-%m-%d")
            cleaned = text[:m.start()] + text[m.end():]
            return re.sub(r"\s+", " ", cleaned).strip(), target
        except ValueError:
            pass

    return text, now


WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50,
}


def _words_to_numbers(text: str) -> str:
    """Convert word-based hours+minutes to decimal hours.

    Handles:
      "six hours" → "6 hours"
      "seven and a half hours" → "7.5 hours"
      "six hours and thirty minutes" → "6.5 hours"
      "eight hours and forty-five minutes" → "8.75 hours"
      "six and a quarter hours" → "6.25 hours"
    """
    result = text.lower()

    # 1. "X hours and Y minutes" → decimal (must be before simple word replacement)
    def _hours_and_minutes(m):
        h_word = m.group(1)
        m_word = m.group(2)
        h = WORD_TO_NUM.get(h_word)
        mins = WORD_TO_NUM.get(m_word)
        if h is not None and mins is not None:
            return f"{h + mins / 60:.2f} hours"
        return m.group(0)

    result = re.sub(
        r"\b(\w+(?:-\w+)?)\s+hours?\s+and\s+(\w+(?:-\w+)?)\s+minutes?\b",
        _hours_and_minutes, result, flags=re.IGNORECASE
    )

    # 2. "X and a half hours" → decimal
    def _and_a_half(m):
        w = m.group(1)
        n = WORD_TO_NUM.get(w)
        if n is not None:
            return f"{n + 0.5} hours"
        return m.group(0)

    result = re.sub(r"\b(\w+)\s+and\s+a\s+half\s+hours?\b", _and_a_half, result, flags=re.IGNORECASE)

    # 3. "X and a quarter hours" → decimal
    def _and_a_quarter(m):
        w = m.group(1)
        n = WORD_TO_NUM.get(w)
        if n is not None:
            return f"{n + 0.25} hours"
        return m.group(0)

    result = re.sub(r"\b(\w+)\s+and\s+a\s+quarter\s+hours?\b", _and_a_quarter, result, flags=re.IGNORECASE)

    # 4. Simple word → digit (only before "hours/h/hr")
    for word, num in WORD_TO_NUM.items():
        result = re.sub(rf"\b{re.escape(word)}\b(?=\s*(?:hours?|hrs?|h)\b)", str(num), result, flags=re.IGNORECASE)

    return result


def log_hours(text: str) -> str:
    """Parse 'worked X hours' and log to SQLite + Notion salary timesheet.

    Week starts Sunday. Shows tax (20%) and savings suggestion (30% of after-tax).
    Supports: numbers, floats, words ("six hours", "seven and a half hours")
    Supports past dates: "yesterday", "on monday", "march 24"
    """
    # Convert word numbers to digits first
    text = _words_to_numbers(text)

    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", text, re.IGNORECASE)
    if not match:
        return ("Couldn't parse hours. Try:\n"
                "  worked 7 hours\n"
                "  worked 3.5h yesterday\n"
                "  worked 8h on monday\n"
                "  worked 6h march 24")

    hours = float(match.group(1))
    if hours <= 0 or hours > 24:
        return "Hours must be between 0 and 24."

    # Extract date (remove hours part first to avoid confusing date parser)
    text_without_hours = text[:match.start()] + text[match.end():]
    _, target_date = _parse_date_from_text(text_without_hours)

    rate = HOURLY_RATE
    gross = round(hours * rate, 2)
    tax = round(gross * TAX_RATE, 2)
    after_tax = round(gross - tax, 2)
    savings_suggestion = round(after_tax * SAVINGS_RATE, 2)

    date_str = target_date.strftime("%Y-%m-%d")
    week_start = _get_salary_week_start(target_date)

    # Get or create Notion timesheet page for this week
    page_id = _get_or_create_salary_page(week_start)

    # Store in SQLite
    conn = _get_conn()
    conn.execute(
        "INSERT INTO work_hours (hours, hourly_rate, total_earned, date, week_start, notion_page_id, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (hours, rate, gross, date_str, week_start, page_id, datetime.now().isoformat())
    )
    conn.commit()

    # Get week totals
    week_totals = conn.execute(
        "SELECT SUM(hours) as h, SUM(total_earned) as e FROM work_hours WHERE week_start=?",
        (week_start,)
    ).fetchone()
    conn.close()

    week_hours = week_totals["h"] or 0
    week_gross = week_totals["e"] or 0
    week_tax = round(week_gross * TAX_RATE, 2)
    week_after_tax = round(week_gross - week_tax, 2)
    week_savings = round(week_after_tax * SAVINGS_RATE, 2)

    # Add row to Notion timesheet table
    _add_row_to_salary_page(page_id, hours, rate, date_str, gross)

    # Also log as income in the main budget
    add_transaction(gross, f"Salary ({hours}h)", "Salary", "income", "income")
    sync_expense_to_notion({"category": "Salary", "week_start": _get_week_start(target_date),
                            "description": f"Salary ({hours}h)", "date": date_str})

    event_logger.log_event(
        event_type="budget_transaction",
        agent_name="budget_agent",
        action="logged_hours",
        content=f"{hours}h × £{rate:.2f} = £{gross:.2f}",
        metadata={"hours": hours, "rate": rate, "gross": gross, "tax": tax, "after_tax": after_tax},
    )

    page_url = f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else ""
    notion_link = f"\n📎 Timesheet: {page_url}" if page_url else ""
    budget_link = f"\n📎 Budget: {get_notion_budget_url()}"

    date_label = f" ({date_str})" if date_str != datetime.now().strftime("%Y-%m-%d") else ""
    return (f"⏱️ Logged: {hours}h × £{rate:.2f} = £{gross:.2f}{date_label}\n"
            f"  Tax (20%): -£{tax:.2f}\n"
            f"  After tax: £{after_tax:.2f}\n\n"
            f"📊 This week (Sun–Sat): {week_hours:.1f}h\n"
            f"  Gross:     £{week_gross:.2f}\n"
            f"  Tax (20%): -£{week_tax:.2f}\n"
            f"  After tax: £{week_after_tax:.2f}\n"
            f"  💰 Save 30%: £{week_savings:.2f}\n\n"
            f"💡 Transfer £{week_savings:.2f} to savings. Reply \"saved\" to confirm."
            f"{notion_link}{budget_link}")


def confirm_savings_transfer(week_start: str = None) -> str:
    """User confirmed they transferred savings. Log it and update Notion."""
    now = datetime.now()
    week_start = week_start or _get_salary_week_start(now)

    conn = _get_conn()
    week_totals = conn.execute(
        "SELECT SUM(total_earned) as e FROM work_hours WHERE week_start=?",
        (week_start,)
    ).fetchone()
    conn.close()

    week_gross = week_totals["e"] or 0
    if week_gross == 0:
        return "No hours logged this week — nothing to save."

    week_tax = round(week_gross * TAX_RATE, 2)
    week_after_tax = round(week_gross - week_tax, 2)
    savings_amount = round(week_after_tax * SAVINGS_RATE, 2)

    # Log as savings transaction
    add_transaction(savings_amount, f"Weekly savings (30% of £{week_after_tax:.2f})",
                    "Savings", "savings", "savings")
    sync_expense_to_notion({"category": "Savings", "week_start": _get_week_start(now),
                            "description": f"Weekly savings", "date": now.strftime("%Y-%m-%d")})

    # Add "Saved" row to the Notion timesheet
    conn2 = _get_conn()
    row = conn2.execute(
        "SELECT notion_page_id FROM work_hours WHERE week_start=? AND notion_page_id != '' LIMIT 1",
        (week_start,)
    ).fetchone()
    conn2.close()

    if row and row["notion_page_id"]:
        page_id = row["notion_page_id"]
        blocks = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")
        table_id = None
        for block in blocks.get("results", []):
            if block.get("type") == "table":
                table_id = block["id"]
                break
        if table_id:
            _notion_api("PATCH", f"/blocks/{table_id}/children", {
                "children": [
                    {"object": "block", "type": "table_row", "table_row": {
                        "cells": [
                            [{"type": "text", "text": {"content": "SAVED THIS WEEK"}}],
                            [{"type": "text", "text": {"content": "30% of after-tax"}}],
                            [{"type": "text", "text": {"content": now.strftime("%Y-%m-%d")}}],
                            [{"type": "text", "text": {"content": f"£{savings_amount:.2f}"}}],
                        ]
                    }}
                ]
            })

    notion_url = get_notion_budget_url()
    return (f"🏦 Confirmed! £{savings_amount:.2f} logged as savings.\n\n"
            f"  Gross this week: £{week_gross:.2f}\n"
            f"  Tax (20%): -£{week_tax:.2f}\n"
            f"  After tax: £{week_after_tax:.2f}\n"
            f"  Saved (30%): £{savings_amount:.2f}\n"
            f"  Remaining: £{round(week_after_tax - savings_amount, 2):.2f}\n\n"
            f"📎 {notion_url}")


def get_hours_summary(week_start: str = None) -> str:
    """Get formatted work hours summary for the week (Sunday–Saturday)."""
    week_start = week_start or _get_salary_week_start()
    conn = _get_conn()

    rows = conn.execute(
        "SELECT hours, hourly_rate, total_earned, date FROM work_hours "
        "WHERE week_start=? ORDER BY date", (week_start,)
    ).fetchall()

    totals = conn.execute(
        "SELECT SUM(hours) as h, SUM(total_earned) as e FROM work_hours WHERE week_start=?",
        (week_start,)
    ).fetchone()
    conn.close()

    if not rows:
        return "⏱️ No hours logged this week (Sun–Sat)."

    total_h = totals["h"] or 0
    total_gross = totals["e"] or 0
    total_tax = round(total_gross * TAX_RATE, 2)
    total_after_tax = round(total_gross - total_tax, 2)
    total_savings = round(total_after_tax * SAVINGS_RATE, 2)

    week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    lines = [f"⏱️ WORK HOURS ({week_start} to {week_end}):\n"]
    for r in rows:
        lines.append(f"  {r['date']} — {r['hours']:.1f}h × £{r['hourly_rate']:.2f} = £{r['total_earned']:.2f}")

    lines.append(f"\n  {'─' * 35}")
    lines.append(f"  TOTAL:     {total_h:.1f}h = £{total_gross:.2f}")
    lines.append(f"  Tax (20%): -£{total_tax:.2f}")
    lines.append(f"  After tax: £{total_after_tax:.2f}")
    lines.append(f"  💰 Save 30%: £{total_savings:.2f}")
    lines.append(f"  Remaining: £{round(total_after_tax - total_savings, 2):.2f}")

    page_url = ""
    conn2 = _get_conn()
    row = conn2.execute(
        "SELECT notion_page_id FROM work_hours WHERE week_start=? AND notion_page_id != '' LIMIT 1",
        (week_start,)
    ).fetchone()
    conn2.close()
    if row:
        page_url = f"\n\n📎 https://www.notion.so/{row['notion_page_id'].replace('-', '')}"

    return "\n".join(lines) + page_url


def _rebuild_notion_timesheet(page_id: str, salary_week_start: str):
    """Delete all data rows from Notion table and re-add from SQLite.

    SQLite is the source of truth. This ensures Notion matches after undo.
    Keeps the header row (first row), deletes everything else, then re-adds.
    """
    if not page_id:
        return

    # Find the table
    blocks = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")
    table_id = None
    for block in blocks.get("results", []):
        if block.get("type") == "table":
            table_id = block["id"]
            break

    if not table_id:
        logger.warning("No table found in timesheet page %s", page_id)
        return

    # Get all table rows
    table_rows = _notion_api("GET", f"/blocks/{table_id}/children?page_size=100")
    all_rows = table_rows.get("results", [])

    # Delete all rows EXCEPT the header (first row)
    for row in all_rows[1:]:
        _notion_api("DELETE", f"/blocks/{row['id']}")

    # Re-add data rows from SQLite
    conn = _get_conn()
    entries = conn.execute(
        "SELECT hours, hourly_rate, total_earned, date FROM work_hours "
        "WHERE week_start=? ORDER BY date",
        (salary_week_start,)
    ).fetchall()

    totals = conn.execute(
        "SELECT SUM(hours) as h, SUM(total_earned) as e FROM work_hours WHERE week_start=?",
        (salary_week_start,)
    ).fetchone()
    conn.close()

    # Add each data row
    new_rows = []
    for entry in entries:
        new_rows.append({
            "object": "block", "type": "table_row", "table_row": {
                "cells": [
                    [{"type": "text", "text": {"content": f"{entry['hours']:.1f}"}}],
                    [{"type": "text", "text": {"content": f"£{entry['hourly_rate']:.2f}"}}],
                    [{"type": "text", "text": {"content": entry['date']}}],
                    [{"type": "text", "text": {"content": f"£{entry['total_earned']:.2f}"}}],
                ]
            }
        })

    # Add TOTAL row
    total_h = totals['h'] or 0
    total_e = totals['e'] or 0
    if total_h > 0:
        total_tax = round(total_e * TAX_RATE, 2)
        total_after_tax = round(total_e - total_tax, 2)
        total_savings = round(total_after_tax * SAVINGS_RATE, 2)
        new_rows.append({
            "object": "block", "type": "table_row", "table_row": {
                "cells": [
                    [{"type": "text", "text": {"content": f"TOTAL: {total_h:.1f}h"}}],
                    [{"type": "text", "text": {"content": f"£{HOURLY_RATE:.2f}/hr"}}],
                    [{"type": "text", "text": {"content": f"Week of {salary_week_start}"}}],
                    [{"type": "text", "text": {"content": f"£{total_e:.2f} gross | £{total_after_tax:.2f} net | Save £{total_savings:.2f}"}}],
                ]
            }
        })

    if new_rows:
        _notion_api("PATCH", f"/blocks/{table_id}/children", {"children": new_rows})

    logger.info("Rebuilt timesheet for %s: %d entries", salary_week_start, len(entries))


def undo_hours(pick: int = None) -> str:
    """Show last 5 hour entries for selection, or delete a specific one by number."""
    conn = _get_conn()
    recent = conn.execute(
        "SELECT * FROM work_hours ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    conn.close()

    if not recent:
        return "No hours logged to undo."

    recent = [dict(r) for r in recent]

    # Show list
    if pick is None:
        lines = ["↩️ UNDO HOURS — Select an entry to remove:\n"]
        for i, entry in enumerate(recent, 1):
            lines.append(f"  {i}. ⏱️ {entry['hours']:.1f}h × £{entry['hourly_rate']:.2f} = £{entry['total_earned']:.2f} ({entry['date']})")
        lines.append("\nReply: undo hours 1, undo hours 2, or undo hours 1,3")
        # Show timesheet link
        page_id = recent[0].get("notion_page_id", "")
        if page_id:
            lines.append(f"\n📎 Timesheet: https://www.notion.so/{page_id.replace('-', '')}")
        return "\n".join(lines)

    # Delete specific entry
    if pick < 1 or pick > len(recent):
        return f"Invalid number. Choose 1-{len(recent)}."

    target = recent[pick - 1]

    conn = _get_conn()
    conn.execute("DELETE FROM work_hours WHERE id=?", (target["id"],))
    conn.commit()
    conn.close()

    # Also remove the matching salary income transaction
    # Use exact hours + rate match to avoid fuzzy LIKE collisions
    hourly_rate = float(os.getenv("HOURLY_RATE", "13.99"))
    expected_gross = round(target["hours"] * hourly_rate, 2)
    conn2 = _get_conn()
    conn2.execute(
        "DELETE FROM transactions WHERE id = ("
        "  SELECT id FROM transactions WHERE amount=? AND date=? AND type='income' "
        "  AND description LIKE 'Salary%' ORDER BY created_at DESC LIMIT 1"
        ")",
        (expected_gross, target["date"])
    )
    conn2.commit()
    conn2.close()

    # Recalculate Notion budget
    try:
        week_start = target["week_start"]
        budget_week = _get_week_start(datetime.strptime(week_start, "%Y-%m-%d"))
        sync_expense_to_notion({"category": "Salary", "week_start": budget_week,
                                "description": "Salary (recalc)", "date": target["date"]})
        _update_section_totals(budget_week)
    except Exception as e:
        logger.warning("Undo hours Notion budget sync: %s", e)

    # Rebuild the entire Notion timesheet table from SQLite (source of truth)
    timesheet_url = ""
    try:
        page_id = target.get("notion_page_id", "")
        if page_id:
            _rebuild_notion_timesheet(page_id, week_start)
            timesheet_url = f"\n📎 Timesheet: https://www.notion.so/{page_id.replace('-', '')}"
    except Exception as e:
        logger.warning("Undo hours timesheet sync: %s", e)

    notion_url = get_notion_budget_url()
    return (f"✅ Removed: {target['hours']:.1f}h × £{target['hourly_rate']:.2f} "
            f"= £{target['total_earned']:.2f} ({target['date']})\n\n"
            f"Budget + timesheet updated.{timesheet_url}\n📎 Budget: {notion_url}")


init_db()
