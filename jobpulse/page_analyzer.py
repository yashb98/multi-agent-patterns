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

_REASONER_TYPE_MAP = {
    "job_description": PageType.JOB_DESCRIPTION,
    "application_form": PageType.APPLICATION_FORM,
    "login_form": PageType.LOGIN_FORM,
    "signup_form": PageType.SIGNUP_FORM,
    "email_verification": PageType.EMAIL_VERIFICATION,
    "confirmation": PageType.CONFIRMATION,
    "verification_wall": PageType.VERIFICATION_WALL,
    "consent_gate": PageType.CONSENT_GATE,
    "session_expired": PageType.SESSION_EXPIRED,
    "site_prompt": PageType.JOB_DESCRIPTION,
}


def _map_reasoner_type(page_type_str: str) -> PageType | None:
    return _REASONER_TYPE_MAP.get(page_type_str)


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

    vision_model = "gpt-4o-mini"
    if is_local_llm():
        from openai import OpenAI as _OpenAI
        client = _OpenAI()
    else:
        client = get_openai_client()
        vision_model = get_model_name("gpt-4o-mini")
    b64 = base64.b64encode(screenshot_bytes).decode()

    try:
        response = client.chat.completions.create(
            model=vision_model,
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
            **_token_limit_kwargs(vision_model, 100),
            temperature=0,
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="page_analyzer", model_hint=vision_model)
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
        """Detect page type: DOM → semantic reasoning → vision (ascending cost)."""
        page_type, confidence = _dom_detect(snapshot)

        # Stability wait for APPLICATION_FORM/UNKNOWN when platform data predicts more fields
        if self.form_experience is not None and page_type in (PageType.APPLICATION_FORM, PageType.UNKNOWN):
            url = snapshot.get("url", "")
            snapshot = await self._stability_wait(snapshot, url)
            page_type, confidence = _dom_detect(snapshot)

        if confidence >= _VISION_THRESHOLD:
            logger.debug("DOM detection: %s (confidence=%.2f)", page_type, confidence)
            return page_type

        # Mid confidence — try semantic reasoning (~$0.001, text-only, cached)
        logger.info(
            "DOM detection low confidence (%.2f for %s) — trying semantic reasoning",
            confidence, page_type,
        )
        try:
            from jobpulse.page_analysis.page_reasoner import get_page_reasoner
            reasoner = get_page_reasoner()
            action = await reasoner.reason(snapshot)
            if action.confidence > confidence and action.page_type != "unknown":
                semantic_type = _map_reasoner_type(action.page_type)
                if semantic_type is not None:
                    logger.info(
                        "Semantic reasoning: %s (confidence=%.2f) — %s",
                        semantic_type, action.confidence, action.page_understanding[:80],
                    )
                    return semantic_type
        except Exception as exc:
            logger.debug("Semantic reasoning fallback failed: %s", exc)

        # Low confidence — try vision (expensive, screenshot-based)
        # Cached per (domain, content_hash) for 1 hour. The screenshot
        # itself is not in the key — pixel-level diffs (cursor,
        # animations, scroll position) defeat plain image hashing.
        # Instead we hash the stable DOM features (field count, button
        # labels, page text head) that determine page-type classification.
        try:
            screenshot_bytes = await self.bridge.screenshot()
            if not screenshot_bytes:
                logger.warning("Vision fallback skipped: screenshot() returned empty/None")
            else:
                cache_domain, cache_content = _vision_cache_key_for(snapshot)
                cached = _vision_classification_cache_lookup(
                    cache_domain, cache_content,
                )
                if cached is not None:
                    cached_type, cached_conf = cached
                    if cached_conf > confidence:
                        logger.info(
                            "Vision cache hit: %s (confidence=%.2f, domain=%s)",
                            cached_type, cached_conf, cache_domain,
                        )
                        return cached_type
                logger.debug("Vision fallback: screenshot %d bytes", len(screenshot_bytes))
                vision_type, vision_confidence = await _vision_detect(screenshot_bytes)
                try:
                    _vision_classification_cache_store(
                        cache_domain, cache_content, vision_type, vision_confidence,
                    )
                except Exception as exc:
                    logger.debug("vision cache store failed: %s", exc)
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


