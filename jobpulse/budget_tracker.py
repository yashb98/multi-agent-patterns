"""Budget Tracker v2 — Category sub-pages with individual transaction rows in Notion.

Every category (17 total) gets a weekly sub-page under the budget page.
Each transaction is logged as a row: Amount | Date | Description | Items | Store | Running Total.

SQLite is the source of truth. Notion is the display layer.
"""

import re
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR
from jobpulse.notion_agent import _notion_api

logger = get_logger(__name__)

DB_PATH = DATA_DIR / "budget.db"
KNOWN_STORES_FILE = DATA_DIR / "known_stores.json"

# The 17 categories that get sub-pages (NOT totals rows)
TRACKABLE_CATEGORIES = [
    # Income
    "Salary", "Freelance", "Other",
    # Fixed
    "Rent / Mortgage", "Utilities", "Phone / Internet", "Subscriptions", "Insurance",
    # Variable
    "Groceries", "Eating out", "Transport", "Shopping", "Entertainment", "Health", "Misc",
    # Savings
    "Savings", "Investments", "Credit card / Loan payment",
]

# Default known stores
DEFAULT_STORES = [
    "tesco", "aldi", "lidl", "sainsbury", "asda", "morrisons", "waitrose",
    "marks and spencer", "m&s", "co-op", "iceland",
    "pret", "costa", "starbucks", "greggs", "mcdonald", "kfc", "nando",
    "subway", "domino", "pizza hut", "wagamama", "wetherspoon",
    "uber", "bolt", "addison lee", "national rail", "tfl",
    "amazon", "ebay", "argos", "jd sports", "primark", "tk maxx", "next", "asos", "zara",
    "boots", "superdrug", "holland and barrett",
    "netflix", "spotify", "apple", "google", "microsoft", "adobe",
    "gym", "puregym", "the gym", "virgin active",
]


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tracker_db():
    """Create new tables for v2 tracking."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS category_pages (
            week_start TEXT NOT NULL,
            category TEXT NOT NULL,
            notion_page_id TEXT NOT NULL,
            PRIMARY KEY (week_start, category)
        );

        CREATE TABLE IF NOT EXISTS weekly_archives (
            week_start TEXT PRIMARY KEY,
            notion_page_id TEXT NOT NULL,
            total_income REAL DEFAULT 0,
            total_spending REAL DEFAULT 0,
            total_savings REAL DEFAULT 0,
            net REAL DEFAULT 0,
            archived_at TEXT NOT NULL
        );
    """)

    # Add new columns to transactions if they don't exist
    cursor = conn.execute("PRAGMA table_info(transactions)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    for col, col_type in [("items", "TEXT DEFAULT ''"), ("store", "TEXT DEFAULT ''"),
                          ("time_of_day", "TEXT DEFAULT ''"), ("notion_sub_page_id", "TEXT DEFAULT ''")]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} {col_type}")
            logger.info("Added column %s to transactions", col)

    conn.commit()
    conn.close()


# ── Item + Store Extraction ──

def _load_known_stores() -> list[str]:
    """Load known stores from JSON + defaults."""
    stores = list(DEFAULT_STORES)
    if KNOWN_STORES_FILE.exists():
        try:
            extra = json.loads(KNOWN_STORES_FILE.read_text())
            stores.extend(extra)
        except Exception:
            pass
    return stores


def _save_new_store(store: str):
    """Add a newly discovered store to the known stores list."""
    stores = []
    if KNOWN_STORES_FILE.exists():
        try:
            stores = json.loads(KNOWN_STORES_FILE.read_text())
        except Exception:
            pass

    store_lower = store.lower().strip()
    if store_lower not in [s.lower() for s in stores] and store_lower not in [s.lower() for s in DEFAULT_STORES]:
        stores.append(store_lower)
        try:
            KNOWN_STORES_FILE.write_text(json.dumps(stores, indent=2))
        except Exception:
            pass


