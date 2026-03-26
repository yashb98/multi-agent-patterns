#!/usr/bin/env python3
"""Install system crontab for all JobPulse agents.

All jobs now use Python runner (no shell scripts for agents).
PATH is set at the top for homebrew + system binaries.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
PYTHON = sys.executable
RUNNER = f"cd {PROJECT_DIR} && {PYTHON} -m jobpulse.runner"

CRONTAB = f"""# ══════════════════════════════════════════════════════════
# JobPulse — Fully Autonomous Agent Schedule
# Primary: local daemon + cron | Backup: GitHub Actions
# ══════════════════════════════════════════════════════════

# PATH for homebrew (gh, node, vercel, etc.)
PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# ── DAILY ──

# arXiv AI research digest (7:57am) — Python agent, NOT shell script
57 7 * * * {RUNNER} arxiv >> {PROJECT_DIR}/logs/arxiv.log 2>&1

# Morning briefing (8:03am) — all agents → one Telegram message
 3 8 * * * {RUNNER} briefing >> {PROJECT_DIR}/logs/morning.log 2>&1

# Calendar reminders (9am, 12pm, 3pm) — 2-hour lookahead
 0 9 * * * {RUNNER} calendar-remind >> {PROJECT_DIR}/logs/calendar.log 2>&1
 0 12 * * * {RUNNER} calendar-remind >> {PROJECT_DIR}/logs/calendar.log 2>&1
 0 15 * * * {RUNNER} calendar-remind >> {PROJECT_DIR}/logs/calendar.log 2>&1

# Gmail recruiter checks (1pm, 3pm, 5pm) — instant alerts
 2 13 * * * {RUNNER} gmail >> {PROJECT_DIR}/logs/gmail.log 2>&1
 2 15 * * * {RUNNER} gmail >> {PROJECT_DIR}/logs/gmail.log 2>&1
 2 17 * * * {RUNNER} gmail >> {PROJECT_DIR}/logs/gmail.log 2>&1

# ── WEEKLY ──

# Weekly research papers to Notion (Monday 8:33am) — Python agent
33 8 * * 1 {RUNNER} notion-papers >> {PROJECT_DIR}/logs/notion.log 2>&1

# Weekly report summary (Sunday 8pm)
 0 20 * * 0 {RUNNER} weekly-report >> {PROJECT_DIR}/logs/weekly.log 2>&1

# ── MONITORING ──

# Health watchdog (every 10 min) — alerts if daemon is down
*/10 * * * * {RUNNER} health >> {PROJECT_DIR}/logs/health.log 2>&1
"""


def main():
    print("Installing JobPulse crontab...\n")
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
