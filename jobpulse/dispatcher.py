"""Dispatcher — maps intents to agent functions, executes, returns Telegram reply text."""

from datetime import datetime
from jobpulse.command_router import Intent, ParsedCommand
from jobpulse import event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)


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
        Intent.WEEKLY_REPORT: _handle_weekly_report,
        Intent.EXPORT: _handle_export,
    }

    handler = handlers.get(cmd.intent)
    if not handler:
        return _handle_unknown(cmd)

    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("telegram_dispatcher", "telegram_message")

    try:
        with trail.step("decision", "Classify intent",
                         step_input=cmd.raw[:200]) as s:
            s["output"] = f"Intent: {cmd.intent.value}"
            s["decision"] = f"Routed to {cmd.intent.value} handler"
            s["metadata"] = {"intent": cmd.intent.value, "args": cmd.args[:100] if cmd.args else ""}

        with trail.step("api_call", f"Execute {cmd.intent.value}",
                         step_input=cmd.raw[:200]) as s:
            result = handler(cmd)
            s["output"] = result[:500] if result else ""

        # Log every dispatched command to simulation events
        event_logger.log_event(
            event_type="agent_action",
            agent_name="dispatcher",
            action=cmd.intent.value,
            content=result[:300] if result else "",
            metadata={"intent": cmd.intent.value, "raw_input": cmd.raw[:200]},
        )

        trail.finalize(result[:500] if result else "")
        return result
    except Exception as e:
        trail.log_step("error", f"Error in {cmd.intent.value}", cmd.raw[:200],
                       str(e), None, {"error": str(e)}, "error")
        trail.finalize(f"Error: {e}")
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


def _handle_weekly_report(cmd: ParsedCommand) -> str:
    from jobpulse.weekly_report import build_weekly_report
    return build_weekly_report()


def _handle_export(cmd: ParsedCommand) -> str:
    return "\U0001f4e6 Export triggered. Use `python -m jobpulse.runner export` on your Mac for a full backup."


def _handle_help(cmd: ParsedCommand) -> str:
    return """\U0001f916 JobPulse Commands:

\U0001f4dd TASKS:
  "show tasks" \u2014 see today's todo list
  "mark X done" \u2014 complete a task
  Send a list of items \u2014 creates tasks

\U0001f4c5 CALENDAR:
  "calendar" \u2014 today + tomorrow events

\U0001f4e7 EMAIL:
  "check emails" \u2014 scan for recruiter emails

\U0001f4bb GITHUB:
  "commits" \u2014 yesterday's activity
  "trending" \u2014 hot repos this week

\U0001f4b0 BUDGET:
  "spent 15 on lunch" \u2014 log expense
  "\u00a38.50 coffee" \u2014 log expense
  "earned 500 freelance" \u2014 log income
  "saved 100" \u2014 log savings/investment
  "set budget groceries 50" \u2014 set weekly limit
  "budget" \u2014 weekly summary

\U0001f4ca REPORTS:
  "weekly report" \u2014 7-day summary
  "export" \u2014 data backup instructions

\U0001f4ec OTHER:
  "briefing" \u2014 full morning report
  "papers" \u2014 latest AI research
  "help" \u2014 this message"""


def _handle_unknown(cmd: ParsedCommand) -> str:
    return (f"🤔 Not sure what you mean by: \"{cmd.raw[:50]}\"\n\n"
            "Try: tasks, calendar, emails, commits, trending, briefing, help")
