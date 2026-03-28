"""Weekly report — aggregates data from all agents for the past 7 days."""
from datetime import datetime, timedelta
from shared.logging_config import get_logger
from jobpulse import telegram_agent, event_logger

logger = get_logger(__name__)


def build_weekly_report() -> str:
    """Aggregate 7-day data from all sources and format as report."""
    end = datetime.now()
    start = end - timedelta(days=7)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    sections = {}

    # 1. Email stats
    try:
        from jobpulse.db import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM processed_emails "
            "WHERE processed_at >= ? GROUP BY category", (start_str,)
        ).fetchall()
        conn.close()
        email_stats = {r["category"]: r["cnt"] for r in rows}
        total = sum(email_stats.values())
        lines = [f"  Total processed: {total}"]
        for cat, cnt in sorted(email_stats.items()):
            lines.append(f"  {cat}: {cnt}")
        sections["emails"] = "\n".join(lines) if total else "  No emails processed"
    except Exception as e:
        logger.debug("Weekly report emails: %s", e)
        sections["emails"] = "  Data unavailable"

    # 2. Budget stats
    try:
        from jobpulse.budget_agent import _get_conn as budget_conn
        conn = budget_conn()
        spending = conn.execute(
            "SELECT SUM(amount) as total, COUNT(*) as cnt FROM transactions "
            "WHERE date >= ? AND amount < 0", (start_str,)
        ).fetchone()
        income = conn.execute(
            "SELECT SUM(amount) as total, COUNT(*) as cnt FROM transactions "
            "WHERE date >= ? AND amount > 0", (start_str,)
        ).fetchone()
        conn.close()
        spend_total = abs(spending["total"] or 0)
        income_total = income["total"] or 0
        sections["budget"] = (
            f"  Income: \u00a3{income_total:.2f} ({income['cnt']} transactions)\n"
            f"  Spending: \u00a3{spend_total:.2f} ({spending['cnt']} transactions)\n"
            f"  Net: \u00a3{income_total - spend_total:.2f}"
        )
    except Exception as e:
        logger.debug("Weekly report budget: %s", e)
        sections["budget"] = "  Data unavailable"

    # 3. Agent performance
    try:
        from jobpulse.process_logger import _get_conn as trail_conn
        conn = trail_conn()
        rows = conn.execute(
            "SELECT agent_name, COUNT(DISTINCT run_id) as runs, "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors "
            "FROM agent_process_trails WHERE created_at >= ? "
            "GROUP BY agent_name ORDER BY runs DESC", (start_str,)
        ).fetchall()
        conn.close()
        if rows:
            lines = []
            for r in rows:
                d = dict(r)
                lines.append(f"  {d['agent_name']}: {d['runs']} runs, {d['errors']} errors")
            sections["agents"] = "\n".join(lines)
        else:
            sections["agents"] = "  No agent activity"
    except Exception as e:
        logger.debug("Weekly report agents: %s", e)
        sections["agents"] = "  Data unavailable"

    # 4. Task completion (from events)
    try:
        from jobpulse.event_logger import _get_conn as event_conn
        conn = event_conn()
        task_created = conn.execute(
            "SELECT COUNT(*) FROM simulation_events WHERE event_type='task_created' AND day_date >= ?", (start_str,)
        ).fetchone()[0]
        task_completed = conn.execute(
            "SELECT COUNT(*) FROM simulation_events WHERE event_type='task_completed' AND day_date >= ?", (start_str,)
        ).fetchone()[0]
        conn.close()
        sections["tasks"] = f"  Created: {task_created}\n  Completed: {task_completed}"
    except Exception as e:
        logger.debug("Weekly report tasks: %s", e)
        sections["tasks"] = "  Data unavailable"

    # 5. Job application stats
    try:
        from jobpulse.job_db import JobDB
        job_db = JobDB()
        conn = job_db._conn()
        week_applied = conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE applied_at >= ? AND status = 'Applied'",
            (start_str,),
        ).fetchone()["c"]
        week_interviews = conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE status = 'Interview' AND updated_at >= ?",
            (start_str,),
        ).fetchone()["c"]
        week_found = conn.execute(
            "SELECT COUNT(*) as c FROM job_listings WHERE found_at >= ?",
            (start_str,),
        ).fetchone()["c"]
        avg_ats_row = conn.execute(
            "SELECT AVG(ats_score) as avg FROM applications WHERE applied_at >= ? AND ats_score > 0",
            (start_str,),
        ).fetchone()
        avg_ats = round(avg_ats_row["avg"], 1) if avg_ats_row["avg"] else 0
        conn.close()
        sections["jobs"] = (
            f"  Found: {week_found} | Applied: {week_applied}\n"
            f"  Interviews: {week_interviews} | Avg ATS: {avg_ats}%"
        )
    except Exception as e:
        logger.debug("Weekly report jobs: %s", e)
        sections["jobs"] = "  Data unavailable"

    # Build message
    report = (
        f"\U0001f4ca WEEKLY REPORT ({start.strftime('%b %d')} \u2014 {end.strftime('%b %d, %Y')})\n"
        f"\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"\U0001f4e7 EMAILS:\n"
        f"{sections['emails']}\n"
        f"\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"\U0001f4b0 BUDGET:\n"
        f"{sections['budget']}\n"
        f"\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"\U0001f916 AGENT PERFORMANCE:\n"
        f"{sections['agents']}\n"
        f"\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"\U0001f4dd TASKS:\n"
        f"{sections['tasks']}\n"
        f"\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"\U0001f4bc JOB APPLICATIONS:\n"
        f"{sections['jobs']}\n"
        f"\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"Have a great week ahead! \U0001f680"
    )

    return report


def send_weekly_report(trigger: str = "scheduled"):
    """Build and send weekly report via Telegram."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("weekly_report", trigger)

    with trail.step("api_call", "Build weekly report") as s:
        report = build_weekly_report()
        s["output"] = f"Report: {len(report)} chars"

    with trail.step("api_call", "Send report via Telegram") as s:
        success = telegram_agent.send_message(report)
        s["output"] = "Sent" if success else "FAILED"

    event_logger.log_event(
        event_type="briefing_sent",
        agent_name="weekly_report",
        action="weekly_summary",
        content=report[:500],
        metadata={"trigger": trigger, "success": success},
    )

    trail.finalize(f"Weekly report {'sent' if success else 'FAILED'}")
    logger.info("Weekly report %s", "sent" if success else "FAILED")
    return success
