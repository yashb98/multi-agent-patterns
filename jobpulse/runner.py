"""CLI runner — invoke any agent from command line or cron."""

import sys
from shared.logging_config import get_logger

logger = get_logger(__name__)


def main():
    if len(sys.argv) < 2:
        logger.info("Usage: python -m jobpulse.runner <command>")
        logger.info("Commands: briefing, gmail, calendar, calendar-remind, github, tasks, budget, weekly-report, export, listen, daemon, webhook, slack, discord, multi, health, test")
        sys.exit(1)

    command = sys.argv[1]

    if command == "briefing":
        from jobpulse.morning_briefing import build_and_send
        build_and_send()

    elif command == "gmail":
        from jobpulse.gmail_agent import check_emails
        results = check_emails()
        logger.info("Found %d recruiter emails", len(results))

    elif command == "calendar":
        from jobpulse.calendar_agent import get_today_and_tomorrow, format_events
        cal = get_today_and_tomorrow()
        logger.info("TODAY:")
        logger.info(format_events(cal["today_events"]))
        logger.info("TOMORROW:")
        logger.info(format_events(cal["tomorrow_events"]))

    elif command == "calendar-remind":
        from jobpulse.calendar_agent import get_upcoming_reminders
        from jobpulse.telegram_agent import send_message
        reminders = get_upcoming_reminders(within_minutes=120)
        for r in reminders:
            loc = f" ({r['location']})" if r.get("location") else ""
            send_message(f"⏰ REMINDER: \"{r['title']}\"{loc} starts in {r['in']} — {r['start']}")
            logger.info("Sent reminder: %s", r['title'])
        if not reminders:
            logger.info("No upcoming events in next 2 hours")

    elif command == "github":
        from jobpulse.github_agent import get_yesterday_commits, format_commits
        data = get_yesterday_commits()
        logger.info(format_commits(data))

    elif command == "tasks":
        from jobpulse.notion_agent import get_today_tasks, format_tasks
        tasks = get_today_tasks()
        logger.info(format_tasks(tasks))

    elif command == "budget":
        from jobpulse.budget_agent import get_week_summary, format_week_summary
        logger.info(format_week_summary(get_week_summary()))

    elif command == "weekly-report":
        from jobpulse.weekly_report import send_weekly_report
        send_weekly_report()

    elif command == "export":
        from jobpulse.export import export_all
        path = export_all()
        logger.info("Export saved to: %s", path)

    elif command == "listen":
        from jobpulse.telegram_listener import poll_and_process
        poll_and_process()

    elif command == "daemon":
        from jobpulse.telegram_listener import poll_continuous
        poll_continuous()

    elif command == "health":
        from jobpulse.healthcheck import alert_if_down
        alert_if_down()

    elif command == "webhook":
        from jobpulse.webhook_server import app, register_webhook
        import uvicorn
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        if url:
            register_webhook(url)
        logger.info("Webhook server starting on port 8080")
        uvicorn.run(app, host="0.0.0.0", port=8080)

    elif command == "slack":
        from jobpulse.platforms.slack_adapter import SlackAdapter
        adapter = SlackAdapter()
        adapter.poll_continuous()

    elif command == "discord":
        from jobpulse.platforms.discord_adapter import DiscordAdapter
        adapter = DiscordAdapter()
        adapter.poll_continuous()

    elif command == "multi":
        from jobpulse.multi_listener import start_all
        start_all()

    elif command == "arxiv":
        from jobpulse.arxiv_agent import send_daily_digest
        send_daily_digest()

    elif command == "notion-papers":
        from jobpulse.notion_papers_agent import create_weekly_page
        create_weekly_page()

    elif command == "test":
        from jobpulse.telegram_agent import send_message
        success = send_message("🧪 JobPulse test message — all systems operational!")
        logger.info("Telegram: %s", "OK" if success else "FAILED")

    else:
        logger.error("Unknown command: %s", command)
        sys.exit(1)


if __name__ == "__main__":
    main()
