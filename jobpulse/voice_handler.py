"""Voice handler — transcribes Telegram voice messages via OpenAI Whisper."""
import os
import tempfile
import httpx
from shared.logging_config import get_logger
from jobpulse.config import OPENAI_API_KEY, TELEGRAM_BOT_TOKEN

logger = get_logger(__name__)


def transcribe_voice(file_id: str) -> str:
    """Download voice message from Telegram and transcribe via Whisper.

    Args:
        file_id: Telegram file_id from the voice message
    Returns:
        Transcribed text, or empty string on failure
    """
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set, cannot transcribe voice")
        return ""

    try:
        # Step 1: Get file path from Telegram
        resp = httpx.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=15,
        )
        file_data = resp.json()
        if not file_data.get("ok"):
            logger.warning("Failed to get file info: %s", file_data)
            return ""

        file_path = file_data["result"]["file_path"]

        # Step 2: Download the voice file
        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
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
