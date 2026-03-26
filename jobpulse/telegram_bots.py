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
