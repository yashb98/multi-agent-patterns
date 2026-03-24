"""Budget agent — tracks spending, categorizes with LLM, creates weekly Notion budget pages.

Weekly budget structure in Notion:
  One page per week: "Budget — Week of March 24, 2026"
  Inside: category breakdown table as blocks, with running totals.

Categories:
  🍔 Food & Dining
  🚗 Transport
  🛒 Groceries
  🏠 Rent & Bills
  💊 Health
  🎬 Entertainment
  👕 Shopping
  📱 Subscriptions
  📚 Education
  🎁 Gifts
  💼 Work Expenses
  🔧 Miscellaneous
"""

import json
import sqlite3
from datetime import datetime, timedelta
from jobpulse.config import NOTION_API_KEY, DATA_DIR, NOTION_PARENT_PAGE_ID
from jobpulse.notion_agent import _notion_api

DB_PATH = DATA_DIR / "budget.db"

CATEGORIES = {
    "food": "🍔 Food & Dining",
    "transport": "🚗 Transport",
    "groceries": "🛒 Groceries",
    "rent": "🏠 Rent & Bills",
    "bills": "🏠 Rent & Bills",
    "health": "💊 Health",
    "entertainment": "🎬 Entertainment",
    "shopping": "👕 Shopping",
    "subscriptions": "📱 Subscriptions",
    "education": "📚 Education",
    "gifts": "🎁 Gifts",
    "work": "💼 Work Expenses",
    "misc": "🔧 Miscellaneous",
}

CATEGORY_DISPLAY = list(set(CATEGORIES.values()))


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
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            week_start TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS weekly_budgets (
            week_start TEXT PRIMARY KEY,
            notion_page_id TEXT,
            total_spent REAL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_expense_week ON expenses(week_start);
        CREATE INDEX IF NOT EXISTS idx_expense_date ON expenses(date);
    """)
    conn.commit()
    conn.close()


def _get_week_start(date: datetime = None) -> str:
    """Get Monday of the current week as YYYY-MM-DD."""
    date = date or datetime.now()
    monday = date - timedelta(days=date.weekday())
    return monday.strftime("%Y-%m-%d")


def add_expense(amount: float, description: str, category: str) -> dict:
    """Store an expense. Returns the expense record."""
    now = datetime.now()
    week_start = _get_week_start(now)

    conn = _get_conn()
    cursor = conn.execute(
        "INSERT INTO expenses (amount, description, category, date, week_start, created_at) VALUES (?,?,?,?,?,?)",
        (amount, description, category, now.strftime("%Y-%m-%d"), week_start, now.isoformat())
    )
    expense_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": expense_id,
        "amount": amount,
        "description": description,
        "category": category,
        "date": now.strftime("%Y-%m-%d"),
        "week_start": week_start,
    }


def get_week_summary(week_start: str = None) -> dict:
    """Get spending summary for a week, grouped by category."""
    week_start = week_start or _get_week_start()

    conn = _get_conn()
    rows = conn.execute(
        "SELECT category, SUM(amount) as total, COUNT(*) as count FROM expenses WHERE week_start=? GROUP BY category ORDER BY total DESC",
        (week_start,)
    ).fetchall()

    total = conn.execute(
        "SELECT SUM(amount) FROM expenses WHERE week_start=?", (week_start,)
    ).fetchone()[0] or 0.0

    recent = conn.execute(
        "SELECT amount, description, category, date FROM expenses WHERE week_start=? ORDER BY created_at DESC LIMIT 10",
        (week_start,)
    ).fetchall()

    conn.close()

    return {
        "week_start": week_start,
        "total": total,
        "by_category": [{"category": r["category"], "total": r["total"], "count": r["count"]} for r in rows],
        "recent": [dict(r) for r in recent],
    }


def get_today_spending() -> dict:
    """Get today's spending."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    rows = conn.execute(
        "SELECT amount, description, category FROM expenses WHERE date=? ORDER BY created_at DESC",
        (today,)
    ).fetchall()
    total = sum(r["amount"] for r in rows)
    conn.close()
    return {"date": today, "total": total, "items": [dict(r) for r in rows]}


# ── LLM Category Classification ──

