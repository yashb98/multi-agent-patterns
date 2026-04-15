"""Lightweight alerting via Telegram for outages, cost spikes, quality drops."""

import os
import time
import enum
import urllib.request
import urllib.parse

from shared.logging_config import get_logger

logger = get_logger(__name__)


class AlertLevel(enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def _send_telegram(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_ALERT_CHAT_ID",
                              os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat_id:
        logger.warning("Alert not sent (no Telegram config): %s", message[:100])
        return
    query = urllib.parse.urlencode({"chat_id": chat_id, "text": message, "parse_mode": "HTML"})
    url = f"https://api.telegram.org/bot{token}/sendMessage?{query}"
    try:
        urllib.request.urlopen(url, timeout=10)
    except Exception as exc:
        logger.error("Failed to send alert: %s", exc)


class AlertManager:
    """Deduplicating alert manager. Suppresses identical alerts within 5-min window."""

    def __init__(self, dedup_window: int = 300):
        self._dedup_window = dedup_window
        self._recent: dict[str, float] = {}  # key -> timestamp

    def alert(self, level: AlertLevel, message: str, source: str = ""):
        key = f"{level.value}:{source}:{message}"
        now = time.time()
        if key in self._recent and now - self._recent[key] < self._dedup_window:
            return  # Suppressed
        self._recent[key] = now
        formatted = f"<b>[{level.value}]</b> {message}\nSource: {source}"
        _send_telegram(formatted)

    def cost_alert(self, spent: float, cap: float):
        pct = (spent / cap * 100) if cap > 0 else 0
        self.alert(
            AlertLevel.WARNING,
            f"LLM cost at {pct:.0f}% of budget (${spent:.2f}/${cap:.2f})",
            source="cost_enforcer",
        )

    def outage_alert(self, provider: str):
        self.alert(AlertLevel.CRITICAL, f"{provider} API down — circuit breaker OPEN", source="circuit_breaker")
