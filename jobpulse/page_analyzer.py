"""Hybrid page type detector — DOM analysis first, vision LLM fallback.

DOM detection is free and instant. When confidence is low (< 0.6) or result
is UNKNOWN, takes a screenshot and asks the vision model to classify the page.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ext_models import PageType

logger = get_logger(__name__)

# Confidence threshold — below this, fall back to vision
_VISION_THRESHOLD = 0.6

# --- Button patterns ---
_APPLY_BUTTONS = re.compile(
    r"^(apply\s*(now|for\s*this)?|submit\s*application|start\s*application|apply\s*for\s*(this\s*)?job)$",
    re.IGNORECASE,
)
_LOGIN_BUTTONS = re.compile(r"^(sign\s*in|log\s*in|login)$", re.IGNORECASE)
_SIGNUP_BUTTONS = re.compile(
    r"^(create\s*account|sign\s*up|register|join\s*now|get\s*started)$", re.IGNORECASE
)

# --- Page text patterns ---
_CONFIRMATION_PATTERNS = re.compile(
    r"(thank\s*you\s*(for\s*)?(applying|your\s*application|submitting)"
    r"|application\s*(received|submitted|sent)"
    r"|we\s*(have\s*)?received\s*your\s*application"
    r"|successfully\s*submitted)",
    re.IGNORECASE,
)
_EMAIL_VERIFY_PATTERNS = re.compile(
    r"(check\s*your\s*email|verify\s*your\s*(email|account)"
    r"|sent\s*(a\s*)?(verification|confirmation)\s*(email|link)"
    r"|click\s*the\s*link\s*(in\s*your\s*email|to\s*verify)"
    r"|confirm\s*your\s*email\s*address)",
    re.IGNORECASE,
)

# --- Field labels that indicate application forms ---
_APPLICATION_LABELS = re.compile(
    r"(first\s*name|last\s*name|phone|resume|cv|cover\s*letter|linkedin|portfolio"
    r"|work\s*experience|education|sponsorship|right\s*to\s*work|salary|notice\s*period"
    r"|why\s*(are\s*you|do\s*you)\s*(interested|applying))",
    re.IGNORECASE,
)


def _dom_detect(snapshot: dict) -> tuple[PageType, float]:
    """Classify page type from DOM snapshot. Returns (PageType, confidence 0.0-1.0)."""
    buttons = snapshot.get("buttons", [])
    fields = snapshot.get("fields", [])
    page_text = snapshot.get("page_text_preview", "")
    verification_wall = snapshot.get("verification_wall")

    button_texts = [b.get("text", "") for b in buttons]
    field_types = [f.get("type", "") for f in fields]
    field_labels = [f.get("label", "") for f in fields]

    # 1. Verification wall (CAPTCHA) — highest priority
    if verification_wall:
        return PageType.VERIFICATION_WALL, 0.95

    # 2. Confirmation page
    if _CONFIRMATION_PATTERNS.search(page_text):
        return PageType.CONFIRMATION, 0.95

    # 3. Email verification page
    if _EMAIL_VERIFY_PATTERNS.search(page_text):
        return PageType.EMAIL_VERIFICATION, 0.9

    # 4. Signup form: confirm password OR signup button + password
    password_count = sum(1 for t in field_types if t == "password")
    has_signup_button = any(_SIGNUP_BUTTONS.search(t) for t in button_texts if t)

    if password_count >= 2:
        return PageType.SIGNUP_FORM, 0.95
    if has_signup_button and password_count >= 1:
        return PageType.SIGNUP_FORM, 0.85

    # 5. Login form: email + password + sign-in, no application fields
    has_login_button = any(_LOGIN_BUTTONS.search(t) for t in button_texts if t)
    has_password = password_count >= 1
    has_email = any(t == "email" for t in field_types) or any(
        "email" in lbl.lower() for lbl in field_labels
    )
    has_application_fields = any(_APPLICATION_LABELS.search(lbl) for lbl in field_labels if lbl)

    if has_login_button and has_password and has_email and not has_application_fields:
        return PageType.LOGIN_FORM, 0.9

    # 6. Job description: Apply button, few form fields
    has_apply_button = any(_APPLY_BUTTONS.search(t) for t in button_texts if t)
    if has_apply_button and len(fields) <= 2 and not has_application_fields:
        return PageType.JOB_DESCRIPTION, 0.85

    # 7. Application form: form fields (contact, resume, screening)
    has_file_input = snapshot.get("has_file_inputs", False)
    if has_application_fields or has_file_input:
        return PageType.APPLICATION_FORM, 0.85
    if len(fields) >= 3:
        return PageType.APPLICATION_FORM, 0.65

    # 8. Unknown — low confidence
    return PageType.UNKNOWN, 0.2


async def _vision_detect(screenshot_bytes: bytes) -> tuple[PageType, float]:
    """Ask vision LLM to classify a page screenshot."""
    import base64
    import json

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("OpenAI not available for vision detection")
        return PageType.UNKNOWN, 0.0

    client = OpenAI()
    b64 = base64.b64encode(screenshot_bytes).decode()

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify web page screenshots for a job application bot. "
                        "Return ONLY a JSON object with 'page_type' and 'confidence' (0.0-1.0).\n"
                        "Page types: job_description, login_form, signup_form, "
                        "email_verification, application_form, confirmation, "
                        "verification_wall, unknown"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": "What type of page is this? Classify it.",
                        },
                    ],
                },
            ],
            max_tokens=100,
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        # Parse JSON from response
        if "{" in text:
            text = text[text.index("{") : text.rindex("}") + 1]
        data = json.loads(text)
        page_type_str = data.get("page_type", "unknown")
        confidence = float(data.get("confidence", 0.5))

        try:
            page_type = PageType(page_type_str)
        except ValueError:
            page_type = PageType.UNKNOWN
            confidence = 0.3

        logger.info("Vision detected: %s (confidence=%.2f)", page_type, confidence)
        return page_type, confidence

    except Exception as exc:
        logger.warning("Vision page detection failed: %s", exc)
        return PageType.UNKNOWN, 0.0


class PageAnalyzer:
    """Hybrid page type detector: DOM first, vision LLM fallback."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    async def detect(self, snapshot: dict) -> PageType:
        """Detect page type. Uses DOM analysis first; falls back to vision if unsure."""
        page_type, confidence = _dom_detect(snapshot)

        if confidence >= _VISION_THRESHOLD:
            logger.debug("DOM detection: %s (confidence=%.2f)", page_type, confidence)
            return page_type

        # Low confidence — try vision
        logger.info(
            "DOM detection low confidence (%.2f for %s) — trying vision",
            confidence,
            page_type,
        )
        try:
            screenshot_bytes = await self.bridge.screenshot()
            if screenshot_bytes:
                vision_type, vision_confidence = await _vision_detect(screenshot_bytes)
                if vision_confidence > confidence:
                    return vision_type
        except Exception as exc:
            logger.warning("Vision fallback failed: %s", exc)

        return page_type
