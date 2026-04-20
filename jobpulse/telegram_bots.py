"""Multi-bot Telegram setup — routes messages to the right bot based on category.

4 bots, each with its own chat:
  Main Bot:     All commands, conversation, remote control
  Budget Bot:   Budget-only commands (spend, earn, save, undo, recurring, budget)
  Research Bot: Papers, arXiv digest, knowledge queries
  Alert Bot:    Send-only — recruiter emails, calendar reminders, budget alerts
"""

import json
import random
import subprocess
import time
from shared.logging_config import get_logger
from shared.telegram_client import telegram_url
from jobpulse.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    TELEGRAM_BUDGET_BOT_TOKEN, TELEGRAM_RESEARCH_BOT_TOKEN, TELEGRAM_ALERT_BOT_TOKEN,
    TELEGRAM_JOBS_BOT_TOKEN,
)

logger = get_logger(__name__)


MAX_MSG_LEN = 4000  # Telegram limit is 4096, leave room for safety


def _send_one(token: str, text: str, chat_id: str, max_retries: int = 3) -> bool:
    """Send a single message (must be under 4096 chars) with exponential backoff retry."""
    payload = json.dumps({"chat_id": chat_id, "text": text})
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "-X", "POST",
                 telegram_url(token, "sendMessage"),
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=15
            )
            resp = json.loads(result.stdout)
            if resp.get("ok"):
                return True
            desc = resp.get("description", "").lower()
            # Rate limit — respect retry_after if provided
            retry_after = resp.get("parameters", {}).get("retry_after", 0)
            if retry_after and attempt < max_retries:
                logger.warning("Telegram rate limit (%s), retrying after %ds...", desc, retry_after)
                time.sleep(retry_after + 1)
                continue
            # Other retryable errors
            if any(k in desc for k in ("timeout", "connection", "network", "temporary", "bad gateway")):
                if attempt < max_retries:
                    delay = min(2 ** attempt, 30) * (0.5 + random.random())
                    logger.warning("Telegram transient error, retrying in %.1fs...", delay)
                    time.sleep(delay)
                    continue
            logger.warning("Telegram API error: %s", resp.get("description", "unknown"))
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 30) * (0.5 + random.random())
                logger.warning("Telegram send failed (attempt %d/%d): %s. Retrying in %.1fs...",
                               attempt + 1, max_retries + 1, e, delay)
                time.sleep(delay)
            else:
                logger.warning("Telegram send failed after %d attempts: %s", max_retries + 1, e)
                return False
    return False