def extract_items_and_store(description: str) -> dict:
    """Extract individual items and store from a transaction description.

    Examples:
        "yogurt and protein shake at Tesco" → items=["yogurt", "protein shake"], store="Tesco"
        "coffee and sandwich at Pret" → items=["coffee", "sandwich"], store="Pret"
        "uber ride" → items=["uber ride"], store="Uber"
        "monthly subscription" → items=["monthly subscription"], store=""
    """
    text = description.strip()
    store = ""
    items = []

    # 1. Extract store from "at X" / "from X" / "in X" (capitalize first word)
    store_match = re.search(r"\b(?:at|from|in)\s+([A-Za-z][\w\s&']+?)(?:\s+(?:on|for|today|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\s*$)", text, re.IGNORECASE)
    if store_match:
        store = store_match.group(1).strip().title()
        # Remove the "at Store" part from text for item extraction
        text = text[:store_match.start()].strip()

    # 2. Auto-detect known stores — match whole words only to avoid false positives
    if not store:
        known = _load_known_stores()
        desc_lower = description.lower()
        for s in known:
            # Use word boundary matching to avoid "tfl" matching in "netflix"
            if re.search(rf"\b{re.escape(s.lower())}\b", desc_lower):
                store = s.title()
                text = re.sub(rf"\b{re.escape(s)}\b", "", text, flags=re.IGNORECASE).strip()
                break

    # 3. Extract items: split on "and" / ","
    # Clean up leftover prepositions
    text = re.sub(r"^\s*(on|for|at|to|some|a|the)\s+", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+(on|for|at)\s*$", "", text, flags=re.IGNORECASE).strip()

    if text:
        raw_items = re.split(r"\s+and\s+|,\s*|&\s*", text)
        items = [item.strip() for item in raw_items if item.strip() and len(item.strip()) > 1]

    if not items:
        fallback = description.strip()
        items = [fallback] if fallback else []

    # Save new store for future detection
    if store and len(store) > 2:
        _save_new_store(store)

    return {"items": items, "store": store}


def _get_time_of_day() -> str:
    """Return time bucket: morning/afternoon/evening/night."""
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    elif hour < 21:
        return "evening"
    return "night"


# ── Category Sub-Pages ──

def _get_budget_page_id():
    """Get the main budget page ID."""
    from jobpulse.budget_agent import BUDGET_PAGE_ID
    return BUDGET_PAGE_ID


def get_or_create_category_page(category: str, week_start: str) -> str:
    """Get or create a Notion sub-page for a category's transactions this week."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT notion_page_id FROM category_pages WHERE week_start=? AND category=?",
        (week_start, category)
    ).fetchone()
    conn.close()

    if row and row["notion_page_id"]:
        return row["notion_page_id"]

    # Create new sub-page
    parent_id = _get_budget_page_id()
    try:
        week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    except ValueError:
        logger.error("get_or_create_category_page: invalid week_start date: %s", week_start)
        return ""
    title = f"{category} — {week_start} to {week_end}"

    # Choose columns based on category type
    from jobpulse.budget_agent import INCOME_CATEGORIES, SAVINGS_CATEGORIES, FIXED_EXPENSE_CATEGORIES

    # Determine which section this category belongs to
    is_variable = category in ["Groceries", "Eating out", "Transport", "Shopping", "Entertainment", "Health", "Misc"]
    is_income = category in ["Salary", "Freelance", "Other"]
    is_savings = category in ["Savings", "Investments", "Credit card / Loan payment"]

    if is_variable:
        headers = ["Amount", "Date", "Items", "Store", "Running Total"]
        table_width = 5
    elif is_income:
        headers = ["Amount", "Date", "Description", "Source", "Running Total"]
        table_width = 5
    elif is_savings:
        headers = ["Amount", "Date", "Description", "What", "Running Total"]
        table_width = 5
    else:
        # Fixed expenses
        headers = ["Amount", "Date", "Description", "Running Total"]
        table_width = 4

    header_cells = [[{"type": "text", "text": {"content": h}}] for h in headers]

    data = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]},
        },
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": f"{category} Transactions"}}]
            }},
            {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"text": {"content": f"Week: {week_start} to {week_end}"}}]
            }},
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "table", "table": {
                "table_width": table_width,
                "has_column_header": True,
                "has_row_header": False,
                "children": [
                    {"object": "block", "type": "table_row", "table_row": {"cells": header_cells}},
                ]
            }},
        ]
    }

    result = _notion_api("POST", "/pages", data)
    page_id = result.get("id", "")

    if page_id:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO category_pages (week_start, category, notion_page_id) VALUES (?,?,?)",
            (week_start, category, page_id)
        )
        conn.commit()
        conn.close()
        logger.info("Created category page: %s (%s)", category, page_id)

    return page_id


def _add_category_link_to_budget_page(budget_page_id: str, category: str, sub_page_id: str):
    """Add a clickable link_to_page block on the main budget page.

    First checks if a 'Category Details' heading exists. If not, creates it.
    Then appends a link_to_page block pointing to the category sub-page.
    """
    # Check if we already have the heading + this link
    blocks = _notion_api("GET", f"/blocks/{budget_page_id}/children?page_size=100")
    has_heading = False
    has_this_link = False

    for block in blocks.get("results", []):
        if block.get("type") == "heading_3":
            text = "".join(t.get("plain_text", "") for t in block.get("heading_3", {}).get("rich_text", []))
            if "Category Details" in text:
                has_heading = True
        if block.get("type") == "link_to_page":
            linked_id = block.get("link_to_page", {}).get("page_id", "")
            if linked_id == sub_page_id:
                has_this_link = True

    if has_this_link:
        return  # Already linked

    children = []

    if not has_heading:
        children.append({
            "object": "block", "type": "divider", "divider": {}
        })
        children.append({
            "object": "block", "type": "heading_3", "heading_3": {
                "rich_text": [{"text": {"content": "📂 Category Details (click to view transactions)"}}]
            }
        })

    # Add link_to_page block — this is clickable, not editable
    children.append({
        "object": "block", "type": "link_to_page",
        "link_to_page": {"type": "page_id", "page_id": sub_page_id}
    })

    if children:
        _notion_api("PATCH", f"/blocks/{budget_page_id}/children", {"children": children})
        logger.info("Added %s link to budget page", category)


def add_transaction_row(category: str, week_start: str, amount: float,
                        date_str: str, description: str, items: list,
                        store: str, section: str) -> str:
    """Append a transaction row to the category's sub-page table. Returns page URL."""
    page_id = get_or_create_category_page(category, week_start)
    if not page_id:
        logger.warning(
            "add_transaction_row: category page creation failed for %s/%s — "
            "transaction saved in SQLite but not synced to Notion",
            category, week_start,
        )
        return ""

    # Find the table block
    blocks = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")
    table_id = None
    for block in blocks.get("results", []):
        if block.get("type") == "table":
            table_id = block["id"]
            break

    if not table_id:
        logger.warning("No table in category page %s", page_id)
        return ""

    # Remove old TOTAL row if exists
    table_rows = _notion_api("GET", f"/blocks/{table_id}/children?page_size=100")
    for row in table_rows.get("results", []):
        cells = row.get("table_row", {}).get("cells", [])
        if cells and cells[0]:
            text = "".join(t.get("plain_text", "") for t in cells[0])
            if "TOTAL" in text.upper():
                _notion_api("DELETE", f"/blocks/{row['id']}")

    # Calculate running total
    conn = _get_conn()
    running = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE week_start=? AND category=?",
        (week_start, category)
    ).fetchone()[0]
    conn.close()

    # Get planned budget for percentage
    from jobpulse.budget_agent import _get_conn as budget_conn
    bconn = budget_conn()
    planned_row = bconn.execute(
        "SELECT planned_amount FROM planned_budgets WHERE week_start=? AND category=?",
        (week_start, category)
    ).fetchone()
    bconn.close()
    planned = planned_row["planned_amount"] if planned_row else 0

    if planned > 0:
        pct = round(running / planned * 100)
        warn = " !!!" if pct >= 80 else ""
        running_str = f"£{running:.2f} / £{planned:.0f} ({pct}%){warn}"
    else:
        running_str = f"£{running:.2f}"

    items_str = ", ".join(items) if items else (description or "-")
    is_variable = category in ["Groceries", "Eating out", "Transport", "Shopping", "Entertainment", "Health", "Misc"]
    is_income = category in ["Salary", "Freelance", "Other"]
    is_savings = category in ["Savings", "Investments", "Credit card / Loan payment"]

    if is_variable:
        cells = [
            [{"type": "text", "text": {"content": f"£{amount:.2f}"}}],
            [{"type": "text", "text": {"content": date_str}}],
            [{"type": "text", "text": {"content": items_str}}],
            [{"type": "text", "text": {"content": store or "-"}}],
            [{"type": "text", "text": {"content": running_str}}],
        ]
    elif is_income:
        cells = [
            [{"type": "text", "text": {"content": f"£{amount:.2f}"}}],
            [{"type": "text", "text": {"content": date_str}}],
            [{"type": "text", "text": {"content": description}}],
            [{"type": "text", "text": {"content": store or "-"}}],
            [{"type": "text", "text": {"content": running_str}}],
        ]
    elif is_savings:
        cells = [
            [{"type": "text", "text": {"content": f"£{amount:.2f}"}}],
            [{"type": "text", "text": {"content": date_str}}],
            [{"type": "text", "text": {"content": description}}],
            [{"type": "text", "text": {"content": items_str}}],
            [{"type": "text", "text": {"content": running_str}}],
        ]
    else:
        cells = [
            [{"type": "text", "text": {"content": f"£{amount:.2f}"}}],
            [{"type": "text", "text": {"content": date_str}}],
            [{"type": "text", "text": {"content": description}}],
            [{"type": "text", "text": {"content": running_str}}],
        ]

    # Add data row
    _notion_api("PATCH", f"/blocks/{table_id}/children", {
        "children": [
            {"object": "block", "type": "table_row", "table_row": {"cells": cells}},
        ]
    })

    # Add TOTAL row
    count = len(table_rows.get("results", [])) - 1  # minus header, plus new row
    total_cells_count = len(cells)
    total_cells = [[{"type": "text", "text": {"content": f"TOTAL ({count + 1} transactions)"}}]]
    for _ in range(total_cells_count - 2):
        total_cells.append([{"type": "text", "text": {"content": ""}}])
    total_cells.append([{"type": "text", "text": {"content": running_str}}])

    _notion_api("PATCH", f"/blocks/{table_id}/children", {
        "children": [
            {"object": "block", "type": "table_row", "table_row": {"cells": total_cells}},
        ]
    })

    return f"https://www.notion.so/{page_id.replace('-', '')}"


