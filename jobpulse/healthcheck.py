"""Health check + heartbeat — monitors daemon liveness, sends alert if down."""

import time
from datetime import datetime
from pathlib import Path
from jobpulse.config import DATA_DIR, LOGS_DIR
from jobpulse import telegram_agent
from shared.logging_config import get_logger

logger = get_logger(__name__)

HEARTBEAT_FILE = DATA_DIR / "daemon_heartbeat.txt"
HEALTH_LOG = LOGS_DIR / "health.log"


def write_heartbeat():
    """Called by daemon on every successful poll cycle."""
    HEARTBEAT_FILE.write_text(datetime.now().isoformat())


def read_heartbeat() -> str:
    """Read last heartbeat timestamp."""
    try:
        return HEARTBEAT_FILE.read_text().strip()
    except FileNotFoundError:
        return ""


def check_daemon_health(max_age_minutes: int = 10) -> dict:
    """Check if daemon is alive based on heartbeat file age."""
    hb = read_heartbeat()
    if not hb:
        return {"alive": False, "reason": "No heartbeat file", "last_seen": "never"}

    try:
        last = datetime.fromisoformat(hb)
        age = (datetime.now() - last).total_seconds() / 60
        alive = age < max_age_minutes
        return {
            "alive": alive,
            "last_seen": hb,
            "age_minutes": round(age, 1),
            "reason": "OK" if alive else f"Heartbeat stale ({age:.0f}min old)",
        }
    except (ValueError, TypeError):
        return {"alive": False, "reason": "Invalid heartbeat", "last_seen": hb}


def alert_if_down():
    """Send Telegram alert if daemon appears dead. Called by cron as a watchdog."""
    health = check_daemon_health(max_age_minutes=10)

    log_msg = f"[{datetime.now().isoformat()}] Health: alive={health['alive']} last={health['last_seen']} {health['reason']}"
    with open(HEALTH_LOG, "a") as f:
        f.write(log_msg + "\n")

    if not health["alive"]:
        telegram_agent.send_message(
            f"⚠️ JOBPULSE DOWN\n\n"
            f"The Telegram daemon hasn't responded in {health.get('age_minutes', '?')} minutes.\n"
            f"Last heartbeat: {health['last_seen']}\n\n"
            f"To restart:\n"
            f"  ./scripts/install_daemon.sh restart\n\n"
            f"Or the GitHub Actions backup will handle scheduled jobs."
        )
        logger.warning("ALERT sent — daemon down since %s", health['last_seen'])
    else:
        logger.info("OK — last heartbeat %smin ago", health['age_minutes'])