def _send(token: str, text: str, chat_id: str = None) -> bool:
    """Send a message via a specific bot token. Auto-splits long messages."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not token or not cid:
        return False

    # If short enough, send directly
    if len(text) <= MAX_MSG_LEN:
        return _send_one(token, text, cid)

    # Split on section dividers first, then on newlines
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_MSG_LEN:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line

    if current:
        chunks.append(current)

    # Send each chunk
    all_ok = True
    for i, chunk in enumerate(chunks):
        ok = _send_one(token, chunk.strip(), cid)
        if not ok:
            all_ok = False
    return all_ok


def _get_updates(token: str, offset: int = 0, long_poll: bool = False, max_retries: int = 2) -> list[dict]:
    """Get updates from a specific bot token with retry."""
    if not token:
        return []
    timeout_param = 30 if long_poll else 1
    curl_timeout = timeout_param + 10

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s",
                 f"{telegram_url(token, 'getUpdates')}"
                 f"?offset={offset}&timeout={timeout_param}"],
                capture_output=True, text=True, timeout=curl_timeout
            )
            data = json.loads(result.stdout)
            return data.get("result", [])
        except subprocess.TimeoutExpired:
            return []  # Normal for long-poll when no messages
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 10) * (0.5 + random.random())
                logger.debug("Telegram get_updates failed (attempt %d): %s. Retrying in %.1fs...",
                             attempt + 1, e, delay)
                time.sleep(delay)
            else:
                logger.debug("Telegram get_updates failed after %d attempts: %s", max_retries + 1, e)
                return []
    return []


# ── Convenience functions per bot ──

def send_main(text: str) -> bool:
    """Send via main bot."""
    return _send(TELEGRAM_BOT_TOKEN, text)


def send_budget(text: str) -> bool:
    """Send via budget bot. Falls back to main if budget bot not configured."""
    token = TELEGRAM_BUDGET_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    return _send(token, text)


def send_research(text: str) -> bool:
    """Send via research bot. Falls back to main if not configured."""
    token = TELEGRAM_RESEARCH_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    return _send(token, text)


def send_alert(text: str) -> bool:
    """Send via alert bot (read-only). Falls back to main if not configured."""
    token = TELEGRAM_ALERT_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    return _send(token, text)


def send_jobs(text: str) -> bool:
    """Send via jobs bot. Falls back to main if not configured."""
    token = TELEGRAM_JOBS_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    return _send(token, text)


def send_jobs_photo(photo_path: str, caption: str = "", max_retries: int = 2) -> bool:
    """Send a photo via jobs bot with retry. Falls back to main if not configured."""
    token = TELEGRAM_JOBS_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    cid = TELEGRAM_CHAT_ID
    if not token or not cid:
        return False
    for attempt in range(max_retries + 1):
        try:
            cmd = [
                "curl", "-s", "-X", "POST",
                telegram_url(token, "sendPhoto"),
                "-F", f"chat_id={cid}",
                "-F", f"photo=@{photo_path}",
            ]
            if caption:
                cmd.extend(["-F", f"caption={caption[:1024]}"])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            resp = json.loads(result.stdout)
            if resp.get("ok"):
                return True
            desc = resp.get("description", "").lower()
            retry_after = resp.get("parameters", {}).get("retry_after", 0)
            if retry_after and attempt < max_retries:
                time.sleep(retry_after + 1)
                continue
            if any(k in desc for k in ("timeout", "connection", "network", "temporary")):
                if attempt < max_retries:
                    delay = min(2 ** attempt, 10) * (0.5 + random.random())
                    time.sleep(delay)
                    continue
            logger.warning("Telegram sendPhoto error: %s", resp.get("description", "unknown"))
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 10) * (0.5 + random.random())
                time.sleep(delay)
            else:
                logger.warning("Telegram sendPhoto failed after %d attempts: %s", max_retries + 1, e)
                return False
    return False


# ── Intent → Bot mapping ──

# Which intents route to which bot for REPLIES
BUDGET_INTENTS = {
    "log_spend", "log_income", "log_savings", "set_budget",
    "show_budget", "undo_budget", "recurring_budget",
    "log_hours", "show_hours", "confirm_savings", "undo_hours",
}

RESEARCH_INTENTS = {
    "arxiv",
}

JOBS_INTENTS = {
    "show_jobs", "approve_jobs", "reject_job", "job_stats",
    "search_config", "pause_jobs", "resume_jobs", "job_detail",
    "scan_jobs",
}

# Alert bot is send-only — these are for proactive notifications, not replies
ALERT_CATEGORIES = {
    "recruiter_email",    # Gmail agent alerts
    "calendar_reminder",  # Calendar reminders
    "budget_alert",       # 80% budget warnings
    "daemon_down",        # Health watchdog alerts
}


def send_chat_action_for_token(token: str, action: str = "typing", chat_id: str = None, max_retries: int = 2) -> bool:
    """Send a typing/chat action via a specific bot token with retry."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not token or not cid:
        return False
    payload = json.dumps({"chat_id": cid, "action": action})
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "-X", "POST",
                 telegram_url(token, "sendChatAction"),
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=5
            )
            resp = json.loads(result.stdout)
            if resp.get("ok"):
                return True
            desc = resp.get("description", "").lower()
            retry_after = resp.get("parameters", {}).get("retry_after", 0)
            if retry_after and attempt < max_retries:
                time.sleep(retry_after + 1)
                continue
            if any(k in desc for k in ("timeout", "connection", "network", "temporary")):
                if attempt < max_retries:
                    delay = min(2 ** attempt, 10) * (0.5 + random.random())
                    time.sleep(delay)
                    continue
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 10) * (0.5 + random.random())
                time.sleep(delay)
            else:
                logger.debug("send_chat_action_for_token failed: %s", e)
                return False
    return False


def send_for_intent(intent: str, text: str) -> bool:
    """Route a reply to the correct bot based on intent."""
    if intent in BUDGET_INTENTS:
        return send_budget(text)
    if intent in RESEARCH_INTENTS:
        return send_research(text)
    if intent in JOBS_INTENTS:
        return send_jobs(text)
    return send_main(text)


# ── Per-bot help messages ──

