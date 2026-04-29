"""Hybrid page type detector — DOM analysis first, vision LLM fallback.

DOM detection is free and instant. When confidence is low (< 0.6) or result
is UNKNOWN, takes a screenshot and asks the vision model to classify the page.
"""
from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

from jobpulse.form_models import PageType
from jobpulse.page_analysis.classifier import PageTypeClassifier

logger = get_logger(__name__)

# Confidence threshold — below this, fall back to vision
_VISION_THRESHOLD = 0.6

_classifier = PageTypeClassifier()


def _dom_detect(snapshot: dict | Any) -> tuple[PageType, float]:
    """Classify page type from DOM snapshot. Returns (PageType, confidence 0.0-1.0)."""
    return _classifier.classify(snapshot)


async def _vision_detect(screenshot_bytes: bytes) -> tuple[PageType, float]:
    """Ask vision LLM to classify a page screenshot."""
    import base64
    import json

    try:
        from shared.agents import get_openai_client, get_model_name, is_local_llm, _token_limit_kwargs
    except ImportError:
        logger.warning("OpenAI not available for vision detection")
        return PageType.UNKNOWN, 0.0

    client = get_openai_client()
    b64 = base64.b64encode(screenshot_bytes).decode()

    try:
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify web page screenshots for a job application bot. "
                        "Return ONLY a JSON object with 'page_type' and 'confidence' (0.0-1.0).\n"
                        "Page types: job_description, login_form, signup_form, "
                        "email_verification, application_form, confirmation, "
                        "verification_wall, session_expired, consent_gate, unknown"
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
            **_token_limit_kwargs(get_model_name(), 300 if is_local_llm() else 100),
            temperature=0,
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="page_analyzer", model_hint=get_model_name())
        except Exception:
            pass

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

    def __init__(self, bridge: Any, form_experience=None):
        self.bridge = bridge
        self.form_experience = form_experience

    async def detect(self, snapshot: dict) -> PageType:
        """Detect page type. Uses DOM analysis first; falls back to vision if unsure."""
        page_type, confidence = _dom_detect(snapshot)

        # Stability wait for APPLICATION_FORM/UNKNOWN when platform data predicts more fields
        if self.form_experience is not None and page_type in (PageType.APPLICATION_FORM, PageType.UNKNOWN):
            url = snapshot.get("url", "")
            snapshot = await self._stability_wait(snapshot, url)
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
            if not screenshot_bytes:
                logger.warning("Vision fallback skipped: screenshot() returned empty/None")
            else:
                logger.debug("Vision fallback: screenshot %d bytes", len(screenshot_bytes))
                vision_type, vision_confidence = await _vision_detect(screenshot_bytes)
                if vision_confidence > confidence:
                    return vision_type
        except Exception as exc:
            logger.warning("Vision fallback failed: %r", exc, exc_info=True)

        return page_type

    async def _stability_wait(self, snapshot: dict, url: str, max_polls: int = 6, interval: float = 0.5) -> dict:
        """Wait for DOM to stabilize when platform data predicts more fields."""
        import asyncio

        if not url:
            return snapshot

        result = self._get_expected_field_count(url)
        if result is None:
            return snapshot

        expected_count, confidence_ratio = result
        current_count = len(snapshot.get("fields", []))
        if current_count >= expected_count * confidence_ratio:
            return snapshot

        logger.info("DOM stability wait: %d fields, expected %.0f (ratio=%.1f) — polling",
                    current_count, expected_count, confidence_ratio)
        for _ in range(max_polls):
            await asyncio.sleep(interval)
            try:
                fresh = await self.bridge.get_snapshot(force_refresh=True)
                if hasattr(fresh, "model_dump"):
                    fresh = fresh.model_dump()
                new_count = len(fresh.get("fields", []))
                if new_count >= expected_count * confidence_ratio:
                    logger.info("DOM stabilized: %d fields (expected %.0f)", new_count, expected_count)
                    return fresh
                if new_count == current_count:
                    return fresh
                current_count = new_count
                snapshot = fresh
            except Exception:
                break
        return snapshot

    def _get_expected_field_count(self, url: str) -> tuple[float, float] | None:
        """Get expected field count with confidence. Returns (count, confidence_ratio) or None."""
        import json as _json

        per_domain = self.form_experience.lookup(url)
        if per_domain and per_domain.get("success"):
            stored = per_domain.get("field_types", "[]")
            if isinstance(stored, str):
                stored = _json.loads(stored)
            return float(len(stored)), 0.6  # per-domain = high confidence

        platform = self._infer_platform(url)
        if platform:
            agg = self.form_experience.get_platform_aggregate(platform)
            if agg and agg["observation_count"] >= 1:
                obs = agg["observation_count"]
                # Adaptive: 1 obs → 0.3, 2 obs → 0.4, 3+ obs → 0.6
                if obs >= 3:
                    confidence = 0.6
                elif obs == 2:
                    confidence = 0.4
                else:
                    confidence = 0.3
                return agg["avg_field_count"], confidence

        return None

    @staticmethod
    def _infer_platform(url: str) -> str | None:
        url_lower = url.lower()
        for platform, pattern in [
            ("greenhouse", "greenhouse"),
            ("lever", "lever.co"),
            ("workday", "myworkdayjobs"),
            ("smartrecruiters", "smartrecruiters"),
            ("indeed", "indeed.com"),
            ("ashby", "ashbyhq.com"),
            ("icims", "icims.com"),
        ]:
            if pattern in url_lower:
                return platform
        return None
