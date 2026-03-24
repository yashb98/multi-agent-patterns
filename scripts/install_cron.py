#!/usr/bin/env python3
"""Install system crontab for all JobPulse agents."""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PYTHON = sys.executable
RUNNER = f"cd {PROJECT_DIR} && {PYTHON} -m jobpulse.runner"

CRONTAB = f"""# ══════════════════════════════════════════════════════════
# JobPulse — Daily Agent Schedule
# ══════════════════════════════════════════════════════════

# Morning briefing (8:03am) — all sections → one Telegram message
 3 8 * * * {RUNNER} briefing >> {PROJECT_DIR}/logs/morning.log 2>&1

# Gmail recruiter checks (1pm, 3pm, 5pm) — instant alerts
 2 13 * * * {RUNNER} gmail >> {PROJECT_DIR}/logs/gmail.log 2>&1
 2 15 * * * {RUNNER} gmail >> {PROJECT_DIR}/logs/gmail.log 2>&1
 2 17 * * * {RUNNER} gmail >> {PROJECT_DIR}/logs/gmail.log 2>&1

# Calendar reminders (9am, 12pm, 3pm) — 2-hour lookahead
 0 9 * * * {RUNNER} calendar-remind >> {PROJECT_DIR}/logs/calendar.log 2>&1
 0 12 * * * {RUNNER} calendar-remind >> {PROJECT_DIR}/logs/calendar.log 2>&1
 0 15 * * * {RUNNER} calendar-remind >> {PROJECT_DIR}/logs/calendar.log 2>&1

# arXiv daily papers (7:57am) — keeps existing shell script
57 7 * * * cd {PROJECT_DIR} && ./scripts/arxiv-daily.sh >> {PROJECT_DIR}/logs/arxiv.log 2>&1

# Weekly research papers to Notion (Monday 8:33am) — keeps existing shell script
33 8 * * 1 cd {PROJECT_DIR} && ./scripts/agents/notion-papers.sh >> {PROJECT_DIR}/logs/notion.log 2>&1

# Telegram listener (every 5 min, 8am-10pm)
*/5 8-22 * * * cd {PROJECT_DIR} && ./scripts/agents/telegram-listener.sh >> {PROJECT_DIR}/logs/telegram-listener.log 2>&1
"""


def main():
    print("Installing JobPulse crontab...")
    print(CRONTAB)

    confirm = input("Install this crontab? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    result = subprocess.run(
        ["crontab", "-"],
        input=CRONTAB,
        text=True,
        capture_output=True,
    )

    if result.returncode == 0:
        print("✅ Crontab installed!")
        subprocess.run(["crontab", "-l"])
    else:
        print(f"❌ Failed: {result.stderr}")


if __name__ == "__main__":
    main()
