"""Notion agent — manages daily tasks and weekly research papers via direct API."""

import json
import subprocess
from datetime import datetime
from jobpulse.config import NOTION_API_KEY, NOTION_TASKS_DB_ID, NOTION_RESEARCH_DB_ID


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
        print(f"[Notion] API error: {e}")
        return {}


def get_today_tasks() -> list[dict]:
    """Fetch today's incomplete tasks from Daily Tasks database."""
    if not NOTION_TASKS_DB_ID:
        print("[Notion] NOTION_TASKS_DB_ID not set")
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    data = {
        "filter": {
            "and": [
                {"property": "Date", "date": {"equals": today}},
                {"property": "Status", "select": {"does_not_equal": "Done"}},
            ]
        },
        "sorts": [{"property": "Task", "direction": "ascending"}]
    }

    result = _notion_api("POST", f"/databases/{NOTION_TASKS_DB_ID}/query", data)
    tasks = []
    for page in result.get("results", []):
        props = page.get("properties", {})
        title_arr = props.get("Task", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_arr)
        status = props.get("Status", {}).get("select", {}).get("name", "")
        if title:
            tasks.append({"title": title, "status": status})

    return tasks


def format_tasks(tasks: list[dict]) -> str:
    """Format tasks as readable checklist."""
    if not tasks:
        return "  No tasks set for today. Add some in Notion!"
    return "\n".join(f"  □ {t['title']}" for t in tasks)


def create_task(title: str, date: str = None) -> bool:
    """Create a single task in the Daily Tasks database."""
    if not NOTION_TASKS_DB_ID:
        return False
    date = date or datetime.now().strftime("%Y-%m-%d")
    data = {
        "parent": {"database_id": NOTION_TASKS_DB_ID},
        "properties": {
            "Task": {"title": [{"text": {"content": title}}]},
            "Status": {"select": {"name": "Not started"}},
            "Date": {"date": {"start": date}},
        }
    }
    result = _notion_api("POST", "/pages", data)
    return "id" in result


def complete_task(task_name: str) -> str:
    """Find a task by name (fuzzy match) and mark it as Done. Returns result message."""
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

    target = task_name.lower().strip()
    for page in result.get("results", []):
        props = page.get("properties", {})
        title = "".join(t.get("plain_text", "") for t in props.get("Task", {}).get("title", []))
        if target in title.lower():
            # PATCH to mark as Done
            _notion_api("PATCH", f"/pages/{page['id']}", {
                "properties": {"Status": {"select": {"name": "Done"}}}
            })
            return f"✅ Marked \"{title}\" as Done!"

    return f"Couldn't find task matching \"{task_name}\""


def create_research_page(title: str, blocks: list[dict]) -> str:
    """Create a weekly research page in the Weekly AI Research database. Returns page URL."""
    if not NOTION_RESEARCH_DB_ID:
        print("[Notion] NOTION_RESEARCH_DB_ID not set")
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
