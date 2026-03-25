"""Multi-platform listener — starts listeners for all configured platforms."""

import os
import time
import threading
from shared.logging_config import get_logger
from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from jobpulse.healthcheck import write_heartbeat

logger = get_logger(__name__)


def _run_platform(adapter, poll_fn, name):
    """Run a platform listener in a loop with error recovery."""
    consecutive_errors = 0
    while True:
        try:
            poll_fn()
            consecutive_errors = 0
            write_heartbeat()
        except KeyboardInterrupt:
            logger.info("%s listener stopped", name)
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error("%s error (%d): %s", name, consecutive_errors, e)
            if consecutive_errors > 5:
                time.sleep(min(60, consecutive_errors * 5))
            else:
                time.sleep(2)


def start_all():
    """Start all configured platform listeners in separate threads."""
    threads = []

    # Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        from jobpulse.telegram_listener import poll_continuous
        t = threading.Thread(target=poll_continuous, name="telegram", daemon=True)
        threads.append(("Telegram", t))

    # Slack
    slack_token = os.getenv("SLACK_BOT_TOKEN", "")
    if slack_token:
        from jobpulse.platforms.slack_adapter import SlackAdapter
        adapter = SlackAdapter()

        def slack_loop():
            _run_platform(adapter, adapter.poll_once, "Slack")
        t = threading.Thread(target=slack_loop, name="slack", daemon=True)
        threads.append(("Slack", t))

    # Discord
    discord_token = os.getenv("DISCORD_BOT_TOKEN", "")
    if discord_token:
        from jobpulse.platforms.discord_adapter import DiscordAdapter
        adapter = DiscordAdapter()

        def discord_loop():
            _run_platform(adapter, adapter.poll_once, "Discord")
        t = threading.Thread(target=discord_loop, name="discord", daemon=True)
        threads.append(("Discord", t))

    if not threads:
        logger.error("No platforms configured. Set TELEGRAM_BOT_TOKEN, SLACK_BOT_TOKEN, or DISCORD_BOT_TOKEN.")
        return

    logger.info("Starting %d platform(s): %s", len(threads), ", ".join(n for n, _ in threads))

    for name, t in threads:
        t.start()
        logger.info("%s listener started", name)

    # Block on first thread (telegram is primary)
    try:
        threads[0][1].join()
    except KeyboardInterrupt:
        logger.info("Multi-listener stopped")