HELP_MAIN = """\U0001f916 MAIN BOT — Full Control

\U0001f4dd TASKS:
  "show tasks" — today's checklist
  "mark X done" — complete a task
  "remove: X" — delete a task
  "!! urgent task" / "! high task" — priority
  "task by Friday" — due date
  "weekly plan" — carry forward undone tasks
  Send a list — creates tasks (dedup + subtasks)

\U0001f4c5 AGENTS:
  "calendar" — today + tomorrow
  "check emails" — Gmail scan
  "commits" — yesterday's GitHub
  "trending" — hot repos
  "briefing" — full morning report
  "period report" — 28-day period summary

\U0001f5a5 REMOTE:
  "run: <cmd>" / "$ <cmd>" — shell command
  "git status" / "git log" / "commit: msg" / "push"
  "show: file.py" / "logs" / "errors" / "status"

\U0001f4ac CHAT:
  Just type anything — conversation with AI
  "clear chat" — reset history
  "export" — full data backup
  "help" — this message

\U0001f3a4 Voice messages transcribed automatically"""

HELP_BUDGET = """\U0001f4b0 BUDGET BOT — Track Your Money

\U0001f4b8 LOG TRANSACTIONS:
  "spent 15 on lunch" — log expense
  "\u00a38.50 coffee" — log expense
  "earned 500 freelance" — log income
  "saved 100" — log savings

\u23f1 SALARY / WORK HOURS:
  "worked 7 hours" — log hours \u00d7 \u00a313.99/hr
  "worked 3.5h" — same, shorter format
  "hours" / "timesheet" — this period's summary
  "saved" / "transferred" — confirm savings transfer
  28-day period from 2nd of each cycle. Shows tax (20%) + savings (30%).

\U0001f4ca OVERVIEW:
  "budget" — period summary with alerts
  "set budget groceries 50" — set period limit

\U0001f504 RECURRING:
  "recurring: 12 netflix monthly" — auto-log on schedule
  "list recurring" — show all recurring
  "stop recurring netflix" — remove one

\u21a9\ufe0f UNDO:
  "undo" — show last 5, pick one to remove
  "undo 3" / "undo 1,3" — remove by number

All amounts auto-classified into 17 categories.
Synced to Notion. Budget alerts at 80% of planned spend.
Salary creates a Notion timesheet with hours table."""

HELP_RESEARCH = """\U0001f4da RESEARCH BOT — AI Papers & Knowledge

\U0001f4f0 PAPERS:
  "papers" — today's AI research digest (top 5)
  "papers weekly" — last 7 days compilation

\U0001f9e0 HOW IT WORKS:
  1. Scans arXiv (cs.AI, cs.LG, cs.CL, cs.MA)
  2. Ranks by relevance to YOUR projects
  3. Summarizes with actionable takeaways
  4. Extracts to knowledge graph

Daily digest runs at 7:57am automatically.
Notion summary posted periodically."""

HELP_JOBS = """💼 JOBS BOT — Job Autopilot

📋 REVIEW:
  "jobs" — show pending review jobs
  "job 3" — full details for job #3
  "apply 1,3,5" — approve specific jobs
  "apply all" — approve all pending
  "reject 2" — skip a job

📊 STATS:
  "job stats" — today's numbers

🔍 SEARCH:
  "search: add title NLP Engineer"
  "search: exclude company X"

⏯️ CONTROL:
  "pause jobs" — stop autopilot
  "resume jobs" — restart autopilot

Runs: 7am, 10am, 1pm, 4:30pm, 7pm, 2am
Auto-applies 90%+ ATS. Sends 82-89% for review."""

HELP_ALERT = """\U0001f514 ALERT BOT — Notifications Only

This bot sends you alerts automatically:

\U0001f4e7 Recruiter email alerts (SELECTED/INTERVIEW/REJECTED)
\u23f0 Calendar reminders (2hr before events)
\u26a0\ufe0f Budget alerts (80% of planned spend)
\U0001f6a8 Daemon down warnings

No commands needed — this chat is read-only.
Alerts arrive as they happen throughout the day."""


def get_help_for_bot(bot_name: str) -> str:
    """Get the help message for a specific bot."""
    return {
        "main": HELP_MAIN,
        "budget": HELP_BUDGET,
        "research": HELP_RESEARCH,
        "alert": HELP_ALERT,
        "jobs": HELP_JOBS,
    }.get(bot_name, HELP_MAIN)
