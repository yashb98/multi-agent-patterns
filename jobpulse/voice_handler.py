"""Voice handler — transcribes voice messages via OpenAI Whisper.

Supports Telegram (file_id) and generic URLs (Discord, Slack, etc.).
"""
import os
import tempfile
import httpx
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY, TELEGRAM_BOT_TOKEN

logger = get_logger(__name__)


def transcribe_voice(file_id: str, bot_token: str = None) -> str:
    """Download voice message from Telegram and transcribe via Whisper.

    Args:
        file_id: Telegram file_id from the voice message
        bot_token: Token of the bot that received the voice (each bot has its own file API)
    Returns:
        Transcribed text, or empty string on failure
    """
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set, cannot transcribe voice")
        return ""

    token = bot_token or TELEGRAM_BOT_TOKEN

    try:
        # Step 1: Get file path from Telegram (must use the receiving bot's token)
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=15,
        )
        file_data = resp.json()
        if not file_data.get("ok"):
            logger.warning("Failed to get file info: %s", file_data)
            return ""

        file_path = file_data["result"]["file_path"]

        # Step 2: Download the voice file
        download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        audio_resp = httpx.get(download_url, timeout=30)

        if audio_resp.status_code != 200:
            logger.warning("Failed to download voice file: %d", audio_resp.status_code)
            return ""

        # Step 3: Save to temp file and transcribe with Whisper
        suffix = ".ogg" if file_path.endswith(".oga") or file_path.endswith(".ogg") else ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_resp.content)
            tmp_path = tmp.name

        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
            text = transcript.text.strip()
            logger.info("Voice transcribed: '%s' (%d chars)", text[:50], len(text))
            return text
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error("Voice transcription failed: %s", e)
        return ""


def transcribe_voice_url(url: str) -> str:
    """Download audio from a URL and transcribe via Whisper.

    Works with Discord attachment URLs, Slack file URLs, or any direct audio link.
    """
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set, cannot transcribe voice")
        return ""

    try:
        audio_resp = httpx.get(url, timeout=30, follow_redirects=True)
        if audio_resp.status_code != 200:
            logger.warning("Failed to download audio from URL: %d", audio_resp.status_code)
            return ""

        # Determine suffix from URL or default to .ogg
        suffix = ".ogg"
        for ext in (".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4"):
            if ext in url.lower():
                suffix = ext
                break

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_resp.content)
            tmp_path = tmp.name

        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
            text = transcript.text.strip()
            logger.info("Voice (URL) transcribed: '%s' (%d chars)", text[:50], len(text))
            return text
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error("Voice URL transcription failed: %s", e)
        return ""
