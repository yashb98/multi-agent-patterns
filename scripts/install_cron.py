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
PYTHON_BIN_DIR = str(Path(PYTHON).parent)
RUNNER = f"cd {PROJECT_DIR} && {PYTHON} -m jobpulse.runner"

CRONTAB = f"""# ══════════════════════════════════════════════════════════
# JobPulse — Fully Autonomous Agent Schedule
# Primary: local daemon + cron | Backup: GitHub Actions
# ══════════════════════════════════════════════════════════

# PATH includes the Python interpreter's directory + standard system paths
# (works on both macOS and Linux — /opt/homebrew only exists on macOS)
PATH={PYTHON_BIN_DIR}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin

# ── DAILY ──

# arXiv AI research digest (7:57am) — Python agent, NOT shell script
57 7 * * * {RUNNER} arxiv >> {PROJECT_DIR}/logs/arxiv.log 2>&1

# Daily Research Journal (8:00am — runs after the 7:57am arxiv digest)
 0 8 * * * {RUNNER} journal-daily >> {PROJECT_DIR}/logs/journal.log 2>&1

# Weekly Journal quality audit (Sun 9:00pm)
 0 21 * * 0 {RUNNER} journal-quality-audit >> {PROJECT_DIR}/logs/journal.log 2>&1

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

# ── JOB AUTOPILOT ──

# Full scan — Reed + LinkedIn + Indeed (3 platforms; Glassdoor + TotalJobs
# were removed from PLATFORM_SCANNERS in jobpulse/job_scanner.py)
 0 7 * * * {RUNNER} job-scan >> {PROJECT_DIR}/logs/jobs.log 2>&1
 0 13 * * * {RUNNER} job-scan >> {PROJECT_DIR}/logs/jobs.log 2>&1
 0 19 * * * {RUNNER} job-scan >> {PROJECT_DIR}/logs/jobs.log 2>&1

# Quick scan — same 3 platforms as job-scan (kept for the off-peak slots
# at 10am / 4:30pm; functionally identical until per-platform throttling
# is added)
 0 10 * * * {RUNNER} job-scan-quick >> {PROJECT_DIR}/logs/jobs.log 2>&1
30 16 * * * {RUNNER} job-scan-quick >> {PROJECT_DIR}/logs/jobs.log 2>&1

# Removed 2026-05-04: `job-scan-slow` / 2am overnight scan (Glassdoor +
# TotalJobs). Both platforms were deleted from PLATFORM_SCANNERS and the
# runner has no `job-scan-slow` handler — the cron entry was firing
# nightly with no effect.

# Nightly skill/project profile sync (3am)
 0 3 * * * {RUNNER} profile-sync >> {PROJECT_DIR}/logs/profile_sync.log 2>&1

# Follow-up reminders (9am daily)
 0 9 * * * {RUNNER} job-follow-ups >> {PROJECT_DIR}/logs/jobs.log 2>&1

# ── WEEKLY ──

# Weekly research papers to Notion (Monday 8:33am) — Python agent
33 8 * * 1 {RUNNER} notion-papers >> {PROJECT_DIR}/logs/notion.log 2>&1

# Archive budget week + carry over planned (Sunday 7am)
 0 7 * * 0 {RUNNER} archive-week >> {PROJECT_DIR}/logs/budget.log 2>&1

# Weekly report summary (Sunday 8pm)
 0 20 * * 0 {RUNNER} weekly-report >> {PROJECT_DIR}/logs/weekly.log 2>&1

# Weekly optimization maintenance (Sunday 9pm) — exports trajectories,
# rotates trajectory store, runs forgetting sweep on memory engines
 0 21 * * 0 {RUNNER} learning-maintenance >> {PROJECT_DIR}/logs/optimize.log 2>&1

# ── LEARNING ──

# Hourly optimization cycle — aggregator pattern detection + policy
# decisions + tracker measurement + signal flush. shared/optimization
# CLAUDE.md specifies hourly cadence.
15 * * * * {RUNNER} optimize >> {PROJECT_DIR}/logs/optimize.log 2>&1

# Daily DB-retrieval drop-rate summary (8:30am) — flags any
# (db, table) whose returned data is being dropped downstream
# above 50% over a 7-day window. Emits a `failure` signal to the
# optimization engine and appends an OPRAL investigation prompt
# to .claude/mistakes.md.
30 8 * * * cd {PROJECT_DIR} && {PYTHON} -m scripts.db_observability_summary >> {PROJECT_DIR}/logs/db_observability.log 2>&1

# ── MONITORING ──

# Health watchdog (every 10 min) — alerts if daemon is down
*/10 * * * * {RUNNER} health >> {PROJECT_DIR}/logs/health.log 2>&1

# ── AUTO-RESTART ──

# Restart daemon every 3 hours to prevent degradation
# (memory leaks, stale SSL, hung connections, CPU accumulation)
0 */3 * * * {PROJECT_DIR}/scripts/restart_daemon.sh >> {PROJECT_DIR}/logs/restart.log 2>&1
"""


MARKER_BEGIN = "# >>> JOBPULSE BEGIN >>>"
MARKER_END = "# <<< JOBPULSE END <<<"


def _merge_crontab(new_block: str) -> str:
    """Read existing crontab, strip old JobPulse block, insert new one."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    lines = existing.splitlines(keepends=True)
    merged: list[str] = []
    inside_block = False
    for line in lines:
        if MARKER_BEGIN in line:
            inside_block = True
            continue
        if MARKER_END in line:
            inside_block = False
            continue
        if not inside_block:
            merged.append(line)

    while merged and merged[-1].strip() == "":
        merged.pop()

    if merged:
        merged.append("\n")
    merged.append(f"{MARKER_BEGIN}\n")
    merged.append(new_block.strip() + "\n")
    merged.append(f"{MARKER_END}\n")
    return "".join(merged)


def main():
    python_path_file = PROJECT_DIR / ".python_path"
    python_path_file.write_text(PYTHON)
    print(f"Saved Python path: {PYTHON} → {python_path_file}")

    merged = _merge_crontab(CRONTAB)
    print("Installing JobPulse crontab (preserving non-JobPulse entries)...\n")
    print(merged)

    confirm = input("Install this crontab? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    result = subprocess.run(
        ["crontab", "-"],
        input=merged,
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