def classify_expense(description: str, amount: float) -> str:
    """Use LLM to classify an expense into a category."""
    try:
        from openai import OpenAI
        from jobpulse.config import OPENAI_API_KEY

        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Classify this expense into ONE category:

food — meals, restaurants, coffee, takeaway, snacks
transport — uber, taxi, bus, train, fuel, parking
groceries — supermarket, weekly shop, ingredients
rent — rent, electricity, water, gas, internet, phone bill
health — pharmacy, doctor, gym, dentist, medicine
entertainment — movies, concerts, games, streaming, drinks out
shopping — clothes, electronics, furniture, amazon
subscriptions — spotify, netflix, cloud services, apps
education — books, courses, tutorials, certifications
gifts — presents, donations, charity
work — office supplies, coworking, tools
misc — anything that doesn't fit above

Expense: £{amount:.2f} — "{description}"

Respond with ONLY the category key (food/transport/groceries/rent/health/entertainment/shopping/subscriptions/education/gifts/work/misc). Nothing else."""}],
            max_tokens=10,
            temperature=0,
        )
        cat = response.choices[0].message.content.strip().lower()
        # Validate
        if cat in CATEGORIES:
            return CATEGORIES[cat]
        # Fuzzy match
        for key, display in CATEGORIES.items():
            if key in cat:
                return display
        return "🔧 Miscellaneous"
    except Exception as e:
        print(f"[Budget] LLM classification failed: {e}")
        return "🔧 Miscellaneous"


# ── Notion Integration ──

def _get_or_create_weekly_budget_page(week_start: str = None) -> str:
    """Get or create this week's budget page in Notion. Returns page ID."""
    week_start = week_start or _get_week_start()
    week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    week_label = datetime.strptime(week_start, "%Y-%m-%d").strftime("%B %d")

    # Check SQLite for cached page ID
    conn = _get_conn()
    row = conn.execute("SELECT notion_page_id FROM weekly_budgets WHERE week_start=?", (week_start,)).fetchone()
    if row and row["notion_page_id"]:
        conn.close()
        return row["notion_page_id"]

    # Create new Notion page
    if not NOTION_PARENT_PAGE_ID:
        conn.close()
        return ""

    data = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": {"title": [{"text": {"content": f"💰 Budget — Week of {week_label}"}}]}
        },
        "children": [
            {"object": "block", "type": "heading_1", "heading_1": {
                "rich_text": [{"text": {"content": f"💰 Weekly Budget — {week_label}"}}]
            }},
            {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"text": {"content": f"Tracking spending from {week_start} to {week_end}"}}]
            }},
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": "📊 Category Breakdown"}}]
            }},
            {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"text": {"content": "(Updates automatically as you log expenses)"}}]
            }},
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": "📝 Expense Log"}}]
            }},
        ]
    }

    result = _notion_api("POST", "/pages", data)
    page_id = result.get("id", "")

    if page_id:
        conn.execute(
            "INSERT OR REPLACE INTO weekly_budgets (week_start, notion_page_id, created_at) VALUES (?,?,?)",
            (week_start, page_id, datetime.now().isoformat())
        )
        conn.commit()

    conn.close()
    return page_id


def sync_expense_to_notion(expense: dict):
    """Append an expense entry to this week's Notion budget page."""
    page_id = _get_or_create_weekly_budget_page(expense["week_start"])
    if not page_id:
        return

    # Append expense as a bulleted item
    _notion_api("PATCH", f"/blocks/{page_id}/children", {
        "children": [
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [
                    {"text": {"content": f"£{expense['amount']:.2f}"}, "annotations": {"bold": True}},
                    {"text": {"content": f" — {expense['description']} "}},
                    {"text": {"content": f"[{expense['category']}]"}, "annotations": {"color": "gray"}},
                    {"text": {"content": f" ({expense['date']})"}, "annotations": {"color": "gray"}},
                ]
            }}
        ]
    })


def update_notion_budget_summary(week_start: str = None):
    """Update the category breakdown section of the Notion budget page."""
    week_start = week_start or _get_week_start()
    summary = get_week_summary(week_start)
    page_id = _get_or_create_weekly_budget_page(week_start)
    if not page_id:
        return

    # Build summary text
    lines = [f"Total spent: £{summary['total']:.2f}\n"]
    for cat in summary["by_category"]:
        bar_len = int((cat["total"] / max(summary["total"], 1)) * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"{cat['category']}: £{cat['total']:.2f} ({cat['count']}x)")

    # We can't easily update existing blocks, so we append a summary callout
    _notion_api("PATCH", f"/blocks/{page_id}/children", {
        "children": [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "callout", "callout": {
                "icon": {"emoji": "📊"},
                "rich_text": [{"text": {"content": "\n".join(lines)}}],
            }}
        ]
    })


