"""Dispatcher — maps intents to agent functions, executes, returns Telegram reply text.

Error Handling (Domain 2, Task 2.2):
All errors return structured DispatchError objects with:
- errorCategory: transient | validation | permission | business
- isRetryable: whether the caller should retry
- partialResults: any partial data collected before failure
- agentName: which agent failed
- attemptedAction: what was being attempted
"""

from datetime import datetime
from jobpulse.command_router import Intent, ParsedCommand
from jobpulse import event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)


class DispatchError:
    """Structured error for dispatcher failures (Domain 2, Task 2.2)."""

    def __init__(self, error_category: str, message: str,
                 is_retryable: bool = False, partial_results: str | None = None,
                 agent_name: str = "", attempted_action: str = ""):
        self.error_category = error_category
        self.message = message
        self.is_retryable = is_retryable
        self.partial_results = partial_results
        self.agent_name = agent_name
        self.attempted_action = attempted_action

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "errorCategory": self.error_category,
            "message": self.message,
            "isRetryable": self.is_retryable,
            "partialResults": self.partial_results,
            "agentName": self.agent_name,
            "attemptedAction": self.attempted_action,
        }

    def to_user_message(self) -> str:
        """Format error for Telegram display."""
        retry_hint = " Try again in a moment." if self.is_retryable else ""
        partial = f"\n\nPartial result:\n{self.partial_results}" if self.partial_results else ""
        return f"⚠️ {self.agent_name} error ({self.error_category}): {self.message}{retry_hint}{partial}"


def _classify_error(e: Exception) -> tuple[str, bool]:
    """Classify an exception into errorCategory and isRetryable."""
    err_str = str(e).lower()
    err_type = type(e).__name__

    # Transient: timeouts, rate limits, connection errors
    if any(kw in err_str for kw in ("timeout", "timed out", "rate limit", "429", "503", "502")):
        return "transient", True
    if any(kw in err_type for kw in ("Timeout", "ConnectionError", "ConnectionReset")):
        return "transient", True

    # Permission: auth failures
    if any(kw in err_str for kw in ("401", "403", "unauthorized", "forbidden", "permission")):
        return "permission", False

    # Validation: bad input
    if any(kw in err_str for kw in ("invalid", "missing", "required", "400", "validation")):
        return "validation", False

    # Default: unknown, not retryable
    return "business", False


