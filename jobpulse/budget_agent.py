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
    """Get the Notion URL for this week's budget page."""
    week_start = week_start or _get_week_start()
    conn = _get_conn()
    row = conn.execute("SELECT notion_page_id FROM weekly_budgets WHERE week_start=?", (week_start,)).fetchone()
    conn.close()
    if row and row["notion_page_id"]:
        page_id = row["notion_page_id"].replace("-", "")
        return f"https://www.notion.so/{page_id}"
    return ""


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

    notion_url = get_notion_budget_url()
    link_line = f"\n📎 {notion_url}" if notion_url else ""
    return f"📋 Budget set: {category} = £{amount:.2f}/week{link_line}"


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
