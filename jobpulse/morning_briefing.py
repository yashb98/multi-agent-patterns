"""Morning briefing — collects all agents and sends consolidated Telegram message."""

from datetime import datetime
from jobpulse import gmail_agent, calendar_agent, github_agent, notion_agent, telegram_agent, budget_agent, event_logger


def build_and_send():
    """Collect all sections and send one consolidated Telegram message."""
    today = datetime.now().strftime("%A, %B %d, %Y")
    print(f"[Briefing] Building morning digest for {today}...")

    # ── Section 1: Recruiter Emails ──
    emails = gmail_agent.get_yesterday_recruiter_emails()
    if emails:
        email_lines = []
        for e in emails:
            label = gmail_agent.CATEGORY_EMOJI.get(e["category"], e["category"])
            sender = e["sender"].split("<")[0].strip() if "<" in e["sender"] else e["sender"]
            email_lines.append(f'  {label}: {sender} — "{e["subject"]}"')
        section_emails = "\n".join(email_lines)
    else:
        section_emails = "  No recruiter emails yesterday"

    # ── Section 2: Calendar ──
    cal = calendar_agent.get_today_and_tomorrow()
    section_today = calendar_agent.format_events(cal["today_events"])
    section_tomorrow = calendar_agent.format_events(cal["tomorrow_events"])
    if not cal["today_events"]:
        section_today = "  No events today"
    if not cal["tomorrow_events"]:
        section_tomorrow = "  Nothing scheduled tomorrow"

    # ── Section 3: Notion Tasks ──
    tasks = notion_agent.get_today_tasks()
    section_tasks = notion_agent.format_tasks(tasks)

    # ── Section 4: GitHub ──
    commits_data = github_agent.get_yesterday_commits()
    section_github = github_agent.format_commits(commits_data)

    # ── Section 5: Trending ──
    trending = github_agent.get_trending_repos()
    section_trending = github_agent.format_trending(trending)

    # ── Section 6: Budget ──
    try:
        week_summary = budget_agent.get_week_summary()
        if week_summary["by_category"]:
            section_budget = budget_agent.format_week_summary(week_summary)
        else:
            section_budget = "  No transactions logged this week"
    except Exception:
        section_budget = "  Budget data unavailable"

    # ── Build Message ──
    message = f"""☀️ Good Morning Yash! Here's your briefing for {today}:

━━━━━━━━━━━━━━━━━━━━

📧 RECRUITER EMAILS (yesterday):
{section_emails}

━━━━━━━━━━━━━━━━━━━━

📅 TODAY'S CALENDAR:
{section_today}

📅 TOMORROW PREVIEW:
{section_tomorrow}

━━━━━━━━━━━━━━━━━━━━

📝 TODAY'S TASKS (from Notion):
{section_tasks}

━━━━━━━━━━━━━━━━━━━━

💻 YESTERDAY'S GITHUB:
{section_github}

━━━━━━━━━━━━━━━━━━━━

🔥 TRENDING ON GITHUB:
{section_trending}

━━━━━━━━━━━━━━━━━━━━

{section_budget}

━━━━━━━━━━━━━━━━━━━━

Have a productive day! 🚀"""

    # Send digest
    success = telegram_agent.send_message(message)
    print(f"[Briefing] Digest {'sent' if success else 'FAILED'}")

    # Log briefing to simulation events
    event_logger.log_event(
        event_type="briefing_sent",
        agent_name="morning_briefing",
        action="daily_briefing",
        content=message[:500],
        metadata={"channel": "telegram", "success": success},
    )

    # Send separate Notion todo prompt if no tasks
    if not tasks:
        todo_prompt = """📝 Hey Yash! Quick check on your day:

I didn't find a todo list for today in Notion.

How does your day look? Would you like me to create a todo list for you?

Just reply with your tasks and I'll add them to Notion. For example:
  • Fix NexusMind CORS bug
  • Apply to 5 roles
  • Prepare for interview

Or reply 'skip' if you're good for today."""
        telegram_agent.send_message(todo_prompt)

    return success
