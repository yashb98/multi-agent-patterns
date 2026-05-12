"""Vision-augment gate for sparse-field scans on confident form pages.

When the reasoner is confident a page is an application_form (>=0.7) but the
DOM scanner returns suspiciously few fields, force the vision LLM to find
what the shape-based scanners missed. The predicate is pure and free; the
actual augment call (vision_augment_scan) is in the same module so callers
have a single import surface.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Threshold below which we treat a scan as suspicious. Tuned to the observed
# range (trivial CV-only pages 1-3, sparse screening 6-10, healthy 12-30).
SPARSE_FIELD_THRESHOLD = 10

# Confidence floor — below this, the existing vision_gate already runs vision.
HIGH_CONFIDENCE_FLOOR = 0.7


def should_force_vision(
    scanner_field_count: int,
    page_type: str,
    reasoner_confidence: float,
) -> bool:
    """True when the scanner result looks too sparse for a confident form."""
    if page_type != "application_form":
        return False
    if reasoner_confidence < HIGH_CONFIDENCE_FLOOR:
        return False
    return scanner_field_count <= SPARSE_FIELD_THRESHOLD


# Content-hash → list[field]. Bounded LRU not needed in practice — the
# orchestrator clears _CACHE on host change.
_CACHE: dict[str, list[dict]] = {}


_VISION_PROMPT_TEMPLATE = """\
You are auditing a job-application form. Below is the list of fields the
DOM scanner already found. Look at the page screenshot and identify any
visible question or input that is NOT in this list. Return JSON with
key `missing_fields`, each item having `label` (the visible question
text), `type` (one of: text, textarea, select, multiselect, switch,
checkbox, file), and `options` if it's a select/multiselect.

Page URL: {url}
Page title: {title}

DOM-scanner already found these fields ({n_existing}):
{existing_summary}

Respond with JSON only. If the scanner caught everything, return
{{"missing_fields": []}}.
"""


def _content_hash(url: str, screenshot_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update((url or "").encode("utf-8"))
    h.update(screenshot_bytes[:200_000])
    return h.hexdigest()[:24]


def _summarize_existing(fields: list[dict]) -> str:
    if not fields:
        return "(none)"
    lines = []
    for f in fields[:40]:
        lines.append(f"  - {f.get('label', '')[:80]} [{f.get('type', '')}]")
    return "\n".join(lines)


async def _call_vision_llm(
    screenshot_b64: str, prompt: str
) -> dict[str, Any]:
    """Call OpenAI vision via the existing responses.create pattern.

    Mirrors `jobpulse.vision_tier.analyze_field_screenshot`: uses
    `get_openai_client()` + `client.responses.create(...)` with the
    input_text/input_image content blocks. Cost is recorded via
    `record_openai_usage` like the other vision callers.
    """
    from shared.agents import get_openai_client
    from jobpulse.config import OPENAI_API_KEY

    if not OPENAI_API_KEY:
        return {}

    client = get_openai_client()
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{screenshot_b64}",
                },
            ],
        }],
    )
    try:
        from shared.cost_tracker import record_openai_usage
        record_openai_usage(response, agent_name="vision_augment_scan",
                            model_hint="gpt-4.1-mini")
    except Exception:
        pass

    text = (response.output_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}


async def vision_augment_scan(
    page: Any, existing_fields: list[dict]
) -> list[dict]:
    """Take a page screenshot, ask the vision LLM what fields the DOM
    scanner missed, return them tagged `vision_only=True`.

    Cached per (url, screenshot-hash). Returns [] on any failure so the
    caller can transparently fall through to the existing flow.
    """
    try:
        screenshot = await page.screenshot()
        url = page.url or ""
        title = await page.title()
    except Exception as exc:
        logger.warning("vision_augment_scan: screenshot failed: %s", exc)
        return []

    cache_key = _content_hash(url, screenshot)
    if cache_key in _CACHE:
        return list(_CACHE[cache_key])

    prompt = _VISION_PROMPT_TEMPLATE.format(
        url=url[:200],
        title=title[:200],
        n_existing=len(existing_fields),
        existing_summary=_summarize_existing(existing_fields),
    )
    screenshot_b64 = base64.b64encode(screenshot).decode("ascii")

    try:
        parsed = await _call_vision_llm(screenshot_b64, prompt)
    except Exception as exc:
        logger.warning("vision_augment_scan: LLM call failed: %s", exc)
        _CACHE[cache_key] = []
        return []

    raw = parsed.get("missing_fields") if isinstance(parsed, dict) else None
    if raw is None:
        raw = []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        if not label:
            continue
        out.append({
            "label": label[:300],
            "type": (item.get("type") or "text").lower(),
            "options": item.get("options") or None,
            "vision_only": True,
            "value": "",
        })

    _CACHE[cache_key] = out
    logger.info(
        "vision_augment_scan: %d fields recovered for %s (cache=%s)",
        len(out), url[:80], cache_key,
    )
    return out