# ── Spend Parsing ──

def parse_spend(text: str) -> dict | None:
    """Parse a natural language spend message into {amount, description}.

    Handles:
      "spent 15 on lunch" → {amount: 15.0, description: "lunch"}
      "£8.50 coffee" → {amount: 8.50, description: "coffee"}
      "uber 12.40" → {amount: 12.40, description: "uber"}
      "45 groceries at tesco" → {amount: 45.0, description: "groceries at tesco"}
      "lunch 7.50" → {amount: 7.50, description: "lunch"}
    """
    import re

    text = text.strip()

    # Remove "spent" / "spend" / "paid" prefix
    text = re.sub(r"^(spent|spend|paid|bought|got)\s+", "", text, flags=re.IGNORECASE)

    # Try to find amount (with or without £/$)
    # Pattern: optional currency symbol, digits, optional decimal
    amount_pattern = r"[£$€]?\s*(\d+(?:\.\d{1,2})?)"

    match = re.search(amount_pattern, text)
    if not match:
        return None

    amount = float(match.group(1))
    if amount <= 0 or amount > 50000:
        return None

    # Remove the amount (including currency symbol) from text to get description
    start = match.start()
    # Include preceding currency symbol if present
    if start > 0 and text[start-1] in "£$€":
        start -= 1
    desc = text[:start] + " " + text[match.end():]
    # Clean up filler words and whitespace
    desc = re.sub(r"\s+", " ", desc).strip()
    desc = re.sub(r"^(on|for|at|to)\s+", "", desc, flags=re.IGNORECASE)
    desc = re.sub(r"\s+(on|for|at)$", "", desc, flags=re.IGNORECASE)
    desc = desc.strip()

    if not desc:
        desc = "Unspecified"

    return {"amount": amount, "description": desc}


def log_spend(text: str) -> str:
    """Full pipeline: parse → classify → store → sync to Notion → return reply."""
    parsed = parse_spend(text)
    if not parsed:
        return ("Couldn't parse that. Try:\n"
                "  spent 15 on lunch\n"
                "  £8.50 coffee\n"
                "  uber 12.40\n"
                "  45 groceries tesco")

    amount = parsed["amount"]
    description = parsed["description"]

    # Classify category with LLM
    category = classify_expense(description, amount)

    # Store in SQLite
    expense = add_expense(amount, description, category)

    # Sync to Notion
    sync_expense_to_notion(expense)

    # Get today's running total
    today = get_today_spending()

    return (f"💸 Logged: £{amount:.2f} — {description}\n"
            f"   Category: {category}\n"
            f"   Today's total: £{today['total']:.2f}")


# ── Formatting ──

def format_week_summary(summary: dict) -> str:
    """Format weekly budget summary for Telegram."""
    if not summary["by_category"]:
        return "💰 No spending logged this week yet."

    lines = [f"💰 WEEKLY BUDGET (since {summary['week_start']}):\n"]
    lines.append(f"  Total: £{summary['total']:.2f}\n")

    for cat in summary["by_category"]:
        pct = (cat["total"] / summary["total"] * 100) if summary["total"] > 0 else 0
        lines.append(f"  {cat['category']}: £{cat['total']:.2f} ({pct:.0f}%)")

    if summary["recent"]:
        lines.append(f"\n  Recent:")
        for item in summary["recent"][:5]:
            lines.append(f"    £{item['amount']:.2f} — {item['description']}")

    return "\n".join(lines)


def format_today_spending(data: dict) -> str:
    """Format today's spending for Telegram."""
    if not data["items"]:
        return "💰 No spending logged today."

    lines = [f"💰 TODAY'S SPENDING: £{data['total']:.2f}\n"]
    for item in data["items"]:
        lines.append(f"  £{item['amount']:.2f} — {item['description']} [{item['category']}]")
    return "\n".join(lines)


# Initialize DB on import
init_db()
