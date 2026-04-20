"""Pipeline alerting — send escalation messages on critical failures.

Uses the Telegram Alert bot for real-time notifications.
Set ALERTING_ENABLED=false to disable.
"""

from __future__ import annotations

import os
import threading
import time
from enum import Enum

from shared.logging_config import get_logger

logger = get_logger(__name__)

ALERTING_ENABLED = os.environ.get("ALERTING_ENABLED", "true").lower() in ("true", "1", "yes")

_SEVERITY_EMOJI = {
    "warning": "⚠️",
    "error": "❌",
    "critical": "🚨",
}


class AlertLevel(Enum):
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


def _send_telegram(text: str) -> bool:
    """Low-level Telegram send via alert bot."""
    try:
        from jobpulse.telegram_bots import send_alert
        return send_alert(text)
    except Exception as e:
        logger.warning("Failed to send Telegram alert: %s", e)
        return False


class AlertManager:
    """Central alert manager with deduplication and rate limiting."""

    def __init__(self):
        self._last_alert: dict[str, float] = {}
        self._lock = threading.Lock()
        self._dedup_window_s = 300.0  # 5 minutes

    def alert(self, level: AlertLevel, message: str, source: str = "") -> bool:
        """Send an alert with deduplication."""
        if not ALERTING_ENABLED:
            return False

        key = f"{level.value}:{source}:{message}"
        now = time.time()
        with self._lock:
            last = self._last_alert.get(key, 0)
            if now - last < self._dedup_window_s:
                logger.debug("Alert deduplicated: %s", key)
                return False
            self._last_alert[key] = now

        emoji = _SEVERITY_EMOJI.get(level.value, "⚠️")
        full_msg = f"{emoji} ALERT ({level.value.upper()})"
        if source:
            full_msg += f" [{source}]"
        full_msg += f"\n\n{message}"

        result = _send_telegram(full_msg)
        if result:
            logger.info("Alert sent (%s): %s", level.value, message[:80])
        return result

    def cost_alert(self, spent: float, cap: float) -> bool:
        """Send a cost/budget alert."""
        pct = (spent / cap * 100) if cap > 0 else 0
        return self.alert(
            AlertLevel.WARNING,
            f"Cost alert: ${spent:.2f} / ${cap:.2f} ({pct:.0f}%)",
            source="cost_enforcer",
        )


def send_pipeline_alert(
    message: str,
    severity: str = "warning",
    category: str = "general",
) -> bool:
    """Send a pipeline failure alert via Telegram Alert bot.

    Args:
        message: Alert message text (keep concise).
        severity: 'warning', 'error', or 'critical'.
        category: Rate-limiting bucket (e.g. 'scan', 'apply', 'gmail').

    Returns:
        True if alert sent successfully.
    """
    if not ALERTING_ENABLED:
        return False

    now = time.time()

    # Use a module-level singleton for rate limiting across calls
    _state = getattr(send_pipeline_alert, "_state", None)
    if _state is None:
        _state = {"last_alert_ts": {}, "lock": threading.Lock()}
        setattr(send_pipeline_alert, "_state", _state)

    min_interval = 60.0
    with _state["lock"]:
        last = _state["last_alert_ts"].get(category, 0)
        if now - last < min_interval:
            logger.debug("Alert rate-limited for category '%s'", category)
            return False
        _state["last_alert_ts"][category] = now

    try:
        from jobpulse.telegram_bots import send_alert

        emoji = _SEVERITY_EMOJI.get(severity, "⚠️")
        full_msg = f"{emoji} PIPELINE ALERT ({severity.upper()})\n\n{message}"
        result = send_alert(full_msg)
        if result:
            logger.info("Pipeline alert sent (%s): %s", severity, message[:80])
        return result
    except Exception as e:
        logger.warning("Failed to send pipeline alert: %s", e)
        return False
