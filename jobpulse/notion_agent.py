"""Notion agent — manages daily tasks and weekly research papers via direct API."""

import re
import json
import subprocess
from datetime import datetime, timedelta
from jobpulse.config import NOTION_API_KEY, NOTION_TASKS_DB_ID, NOTION_RESEARCH_DB_ID
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _notion_api(method: str, endpoint: str, data: dict = None) -> dict:
    """Call Notion API via curl (avoids Python SSL issues)."""
    cmd = ["curl", "-s", "-X", method,
           f"https://api.notion.com/v1{endpoint}",
           "-H", f"Authorization: Bearer {NOTION_API_KEY}",
           "-H", "Content-Type: application/json",
           "-H", "Notion-Version: 2022-06-28"]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        logger.error("API error: %s", e)
        return {}


def get_today_tasks() -> list[dict]:
    """Fetch today's tasks from the daily todo page (reads to_do blocks)."""
    if not NOTION_TASKS_DB_ID:
        logger.warning("NOTION_TASKS_DB_ID not set")
        return []

    today = datetime.now().strftime("%Y-%m-%d")

    # Find today's page
    result = _notion_api("POST", f"/databases/{NOTION_TASKS_DB_ID}/query", {
        "filter": {"property": "Date", "date": {"equals": today}}
    })

    pages = result.get("results", [])
    if not pages:
        return []

    page_id = pages[0]["id"]

    # Read the to_do blocks from the page
    blocks_result = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")
    tasks = []
    for block in blocks_result.get("results", []):
        if block.get("type") == "to_do":
            todo = block.get("to_do", {})
            title = "".join(t.get("plain_text", "") for t in todo.get("rich_text", []))
            checked = todo.get("checked", False)
            if title:
                tasks.append({
                    "title": title,
                    "status": "Done" if checked else "Not started",
                    "block_id": block["id"],
                })

    return tasks


def format_tasks(tasks: list[dict]) -> str:
    """Format tasks as readable checklist with priority indicators."""
    if not tasks:
        return "  No tasks set for today. Add some in Notion!"
    lines = []
    for t in tasks:
        title = t['title']
        # Priority indicators are already in the title as emoji prefixes
        checkbox = "✅" if t.get("status") == "Done" else "□"
        lines.append(f"  {checkbox} {title}")
    return "\n".join(lines)


def _get_or_create_daily_page(date: str = None) -> str:
    """Get today's todo page ID, or create one if it doesn't exist."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    day_name = datetime.strptime(date, "%Y-%m-%d").strftime("%A, %B %d")

    if not NOTION_TASKS_DB_ID:
        return ""

    # Search for existing page with this date
    result = _notion_api("POST", f"/databases/{NOTION_TASKS_DB_ID}/query", {
        "filter": {"property": "Date", "date": {"equals": date}}
    })

    pages = result.get("results", [])
    if pages:
        return pages[0]["id"]

    # Create new daily page with heading
    data = {
        "parent": {"database_id": NOTION_TASKS_DB_ID},
        "properties": {
            "Task": {"title": [{"text": {"content": f"Tasks — {day_name}"}}]},
            "Status": {"select": {"name": "Not started"}},
            "Date": {"date": {"start": date}},
        },
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": f"📝 Todo List — {day_name}"}}]
            }},
            {"object": "block", "type": "divider", "divider": {}},
        ]
    }
    result = _notion_api("POST", "/pages", data)
    return result.get("id", "")


def create_task(title: str, date: str = None, priority: str = "normal",
                due_date: str | None = None) -> bool:
    """Add a to_do checkbox item to today's daily page.

    Args:
        title: Task title text
        date: Page date (default today)
        priority: "normal", "high", or "urgent"
        due_date: Optional due date string (YYYY-MM-DD)
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    page_id = _get_or_create_daily_page(date)
    if not page_id:
        return False

    # Prepend priority emoji
    display_title = title
    if priority == "urgent":
        display_title = f"\U0001f534 {title}"
    elif priority == "high":
        display_title = f"\U0001f7e1 {title}"

    # Append due date if provided
    if due_date:
        try:
            dt = datetime.strptime(due_date, "%Y-%m-%d")
            display_title += f" (due: {dt.strftime('%b %d')})"
        except ValueError:
            display_title += f" (due: {due_date})"

    # Append a to_do block to the page
    result = _notion_api("PATCH", f"/blocks/{page_id}/children", {
        "children": [
            {"object": "block", "type": "to_do", "to_do": {
                "rich_text": [{"text": {"content": display_title}}],
                "checked": False,
            }}
        ]
    })
    return "results" in result


