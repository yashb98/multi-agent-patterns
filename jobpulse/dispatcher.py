"""Dispatcher — maps intents to agent functions, executes, returns Telegram reply text."""

from datetime import datetime
from jobpulse.command_router import Intent, ParsedCommand


def dispatch(cmd: ParsedCommand) -> str:
    """Execute the right agent for a classified command. Returns reply text."""

    handlers = {
        Intent.SHOW_TASKS: _handle_show_tasks,
        Intent.CREATE_TASKS: _handle_create_tasks,
        Intent.CALENDAR: _handle_calendar,
        Intent.GMAIL: _handle_gmail,
        Intent.GITHUB: _handle_github,
        Intent.TRENDING: _handle_trending,
        Intent.BRIEFING: _handle_briefing,
        Intent.ARXIV: _handle_arxiv,
        Intent.COMPLETE_TASK: _handle_complete_task,
        Intent.CREATE_EVENT: _handle_create_event,
        Intent.HELP: _handle_help,
    }

    handler = handlers.get(cmd.intent)
    if not handler:
        return _handle_unknown(cmd)

    try:
        return handler(cmd)
    except Exception as e:
        return f"⚠️ Error running {cmd.intent.value}: {e}"


def _handle_show_tasks(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import get_today_tasks
    tasks = get_today_tasks()
    if not tasks:
        return "📝 No tasks for today. Send me a list to create some!"
    lines = [f"📝 Today's Tasks ({len(tasks)}):\n"]
    for t in tasks:
        status = "✅" if t["status"] == "Done" else "□"
        lines.append(f"  {status} {t['title']}")
    return "\n".join(lines)


def _handle_create_tasks(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import create_task
    from jobpulse.telegram_listener import _parse_tasks

    tasks = _parse_tasks(cmd.raw)
    if not tasks:
        return "Couldn't parse any tasks. Send them one per line:\n\nFix bug\nApply to jobs\nTailor resume"

    today = datetime.now().strftime("%Y-%m-%d")
    created = 0
    for task in tasks:
        if create_task(task, today):
            created += 1

    task_list = "\n".join(f"  □ {t}" for t in tasks)
    return f"✅ Created {created} tasks in Notion:\n\n{task_list}"


def _handle_calendar(cmd: ParsedCommand) -> str:
    from jobpulse.calendar_agent import get_today_and_tomorrow, format_events
    cal = get_today_and_tomorrow()

    today_fmt = format_events(cal["today_events"])
    tomorrow_fmt = format_events(cal["tomorrow_events"])

    if not cal["today_events"]:
        today_fmt = "  No events today"
    if not cal["tomorrow_events"]:
        tomorrow_fmt = "  Nothing scheduled tomorrow"

    return f"📅 TODAY'S CALENDAR:\n{today_fmt}\n\n📅 TOMORROW PREVIEW:\n{tomorrow_fmt}"


def _handle_gmail(cmd: ParsedCommand) -> str:
    from jobpulse.gmail_agent import check_emails, CATEGORY_EMOJI
    emails = check_emails()

    if not emails:
        return "📧 No new recruiter emails since last check."

    lines = [f"📧 Found {len(emails)} recruiter email(s):\n"]
    for e in emails:
        label = CATEGORY_EMOJI.get(e["category"], e["category"])
        lines.append(f"  {label}: {e['sender']} — \"{e['subject']}\"")
    return "\n".join(lines)


def _handle_github(cmd: ParsedCommand) -> str:
    from jobpulse.github_agent import get_yesterday_commits, format_commits
    data = get_yesterday_commits()
    return f"💻 YESTERDAY'S GITHUB:\n{format_commits(data)}"


def _handle_trending(cmd: ParsedCommand) -> str:
    from jobpulse.github_agent import get_trending_repos, format_trending
    repos = get_trending_repos()
    return f"🔥 TRENDING ON GITHUB:\n{format_trending(repos)}"


def _handle_briefing(cmd: ParsedCommand) -> str:
    from jobpulse.morning_briefing import build_and_send
    build_and_send()
    return "📬 Full morning briefing sent!"


def _handle_arxiv(cmd: ParsedCommand) -> str:
    # arXiv still uses claude -p via shell script, so we give a helpful response
    return ("📚 The arXiv agent runs daily at 7:57am and sends top 5 papers to this chat.\n\n"
            "To trigger it now, run on your Mac:\n"
            "./scripts/arxiv-daily.sh")


def _handle_complete_task(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import get_today_tasks, _notion_api
    import json

    target = cmd.args.lower().strip()
    if not target:
        return "Which task? Say: done: task name"

    tasks = get_today_tasks()
    # Find best match
    matched = None
    for t in tasks:
        if target in t["title"].lower():
            matched = t
            break

    if not matched:
        task_list = "\n".join(f"  □ {t['title']}" for t in tasks)
        return f"Couldn't find task matching \"{cmd.args}\". Your tasks:\n\n{task_list}"

    # We need the page ID to update — fetch it
    from jobpulse.config import NOTION_API_KEY, NOTION_TASKS_DB_ID
    import subprocess

    today = datetime.now().strftime("%Y-%m-%d")
    result = _notion_api("POST", f"/databases/{NOTION_TASKS_DB_ID}/query", {
        "filter": {
            "and": [
                {"property": "Date", "date": {"equals": today}},
                {"property": "Status", "select": {"does_not_equal": "Done"}},
            ]
        }
    })

    for page in result.get("results", []):
        props = page.get("properties", {})
        title = "".join(t.get("plain_text", "") for t in props.get("Task", {}).get("title", []))
        if target in title.lower():
            # Update status to Done
            _notion_api("PATCH", f"/pages/{page['id']}", {
                "properties": {"Status": {"select": {"name": "Done"}}}
            })
            return f"✅ Marked \"{title}\" as Done!"

    return f"Couldn't find \"{cmd.args}\" in today's tasks."


def _handle_create_event(cmd: ParsedCommand) -> str:
    return ("📅 Calendar event creation coming soon!\n\n"
            f"You said: \"{cmd.args}\"\n\n"
            "For now, add events directly in Google Calendar.")


def _handle_help(cmd: ParsedCommand) -> str:
    return """🤖 JobPulse Commands:

📝 TASKS:
  "show tasks" — see today's todo list
  "mark X done" — complete a task
  Send a list of items — creates tasks

📅 CALENDAR:
  "calendar" — today + tomorrow events

📧 EMAIL:
  "check emails" — scan for recruiter emails

💻 GITHUB:
  "commits" — yesterday's activity
  "trending" — hot repos this week

📬 OTHER:
  "briefing" — full morning report
  "papers" — latest AI research
  "help" — this message"""


def _handle_unknown(cmd: ParsedCommand) -> str:
    return (f"🤔 Not sure what you mean by: \"{cmd.raw[:50]}\"\n\n"
            "Try: tasks, calendar, emails, commits, trending, briefing, help")


# Need to add PATCH method support to notion_agent
def _patch_notion_api():
    """Add PATCH support to notion_agent._notion_api if not present."""
    from jobpulse import notion_agent
    original = notion_agent._notion_api

    def patched(method, endpoint, data=None):
        import json as _json
        import subprocess as _sp
        cmd = ["curl", "-s", "-X", method,
               f"https://api.notion.com/v1{endpoint}",
               "-H", f"Authorization: Bearer {notion_agent.NOTION_API_KEY}",
               "-H", "Content-Type: application/json",
               "-H", "Notion-Version: 2022-06-28"]
        if data:
            cmd.extend(["-d", _json.dumps(data)])
        try:
            result = _sp.run(cmd, capture_output=True, text=True, timeout=15)
            return _json.loads(result.stdout) if result.stdout else {}
        except Exception:
            return {}

    notion_agent._notion_api = patched

_patch_notion_api()
