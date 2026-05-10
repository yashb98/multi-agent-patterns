"""Vision tier — screenshot analysis via GPT-4o-mini for stuck form fields.

Used as Tier 5 when pattern match, semantic cache, Gemini Nano, and LLM
all fail to produce a confident answer. Typically triggered ~5% of applications.
"""

from __future__ import annotations

import base64

from shared.agents import get_openai_client
from shared.logging_config import get_logger

from jobpulse.config import OPENAI_API_KEY

logger = get_logger(__name__)


def _build_vision_prompt(question: str, input_type: str) -> str:
    """Build the vision analysis prompt."""
    try:
        from shared.profile_store import get_profile_store
        ps = get_profile_store()
        ident = ps.identity()
        parts = [ident.full_name, ident.education, f"based in {ident.location}"]
        visa = ps.sensitive("visa_type")
        if visa:
            parts.append(f"with {visa}")
        bio = ", ".join(parts)
    except Exception:
        bio = "the applicant"
    return (
        "You are filling out a job application form. "
        f'The current field asks: "{question}" (input type: {input_type}). '
        "Look at the screenshot of the form and determine the best answer. "
        f"The applicant is {bio}. "
        "Return ONLY the answer value — no explanation, no quotes, no formatting."
    )


async def analyze_field_screenshot(
    question: str,
    screenshot_png: bytes,
    input_type: str,
) -> str | None:
    """Send a screenshot to GPT-4o-mini and extract the answer.

    Args:
        question: The field label/question text.
        screenshot_png: Raw PNG bytes of the page screenshot.
        input_type: HTML input type (text, select, radio, etc.).

    Returns:
        The answer string, or None if analysis fails.
    """
    if not OPENAI_API_KEY:
        logger.debug("Vision tier skipped — no OPENAI_API_KEY")
        return None

    try:
        b64_image = base64.b64encode(screenshot_png).decode("ascii")
        client = get_openai_client()

        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _build_vision_prompt(question, input_type)},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{b64_image}",
                    },
                ],
            }],
        )

        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="vision_tier", model_hint="gpt-4.1-mini")
        except Exception:
            pass

        answer = response.output_text.strip()
        logger.debug("Vision tier answer for '%s': '%s'", question[:60], answer[:80])
        return answer if answer else None

    except Exception as exc:
        logger.warning("Vision tier failed: %s", exc)
        return None


_PAGE_TYPE_PROMPT = (
    "Look at this screenshot of a web page. Classify the page into ONE of these types:\n"
    "  job_description — a job listing with description and Apply button\n"
    "  application_form — a form to fill in personal/application details\n"
    "  login_form — a login page with email + password\n"
    "  signup_form — an account creation page\n"
    "  email_verification — a page asking to check email\n"
    "  confirmation — application submitted successfully\n"
    "  verification_wall — CAPTCHA / Cloudflare / hCaptcha challenge\n"
    "  consent_gate — cookie banner / privacy consent page blocking access\n"
    "  session_expired — session-expired or login-required notice\n"
    "  expired_job — job no longer available / closed / filled\n"
    "  unknown — anything else\n\n"
    "Return ONLY the page type string, nothing else."
)


_VALID_PAGE_TYPES = {
    "job_description", "application_form", "login_form", "signup_form",
    "email_verification", "confirmation", "verification_wall", "consent_gate",
    "session_expired", "expired_job", "unknown",
}


async def classify_page_type_from_screenshot(
    screenshot_png: bytes,
    *,
    domain: str | None = None,
    content_hash: str | None = None,
) -> str | None:
    """Classify the page type from a rendered screenshot via gpt-4.1-mini.

    Used by FormNavigator as a tiebreaker when DOM-based PageReasoner
    confidence is low. Returns None if the API key is missing or call fails.

    Optional ``domain`` + ``content_hash`` enable a 1-hour cache (Item 12)
    so repeat visits to the same page skip the LLM call. Pass None / None
    (the legacy signature) and the cache is bypassed — pixel hashing of
    the screenshot would defeat itself, so we don't cache without a stable
    DOM-derived key.
    """

    if domain and content_hash:
        try:
            from jobpulse.page_analyzer import (
                _vision_classification_cache_lookup,
            )
            cached = _vision_classification_cache_lookup(domain, content_hash)
            if cached is not None:
                page_type, _confidence = cached
                logger.info(
                    "vision_tier cache hit: %s (domain=%s)",
                    page_type, domain,
                )
                return (
                    page_type.value if hasattr(page_type, "value")
                    else str(page_type)
                )
        except Exception as exc:
            logger.debug("vision_tier cache lookup failed: %s", exc)

    if not OPENAI_API_KEY:
        logger.debug("vision page-type classifier skipped — no OPENAI_API_KEY")
        return None
    try:
        b64_image = base64.b64encode(screenshot_png).decode("ascii")
        client = get_openai_client()
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _PAGE_TYPE_PROMPT},
                    {"type": "input_image",
                     "image_url": f"data:image/png;base64,{b64_image}"},
                ],
            }],
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="vision_tier_pagetype",
                                model_hint="gpt-4.1-mini")
        except Exception:
            pass
        raw = (response.output_text or "").strip().lower().split()
        if not raw:
            return "unknown"
        page_type = raw[0].strip(".,'\" ")
        if page_type not in _VALID_PAGE_TYPES:
            page_type = "unknown"

        if domain and content_hash and page_type != "unknown":
            try:
                from jobpulse.form_models import PageType
                from jobpulse.page_analyzer import (
                    _vision_classification_cache_store,
                )
                try:
                    pt_enum = PageType(page_type)
                except ValueError:
                    pt_enum = PageType.UNKNOWN
                _vision_classification_cache_store(
                    domain, content_hash, pt_enum, 0.85,
                )
            except Exception as exc:
                logger.debug("vision_tier cache store failed: %s", exc)
        return page_type
    except Exception as exc:
        logger.warning("vision page-type classifier failed: %s", exc)
        return None
