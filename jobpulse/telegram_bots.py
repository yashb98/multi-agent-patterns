"""Multi-bot Telegram setup — routes messages to the right bot based on category.

4 bots, each with its own chat:
  Main Bot:     All commands, conversation, remote control
  Budget Bot:   Budget-only commands (spend, earn, save, undo, recurring, budget)
  Research Bot: Papers, arXiv digest, knowledge queries
  Alert Bot:    Send-only — recruiter emails, calendar reminders, budget alerts
"""

import json
import subprocess
from shared.logging_config import get_logger
from jobpulse.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    TELEGRAM_BUDGET_BOT_TOKEN, TELEGRAM_RESEARCH_BOT_TOKEN, TELEGRAM_ALERT_BOT_TOKEN,
)

logger = get_logger(__name__)


def _send(token: str, text: str, chat_id: str = None) -> bool:
    """Send a message via a specific bot token."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not token or not cid:
        return False

    payload = json.dumps({"chat_id": cid, "text": text})
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=15
        )
        resp = json.loads(result.stdout)
        return resp.get("ok", False)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


def _get_updates(token: str, offset: int = 0, long_poll: bool = False) -> list[dict]:
    """Get updates from a specific bot token."""
    if not token:
        return []
    timeout_param = 30 if long_poll else 1
    curl_timeout = timeout_param + 10

    try:
        result = subprocess.run(
            ["curl", "-s",
             f"https://api.telegram.org/bot{token}/getUpdates"
             f"?offset={offset}&timeout={timeout_param}"],
            capture_output=True, text=True, timeout=curl_timeout
        )
        data = json.loads(result.stdout)
        return data.get("result", [])
    except subprocess.TimeoutExpired:
        return []
    except Exception:
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


# ── Intent → Bot mapping ──

# Which intents route to which bot for REPLIES
BUDGET_INTENTS = {
    "log_spend", "log_income", "log_savings", "set_budget",
    "show_budget", "undo_budget", "recurring_budget",
    "log_hours", "show_hours", "confirm_savings",
}

RESEARCH_INTENTS = {
    "arxiv",
}

# Alert bot is send-only — these are for proactive notifications, not replies
ALERT_CATEGORIES = {
    "recruiter_email",    # Gmail agent alerts
    "calendar_reminder",  # Calendar reminders
    "budget_alert",       # 80% budget warnings
    "daemon_down",        # Health watchdog alerts
}


def send_for_intent(intent: str, text: str) -> bool:
    """Route a reply to the correct bot based on intent."""
    if intent in BUDGET_INTENTS:
        return send_budget(text)
    if intent in RESEARCH_INTENTS:
        return send_research(text)
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
  "weekly report" — 7-day summary

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
  "hours" / "timesheet" — this week's summary
  "saved" / "transferred" — confirm savings transfer
  Week runs Sun\u2013Sat. Shows tax (20%) + savings (30%).

\U0001f4ca OVERVIEW:
  "budget" — weekly summary with alerts
  "set budget groceries 50" — set weekly limit

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
Weekly Notion summary posted Mondays 8:33am."""

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
    }.get(bot_name, HELP_MAIN)
