"""JD metadata extractor — title + company from JD text via LLM.

Audit 2026-05-10 / Slice S6 / TP-11. Replaces hardcoded LinkedIn/Indeed
CSS selectors in `scan_pipeline.process_single_url:1060-1069` that produced
`Unknown Role @ Unknown Company` for every non-LinkedIn ATS (Lever, Ashby,
Greenhouse, SmartRecruiters, iCIMS, Workday, ...).

Per `.claude/rules/jobpulse.md → Dynamic Over Hardcoded`: selectors must be
discovered via DOM/a11y/LLM at runtime, not hardcoded. This module routes
extraction through the existing Kimi-mandated `cognitive_llm_call` wrapper
and caches per `jd_hash` so each unique JD costs one LLM call lifetime.

Output contract:
    {"title": "Software Engineer", "company": "Acme Corp"}

Both fields default to "" on any failure mode (empty input, LLM error,
sentinel response). Caller's downstream `or "Unknown Role"` fallback
chain handles the empty case.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ── Cache (in-memory LRU; bounded so we don't accumulate forever) ──

_CACHE_MAX_ENTRIES = 256
_CACHE: "OrderedDict[str, dict[str, str]]" = OrderedDict()

# ── Sentinel values the LLM occasionally emits when it can't extract ──
# Treated as empty so the caller's fallback chain runs (better than letting
# the sentinel flow into Notion / CV path / DB rows).
_EMPTY_SENTINELS: frozenset[str] = frozenset({
    "unknown", "unknown role", "unknown company",
    "n/a", "na", "not specified", "not applicable",
    "tbd", "tba", "title not found", "company not found",
    "none", "null",
})

_MAX_FIELD_LEN = 200  # cap per field; matches B2 input-truncation policy


def _jd_hash(jd_text: str) -> str:
    """Stable short hash for cache keying. Whitespace-insensitive at the
    boundaries so callers can pass jd_text with or without leading newlines."""
    normalized = (jd_text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _cache_get(key: str) -> dict[str, str] | None:
    if key in _CACHE:
        # Move to end → LRU recency bump
        _CACHE.move_to_end(key)
        return _CACHE[key]
    return None


def _cache_set(key: str, value: dict[str, str]) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


def _validate_extraction(raw: dict[str, Any]) -> dict[str, str]:
    """Sanitize LLM output — strip whitespace, reject sentinels, cap length."""
    def _clean(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        stripped = value.strip()
        if not stripped:
            return ""
        if stripped.lower() in _EMPTY_SENTINELS:
            return ""
        return stripped[:_MAX_FIELD_LEN]

    return {
        "title": _clean(raw.get("title")),
        "company": _clean(raw.get("company")),
    }


def _llm_extract(jd_text: str) -> dict[str, str]:
    """Single LLM call to pull `{title, company}` from JD text.

    Truncates jd_text to 3000 chars. Temperature 0 (via cognitive engine
    defaults). JSON response. Routes through `cognitive_llm_call` so it
    obeys the Kimi mandate + appears in the cost tracker.
    """
    from shared.agents import cognitive_llm_call

    truncated = jd_text[:3000]

    system_prompt = (
        "You are a precise job-description parser. Extract the role title "
        "and the hiring company name from the JD text. Return ONLY a JSON "
        "object: {\"title\": \"<role title>\", \"company\": \"<company name>\"}. "
        "If a field cannot be confidently extracted, return an empty string "
        "for it. Do not invent values. Do not include location, seniority, "
        "or department in the title — title is just the role (e.g. "
        "\"Software Engineer\", \"Forward Deployed AI Engineer\"). The "
        "company is the hiring entity (e.g. \"Anthropic\", \"Palantir "
        "Technologies\", \"OpenAI\"), not a parent group or platform."
    )

    task = f"SYSTEM: {system_prompt}\nUSER: {truncated}"
    fallback_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": truncated},
    ]

    response_text = cognitive_llm_call(
        task=task,
        domain="jd_metadata_extraction",
        stakes="low",
        fallback_messages=fallback_messages,
        response_format={"type": "json_object"},
    )

    if not response_text:
        return {"title": "", "company": ""}

    try:
        parsed = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "jd_metadata_extractor: LLM returned non-JSON response (%d chars) — "
            "treating as empty extraction",
            len(response_text or ""),
        )
        return {"title": "", "company": ""}

    return _validate_extraction(parsed if isinstance(parsed, dict) else {})


def extract_title_company(jd_text: str) -> dict[str, str]:
    """Extract role title + company name from JD text.

    Returns:
        {"title": "...", "company": "..."} — both strings, empty if extraction fails.

    Caching:
        Keyed on `_jd_hash(jd_text)`. Repeat calls with identical text are free.

    Failure modes (all return `{"title": "", "company": ""}`):
        - Empty / whitespace-only `jd_text`
        - LLM call raises any exception
        - LLM returns non-JSON or sentinel values
    """
    if not jd_text or not jd_text.strip():
        return {"title": "", "company": ""}

    key = _jd_hash(jd_text)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        result = _llm_extract(jd_text)
    except Exception as exc:  # noqa: BLE001 — caller relies on graceful empty fallback
        logger.warning("jd_metadata_extractor: LLM extraction failed: %s", exc)
        result = {"title": "", "company": ""}

    _cache_set(key, result)
    return result
