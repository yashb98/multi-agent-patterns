"""Discord adapter — sends/receives messages via Discord REST API using httpx."""

import os
import time
import httpx
from jobpulse.platforms.base import PlatformAdapter
from jobpulse.command_router import classify
from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID", "")
DISCORD_API = "https://discord.com/api/v10"

# Discord enforces a 2000-character limit per message.
DISCORD_MAX_LEN = 2000

LAST_ID_FILE = DATA_DIR / "discord_last_message_id.txt"


class DiscordAdapter(PlatformAdapter):
    """Platform adapter for Discord REST API."""

    def __init__(self):
        self.token = DISCORD_BOT_TOKEN
        self.channel = DISCORD_CHANNEL_ID
        self.user_id = DISCORD_USER_ID
        if not self.token:
            logger.warning("DISCORD_BOT_TOKEN not set — Discord adapter disabled")

    def get_platform_name(self) -> str:
        return "Discord"

    def send_message(self, text: str, chat_id: str = None) -> bool:
        """Send a message to Discord via channel messages endpoint."""
        if not self.token:
            logger.warning("Cannot send — DISCORD_BOT_TOKEN missing")
            return False

        channel = chat_id or self.channel
        if not channel:
            logger.warning("Cannot send — no Discord channel configured")
            return False

        formatted = self.format_for_platform(text)
        chunks = _split_message(formatted, DISCORD_MAX_LEN)
        success = True

        for chunk in chunks:
            try:
                resp = httpx.post(
                    f"{DISCORD_API}/channels/{channel}/messages",
                    headers={
                        "Authorization": f"Bot {self.token}",
                        "Content-Type": "application/json",
                    },
                    json={"content": chunk},
                    timeout=15,
                )
                if resp.status_code not in (200, 201):
                    logger.error("Discord API error %d: %s", resp.status_code, resp.text[:200])
                    success = False
            except Exception as e:
                logger.error("Discord send failed: %s", e)
                success = False

        return success

    def format_for_platform(self, text: str) -> str:
        """Discord supports standard markdown; pass through as-is."""
        return text

    def poll_once(self):
        """Fetch new messages from Discord, classify, dispatch, reply.

        Uses GET /channels/{id}/messages to fetch recent messages.
        Filters to only process messages from the authorized user (DISCORD_USER_ID).
        """
        if not self.token or not self.channel:
            return

        # Lazy import to avoid circular deps at module level
        _USE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in ("true", "1", "yes")
        if _USE_SWARM:
            from jobpulse.swarm_dispatcher import dispatch
        else:
            from jobpulse.dispatcher import dispatch

        last_id = _get_last_id()
        params = {"limit": 20}
        if last_id:
            params["after"] = last_id

        try:
            resp = httpx.get(
                f"{DISCORD_API}/channels/{self.channel}/messages",
                headers={"Authorization": f"Bot {self.token}"},
                params=params,
                timeout=15,
            )
            if resp.status_code != 200:
                logger.error("Discord fetch error %d: %s", resp.status_code, resp.text[:200])
                return
            messages = resp.json()
        except Exception as e:
            logger.error("Discord poll failed: %s", e)
            return

        if not messages:
            return

        # Messages come newest-first; reverse to process chronologically
        messages.sort(key=lambda m: int(m.get("id", "0")))

        max_id = last_id or "0"

        for msg in messages:
            msg_id = msg.get("id", "0")
            if last_id and int(msg_id) <= int(last_id):
                continue

            max_id = max(max_id, msg_id, key=lambda x: int(x))

            # Only process messages from the authorized user
            author_id = msg.get("author", {}).get("id", "")
            if self.user_id and author_id != self.user_id:
                continue

            # Skip bot messages
            if msg.get("author", {}).get("bot", False):
                continue

            text = msg.get("content", "").strip()
            if not text:
                continue

            # Skip greetings
            if text.lower() in ("hi", "hello", "hey"):
                continue

            logger.info("Discord msg: %s", text[:80])

            cmd = classify(text)
            reply = dispatch(cmd)
            self.send_message(reply)

        if int(max_id) > int(last_id or "0"):
            _save_last_id(max_id)

    def poll_continuous(self):
        """Long-running poll loop for Discord (REST polling every 3s)."""
        from jobpulse.healthcheck import write_heartbeat

        logger.info("Discord listener started (polling mode)")
        consecutive_errors = 0

        while True:
            try:
                self.poll_once()
                consecutive_errors = 0
                write_heartbeat()
                time.sleep(3)
            except KeyboardInterrupt:
                logger.info("Discord listener stopped")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error("Discord error (%d): %s", consecutive_errors, e)
                if consecutive_errors > 5:
                    time.sleep(min(60, consecutive_errors * 5))
                else:
                    time.sleep(2)


def _get_last_id() -> str:
    """Read last processed Discord message ID."""
    try:
        return LAST_ID_FILE.read_text().strip()
    except (FileNotFoundError, ValueError):
        return ""


def _save_last_id(msg_id: str):
    """Save last processed Discord message ID."""
    LAST_ID_FILE.write_text(msg_id)


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message into chunks that fit within max_len."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
