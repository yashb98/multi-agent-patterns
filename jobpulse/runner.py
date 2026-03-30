"""CLI runner — invoke any agent from command line or cron."""

import os
import sys
from shared.logging_config import get_logger

logger = get_logger(__name__)


def main():
    if len(sys.argv) < 2:
        logger.info("Usage: python -m jobpulse.runner <command>")
        logger.info("Commands: stop, restart, briefing, gmail, calendar, calendar-remind, github, tasks, budget, weekly-report, export, listen, daemon, multi-bot, webhook, slack, discord, multi, health, profile-sync, test")
        sys.exit(1)

    command = sys.argv[1]

    if command == "stop":
        import subprocess
        result = subprocess.run(
            ["pgrep", "-f", "jobpulse.runner (daemon|multi|multi-bot|slack|discord)"],
            capture_output=True, text=True,
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p and p != str(os.getpid())]
        if pids:
            for pid in pids:
                os.kill(int(pid), 15)  # SIGTERM
                logger.info("Stopped process %s", pid)
            logger.info("Stopped %d daemon process(es)", len(pids))
        else:
            logger.info("No running daemon found")

    elif command == "restart":
        import subprocess
        # Stop existing
        result = subprocess.run(
            ["pgrep", "-f", "jobpulse.runner (daemon|multi|multi-bot|slack|discord)"],
            capture_output=True, text=True,
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p and p != str(os.getpid())]
        for pid in pids:
            os.kill(int(pid), 15)
        if pids:
            import time
            logger.info("Stopped %d old process(es), restarting...", len(pids))
            time.sleep(2)
        # Start multi (default)
        mode = sys.argv[2] if len(sys.argv) > 2 else "multi"
        subprocess.Popen([sys.executable, "-m", "jobpulse.runner", mode])
        logger.info("Restarted in '%s' mode", mode)

    elif command == "briefing":
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
        from jobpulse.telegram_bots import send_alert
        reminders = get_upcoming_reminders(within_minutes=120)
        for r in reminders:
            loc = f" ({r['location']})" if r.get("location") else ""
            send_alert(f"⏰ REMINDER: \"{r['title']}\"{loc} starts in {r['in']} — {r['start']}")
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

    elif command == "multi-bot":
        from jobpulse.multi_bot_listener import start_all_bots
        start_all_bots()

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

    elif command == "archive-week":
        from jobpulse.budget_tracker import archive_current_week
        result = archive_current_week()
        logger.info(result)

    elif command == "budget-compare":
        from jobpulse.budget_tracker import get_weekly_comparison
        print(get_weekly_comparison())

    elif command == "budget-export":
        from jobpulse.budget_tracker import get_budget_dataset_csv
        path = get_budget_dataset_csv()
        logger.info("Exported to: %s", path)

    elif command == "job-scan":
        from jobpulse.job_autopilot import run_scan_window
        run_scan_window()

    elif command == "job-scan-quick":
        from jobpulse.job_autopilot import run_scan_window
        run_scan_window(["linkedin", "indeed", "reed"])

    elif command == "job-scan-slow":
        from jobpulse.job_autopilot import run_scan_window
        run_scan_window(["glassdoor", "totaljobs"])

    elif command == "job-follow-ups":
        from jobpulse.job_autopilot import check_follow_ups
        check_follow_ups()

    elif command == "job-stats":
        from jobpulse.job_db import JobDB
        db = JobDB()
        stats = db.get_today_stats()
        print(f"Applied: {stats['applied']}")
        print(f"Found: {stats['found']}")
        print(f"Skipped: {stats['skipped']}")
        print(f"Avg ATS: {stats['avg_ats']}%")

    elif command == "profile-sync":
        from jobpulse.github_profile_sync import sync_profile
        sync_profile()

    elif command == "test":
        from jobpulse.telegram_agent import send_message
        success = send_message("🧪 JobPulse test message — all systems operational!")
        logger.info("Telegram: %s", "OK" if success else "FAILED")

    else:
        logger.error("Unknown command: %s", command)
        sys.exit(1)


if __name__ == "__main__":
    main()
