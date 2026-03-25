"""Webhook server — replaces long-polling with push-based Telegram updates."""

import os
from fastapi import FastAPI, Request
from shared.logging_config import get_logger
from jobpulse.command_router import classify
from jobpulse.config import TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN

logger = get_logger(__name__)

USE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in ("true", "1", "yes")
if USE_SWARM:
    from jobpulse.swarm_dispatcher import dispatch
else:
    from jobpulse.dispatcher import dispatch

app = FastAPI(title="JobPulse Webhook")


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram webhook updates."""
    data = await request.json()

    msg = data.get("message", {})
    from_id = str(msg.get("from", {}).get("id", ""))
    text = msg.get("text", "").strip()

    if from_id != TELEGRAM_CHAT_ID or not text:
        return {"ok": True}
    if text.lower() in ("hi", "hello", "hey"):
        return {"ok": True}

    logger.info("Webhook got: %s", text[:80])

    cmd = classify(text)
    reply = dispatch(cmd)

    from jobpulse.platforms.telegram_adapter import TelegramAdapter
    adapter = TelegramAdapter()
    adapter.send_message(reply)

    return {"ok": True}


def register_webhook(url: str):
    """Register webhook URL with Telegram API."""
    import httpx
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
        json={"url": f"{url}/webhook/telegram"},
        timeout=15,
    )
    logger.info("Webhook registered: %s", resp.json())
    return resp.json()


def delete_webhook():
    """Remove webhook (switch back to polling)."""
    import httpx
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook",
        timeout=15,
    )
    logger.info("Webhook deleted: %s", resp.json())
    return resp.json()
