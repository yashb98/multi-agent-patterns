"""Streams application progress to Telegram in real-time."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from shared.logging_config import get_logger
from shared.telegram_client import telegram_url

from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_JOBS_BOT_TOKEN, TELEGRAM_JOBS_CHAT_ID

if TYPE_CHECKING:
    from jobpulse.perplexity import CompanyResearch

logger = get_logger(__name__)

_BOT_TOKEN = TELEGRAM_JOBS_BOT_TOKEN or TELEGRAM_BOT_TOKEN
_CHAT_ID = TELEGRAM_JOBS_CHAT_ID


async def _send_telegram(text: str) -> int | None:
    """Send a message, return message_id."""
    if not _BOT_TOKEN or not _CHAT_ID:
        logger.debug("Telegram stream: no token/chat_id configured")
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                telegram_url(_BOT_TOKEN, "sendMessage"),
                json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            data = resp.json()
            return data.get("result", {}).get("message_id")
    except Exception as exc:
        logger.debug("Telegram send failed: %s", exc)
        return None


async def _edit_telegram(msg_id: int, text: str) -> None:
    """Edit an existing message."""
    if not _BOT_TOKEN or not _CHAT_ID or not msg_id:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                telegram_url(_BOT_TOKEN, "editMessageText"),
                json={
                    "chat_id": _CHAT_ID,
                    "message_id": msg_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
    except Exception as exc:
        logger.debug("Telegram edit failed: %s", exc)


class TelegramApplicationStream:
    """Streams application progress to Telegram."""

    def __init__(self) -> None:
        self._msg_id: int | None = None
        self._lines: list[str] = []
        self._header: str = ""

    @staticmethod
    def _tier_label(tier: int) -> str:
        return ["Pattern", "Nano", "LLM", "Vision"][tier - 1] if 1 <= tier <= 4 else "?"

    async def stream_start(self, job: dict, company_research: CompanyResearch) -> None:
        """Send initial message with company intel."""
        tech = ", ".join(company_research.tech_stack[:5]) if company_research.tech_stack else "N/A"
        self._header = (
            f"*Applying:* {job.get('role', '?')} at {job.get('company', '?')}\n"
            f"{company_research.size} | {company_research.industry}\n"
            f"Tech: {tech}"
        )
        self._lines = []
        self._msg_id = await _send_telegram(self._header)

    async def stream_field(self, label: str, value: str, tier: int, confident: bool) -> None:
        """Update message with field progress."""
        icon = "+" if confident else "?"
        tier_lbl = self._tier_label(tier)
        self._lines.append(f"{icon} {label}: {value[:50]} [{tier_lbl}]")
        if self._msg_id:
            await _edit_telegram(self._msg_id, self._format())

    async def stream_complete(self, success: bool, gate_score: float) -> None:
        """Final status."""
        icon = "Done" if success else "Failed"
        self._lines.append(f"\n{icon} | Score: {gate_score}/10")
        if self._msg_id:
            await _edit_telegram(self._msg_id, self._format())

    def _format(self) -> str:
        body = "\n".join(self._lines)
        return f"{self._header}\n\n{body}"