def dispatch(cmd: ParsedCommand) -> str:
    """Execute the right agent for a classified command. Returns reply text."""

    # Stop / undo last action — handled before normal dispatch
    if cmd.intent == Intent.STOP:
        from jobpulse.last_action import undo_last_action
        return undo_last_action()

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
        Intent.REMOVE_TASK: _handle_remove_task,
        Intent.CREATE_EVENT: _handle_create_event,
        Intent.LOG_SPEND: _handle_log_spend,
        Intent.LOG_INCOME: _handle_log_income,
        Intent.LOG_SAVINGS: _handle_log_savings,
        Intent.SET_BUDGET: _handle_set_budget,
        Intent.SHOW_BUDGET: _handle_show_budget,
        Intent.LOG_HOURS: _handle_log_hours,
        Intent.SHOW_HOURS: _handle_show_hours,
        Intent.CONFIRM_SAVINGS: _handle_confirm_savings,
        Intent.UNDO_HOURS: _handle_undo_hours,
        Intent.UNDO_BUDGET: _handle_undo_budget,
        Intent.RECURRING_BUDGET: _handle_recurring_budget,
        Intent.WEEKLY_PLAN: _handle_weekly_plan,
        Intent.HELP: _handle_help,
        Intent.WEEKLY_REPORT: _handle_weekly_report,
        Intent.EXPORT: _handle_export,
        Intent.CONVERSATION: _handle_conversation,
        Intent.CLEAR_CHAT: _handle_clear_chat,
        Intent.REMOTE_SHELL: _handle_remote_shell,
        Intent.GIT_OPS: _handle_git_ops,
        Intent.FILE_OPS: _handle_file_ops,
        Intent.SYSTEM_STATUS: _handle_system_status,
        Intent.SCAN_JOBS: _handle_scan_jobs,
        Intent.SHOW_JOBS: _handle_show_jobs,
        Intent.APPROVE_JOBS: _handle_approve_jobs,
        Intent.REJECT_JOB: _handle_reject_job,
        Intent.JOB_STATS: _handle_job_stats,
        Intent.SEARCH_CONFIG: _handle_search_config,
        Intent.PAUSE_JOBS: _handle_pause_jobs,
        Intent.RESUME_JOBS: _handle_resume_jobs,
        Intent.JOB_DETAIL: _handle_job_detail,
        Intent.ENGINE_STATS: _handle_engine_stats,
        Intent.ENGINE_COMPARE: _handle_engine_compare,
        Intent.ENGINE_LEARNING: _handle_engine_learning,
        Intent.ENGINE_RESET: _handle_engine_reset,
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

        # Record action for undo ("stop" command)
        from jobpulse.last_action import save_last_action
        save_last_action(cmd.intent.value, cmd.raw, result or "")

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
        # Classify error for structured response (Domain 2, Task 2.2)
        error_cat, retryable = _classify_error(e)
        dispatch_error = DispatchError(
            error_category=error_cat,
            message=str(e),
            is_retryable=retryable,
            agent_name=cmd.intent.value,
            attempted_action=cmd.raw[:200],
        )

        trail.log_step("error", f"Error in {cmd.intent.value}", cmd.raw[:200],
                       str(e), None, dispatch_error.to_dict(), "error")
        trail.finalize(f"Error: {e}")
        event_logger.log_event(
            event_type="error",
            agent_name="dispatcher",
            action=cmd.intent.value,
            content=str(e),
            metadata={
                "intent": cmd.intent.value,
                "raw_input": cmd.raw[:200],
                "errorCategory": error_cat,
                "isRetryable": retryable,
            },
        )
        logger.error("Dispatch error [%s] %s: %s (retryable=%s)",
                     error_cat, cmd.intent.value, e, retryable)
        return dispatch_error.to_user_message()


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
    from jobpulse.notion_agent import create_tasks_batch_smart, suggest_subtasks, create_tasks_batch, create_task, parse_due_date
    from jobpulse.telegram_listener import _parse_tasks

    tasks = _parse_tasks(cmd.raw)
    if not tasks:
        return "Couldn't parse any tasks. Send them one per line:\n\nFix bug\nApply to jobs\nTailor resume"

    # Feature 4: Detect priority prefixes (!! = urgent, ! = high)
    # Feature 5: Extract due dates via NLP
    processed_tasks = []
    for task_text in tasks:
        priority = "normal"
        t = task_text.strip()
        if t.startswith("!!"):
            priority = "urgent"
            t = t[2:].strip()
        elif t.startswith("!"):
            priority = "high"
            t = t[1:].strip()

        # Extract due date
        cleaned, due_date = parse_due_date(t)
        processed_tasks.append({"title": cleaned, "priority": priority, "due_date": due_date})

    # Create tasks individually with priority and due date
    created = []
    duplicates = []
    big_tasks_list = []

    from jobpulse.notion_agent import check_duplicate
    for pt in processed_tasks:
        title = pt["title"]
        if not title:
            continue

        existing = check_duplicate(title)
        if existing:
            duplicates.append({"new": title, "existing": existing})
            continue

        words = title.split()
        has_conjunction = any(w.lower() in ("and", "then", "also", "plus", "&") for w in words)
        if len(words) > 12 or (len(words) > 6 and has_conjunction):
            big_tasks_list.append(pt)
            continue

        success = create_task(title, priority=pt["priority"], due_date=pt["due_date"])
        if success:
            display = title
            if pt["priority"] == "urgent":
                display = "🔴 " + display
            elif pt["priority"] == "high":
                display = "🟡 " + display
            if pt["due_date"]:
                display += f" (due: {pt['due_date']})"
            created.append(display)

    lines = []
    if created:
        lines.append(f"✅ Created {len(created)} tasks:")
        for t in created:
            lines.append(f"  □ {t}")

    if duplicates:
        lines.append(f"\n⚠️ Skipped {len(duplicates)} duplicate(s):")
        for d in duplicates:
            lines.append(f"  ↳ \"{d['new']}\" — already exists as \"{d['existing']}\"")

    if big_tasks_list:
        lines.append(f"\n📋 {len(big_tasks_list)} task(s) seem too big:")
        for bt_info in big_tasks_list:
            bt = bt_info["title"]
            subtasks = suggest_subtasks(bt)
            if subtasks:
                lines.append(f"\n  \"{bt}\"")
                lines.append(f"  Want me to split into:")
                for st in subtasks:
                    lines.append(f"    □ {st}")
                lines.append(f"  Reply \"split: {bt[:30]}\" to create these subtasks")
            else:
                create_task(bt, priority=bt_info["priority"], due_date=bt_info["due_date"])
                lines.append(f"  □ {bt} (created as-is)")

    if not lines:
        return "No tasks to create."

    return "\n".join(lines)


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
    import re
    from jobpulse.arxiv_agent import build_digest, get_paper_detail, mark_paper_read, get_stats_text

    raw = cmd.raw.lower().strip()

    # "blog 3" → generate 2000-word blog from paper #3
    m = re.match(r"blog\s+(\d+)", raw)
    if m:
        from jobpulse.blog_generator import handle_blog_command_v2
        return handle_blog_command_v2(int(m.group(1)))

    # "regenerate 3" → regenerate blog for paper #3
    m = re.match(r"regenerate\s+(\d+)", raw)
    if m:
        from jobpulse.blog_generator import handle_blog_command_v2
        return handle_blog_command_v2(int(m.group(1)))

    # "paper 3" → full abstract for paper #3
    m = re.match(r"paper\s+(\d+)", raw)
    if m:
        return get_paper_detail(int(m.group(1)))

    # "read 1" → mark paper #1 as read
    m = re.match(r"read\s+(\d+)", raw)
    if m:
        return mark_paper_read(int(m.group(1)))

    # "papers stats" / "reading stats"
    if "stat" in raw:
        return get_stats_text()

    # Default: build/show digest
    return build_digest()


