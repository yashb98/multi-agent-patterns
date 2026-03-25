"""Slack adapter — sends/receives messages via Slack Web API using httpx."""

import os
import time
import httpx
from jobpulse.platforms.base import PlatformAdapter
from jobpulse.command_router import classify
from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
SLACK_API = "https://slack.com/api"

# Slack message limit (blocks can be longer, but plain text tops at ~40k; we use 4000 for safety)
SLACK_MAX_LEN = 4000

LAST_TS_FILE = DATA_DIR / "slack_last_ts.txt"


class SlackAdapter(PlatformAdapter):
    """Platform adapter for Slack Web API."""

    def __init__(self):
        self.token = SLACK_BOT_TOKEN
        self.channel = SLACK_CHANNEL_ID
        if not self.token:
            logger.warning("SLACK_BOT_TOKEN not set — Slack adapter disabled")

    def get_platform_name(self) -> str:
        return "Slack"

    def send_message(self, text: str, chat_id: str = None) -> bool:
        """Send a message to Slack via chat.postMessage."""
        if not self.token:
            logger.warning("Cannot send — SLACK_BOT_TOKEN missing")
            return False

        channel = chat_id or self.channel
        if not channel:
            logger.warning("Cannot send — no Slack channel configured")
            return False

        formatted = self.format_for_platform(text)
        chunks = _split_message(formatted, SLACK_MAX_LEN)
        success = True

        for chunk in chunks:
            try:
                resp = httpx.post(
                    f"{SLACK_API}/chat.postMessage",
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"channel": channel, "text": chunk},
                    timeout=15,
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.error("Slack API error: %s", data.get("error", data))
                    success = False
            except Exception as e:
                logger.error("Slack send failed: %s", e)
                success = False

        return success

    def format_for_platform(self, text: str) -> str:
        """Convert common emoji shortcodes to Slack-compatible :emoji: format."""
        emoji_map = {
            "\U0001f4dd": ":memo:",          # memo
            "\U0001f4c5": ":calendar:",       # calendar
            "\U0001f4e7": ":email:",          # email
            "\U0001f4bb": ":computer:",       # computer
            "\U0001f525": ":fire:",           # fire
            "\U0001f4ec": ":mailbox_with_mail:",
            "\U0001f4da": ":books:",          # books
            "\u2705": ":white_check_mark:",   # check mark
            "\u2b1c": ":white_large_square:", # white square
            "\u26a0\ufe0f": ":warning:",      # warning
            "\U0001f4b0": ":moneybag:",       # money bag
            "\U0001f916": ":robot_face:",     # robot
            "\U0001f914": ":thinking_face:",  # thinking
            "\u23f0": ":alarm_clock:",        # alarm clock
            "\U0001f9ea": ":test_tube:",      # test tube
        }
        result = text
        for emoji, slack_code in emoji_map.items():
            result = result.replace(emoji, slack_code)
        return result

    def poll_once(self):
        """Fetch new messages from Slack, classify, dispatch, reply.

        Uses conversations.history to get recent messages since last checkpoint.
        """
        if not self.token or not self.channel:
            return

        # Lazy import to avoid circular deps at module level
        _USE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in ("true", "1", "yes")
        if _USE_SWARM:
            from jobpulse.swarm_dispatcher import dispatch
        else:
            from jobpulse.dispatcher import dispatch

        last_ts = _get_last_ts()
        params = {"channel": self.channel, "limit": 20}
        if last_ts:
            params["oldest"] = last_ts

        try:
            resp = httpx.get(
                f"{SLACK_API}/conversations.history",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
                timeout=15,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.error("Slack history error: %s", data.get("error", data))
                return
        except Exception as e:
            logger.error("Slack poll failed: %s", e)
            return

        messages = data.get("messages", [])
        if not messages:
            return

        # Messages come newest-first; reverse to process chronologically
        messages.sort(key=lambda m: float(m.get("ts", "0")))

        max_ts = last_ts or "0"

        for msg in messages:
            ts = msg.get("ts", "0")
            if last_ts and float(ts) <= float(last_ts):
                continue

            max_ts = max(max_ts, ts, key=float)

            # Skip bot messages and messages without text
            if msg.get("bot_id") or msg.get("subtype"):
                continue
            text = msg.get("text", "").strip()
            if not text:
                continue

            # Skip greetings
            if text.lower() in ("hi", "hello", "hey"):
                continue

            logger.info("Slack msg: %s", text[:80])

            cmd = classify(text)
            reply = dispatch(cmd)
            self.send_message(reply)

        if float(max_ts) > float(last_ts or "0"):
            _save_last_ts(max_ts)

    def poll_continuous(self):
        """Long-running poll loop for Slack (no long-poll API, so we poll every 3s)."""
        from jobpulse.healthcheck import write_heartbeat

        logger.info("Slack listener started (polling mode)")
        consecutive_errors = 0

        while True:
            try:
                self.poll_once()
                consecutive_errors = 0
                write_heartbeat()
                time.sleep(3)  # Slack rate limits: ~1 req/sec tier, 3s is safe
            except KeyboardInterrupt:
                logger.info("Slack listener stopped")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error("Slack error (%d): %s", consecutive_errors, e)
                if consecutive_errors > 5:
                    time.sleep(min(60, consecutive_errors * 5))
                else:
                    time.sleep(2)


def _get_last_ts() -> str:
    """Read last processed Slack message timestamp."""
    try:
        return LAST_TS_FILE.read_text().strip()
    except (FileNotFoundError, ValueError):
        return ""


def _save_last_ts(ts: str):
    """Save last processed Slack message timestamp."""
    LAST_TS_FILE.write_text(ts)


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
