"""Budget Notion sync — update Notion budget sheet rows and totals.

Handles the Actual/Planned column updates, section totals, and summary table
in the user's Notion "Budget Period Sheet".
"""
from datetime import datetime

from shared.logging_config import get_logger
from shared.db import get_db_conn
from jobpulse.notion_agent import _notion_api
from jobpulse.budget_constants import (
    BUDGET_PAGE_ID, TABLE_IDS, ROW_IDS, DB_PATH,
    get_period_start, get_period_end,
)

logger = get_logger(__name__)

_get_week_start = get_period_start


def _get_conn():
    return get_db_conn(DB_PATH)


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
        from jobpulse.budget_constants import get_period_start as _get_salary_week_start
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
