"""Telegram adapter — wraps existing telegram_agent functions behind PlatformAdapter."""

from jobpulse.platforms.base import PlatformAdapter
from jobpulse import telegram_agent
from jobpulse.config import TELEGRAM_CHAT_ID
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Telegram enforces a 4096-character limit per message.
TELEGRAM_MAX_LEN = 4096


class TelegramAdapter(PlatformAdapter):
    """Platform adapter for Telegram Bot API."""

    def get_platform_name(self) -> str:
        return "Telegram"

    def send_message(self, text: str, chat_id: str = None) -> bool:
        """Send a message via Telegram, splitting if it exceeds 4096 chars."""
        formatted = self.format_for_platform(text)
        chunks = _split_message(formatted, TELEGRAM_MAX_LEN)
        success = True
        for chunk in chunks:
            if not telegram_agent.send_message(chunk, chat_id=chat_id):
                success = False
        return success

    def format_for_platform(self, text: str) -> str:
        """Telegram supports basic markdown; pass through as-is."""
        return text

    def get_updates(self, offset: int = 0, long_poll: bool = False) -> list[dict]:
        """Delegate to existing telegram_agent.get_updates."""
        return telegram_agent.get_updates(offset=offset, long_poll=long_poll)


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message into chunks that fit within max_len.

    Splits on newline boundaries when possible, falls back to hard cut.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Try to split at the last newline before the limit
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            # No good newline — hard cut at max_len
            cut = max_len

        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    return chunks
