"""CLI runner — invoke any agent from command line or cron."""

import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m jobpulse.runner <command>")
        print("Commands: briefing, gmail, calendar, calendar-remind, github, tasks, budget, listen, daemon, test")
        sys.exit(1)

    command = sys.argv[1]

    if command == "briefing":
        from jobpulse.morning_briefing import build_and_send
        build_and_send()

    elif command == "gmail":
        from jobpulse.gmail_agent import check_emails
        results = check_emails()
        print(f"Found {len(results)} recruiter emails")

    elif command == "calendar":
        from jobpulse.calendar_agent import get_today_and_tomorrow, format_events
        cal = get_today_and_tomorrow()
        print("TODAY:")
        print(format_events(cal["today_events"]))
        print("TOMORROW:")
        print(format_events(cal["tomorrow_events"]))

    elif command == "calendar-remind":
        from jobpulse.calendar_agent import get_upcoming_reminders
        from jobpulse.telegram_agent import send_message
        reminders = get_upcoming_reminders(within_minutes=120)
        for r in reminders:
            loc = f" ({r['location']})" if r.get("location") else ""
            send_message(f"⏰ REMINDER: \"{r['title']}\"{loc} starts in {r['in']} — {r['start']}")
            print(f"Sent reminder: {r['title']}")
        if not reminders:
            print("No upcoming events in next 2 hours")

    elif command == "github":
        from jobpulse.github_agent import get_yesterday_commits, format_commits
        data = get_yesterday_commits()
        print(format_commits(data))

    elif command == "tasks":
        from jobpulse.notion_agent import get_today_tasks, format_tasks
        tasks = get_today_tasks()
        print(format_tasks(tasks))

    elif command == "budget":
        from jobpulse.budget_agent import get_week_summary, format_week_summary
        print(format_week_summary(get_week_summary()))

    elif command == "listen":
        from jobpulse.telegram_listener import poll_and_process
        poll_and_process()

    elif command == "daemon":
        from jobpulse.telegram_listener import poll_continuous
        poll_continuous()

    elif command == "health":
        from jobpulse.healthcheck import alert_if_down
        alert_if_down()

    elif command == "test":
        from jobpulse.telegram_agent import send_message
        success = send_message("🧪 JobPulse test message — all systems operational!")
        print(f"Telegram: {'OK' if success else 'FAILED'}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