def create_tasks_batch(tasks: list[str], date: str = None) -> int:
    """Add multiple to_do items to today's daily page in one call."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    page_id = _get_or_create_daily_page(date)
    if not page_id:
        return 0

    blocks = [
        {"object": "block", "type": "to_do", "to_do": {
            "rich_text": [{"text": {"content": task}}],
            "checked": False,
        }}
        for task in tasks
    ]

    result = _notion_api("PATCH", f"/blocks/{page_id}/children", {"children": blocks})
    return len(result.get("results", []))


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching — lowercase, strip punctuation, normalize numbers."""
    import re
    text = text.lower().strip()
    # Remove punctuation and extra spaces
    text = re.sub(r"[().,!?;:'\"-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Normalize number words → digits
    word_to_num = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                   "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
    words = text.split()
    words = [word_to_num.get(w, w) for w in words]
    return " ".join(words)


def _fuzzy_score(query: str, title: str) -> float:
    """Score how well a query matches a task title. Higher = better match.

    Uses word overlap ratio instead of exact substring matching.
    'multiagent orchestration day 1' should match 'finish the multi agent orchestration (day 1)'.
    """
    q_words = set(_normalize(query).split())
    t_words = set(_normalize(title).split())

    if not q_words:
        return 0.0

    # Remove common filler words from query
    fillers = {"the", "a", "an", "my", "to", "for", "and", "of", "in", "on", "is", "it", "do", "done"}
    q_words -= fillers

    if not q_words:
        return 0.0

    # Count how many query words appear in the title
    matches = len(q_words & t_words)
    return matches / len(q_words)


def complete_task(task_name: str) -> str:
    """Find a task by intent (fuzzy match) and mark it as Done.

    Uses word overlap scoring instead of exact substring matching.
    'multiagent orchestration day one' matches 'Finish the multi agent orchestration (day 1)'.
    """
    if not NOTION_TASKS_DB_ID:
        return "NOTION_TASKS_DB_ID not set"

    today = datetime.now().strftime("%Y-%m-%d")
    result = _notion_api("POST", f"/databases/{NOTION_TASKS_DB_ID}/query", {
        "filter": {
            "and": [
                {"property": "Date", "date": {"equals": today}},
                {"property": "Status", "select": {"does_not_equal": "Done"}},
            ]
        }
    })

    # Get tasks from today's page (to_do blocks)
    tasks = get_today_tasks()
    unchecked = [t for t in tasks if t["status"] != "Done"]

    if not unchecked:
        return "No open tasks for today."

    # Score all tasks against the query
    candidates = [(t, _fuzzy_score(task_name, t["title"])) for t in unchecked]
    candidates.sort(key=lambda x: x[1], reverse=True)

    best_task, best_score = candidates[0]

    if best_score < 0.4:
        task_list = "\n".join(f"  □ {t['title']}" for t, _ in candidates[:5])
        return f"Couldn't match \"{task_name}\" to any task.\n\nYour open tasks:\n{task_list}\n\nTry: done: [task name]"

    # Toggle the to_do checkbox
    _notion_api("PATCH", f"/blocks/{best_task['block_id']}", {
        "to_do": {"checked": True}
    })
    return f"✅ Marked \"{best_task['title']}\" as Done!"


def uncomplete_task(task_name: str) -> str:
    """Find a completed task by fuzzy match and uncheck it (reverse of complete_task)."""
    tasks = get_today_tasks()
    checked = [t for t in tasks if t["status"] == "Done"]

    if not checked:
        return "No completed tasks to undo."

    candidates = [(t, _fuzzy_score(task_name, t["title"])) for t in checked]
    candidates.sort(key=lambda x: x[1], reverse=True)
    best_task, best_score = candidates[0]

    if best_score < 0.3:
        # If no good match, just undo the most recently checked one (last in list)
        best_task = checked[-1]

    _notion_api("PATCH", f"/blocks/{best_task['block_id']}", {
        "to_do": {"checked": False}
    })
    return f"☐ Unchecked \"{best_task['title']}\" — back to open."


def remove_task(task_name: str) -> str:
    """Find a task by fuzzy match and delete the block from Notion."""
    if not NOTION_TASKS_DB_ID:
        return "NOTION_TASKS_DB_ID not set"

    tasks = get_today_tasks()
    if not tasks:
        return "No tasks for today."

    candidates = [(t, _fuzzy_score(task_name, t["title"])) for t in tasks]
    candidates.sort(key=lambda x: x[1], reverse=True)
    best_task, best_score = candidates[0]

    if best_score < 0.4:
        task_list = "\n".join(f"  {'✅' if t['status'] == 'Done' else '☐'} {t['title']}" for t, _ in candidates[:5])
        return f"Couldn't match \"{task_name}\" to any task.\n\nYour tasks:\n{task_list}\n\nTry: remove: [task name]"

    # Delete the block
    _notion_api("DELETE", f"/blocks/{best_task['block_id']}")
    logger.info("Removed task: %s", best_task["title"])
    return f"🗑️ Removed \"{best_task['title']}\" from today's tasks."


def check_duplicate(new_title: str) -> str | None:
    """Check if a task with similar title already exists today. Returns match or None."""
    tasks = get_today_tasks()
    if not tasks:
        return None

    new_norm = _normalize(new_title)
    for t in tasks:
        score = _fuzzy_score(new_title, t["title"])
        if score >= 0.7:
            return t["title"]
        # Also check exact normalized match
        if _normalize(t["title"]) == new_norm:
            return t["title"]
    return None


def create_tasks_batch_smart(tasks: list[str], date: str = None) -> dict:
    """Create tasks with dedup checking and big-task detection.

    Returns:
        {"created": [...], "duplicates": [...], "big_tasks": [...]}
    """
    created = []
    duplicates = []
    big_tasks = []

    for task in tasks:
        task = task.strip()
        if not task:
            continue

        # Check for duplicates
        existing = check_duplicate(task)
        if existing:
            duplicates.append({"new": task, "existing": existing})
            continue

        # Check if task is too big (heuristic: >10 words or contains "and"/"then"/"also")
        words = task.split()
        has_conjunction = any(w.lower() in ("and", "then", "also", "plus", "&") for w in words)
        if len(words) > 12 or (len(words) > 6 and has_conjunction):
            big_tasks.append(task)
            continue

        # Create normally
        success = create_task(task, date)
        if success:
            created.append(task)

    return {"created": created, "duplicates": duplicates, "big_tasks": big_tasks}


def suggest_subtasks(big_task: str) -> list[str]:
    """Use LLM to suggest subtasks for a large task."""
    import os
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""Break this task into 2-5 small, actionable subtasks.
Each subtask should be completable in under 30 minutes.

Task: {big_task}

Return ONLY the subtasks, one per line, no numbering or bullets. Keep each under 8 words."""
            }],
            max_tokens=150,
            temperature=0.3,
        )

        lines = response.choices[0].message.content.strip().split("\n")
        return [line.strip() for line in lines if line.strip() and len(line.strip()) > 3]
    except Exception as e:
        logger.warning("Subtask suggestion failed: %s", e)
        return []


def create_research_page(title: str, blocks: list[dict]) -> str:
    """Create a weekly research page in the Weekly AI Research database. Returns page URL."""
    if not NOTION_RESEARCH_DB_ID:
        logger.warning("NOTION_RESEARCH_DB_ID not set")
        return ""

    data = {
        "parent": {"database_id": NOTION_RESEARCH_DB_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "Week": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
            "Papers": {"number": 5},
            "Status": {"select": {"name": "Published"}},
        },
        "children": blocks,
    }
    result = _notion_api("POST", "/pages", data)
    return result.get("url", "")


# ── Due Date Parsing (Feature 5) ──

def parse_due_date(text: str) -> tuple[str, str | None]:
    """Extract relative/absolute due dates from task text.

    Returns (cleaned_text_without_date_phrase, date_string_or_None).

    Examples:
        "finish report by Friday" -> ("finish report", "2026-03-27")
        "buy groceries by tomorrow" -> ("buy groceries", "2026-03-27")
        "submit form by March 30" -> ("submit form", "2026-03-30")
        "call dentist today" -> ("call dentist", "2026-03-26")
        "no date here" -> ("no date here", None)
    """
    today = datetime.now()
    text_lower = text.lower()

    # "today"
    m = re.search(r"\b(by\s+)?today\b", text_lower)
    if m:
        cleaned = text[:m.start()] + text[m.end():]
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, today.strftime("%Y-%m-%d")

    # "tomorrow"
    m = re.search(r"\b(by\s+)?tomorrow\b", text_lower)
    if m:
        cleaned = text[:m.start()] + text[m.end():]
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # "by <day_name>" (e.g. "by Friday")
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    m = re.search(r"\bby\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text_lower)
    if m:
        target_day = day_names.index(m.group(1))
        current_day = today.weekday()
        days_ahead = target_day - current_day
        if days_ahead <= 0:
            days_ahead += 7
        due = today + timedelta(days=days_ahead)
        cleaned = text[:m.start()] + text[m.end():]
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, due.strftime("%Y-%m-%d")

    # "by <Month> <day>" (e.g. "by March 30")
    month_names = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month_pattern = "|".join(month_names.keys())
    m = re.search(rf"\bby\s+({month_pattern})\s+(\d{{1,2}})\b", text_lower)
    if m:
        month = month_names[m.group(1)]
        day = int(m.group(2))
        year = today.year
        try:
            due = datetime(year, month, day)
            if due < today:
                due = datetime(year + 1, month, day)
            cleaned = text[:m.start()] + text[m.end():]
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned, due.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return text, None


# ── Weekly Planning (Feature 6) ──

def get_undone_tasks_from_past_days(days: int = 7) -> list[dict]:
    """Fetch unchecked tasks from the past N days' Notion pages."""
    if not NOTION_TASKS_DB_ID:
        return []

    today = datetime.now()
    all_undone = []

    for offset in range(1, days + 1):
        target_date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")

        result = _notion_api("POST", f"/databases/{NOTION_TASKS_DB_ID}/query", {
            "filter": {"property": "Date", "date": {"equals": target_date}}
        })

        pages = result.get("results", [])
        if not pages:
            continue

        page_id = pages[0]["id"]
        blocks_result = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")

        for block in blocks_result.get("results", []):
            if block.get("type") == "to_do":
                todo = block.get("to_do", {})
                checked = todo.get("checked", False)
                if not checked:
                    title = "".join(t.get("plain_text", "") for t in todo.get("rich_text", []))
                    if title:
                        all_undone.append({
                            "title": title,
                            "date": target_date,
                            "block_id": block["id"],
                        })

    return all_undone


def carry_forward_tasks(task_titles: list[str]) -> int:
    """Create given task titles on today's page. Returns count created."""
    count = 0
    for title in task_titles:
        if create_task(title):
            count += 1
    return count