# ---------------------------------------------------------------------------
# vision_classification_cache (Item 11) — 1-hour per-(domain, content) cache
# ---------------------------------------------------------------------------
#
# The vision LLM call costs ~$0.001 per screenshot. Repeat visits to
# the same page (apply-loop retries, navigation step replay, multiple
# applications to the same domain) hit the same classification, so a
# (domain, content_hash) cache with the same TTL as PageReasoner (1h)
# avoids the cost.
#
# Key detail: the cache key is NOT the screenshot bytes. Pixel-level
# differences (cursor, animations, scroll position, even font hinting
# changes between renders) defeat plain image hashing. We hash the
# stable DOM features the LLM is actually classifying — number of
# fields, top button labels, leading page text — same content_hash
# strategy `PageReasoner._cache_key` uses.

import hashlib as _vc_hashlib  # noqa: E402
import threading as _vc_threading  # noqa: E402
from datetime import datetime as _vc_datetime, timedelta as _vc_timedelta  # noqa: E402
from urllib.parse import urlparse as _vc_urlparse  # noqa: E402

_VISION_CACHE_TTL_SECONDS = 3600
_VISION_CACHE_LOCK = _vc_threading.Lock()


def _vision_cache_key_for(snapshot: dict) -> tuple[str, str]:
    """Derive (domain, content_hash) from the snapshot dict.

    Domain is the URL host (sans port). Content hash is a stable digest
    of the page's classification-relevant features. We deliberately
    exclude live state like cursor position, scroll Y, mouse-move
    handlers — anything that bumps without the page actually changing.
    """

    url = str(snapshot.get("url", "") or "")
    try:
        domain = _vc_urlparse(url).hostname or "unknown"
    except Exception:
        domain = "unknown"

    parts = [
        url,
        str(snapshot.get("title", ""))[:160],
        str(snapshot.get("page_text", ""))[:500],
        str(len(snapshot.get("fields", []) or [])),
        ",".join(sorted(
            str((b or {}).get("label", ""))[:30]
            for b in (snapshot.get("buttons", []) or [])
        ))[:300],
        str(snapshot.get("path", ""))[:160],
    ]
    h = _vc_hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return domain, h


def _vision_cache_init(db) -> None:
    conn = db._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vision_classification_cache ("
        "domain TEXT NOT NULL, content_hash TEXT NOT NULL, "
        "page_type TEXT NOT NULL, confidence REAL NOT NULL, "
        "generated_at TEXT NOT NULL, "
        "hit_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (domain, content_hash))"
    )
    conn.commit()


def _vision_classification_cache_lookup(
    domain: str, content_hash: str, *, db=None,
) -> tuple[PageType, float] | None:
    """Return ``(page_type, confidence)`` or ``None`` on miss / TTL expiry."""

    import os as _os
    if not (domain and content_hash):
        return None
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return None
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _VISION_CACHE_LOCK:
        _vision_cache_init(db)
        conn = db._connect()
        row = conn.execute(
            "SELECT page_type, confidence, generated_at FROM vision_classification_cache "
            "WHERE domain = ? AND content_hash = ?",
            (domain, content_hash),
        ).fetchone()
        if not row:
            return None
        try:
            generated = _vc_datetime.fromisoformat(row["generated_at"])
            if (_vc_datetime.now() - generated).total_seconds() > _VISION_CACHE_TTL_SECONDS:
                return None
        except (ValueError, TypeError):
            return None
        try:
            page_type = PageType(row["page_type"])
        except ValueError:
            page_type = PageType.UNKNOWN
        conn.execute(
            "UPDATE vision_classification_cache SET hit_count = hit_count + 1 "
            "WHERE domain = ? AND content_hash = ?",
            (domain, content_hash),
        )
        conn.commit()
        return page_type, float(row["confidence"])


def _vision_classification_cache_store(
    domain: str, content_hash: str, page_type: PageType, confidence: float,
    *, db=None,
) -> None:
    import os as _os
    if not (domain and content_hash):
        return
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return
    # Don't cache low-confidence guesses — they shouldn't fire if the
    # next visit's classifier could resolve more confidently.
    if confidence < 0.5:
        return
    from jobpulse.job_db import JobDB
    db = db or JobDB()
    with _VISION_CACHE_LOCK:
        _vision_cache_init(db)
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO vision_classification_cache "
            "(domain, content_hash, page_type, confidence, generated_at, hit_count) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (
                domain, content_hash,
                page_type.value if isinstance(page_type, PageType) else str(page_type),
                float(confidence),
                _vc_datetime.now().isoformat(),
            ),
        )
        conn.commit()
