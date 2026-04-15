"""Telegram listener — polls for messages, routes to agents via Enhanced Swarm dispatcher."""

import json
from datetime import datetime
from jobpulse.config import TELEGRAM_CHAT_ID, DATA_DIR, LOGS_DIR, JOBPULSE_SWARM
from jobpulse import telegram_agent
from jobpulse.command_router import classify, Intent
from jobpulse.healthcheck import write_heartbeat
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Use Enhanced Swarm dispatcher if enabled, else flat dispatcher
USE_SWARM = JOBPULSE_SWARM
if USE_SWARM:
    from jobpulse.swarm_dispatcher import dispatch
else:
    from jobpulse.dispatcher import dispatch


LAST_UPDATE_FILE = DATA_DIR / "telegram_last_update_id.txt"


def _get_last_update_id() -> int:
    try:
        return int(LAST_UPDATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _save_last_update_id(uid: int):
    LAST_UPDATE_FILE.write_text(str(uid))


def _log(msg: str):
    logger.info(msg)
    log_file = LOGS_DIR / "telegram-listener.log"
    with open(log_file, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")


def _parse_tasks(text: str) -> list[str]:
    """Parse a message into individual task strings."""
    tasks = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for prefix in ["- ", "• ", "□ ", "* ", "✅ ", "☐ ", "And ", "and "]:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        if len(line) > 2 and line[0].isdigit() and line[1] in ".) ":
            line = line[2:].strip()
        elif len(line) > 3 and line[:2].isdigit() and line[2] in ".) ":
            line = line[3:].strip()
        if line and len(line) > 2:
            tasks.append(line)
    return tasks


def poll_and_process():
    """Poll Telegram for new messages, classify intent, dispatch to agent, reply."""
    last_id = _get_last_update_id()
    _log(f"Checking (after update_id: {last_id})")

    updates = telegram_agent.get_updates(offset=last_id + 1)

    if not updates:
        _log("No new messages")
        return

    max_id = last_id

    for update in updates:
        uid = update.get("update_id", 0)
        max_id = max(max_id, uid)

        msg = update.get("message", {})
        from_id = str(msg.get("from", {}).get("id", ""))
        text = msg.get("text", "").strip()

        # Only process messages from Yash
        if from_id != TELEGRAM_CHAT_ID or not text:
            continue

        _log(f"Got: \"{text[:80]}\"")

        # Check for email classification review reply
        from jobpulse.email_review import process_review_reply
        review_response = process_review_reply(text)
        if review_response:
            telegram_agent.send_message(review_response)
            _log(f"Email review: {review_response[:80]}")
            continue

        # Check for pending approval reply
        from jobpulse.approval import process_reply as check_approval
        approval_response = check_approval(text)
        if approval_response:
            telegram_agent.send_message(approval_response)
            _log(f"Approval: {approval_response[:80]}")
            continue

        # Classify intent
        cmd = classify(text)
        _log(f"Intent: {cmd.intent.value}")

        # Show typing indicator before LLM/agent call
        telegram_agent.send_chat_action()

        # Dispatch to agent and get reply
        reply = dispatch(cmd)

        # Route reply to the correct bot based on intent
        from jobpulse.telegram_bots import send_for_intent
        send_for_intent(cmd.intent.value, reply)
        _log(f"Replied: {reply[:80]}...")

    # Save checkpoint
    if max_id > last_id:
        _save_last_update_id(max_id)
        _log(f"Checkpoint: {max_id}")


def poll_continuous():
    """Long-polling daemon — blocks on Telegram API, instant replies.

    Uses Telegram's long-polling: the API call blocks for up to 30s waiting
    for new messages. When one arrives, it returns immediately. This means
    near-instant response (~1-3s) with minimal CPU/network usage.
    """
    import time
    _log("Daemon started (long-polling mode)")
    logger.info("Daemon started — long-polling Telegram. Ctrl+C to stop.")

    last_id = _get_last_update_id()
    consecutive_errors = 0

    while True:
        try:
            # Long-poll: blocks up to 30s waiting for messages
            updates = telegram_agent.get_updates(offset=last_id + 1, long_poll=True)

            # Write heartbeat every cycle (even empty polls)
            write_heartbeat()

            if not updates:
                consecutive_errors = 0
                continue  # no messages, loop back to long-poll again

            max_id = last_id

            for update in updates:
                uid = update.get("update_id", 0)
                max_id = max(max_id, uid)

                msg = update.get("message", {})
                from_id = str(msg.get("from", {}).get("id", ""))
                text = msg.get("text", "").strip()

                # Only process messages from Yash
                if from_id != TELEGRAM_CHAT_ID:
                    continue

                # Handle voice messages
                voice = msg.get("voice") or msg.get("audio")
                if voice and not text:
                    from jobpulse.voice_handler import transcribe_voice
                    text = transcribe_voice(voice["file_id"])
                    if text:
                        _log(f"Voice transcribed: \"{text[:80]}\"")
                        # Send transcription back so user sees what was understood
                        telegram_agent.send_message(f"\U0001f3a4 Heard: \"{text}\"")
                    else:
                        telegram_agent.send_message("\U0001f3a4 Couldn't understand the voice message. Try again or type your message.")
                        continue

                if not text:
                    continue

                _log(f"Got: \"{text[:80]}\"")

                # Check for email classification review reply
                from jobpulse.email_review import process_review_reply
                review_response = process_review_reply(text)
                if review_response:
                    telegram_agent.send_message(review_response)
                    _log(f"Email review: {review_response[:80]}")
                    continue

                # Check for pending approval reply
                from jobpulse.approval import process_reply as check_approval
                approval_response = check_approval(text)
                if approval_response:
                    telegram_agent.send_message(approval_response)
                    _log(f"Approval: {approval_response[:80]}")
                    continue

                # Classify and dispatch
                cmd = classify(text)
                _log(f"Intent: {cmd.intent.value}")

                # Show typing indicator before LLM/agent call
                telegram_agent.send_chat_action()

                reply = dispatch(cmd)
                # Route reply to the correct bot based on intent
                from jobpulse.telegram_bots import send_for_intent
                send_for_intent(cmd.intent.value, reply)
                _log(f"Replied: {reply[:80]}...")

            # Save checkpoint
            if max_id > last_id:
                last_id = max_id
                _save_last_update_id(max_id)

            consecutive_errors = 0
            write_heartbeat()

        except KeyboardInterrupt:
            _log("Daemon stopped by user")
            logger.info("Stopped.")
            break
        except Exception as e:
            consecutive_errors += 1
            _log(f"Error ({consecutive_errors}): {e}")
            # Back off on repeated errors
            if consecutive_errors > 5:
                time.sleep(min(60, consecutive_errors * 5))
            else:
                time.sleep(2)


if __name__ == "__main__":
    poll_and_process()
