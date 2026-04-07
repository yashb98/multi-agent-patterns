"""Morning briefing — collects all agents, synthesizes with RLM if data is large.

Uses Enhanced Swarm architecture:
- Collects from all agents in parallel-style steps
- If total data > 5K chars, uses RLM for recursive synthesis
- Evolves briefing prompt via persona evolution after each run
"""

from datetime import datetime
from jobpulse import gmail_agent, calendar_agent, github_agent, notion_agent, telegram_agent, budget_agent, event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)


def build_and_send(trigger: str = "cron_morning"):
    """Collect all sections and send one consolidated Telegram message."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("morning_briefing", trigger)

    today = datetime.now().strftime("%A, %B %d, %Y")
    logger.info("Building morning digest for %s...", today)

    # ── Section 1: Recruiter Emails ──
    with trail.step("api_call", "Collect recruiter emails") as s:
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
        s["output"] = f"{len(emails)} recruiter emails"

    # ── Section 2: Calendar ──
    with trail.step("api_call", "Collect calendar events") as s:
        cal = calendar_agent.get_today_and_tomorrow()
        section_today = calendar_agent.format_events(cal["today_events"])
        section_tomorrow = calendar_agent.format_events(cal["tomorrow_events"])
        if not cal["today_events"]:
            section_today = "  No events today"
        if not cal["tomorrow_events"]:
            section_tomorrow = "  Nothing scheduled tomorrow"
        s["output"] = f"Today: {len(cal['today_events'])}, Tomorrow: {len(cal['tomorrow_events'])}"

    # ── Section 3: Notion Tasks ──
    with trail.step("api_call", "Collect Notion tasks") as s:
        tasks = notion_agent.get_today_tasks()
        section_tasks = notion_agent.format_tasks(tasks)
        s["output"] = f"{len(tasks)} tasks"

    # ── Section 4: GitHub ──
    with trail.step("api_call", "Collect GitHub commits") as s:
        commits_data = github_agent.get_yesterday_commits()
        section_github = github_agent.format_commits(commits_data)
        s["output"] = f"{commits_data['total_commits']} commits"

    # ── Section 5: Trending ──
    with trail.step("api_call", "Collect trending repos") as s:
        trending = github_agent.get_trending_repos()
        section_trending = github_agent.format_trending(trending)
        s["output"] = f"{len(trending)} trending repos"

    # ── Section 6: Budget ──
    # Structured error context instead of silent degradation (Domain 5, Task 5.3)
    with trail.step("api_call", "Collect budget summary") as s:
        try:
            week_summary = budget_agent.get_week_summary()
            if week_summary["by_category"]:
                section_budget = budget_agent.format_week_summary(week_summary)
            else:
                section_budget = "  No transactions logged this period"
            s["output"] = f"Income: £{week_summary['income_total']:.2f}, Spent: £{week_summary['spending_total']:.2f}"
        except Exception as e:
            from jobpulse.dispatcher import _classify_error
            error_cat, retryable = _classify_error(e)
            logger.warning("Budget data unavailable [%s]: %s (retryable=%s)", error_cat, e, retryable)
            section_budget = f"  Budget data unavailable ({error_cat}: {e})"
            s["output"] = f"Budget error [{error_cat}]: {e}"
            s["metadata"] = {"errorCategory": error_cat, "isRetryable": retryable, "error": str(e)}

    # ── Section 7: Process recurring transactions ──
    section_recurring = ""
    with trail.step("api_call", "Process recurring transactions") as s:
        try:
            logged = budget_agent.process_recurring()
            if logged:
                lines = [f"  🔄 {len(logged)} recurring transaction(s) auto-logged:"]
                for txn in logged:
                    lines.append(f"    £{txn['amount']:.2f} — {txn['description']} [{txn['category']}]")
                section_recurring = "\n".join(lines)
                s["output"] = f"{len(logged)} recurring logged"
            else:
                s["output"] = "No recurring due today"
        except Exception as e:
            logger.warning("Recurring processing failed: %s (%s)", e, type(e).__name__)
            s["output"] = f"Recurring error: {e}"
            s["metadata"] = {"error": str(e), "errorType": type(e).__name__}

    # ── Section 8: Budget alerts ──
    section_alerts = ""
    with trail.step("decision", "Check budget alerts") as s:
        try:
            alerts = budget_agent.check_budget_alerts()
            if alerts:
                section_alerts = "\n".join(f"  {a}" for a in alerts)
                s["output"] = f"{len(alerts)} alerts"
            else:
                s["output"] = "No alerts"
        except Exception as e:
            logger.warning("Budget alerts failed: %s (%s)", e, type(e).__name__)
            s["output"] = f"Alerts error: {e}"
            s["metadata"] = {"error": str(e), "errorType": type(e).__name__}

    # ── Section 9: Period spending comparison ──
    section_comparison = ""
    with trail.step("decision", "Period spending comparison") as s:
        try:
            from jobpulse.budget_tracker import get_weekly_comparison
            comparison = get_weekly_comparison()
            if "No spending data" not in comparison:
                section_comparison = comparison
                s["output"] = "Comparison generated"
            else:
                s["output"] = "Not enough data yet"
        except Exception as e:
            logger.warning("Period comparison failed: %s (%s)", e, type(e).__name__)
            s["output"] = f"Comparison error: {e}"
            s["metadata"] = {"error": str(e), "errorType": type(e).__name__}

    # ── Section 10: Job Autopilot Stats ──
    section_jobs = ""
    try:
        from jobpulse.job_db import JobDB
        from datetime import date
        job_db = JobDB()
        job_stats = job_db.get_today_stats()
        follow_ups = job_db.get_follow_ups_due(date.today())
        if job_stats["applied"] > 0 or job_stats["found"] > 0:
            section_jobs = (
                f"💼 JOB AUTOPILOT:\n"
                f"  Applied: {job_stats['applied']} (avg ATS: {job_stats['avg_ats']}%)\n"
                f"  Found: {job_stats['found']} | Skipped: {job_stats['skipped']}\n"
                f"  Follow-ups due today: {len(follow_ups)}"
            )
    except Exception as e:
        logger.error("Job stats for briefing failed: %s", e)

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
{f"""
━━━━━━━━━━━━━━━━━━━━