def _handle_complete_task(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import complete_task

    target = cmd.args.strip()
    if not target:
        return "Which task? Say: done: task name"

    return complete_task(target)


def _handle_remove_task(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import remove_task

    target = cmd.args.strip()
    if not target:
        return "Which task? Say: remove: task name"

    return remove_task(target)


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
    import re
    from jobpulse.budget_agent import get_week_summary, get_today_spending
    from jobpulse.budget_agent import format_week_summary, format_today

    # If user asked for comparison
    if re.search(r"compar|vs|versus", cmd.raw, re.IGNORECASE):
        from jobpulse.budget_tracker import get_weekly_comparison
        return get_weekly_comparison()

    today = get_today_spending()
    week = get_week_summary()

    parts = []
    if today["items"]:
        parts.append(format_today(today))
    parts.append(format_week_summary(week))
    return "\n\n".join(parts)


def _handle_conversation(cmd: ParsedCommand) -> str:
    from jobpulse.conversation import chat
    return chat(cmd.raw)


def _handle_clear_chat(cmd: ParsedCommand) -> str:
    from jobpulse.conversation import clear_history
    return clear_history()


def _handle_weekly_report(cmd: ParsedCommand) -> str:
    from jobpulse.weekly_report import build_weekly_report
    return build_weekly_report()


def _handle_export(cmd: ParsedCommand) -> str:
    from jobpulse.export import export_all
    try:
        path = export_all()
        return f"\U0001f4e6 Backup created!\n\nSaved to: {path}\n\nIncludes: databases, persona prompts, experiences, A/B tests, rate limits."
    except Exception as e:
        return f"\u26a0\ufe0f Export failed: {e}"


def _handle_remote_shell(cmd: ParsedCommand) -> str:
    import re
    from jobpulse.remote_shell import execute
    # Strip prefix: run:, shell:, exec:, cmd:, or $
    raw = cmd.raw.strip()
    command = re.sub(r"^(run|shell|exec|cmd):\s*", "", raw, flags=re.IGNORECASE)
    command = re.sub(r"^\$\s+", "", command)
    return execute(command)


def _handle_git_ops(cmd: ParsedCommand) -> str:
    import re
    from jobpulse.git_ops import git_status, git_log, git_diff, git_branch, git_commit, git_push
    raw = cmd.raw.strip().lower()
    if raw.startswith("git status"):
        return git_status()
    if raw.startswith("git log"):
        # Extract N from "git log N" or "git log -N"
        m = re.search(r"git log\s+[-]?(\d+)", raw)
        n = int(m.group(1)) if m else 5
        return git_log(n)
    if raw.startswith("git diff"):
        return git_diff()
    if raw.startswith("git branch"):
        return git_branch()
    if raw.startswith("git stash"):
        return "Stash not supported remotely."
    if raw.startswith("git pull"):
        return "Pull not supported remotely. Use the approval flow for push."
    if raw.startswith("commit:"):
        message = cmd.raw.strip()[len("commit:"):].strip()
        return git_commit(message)
    if raw == "push":
        return git_push()
    return "Unknown git command. Try: git status, git log, git diff, git branch, commit: msg, push"


def _handle_file_ops(cmd: ParsedCommand) -> str:
    import re
    from jobpulse.file_ops import show_file, show_logs, show_errors, continue_pagination
    raw = cmd.raw.strip().lower()
    if re.match(r"^(more|next)\s*$", raw):
        return continue_pagination()
    if re.match(r"^(logs?|show logs?|tail logs?)\s*$", raw):
        return show_logs()
    if re.match(r"^(errors?|show errors?|recent errors?)\s*$", raw):
        return show_errors()
    # File path extraction: "show: path" / "read: path" / "cat: path" / "view: path"
    m = re.match(r"^(show|read|cat|view):\s*(.+)", raw)
    if m:
        filepath = m.group(2).strip()
        return show_file(filepath)
    return "Usage: show: <filepath>, logs, errors, more/next"


def _handle_system_status(cmd: ParsedCommand) -> str:
    from jobpulse.file_ops import system_status
    return system_status()


def _handle_log_hours(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import log_hours
    return log_hours(cmd.raw)


def _handle_show_hours(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import get_hours_summary
    return get_hours_summary()


def _handle_confirm_savings(cmd: ParsedCommand) -> str:
    from jobpulse.budget_agent import confirm_savings_transfer
    return confirm_savings_transfer()


# State: which undo list was last shown (hours vs budget)
_last_undo_mode = {"mode": "budget"}  # "budget" or "hours"


def _handle_undo_hours(cmd: ParsedCommand) -> str:
    import re
    from jobpulse.budget_agent import undo_hours

    numbers = re.findall(r"\d+", cmd.raw.replace("hour", ""))
    if numbers:
        picks = sorted(set(int(n) for n in numbers), reverse=True)
        results = []
        for pick in picks:
            results.append(undo_hours(pick=pick))
        _last_undo_mode["mode"] = "budget"  # reset after action
        return "\n\n".join(results)

    _last_undo_mode["mode"] = "hours"  # remember we showed hours list
    return undo_hours()


def _handle_undo_budget(cmd: ParsedCommand) -> str:
    import re
    from jobpulse.budget_agent import undo_last_transaction, undo_hours

    numbers = re.findall(r"\d+", cmd.raw)

    # If user just said "undo 1" after seeing the hours list, redirect to hours
    if numbers and _last_undo_mode["mode"] == "hours":
        picks = sorted(set(int(n) for n in numbers), reverse=True)
        results = []
        for pick in picks:
            results.append(undo_hours(pick=pick))
        _last_undo_mode["mode"] = "budget"  # reset
        return "\n\n".join(results)

    # Normal budget undo
    if numbers:
        # Sort descending — delete highest index first so lower indices don't shift
        picks = sorted(set(int(n) for n in numbers), reverse=True)
        results = []
        for pick in picks:
            result = undo_last_transaction(pick=pick)
            results.append(result)
        return "\n\n".join(results)

    # No number — show the budget list
    _last_undo_mode["mode"] = "budget"
    return undo_last_transaction()


def _handle_recurring_budget(cmd: ParsedCommand) -> str:
    """Parse subcommand: add/list/remove recurring transactions."""
    import re
    from jobpulse.budget_agent import (
        add_recurring, list_recurring, remove_recurring,
        format_recurring, classify_transaction
    )

    raw = cmd.raw.strip()

    # List recurring
    if re.match(r"^(show |list )recurring", raw, re.IGNORECASE):
        items = list_recurring()
        return format_recurring(items)

    # Stop/cancel/remove recurring
    m = re.match(r"^(stop|cancel|remove) recurring[:\s]*(.+)", raw, re.IGNORECASE)
    if m:
        desc = m.group(2).strip()
        return remove_recurring(desc)

    # Add recurring: "recurring: 50 rent monthly"
    m = re.match(r"^recurring:\s*(.+)", raw, re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        # Parse: amount description frequency [day]
        amount_match = re.search(r"[£$€]?\s*(\d+(?:\.\d{1,2})?)", text)
        if not amount_match:
            return ("Format: recurring: <amount> <description> <daily|weekly|monthly> [day]\n"
                    "Examples:\n"
                    "  recurring: 50 rent monthly 1\n"
                    "  recurring: 10 spotify monthly\n"
                    "  recurring: 5 coffee daily")

        amount = float(amount_match.group(1))
        rest = text[:amount_match.start()] + " " + text[amount_match.end():]
        rest = re.sub(r"[£$€]", "", rest).strip()

        # Extract frequency
        frequency = "monthly"  # default
        for freq in ("daily", "weekly", "monthly"):
            if freq in rest.lower():
                frequency = freq
                rest = re.sub(freq, "", rest, flags=re.IGNORECASE).strip()
                break

        # Extract optional day number
        day = None
        day_match = re.search(r"\b(\d{1,2})\b", rest)
        if day_match and int(day_match.group(1)) <= 31:
            day = int(day_match.group(1))
            rest = rest[:day_match.start()] + rest[day_match.end():]

        description = re.sub(r"\s+", " ", rest).strip() or "Unspecified"
        section, category = classify_transaction(description, amount, "expense")

        result = add_recurring(amount, description, category, section, "expense", frequency, day)
        freq_display = frequency
        if frequency == "monthly" and day:
            freq_display = f"monthly (day {day})"
        elif frequency == "weekly" and day is not None:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            freq_display = f"weekly ({days[day]})" if day < 7 else frequency

        return (f"🔄 Added recurring: £{amount:.2f} — {description}\n"
                f"   Category: {category} ({section})\n"
                f"   Frequency: {freq_display}")

    return ("🔄 Recurring commands:\n"
            "  recurring: <amount> <desc> <daily|weekly|monthly> [day]\n"
            "  list recurring\n"
            "  stop recurring <name>")


def _handle_weekly_plan(cmd: ParsedCommand) -> str:
    from jobpulse.notion_agent import get_undone_tasks_from_past_days
    undone = get_undone_tasks_from_past_days(7)

    if not undone:
        return "✅ No undone tasks from the past 7 days. You're all caught up!"

    lines = [f"📋 UNDONE TASKS (past 7 days) — {len(undone)} found:\n"]
    for i, t in enumerate(undone, 1):
        date_str = t.get("date", "")
        lines.append(f"  {i}. {t['title']} ({date_str})")

    lines.append("\nReply 'carry: 1,3,5' to move to today, or 'carry all'")
    return "\n".join(lines)


def _handle_help(cmd: ParsedCommand) -> str:
    return """\U0001f916 JobPulse Commands:

\U0001f4dd TASKS:
  "show tasks" \u2014 see today's todo list
  "mark X done" \u2014 complete a task
  "remove: X" \u2014 delete a task
  Send a list of items \u2014 creates tasks (auto-dedup + big task splitting)

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
  "set budget groceries 50" \u2014 set period limit
  "budget" \u2014 period summary
  "undo" \u2014 undo last transaction
  "recurring: 50 rent monthly 1" \u2014 add recurring expense
  "list recurring" \u2014 show all recurring
  "stop recurring rent" \u2014 remove a recurring expense

\U0001f4dd PLANNING:
  "!! urgent task" \u2014 create urgent priority task
  "! important task" \u2014 create high priority task
  "task by Friday" \u2014 task with due date
  "weekly plan" \u2014 show undone tasks from past period
  "carry: 1,3,5" \u2014 move selected tasks to today

\U0001f4ca REPORTS:
  "period report" \u2014 28-day period summary
  "export" \u2014 data backup instructions

\U0001f4ec OTHER:
  "briefing" \u2014 full morning report
  "papers" \u2014 latest AI research
  "help" \u2014 this message

\U0001f5a5 REMOTE SHELL:
  "run: git status" \u2014 execute a command
  "$ ls -la" \u2014 shorthand

\U0001f500 GIT:
  "git status" \u2014 working tree status
  "git log 5" \u2014 last N commits
  "git diff" \u2014 changes summary
  "git branch" \u2014 current branch
  "commit: fix bug" \u2014 commit (requires approval)
  "push" \u2014 push (requires approval)

\U0001f4c4 FILES:
  "show: path/to/file" \u2014 view file contents
  "logs" \u2014 tail recent logs
  "errors" \u2014 recent agent errors
  "more" / "next" \u2014 paginate

\U0001f4ca SYSTEM:
  "status" \u2014 daemon health + agent stats

\U0001f91d APPROVAL:
  Destructive ops (commit, push) ask for yes/no confirmation

\U0001f4ac CHAT:
  Just type anything \u2014 free-form conversation
  "clear chat" \u2014 reset conversation history"""


def _handle_scan_jobs(cmd: ParsedCommand) -> str:
    """Trigger a job scan — runs the full autopilot pipeline."""
    from jobpulse.job_autopilot import run_scan_window
    return run_scan_window(platforms=["reed"])


def _handle_show_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_db import JobDB
    db = JobDB()
    pending = db.get_applications_by_status("Pending Approval")
    ready = db.get_applications_by_status("Ready")
    jobs = pending + ready
    if not jobs:
        return "No jobs pending review. Try 'job stats' for today's numbers."
    lines = [f"📋 {len(jobs)} jobs ready for review:\n"]
    for i, j in enumerate(jobs[:15], 1):
        lines.append(f"{i}. {j['title']} — {j['company']} ({j['platform']})")
        lines.append(f"   ATS: {j.get('ats_score', 0)}% | {j.get('location', 'UK')}")
    lines.append(f"\nReply: \"apply 1,3,5\" or \"apply all\" or \"reject 2\"")
    return "\n".join(lines)


def _handle_approve_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import approve_jobs
    return approve_jobs(cmd.args)


def _handle_reject_job(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import reject_job
    return reject_job(cmd.args)


def _handle_job_stats(cmd: ParsedCommand) -> str:
    from jobpulse.job_analytics import get_enhanced_job_stats
    return get_enhanced_job_stats()


def _handle_search_config(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import update_search_config
    return update_search_config(cmd.args)


def _handle_pause_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import set_autopilot_paused
    set_autopilot_paused(True)
    return "⏸️ Job Autopilot paused. No new applications until you 'resume jobs'."


def _handle_resume_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import set_autopilot_paused
    set_autopilot_paused(False)
    return "▶️ Job Autopilot resumed. Next scan will run on schedule."


def _handle_job_detail(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import get_job_detail
    return get_job_detail(cmd.args)


def _handle_engine_stats(cmd: ParsedCommand) -> str:
    from jobpulse import ab_dashboard
    return ab_dashboard.engine_stats(cmd.args)


def _handle_engine_compare(cmd: ParsedCommand) -> str:
    from jobpulse import ab_dashboard
    return ab_dashboard.engine_compare(cmd.args)


def _handle_engine_learning(cmd: ParsedCommand) -> str:
    from jobpulse import ab_dashboard
    return ab_dashboard.engine_learning(cmd.args)


def _handle_engine_reset(cmd: ParsedCommand) -> str:
    from jobpulse import ab_dashboard
    return ab_dashboard.engine_reset(cmd.args)


def _handle_unknown(cmd: ParsedCommand) -> str:
    """Suggest the closest matching command when intent is unknown."""
    text = cmd.raw.lower().strip()

    # Map keywords to their likely intent + example command
    SUGGESTIONS = [
        (["task", "todo", "to do", "checklist", "list"], "show tasks", "see your todo list"),
        (["done", "mark", "complete", "finish", "checked"], 'done: <task name>', "complete a task"),
        (["calendar", "schedule", "event", "day", "tomorrow"], "calendar", "see today's events"),
        (["email", "mail", "inbox", "recruiter", "gmail"], "check emails", "scan for recruiter emails"),
        (["commit", "github", "push", "code"], "commits", "see yesterday's GitHub activity"),
        (["trend", "hot", "popular", "repo"], "trending", "see trending repos"),
        (["budget", "spend", "money", "expense", "cost"], "budget", "see period spending"),
        (["earn", "income", "salary", "paid", "freelance"], "earned 500 freelance", "log income"),
        (["save", "saving", "invest"], "saved 100", "log savings"),
        (["brief", "morning", "update", "digest", "report"], "briefing", "get full morning report"),
        (["week", "weekly", "summary", "period"], "weekly report", "period summary"),
        (["paper", "arxiv", "research", "ai paper"], "papers", "latest AI research"),
        (["export", "backup", "dump", "download"], "export", "backup all data"),
        (["help", "command", "menu", "what can"], "help", "see all commands"),
    ]

    # Find best matching suggestion by keyword overlap
    best_match = None
    best_score = 0
    words = set(text.split())

    for keywords, example, description in SUGGESTIONS:
        score = sum(1 for kw in keywords if kw in text or any(kw in w for w in words))
        if score > best_score:
            best_score = score
            best_match = (example, description)

    if best_match and best_score > 0:
        example, description = best_match
        return (f"🤔 I didn't quite get: \"{cmd.raw[:50]}\"\n\n"
                f"Did you mean: \"{example}\" — {description}?\n\n"
                f"If not, type \"help\" to see all commands.")
    else:
        return (f"🤔 I didn't recognize: \"{cmd.raw[:50]}\"\n\n"
                f"Type \"help\" to see all available commands, "
                f"or just tell me what you need!")
