"""Dispatcher — maps intents to agent functions, executes, returns Telegram reply text."""

from datetime import datetime
from jobpulse.command_router import Intent, ParsedCommand
from jobpulse import event_logger


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
        Intent.LOG_SPEND: _handle_log_spend,
        Intent.LOG_INCOME: _handle_log_income,
        Intent.LOG_SAVINGS: _handle_log_savings,
        Intent.SET_BUDGET: _handle_set_budget,
        Intent.SHOW_BUDGET: _handle_show_budget,
        Intent.HELP: _handle_help,
    }

    handler = handlers.get(cmd.intent)
    if not handler:
        return _handle_unknown(cmd)

    try:
        result = handler(cmd)
        # Log every dispatched command to simulation events
        event_logger.log_event(
            event_type="agent_action",
            agent_name="dispatcher",
            action=cmd.intent.value,
            content=result[:300] if result else "",
            metadata={"intent": cmd.intent.value, "raw_input": cmd.raw[:200]},
        )
        return result
    except Exception as e:
        event_logger.log_event(
            event_type="error",
            agent_name="dispatcher",
            action=cmd.intent.value,
            content=str(e),
            metadata={"intent": cmd.intent.value, "raw_input": cmd.raw[:200]},
        )
        return f"⚠️ Error running {cmd.intent.value}: {e}"


def _handle_show_tasks(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import get_today_tasks
    tasks = get_today_tasks()
    if not tasks:
        return "📝 No tasks for today. Send me a list to create some!"
    done = [t for t in tasks if t["status"] == "Done"]
    open_tasks = [t for t in tasks if t["status"] != "Done"]
    lines = [f"📝 Today's Tasks ({len(open_tasks)} open, {len(done)} done):\n"]
    for t in open_tasks:
        lines.append(f"  ☐ {t['title']}")
    for t in done:
        lines.append(f"  ✅ {t['title']}")
    return "\n".join(lines)


def _handle_create_tasks(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import create_tasks_batch
    from jobpulse.telegram_listener import _parse_tasks

    tasks = _parse_tasks(cmd.raw)
    if not tasks:
        return "Couldn't parse any tasks. Send them one per line:\n\nFix bug\nApply to jobs\nTailor resume"

    created = create_tasks_batch(tasks)
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
    from jobpulse.notion_agent import complete_task

    target = cmd.args.strip()
    if not target:
        return "Which task? Say: done: task name"

    return complete_task(target)


def _handle_create_event(cmd: ParsedCommand) -> str:
    return ("📅 Calendar event creation coming soon!\n\n"
            f"You said: \"{cmd.args}\"\n\n"
            "For now, add events directly in Google Calendar.")


def _handle_log_spend(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import log_transaction
    return log_transaction(cmd.raw)


def _handle_log_income(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import log_transaction
    return log_transaction(cmd.raw)


def _handle_log_savings(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import log_transaction
    return log_transaction(cmd.raw)


def _handle_set_budget(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import set_budget
    return set_budget(cmd.raw)


def _handle_show_budget(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import get_week_summary, get_today_spending
    from jobpulse.budget_agent import format_week_summary, format_today

    today = get_today_spending()
    week = get_week_summary()

    parts = []
    if today["items"]:
        parts.append(format_today(today))
    parts.append(format_week_summary(week))
    return "\n\n".join(parts)


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

💰 BUDGET:
  "spent 15 on lunch" — log expense
  "£8.50 coffee" — log expense
  "earned 500 freelance" — log income
  "saved 100" — log savings/investment
  "set budget groceries 50" — set weekly limit
  "budget" — weekly summary

📬 OTHER:
  "briefing" — full morning report
  "papers" — latest AI research
  "help" — this message"""


def _handle_unknown(cmd: ParsedCommand) -> str:
    return (f"🤔 Not sure what you mean by: \"{cmd.raw[:50]}\"\n\n"
            "Try: tasks, calendar, emails, commits, trending, briefing, help")