def get_category_page_url(category: str, week_start: str) -> str:
    """Get the Notion URL for a category's sub-page, if it exists."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT notion_page_id FROM category_pages WHERE week_start=? AND category=?",
        (week_start, category)
    ).fetchone()
    conn.close()
    if row and row["notion_page_id"]:
        return f"https://www.notion.so/{row['notion_page_id'].replace('-', '')}"
    return ""


# ── Weekly Archival + New Week ──

def archive_current_week() -> str:
    """Archive the current week's budget and prepare for next week.

    Called Sunday morning before briefing. Stores summary in weekly_archives,
    carries over planned budgets to the new week.
    """
    from jobpulse.budget_agent import _get_week_start, get_week_summary, BUDGET_PAGE_ID

    now = datetime.now()
    current_week = _get_week_start(now)

    # Check if already archived
    conn = _get_conn()
    existing = conn.execute(
        "SELECT 1 FROM weekly_archives WHERE week_start=?", (current_week,)
    ).fetchone()
    if existing:
        conn.close()
        return f"Week of {current_week} already archived."

    # Get summary
    summary = get_week_summary(current_week)

    # Store archive
    conn.execute(
        "INSERT INTO weekly_archives (week_start, notion_page_id, total_income, total_spending, total_savings, net, archived_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (current_week, BUDGET_PAGE_ID, summary["income_total"], summary["spending_total"],
         summary["savings_total"], summary["net"], now.isoformat())
    )
    conn.commit()

    # Carry over planned budgets to next week
    next_week = (datetime.strptime(current_week, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
    planned = conn.execute(
        "SELECT category, section, planned_amount FROM planned_budgets WHERE week_start=?",
        (current_week,)
    ).fetchall()

    for p in planned:
        conn.execute(
            "INSERT OR IGNORE INTO planned_budgets (week_start, category, section, planned_amount) VALUES (?,?,?,?)",
            (next_week, p["category"], p["section"], p["planned_amount"])
        )

    conn.commit()
    conn.close()

    logger.info("Archived week %s: income=£%.2f, spending=£%.2f, net=£%.2f",
                current_week, summary["income_total"], summary["spending_total"], summary["net"])

    return (f"📦 Week of {current_week} archived.\n"
            f"  Income: £{summary['income_total']:.2f}\n"
            f"  Spending: £{summary['spending_total']:.2f}\n"
            f"  Savings: £{summary['savings_total']:.2f}\n"
            f"  Net: £{summary['net']:.2f}\n\n"
            f"Planned budgets carried over to {next_week}.")


def get_weekly_comparison() -> str:
    """Compare this week vs last week spending per category."""
    from jobpulse.budget_agent import _get_week_start

    now = datetime.now()
    this_week = _get_week_start(now)
    last_week = (datetime.strptime(this_week, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")

    conn = _get_conn()

    this_data = conn.execute(
        "SELECT category, SUM(amount) as total, COUNT(*) as count "
        "FROM transactions WHERE week_start=? AND type='expense' "
        "GROUP BY category ORDER BY total DESC",
        (this_week,)
    ).fetchall()

    last_data = conn.execute(
        "SELECT category, SUM(amount) as total, COUNT(*) as count "
        "FROM transactions WHERE week_start=? AND type='expense' "
        "GROUP BY category ORDER BY total DESC",
        (last_week,)
    ).fetchall()

    conn.close()

    if not this_data and not last_data:
        return "📊 No spending data for comparison yet."

    last_map = {r["category"]: {"total": r["total"], "count": r["count"]} for r in last_data}

    lines = [f"📊 WEEKLY COMPARISON (vs last week):\n"]
    total_this = 0
    total_last = 0

    for row in this_data:
        cat = row["category"]
        this_total = row["total"]
        total_this += this_total
        last = last_map.get(cat, {"total": 0, "count": 0})
        last_total = last["total"]
        total_last += last_total

        diff = this_total - last_total
        if last_total > 0:
            pct = round((diff / last_total) * 100)
            arrow = "↑" if diff > 0 else "↓"
            warn = " ⚠️" if pct > 50 else " ✅" if pct < -10 else ""
            lines.append(f"  {cat:20s} £{this_total:>7.2f}  {arrow}{abs(pct)}% (£{abs(diff):.2f}){warn}")
        else:
            lines.append(f"  {cat:20s} £{this_total:>7.2f}  (new this week)")

    # Categories only in last week (not this week)
    for row in last_data:
        if row["category"] not in [r["category"] for r in this_data]:
            total_last += row["total"]
            lines.append(f"  {row['category']:20s} £{'0.00':>7s}  ↓100% (was £{row['total']:.2f}) ✅")

    lines.append(f"\n  {'TOTAL':20s} £{total_this:>7.2f}  (last week: £{total_last:.2f})")

    return "\n".join(lines)


def get_budget_dataset_csv() -> str:
    """Export all transactions as CSV for ML analysis."""
    import csv
    import io

    conn = _get_conn()
    rows = conn.execute(
        "SELECT amount, description, category, section, type, date, week_start, "
        "items, store, time_of_day, created_at FROM transactions ORDER BY date"
    ).fetchall()
    conn.close()

    if not rows:
        return "No transactions to export."

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "amount", "description", "category", "section", "type",
        "date", "week_start", "items", "store", "time_of_day",
        "day_of_week", "created_at",
    ])
    for r in rows:
        d = dict(r)
        day_of_week = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%A")
        writer.writerow([
            d["amount"], d.get("description", ""), d.get("category", ""),
            d.get("section", ""), d.get("type", ""), d["date"],
            d.get("week_start", ""), d.get("items", ""),
            d.get("store", ""), d.get("time_of_day", ""),
            day_of_week, d.get("created_at", ""),
        ])

    csv_path = DATA_DIR / "transactions_export.csv"
    try:
        csv_path.write_text(output.getvalue(), encoding="utf-8")
    except OSError as exc:
        logger.error("CSV export failed: %s", exc)
        return f"Export failed: {exc}"
    return str(csv_path)


# Initialize on import
init_tracker_db()