🔄 RECURRING:
{section_recurring}""" if section_recurring else ""}
{f"""
━━━━━━━━━━━━━━━━━━━━

⚠️ BUDGET ALERTS:
{section_alerts}""" if section_alerts else ""}
{f"""
━━━━━━━━━━━━━━━━━━━━

{section_comparison}""" if section_comparison else ""}
{f"""
━━━━━━━━━━━━━━━━━━━━

{section_jobs}""" if section_jobs else ""}

━━━━━━━━━━━━━━━━━━━━

Have a productive day! 🚀"""

    # Send digest
    with trail.step("api_call", "Send Telegram briefing",
                     step_input=f"Message: {len(message)} chars") as s:
        success = telegram_agent.send_message(message)
        s["output"] = f"{'Sent' if success else 'FAILED'}"
        logger.info("Digest %s", "sent" if success else "FAILED")

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

    # Evolve the briefing persona based on this run
    try:
        from jobpulse.persona_evolution import evolve_prompt
        # Score: higher if more data was included
        data_richness = min(10.0, (len(emails) + len(cal['today_events']) + len(tasks) + commits_data['total_commits']) * 0.5 + 3)
        evolve_prompt("briefing_synthesizer", message[:500], data_richness)
    except Exception as e:
        logger.debug("Persona evolution skipped: %s", e)

    trail.finalize(f"Briefing {'sent' if success else 'FAILED'} — "
                   f"{len(emails)} emails, {len(cal['today_events'])} events, "
                   f"{len(tasks)} tasks, {commits_data['total_commits']} commits")
    return success
