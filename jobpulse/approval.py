"""Approval Flow — request yes/no confirmation for destructive operations via Telegram.

Only one pending approval at a time. Auto-expires after timeout.
The Telegram listener checks for approval replies before classifying messages.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

# ── Module-level state (single pending approval) ──

_pending: Optional[dict] = None
_PENDING_FILE: Path = DATA_DIR / "pending_approval.json"


def _clear_pending_file() -> None:
    try:
        _PENDING_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("approval: failed clearing pending file: %s", exc)


def _persist_pending(pending: dict) -> None:
    persistent_context = pending.get("persistent_context")
    if not persistent_context:
        _clear_pending_file()
        return

    payload = {
        "id": pending["id"],
        "question": pending["question"],
        "created_at": pending["created_at"],
        "timeout": pending["timeout"],
        "persistent_context": persistent_context,
    }
    try:
        _PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PENDING_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("approval: failed persisting pending approval: %s", exc)


def _load_persisted_pending() -> Optional[dict]:
    if not _PENDING_FILE.exists():
        return None
    try:
        payload = json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("approval: failed reading persisted approval: %s", exc)
        _clear_pending_file()
        return None

    elapsed = time.time() - float(payload.get("created_at", 0))
    if elapsed > float(payload.get("timeout", 0)):
        logger.info("Approval %s expired after %ds", payload.get("id", "?"), int(elapsed))
        _clear_pending_file()
        return None

    payload["callback"] = None
    return payload


def request_approval(
    question: str,
    timeout_seconds: int = 300,
    callback=None,
    persistent_context: dict | None = None,
) -> str:
    """Send an approval question via Telegram, store as pending.

    Args:
        question: The question to ask (e.g., "Commit with message 'fix bug'?")
        timeout_seconds: Auto-expire after this many seconds (default 5 min)
        callback: Optional callable(approved: bool) to invoke when resolved
        persistent_context: Optional serializable context used to recover after restart

    Returns:
        approval_id string
    """
    global _pending

    from jobpulse import telegram_agent

    approval_id = str(uuid.uuid4())[:8]
    _pending = {
        "id": approval_id,
        "question": question,
        "callback": callback,
        "created_at": time.time(),
        "timeout": timeout_seconds,
        "persistent_context": persistent_context,
    }
    _persist_pending(_pending)

    telegram_agent.send_message(
        f"Approval needed:\n\n{question}\n\nReply yes or no."
    )
    logger.info("Approval requested: %s — %s", approval_id, question[:80])
    return approval_id


def get_pending() -> Optional[dict]:
    """Return the current pending approval, or None if none/expired."""
    global _pending
    if _pending is None:
        _pending = _load_persisted_pending()
        if _pending is None:
            return None

    # Check expiry
    elapsed = time.time() - _pending["created_at"]
    if elapsed > _pending["timeout"]:
        logger.info("Approval %s expired after %ds", _pending["id"], int(elapsed))
        _pending = None
        _clear_pending_file()
        return None

    return _pending


def resolve(approved: bool) -> str:
    """Resolve the pending approval. Returns confirmation message."""
    global _pending
    pending = get_pending()
    if pending is None:
        return "No pending approval."

    approval = pending
    _pending = None
    _clear_pending_file()

    action = "Approved" if approved else "Rejected"
    logger.info("Approval %s: %s", approval["id"], action)

    # Execute callback if provided
    if approval.get("callback"):
        try:
            callback_result = approval["callback"](approved)
            if callback_result:
                return f"{action}. {callback_result}"
        except Exception as e:
            logger.error("Approval callback error: %s", e)
            from shared.agent_result import DispatchError, classify_error
            cat, retry = classify_error(e)
            err = DispatchError(cat, str(e), retry, agent_name="approval",
                                attempted_action="execute callback")
            return f"{action}, but callback failed: {err.message}"

    persistent_context = approval.get("persistent_context")
    if persistent_context:
        try:
            from jobpulse.live_review_applicator import resume_persisted_review_action

            callback_result = resume_persisted_review_action(persistent_context, approved)
            if callback_result:
                return f"{action}. {callback_result}"
        except Exception as e:
            logger.error("Approval persistent callback error: %s", e)
            return f"{action}, but recovery failed: {e}"

    return f"{action}."


def process_reply(text: str) -> Optional[str]:
    """Check if text is an approval reply (yes/no/y/n/approve/reject).

    Returns confirmation message if it was an approval reply, None otherwise.
    Called by telegram_listener BEFORE classify().
    """
    pending = get_pending()
    if pending is None:
        return None

    normalized = text.strip().lower()
    positive = {"yes", "y", "approve", "approved", "ok", "yep", "yeah", "sure"}
    negative = {"no", "n", "reject", "rejected", "nope", "nah", "cancel"}

    if normalized in positive:
        return resolve(approved=True)
    elif normalized in negative:
        return resolve(approved=False)

    # Not an approval reply
    return None
