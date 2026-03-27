"""Email review flow — user confirms/corrects pre-classifier decisions via Telegram.

Mirrors approval.py pattern: one pending review at a time, checked before classify().
"""

import time
from typing import Optional
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Module-level state (single pending review)
_pending_review: Optional[dict] = None


def request_review(email_id: str, sender: str, subject: str,
                   category: str, confidence: float, rule_name: str) -> str:
    """Flag an email classification for user review via Telegram.

    Returns the review message to send.
    """
    global _pending_review

    _pending_review = {
        "email_id": email_id,
        "sender": sender,
        "subject": subject,
        "category": category,
        "confidence": confidence,
        "rule_name": rule_name,
        "created_at": time.time(),
        "timeout": 3600,  # 1 hour
    }

    msg = (
        f"\U0001f4e7 Classification Review\n"
        f"From: {sender}\n"
        f"Subject: \"{subject}\"\n"
        f"\u2192 Classified: {category} (confidence: {confidence:.0%})\n"
        f"\u2192 Rule: {rule_name}\n\n"
        f"Reply: \u2705 (correct) or \u274c (wrong) or \U0001f504 CATEGORY (reclassify)"
    )

    logger.info("Review requested for email %s: %s → %s", email_id, subject[:50], category)
    return msg


def get_pending() -> Optional[dict]:
    """Return the current pending review, or None if none/expired."""
    global _pending_review
    if _pending_review is None:
        return None

    elapsed = time.time() - _pending_review["created_at"]
    if elapsed > _pending_review["timeout"]:
        logger.info("Review for %s expired after %ds", _pending_review["email_id"], int(elapsed))
        _pending_review = None
        return None

    return _pending_review


def process_review_reply(text: str) -> Optional[str]:
    """Check if text is a review reply (✅/❌/🔄).

    Returns response message if it was a review reply, None otherwise.
    Called by telegram_listener BEFORE classify().
    """
    pending = get_pending()
    if pending is None:
        return None

    global _pending_review
    stripped = text.strip()

    # ✅ Correct — confirm the classification
    if stripped in ("\u2705", "correct", "yes", "right"):
        email_id = pending["email_id"]
        category = pending["category"]
        rule_name = pending["rule_name"]
        _pending_review = None

        _record_user_feedback(email_id, category, is_correct=True, rule_name=rule_name)

        logger.info("User confirmed: %s → %s", email_id, category)
        return f"\u2705 Confirmed: {pending['subject'][:40]} → {category}"

    # ❌ Incorrect — mark rule as wrong
    if stripped in ("\u274c", "wrong", "incorrect", "no"):
        email_id = pending["email_id"]
        category = pending["category"]
        rule_name = pending["rule_name"]
        _pending_review = None

        _record_user_feedback(email_id, category, is_correct=False, rule_name=rule_name)

        logger.warning("User rejected: %s → %s (rule: %s)", email_id, category, rule_name)
        return f"\u274c Incorrect classification noted. Rule '{rule_name}' flagged for review."

    # 🔄 CATEGORY — reclassify
    if stripped.startswith("\U0001f504") or stripped.lower().startswith("reclassify"):
        parts = stripped.split(maxsplit=1)
        if len(parts) >= 2:
            new_category = parts[1].strip().upper()
            valid = {"SELECTED_NEXT_ROUND", "INTERVIEW_SCHEDULING", "REJECTED", "OTHER",
                     "SELECTED", "INTERVIEW"}
            # Normalize short forms
            if new_category == "SELECTED":
                new_category = "SELECTED_NEXT_ROUND"
            elif new_category == "INTERVIEW":
                new_category = "INTERVIEW_SCHEDULING"

            if new_category in valid:
                email_id = pending["email_id"]
                old_category = pending["category"]
                rule_name = pending["rule_name"]
                _pending_review = None

                _record_user_feedback(email_id, old_category, is_correct=False,
                                     rule_name=rule_name, corrected_to=new_category)
                _update_email_category(email_id, new_category)

                logger.info("User reclassified: %s → %s (was %s)", email_id, new_category, old_category)
                return f"\U0001f504 Reclassified: {pending['subject'][:40]} → {new_category} (was {old_category})"

        _pending_review = None
        return "\U0001f504 Usage: \U0001f504 SELECTED or \U0001f504 INTERVIEW or \U0001f504 REJECTED or \U0001f504 OTHER"

    # Not a review reply
    return None


def _record_user_feedback(email_id: str, rule_category: str, is_correct: bool,
                          rule_name: str, corrected_to: str = None):
    """Store user feedback in audit table and update learned rules."""
    try:
        from jobpulse import db
        db.store_audit(
            email_id=email_id,
            rule_category=rule_category,
            rule_confidence=None,
            rule_name=rule_name,
            llm_category=None,
            user_category=corrected_to if not is_correct else rule_category,
            is_correct=1 if is_correct else 0,
        )

        # Update learned rule confidence based on feedback (2x weight)
        from jobpulse.email_preclassifier import _load_learned_rules, _save_learned_rules
        learned = _load_learned_rules()
        for key in ["sender_rules", "subject_rules", "body_rules"]:
            for rule in learned.get(key, []):
                if rule.get("name") == rule_name:
                    if is_correct:
                        rule["user_verified"] = rule.get("user_verified", 0) + 1
                        rule["confidence"] = min(0.95, rule["confidence"] + 0.02)
                    else:
                        rule["user_corrections"] = rule.get("user_corrections", 0) + 1
                        rule["confidence"] = max(0.3, rule["confidence"] - 0.05)
                        if rule.get("user_corrections", 0) >= 3:
                            rule["confidence"] = 0.0
                            logger.warning("Learned rule '%s' disabled after 3 corrections", rule_name)
        _save_learned_rules(learned)

    except Exception as e:
        logger.error("Failed to record user feedback: %s", e)


def _update_email_category(email_id: str, new_category: str):
    """Update a stored email's category after user correction."""
    try:
        from jobpulse import db
        conn = db.get_conn()
        conn.execute("UPDATE processed_emails SET category=? WHERE email_id=?",
                    (new_category, email_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to update email category: %s", e)
