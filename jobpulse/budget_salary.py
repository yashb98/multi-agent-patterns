"""Salary / work-hours tracking — extracted from budget_agent.py (SRP split).

Tracks hours worked at £HOURLY_RATE/hr, logs to SQLite + Notion timesheet,
calculates tax (20%) and savings suggestion (30% of after-tax).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from shared.logging_config import get_logger
from jobpulse import event_logger

logger = get_logger(__name__)

# ── Constants ──

HOURLY_RATE = float(os.getenv("HOURLY_RATE", "13.99"))
TAX_RATE = 0.20   # UK basic rate income tax (no NI)
SAVINGS_RATE = 0.30  # 30% of after-tax goes to savings

WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50,
}


# ── Helpers (imported from budget_agent at call time to avoid circular import) ──

def _budget():
    """Lazy import of budget_agent to avoid circular dependency."""
    import jobpulse.budget_agent as _ba
    return _ba


def _get_salary_week_start(date: datetime = None) -> str:
    """Get the start of the 28-day salary period containing *date*.

    Uses the same anchor as the budget period so budget + salary align.
    """
    return _budget()._get_period_start(date)


def _get_or_create_salary_page(week_start: str) -> str:
    """Get or create a Notion sub-page for this week's salary timesheet."""
    ba = _budget()
    # Check if we already have a page for this week
    conn = ba._get_conn()
    row = conn.execute(
        "SELECT DISTINCT notion_page_id FROM work_hours WHERE week_start=? AND notion_page_id != ''",
        (week_start,)
    ).fetchone()
    conn.close()

    if row and row["notion_page_id"]:
        return row["notion_page_id"]

    # Create a new Notion page for this period's timesheet
    from jobpulse.budget_constants import BUDGET_PAGE_ID
    from jobpulse.notion_agent import _notion_api

    parent_id = BUDGET_PAGE_ID
    period_end = ba._get_period_end(week_start)
    title = f"Salary Timesheet — {week_start} to {period_end}"

    data = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]},
        },
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": f"⏱️ Work Hours — {week_start} to {period_end}"}}]
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

    from jobpulse.notion_agent import _notion_api
    ba = _budget()

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
    conn = ba._get_conn()
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
                    [{"type": "text", "text": {"content": f"Period {salary_week}"}}],
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

    28-day budget periods anchored to 2026-04-02. Shows tax (20%) and savings suggestion (30% of after-tax).
    Supports: numbers, floats, words ("six hours", "seven and a half hours")
    Supports past dates: "yesterday", "on monday", "march 24"
    """
    ba = _budget()

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
    conn = ba._get_conn()
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
    ba.add_transaction(gross, f"Salary ({hours}h)", "Salary", "income", "income")
    ba.sync_expense_to_notion({"category": "Salary", "week_start": ba._get_week_start(target_date),
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
    budget_link = f"\n📎 Budget: {ba.get_notion_budget_url()}"

    date_label = f" ({date_str})" if date_str != datetime.now().strftime("%Y-%m-%d") else ""
    return (f"⏱️ Logged: {hours}h × £{rate:.2f} = £{gross:.2f}{date_label}\n"
            f"  Tax (20%): -£{tax:.2f}\n"
            f"  After tax: £{after_tax:.2f}\n\n"
            f"📊 This period: {week_hours:.1f}h\n"
            f"  Gross:     £{week_gross:.2f}\n"
            f"  Tax (20%): -£{week_tax:.2f}\n"
            f"  After tax: £{week_after_tax:.2f}\n"
            f"  💰 Save 30%: £{week_savings:.2f}\n\n"
            f"💡 Transfer £{week_savings:.2f} to savings. Reply \"saved\" to confirm."
            f"{notion_link}{budget_link}")


def confirm_savings_transfer(week_start: str = None) -> str:
    """User confirmed they transferred savings. Log it and update Notion."""
    ba = _budget()
    from jobpulse.notion_agent import _notion_api

    now = datetime.now()
    week_start = week_start or _get_salary_week_start(now)

    conn = ba._get_conn()
    week_totals = conn.execute(
        "SELECT SUM(total_earned) as e FROM work_hours WHERE week_start=?",
        (week_start,)
    ).fetchone()
    conn.close()

    week_gross = week_totals["e"] or 0
    if week_gross == 0:
        return "No hours logged this period — nothing to save."

    week_tax = round(week_gross * TAX_RATE, 2)
    week_after_tax = round(week_gross - week_tax, 2)
    savings_amount = round(week_after_tax * SAVINGS_RATE, 2)

    # Log as savings transaction
    ba.add_transaction(savings_amount, f"Period savings (30% of £{week_after_tax:.2f})",
                    "Savings", "savings", "savings")
    ba.sync_expense_to_notion({"category": "Savings", "week_start": ba._get_week_start(now),
                            "description": f"Period savings", "date": now.strftime("%Y-%m-%d")})

    # Add "Saved" row to the Notion timesheet
    conn2 = ba._get_conn()
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
                            [{"type": "text", "text": {"content": "SAVED THIS PERIOD"}}],
                            [{"type": "text", "text": {"content": "30% of after-tax"}}],
                            [{"type": "text", "text": {"content": now.strftime("%Y-%m-%d")}}],
                            [{"type": "text", "text": {"content": f"£{savings_amount:.2f}"}}],
                        ]
                    }}
                ]
            })

    notion_url = ba.get_notion_budget_url()
    return (f"🏦 Confirmed! £{savings_amount:.2f} logged as savings.\n\n"
            f"  Gross this period: £{week_gross:.2f}\n"
            f"  Tax (20%): -£{week_tax:.2f}\n"
            f"  After tax: £{week_after_tax:.2f}\n"
            f"  Saved (30%): £{savings_amount:.2f}\n"
            f"  Remaining: £{round(week_after_tax - savings_amount, 2):.2f}\n\n"
            f"📎 {notion_url}")


def get_hours_summary(week_start: str = None) -> str:
    """Get formatted work hours summary for the current budget period."""
    ba = _budget()

    week_start = week_start or _get_salary_week_start()
    conn = ba._get_conn()

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
        return "⏱️ No hours logged this period yet."

    total_h = totals["h"] or 0
    total_gross = totals["e"] or 0
    total_tax = round(total_gross * TAX_RATE, 2)
    total_after_tax = round(total_gross - total_tax, 2)
    total_savings = round(total_after_tax * SAVINGS_RATE, 2)

    period_end = ba._get_period_end(week_start)
    lines = [f"⏱️ WORK HOURS ({week_start} to {period_end}):\n"]
    for r in rows:
        lines.append(f"  {r['date']} — {r['hours']:.1f}h × £{r['hourly_rate']:.2f} = £{r['total_earned']:.2f}")

    lines.append(f"\n  {'─' * 35}")
    lines.append(f"  TOTAL:     {total_h:.1f}h = £{total_gross:.2f}")
    lines.append(f"  Tax (20%): -£{total_tax:.2f}")
    lines.append(f"  After tax: £{total_after_tax:.2f}")
    lines.append(f"  💰 Save 30%: £{total_savings:.2f}")
    lines.append(f"  Remaining: £{round(total_after_tax - total_savings, 2):.2f}")

    page_url = ""
    conn2 = ba._get_conn()
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

    from jobpulse.notion_agent import _notion_api
    ba = _budget()

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
    conn = ba._get_conn()
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
                    [{"type": "text", "text": {"content": f"Period {salary_week_start}"}}],
                    [{"type": "text", "text": {"content": f"£{total_e:.2f} gross | £{total_after_tax:.2f} net | Save £{total_savings:.2f}"}}],
                ]
            }
        })

    if new_rows:
        _notion_api("PATCH", f"/blocks/{table_id}/children", {"children": new_rows})

    logger.info("Rebuilt timesheet for %s: %d entries", salary_week_start, len(entries))


def undo_hours(pick: int = None) -> str:
    """Show last 5 hour entries for selection, or delete a specific one by number."""
    ba = _budget()

    conn = ba._get_conn()
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

    conn = ba._get_conn()
    conn.execute("DELETE FROM work_hours WHERE id=?", (target["id"],))
    conn.commit()
    conn.close()

    # Also remove the matching salary income transaction
    # Use exact hours + rate match to avoid fuzzy LIKE collisions
    hourly_rate = float(os.getenv("HOURLY_RATE", "13.99"))
    expected_gross = round(target["hours"] * hourly_rate, 2)
    conn2 = ba._get_conn()
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
        budget_week = ba._get_week_start(datetime.strptime(week_start, "%Y-%m-%d"))
        ba.sync_expense_to_notion({"category": "Salary", "week_start": budget_week,
                                "description": "Salary (recalc)", "date": target["date"]})
        ba._update_section_totals(budget_week)
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

    notion_url = ba.get_notion_budget_url()
    return (f"✅ Removed: {target['hours']:.1f}h × £{target['hourly_rate']:.2f} "
            f"= £{target['total_earned']:.2f} ({target['date']})\n\n"
            f"Budget + timesheet updated.{timesheet_url}\n📎 Budget: {notion_url}")
