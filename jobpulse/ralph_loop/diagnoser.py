"""Diagnoser — screenshot + DOM capture and GPT-4.1-mini vision diagnosis.

When an ATS adapter fails, the diagnoser captures the page state
(screenshot + DOM + visible text + console errors) and asks GPT-4.1-mini
to diagnose the issue and suggest a structured fix.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR
from jobpulse.utils.safe_io import safe_openai_call

logger = get_logger(__name__)

# Max sizes for context sent to LLM
_MAX_DOM_CHARS = 15_000
_MAX_VISIBLE_TEXT_CHARS = 3_000
_MAX_CONSOLE_ERRORS = 20


@dataclass
class FailureContext:
    """Everything captured from a failed page interaction."""

    screenshot_path: Path
    dom_snapshot_path: Path
    visible_text: str
    current_url: str
    page_title: str
    console_errors: list[str]
    step_name: str
    error_message: str


# ---------------------------------------------------------------------------
# Step inference from error messages
# ---------------------------------------------------------------------------

_STEP_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("click_apply_button", re.compile(r"easy.?apply|apply.?button|apply.?btn", re.I)),
    ("file_upload", re.compile(r"file|upload|resume|cv|cover.?letter|input\[type=.?file", re.I)),
    ("contact_info", re.compile(r"phone|email|name|first.?name|last.?name|contact", re.I)),
    ("form_navigation", re.compile(r"next|continue|step|page|forward|back", re.I)),
    ("final_submit", re.compile(r"submit|review|confirm|send.?application", re.I)),
    ("modal_interaction", re.compile(r"modal|dialog|popup|overlay|role=.?dialog", re.I)),
    ("dropdown_select", re.compile(r"select|dropdown|option|listbox|combobox", re.I)),
    ("location_typeahead", re.compile(r"location|typeahead|autocomplete|city|address", re.I)),
    ("page_load", re.compile(r"timeout|timed?\s*out|navigation|load|goto|waiting", re.I)),
    ("verification_wall", re.compile(r"captcha|cloudflare|recaptcha|hcaptcha|verify|robot|blocked|403|429", re.I)),
]


def infer_step_from_error(error_message: str, platform: str) -> str:
    """Map an error message to a step name using pattern matching."""
    for step_name, pattern in _STEP_PATTERNS:
        if pattern.search(error_message):
            return step_name
    return "unknown"


# ---------------------------------------------------------------------------
# Page capture
# ---------------------------------------------------------------------------


def capture_failure_context(
    page: Any,
    job_id: str,
    step_name: str,
    error_message: str,
    iteration: int = 1,
    console_errors: list[str] | None = None,
) -> FailureContext:
    """Capture screenshot, DOM, and visible text from the current page state.

    Uses Playwright sync API (matching existing adapter pattern).
    """
    dest_dir = DATA_DIR / "applications" / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Screenshot
    screenshot_path = dest_dir / f"ralph_{step_name}_{iteration}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as exc:
        logger.warning("Failed to capture screenshot: %s", exc)
        screenshot_path = dest_dir / "ralph_fallback.png"
        try:
            page.screenshot(path=str(screenshot_path))
        except Exception:
            pass

    # DOM snapshot (truncated)
    dom_snapshot_path = dest_dir / f"ralph_{step_name}_{iteration}.html"
    try:
        dom_html = page.content()
        dom_snapshot_path.write_text(dom_html[:_MAX_DOM_CHARS], encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to capture DOM: %s", exc)
        dom_snapshot_path.write_text("", encoding="utf-8")

    # Visible text
    try:
        visible_text = page.inner_text("body")[:_MAX_VISIBLE_TEXT_CHARS]
    except Exception:
        visible_text = ""

    # Page metadata
    try:
        current_url = page.url
    except Exception:
        current_url = ""
    try:
        page_title = page.title()
    except Exception:
        page_title = ""

    return FailureContext(
        screenshot_path=screenshot_path,
        dom_snapshot_path=dom_snapshot_path,
        visible_text=visible_text,
        current_url=current_url,
        page_title=page_title,
        console_errors=(console_errors or [])[:_MAX_CONSOLE_ERRORS],
        step_name=step_name,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# GPT-4.1-mini vision diagnosis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a web automation debugging expert. You analyze failed job application \
form interactions on ATS platforms (LinkedIn, Greenhouse, Workday, Indeed, Lever, Reed, etc.).

You receive a screenshot of the current page state, a DOM snippet, and the error that occurred.
Your job is to diagnose what went wrong and suggest a specific fix.

Return ONLY valid JSON with this exact structure:
{
    "diagnosis": "human-readable explanation of what went wrong",
    "fix_type": "selector_override | strategy_switch | interaction_change | wait_adjustment | field_remap",
    "fix_payload": {
        // For selector_override: {"original_selector": "...", "new_selector": "..."}
        // For strategy_switch: {"step": "...", "original_strategy": "...", "new_strategy": "..."}
        // For interaction_change: {"action": "...", "modifier": "scroll_first|force_click|js_click", "wait_ms": 2000}
        // For wait_adjustment: {"step": "...", "wait_for_selector": "...", "timeout_ms": 10000}
        // For field_remap: {"field_label": "...", "profile_key": "..."}
    },
    "confidence": 0.0-1.0
}"""


def diagnose_with_vision(
    context: FailureContext,
    platform: str,
) -> dict | None:
    """Send screenshot + DOM + error to GPT-4.1-mini for diagnosis.

    Returns parsed diagnosis dict or None on failure.
    """
    # Read screenshot as base64
    screenshot_b64 = ""
    if context.screenshot_path.exists():
        try:
            raw = context.screenshot_path.read_bytes()
            screenshot_b64 = base64.b64encode(raw).decode("ascii")
        except Exception as exc:
            logger.warning("Failed to read screenshot for diagnosis: %s", exc)

    # Read DOM snippet
    dom_html = ""
    if context.dom_snapshot_path.exists():
        try:
            dom_html = context.dom_snapshot_path.read_text(encoding="utf-8")[:_MAX_DOM_CHARS]
        except Exception:
            pass

    # Build user message with text + image
    text_content = (
        f"Platform: {platform}\n"
        f"Step that failed: {context.step_name}\n"
        f"Error message: {context.error_message}\n"
        f"Current URL: {context.current_url}\n"
        f"Page title: {context.page_title}\n\n"
        f"DOM snippet (first {len(dom_html)} chars):\n{dom_html}\n\n"
        f"Visible text (first {len(context.visible_text)} chars):\n{context.visible_text}\n\n"
        f"Console errors:\n{chr(10).join(context.console_errors) if context.console_errors else 'None'}"
    )

    # Build multimodal message
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": text_content},
    ]
    if screenshot_b64:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{screenshot_b64}",
                "detail": "high",
            },
        })

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    from shared.agents import get_openai_client
    client = get_openai_client()
    response = safe_openai_call(
        client,
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.3,
        timeout=90.0,
        caller="ralph_diagnoser",
    )

    if not response:
        logger.warning("Vision diagnosis returned None for %s/%s", platform, context.step_name)
        return None

    try:
        # Strip markdown code fences if present
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Vision diagnosis returned invalid JSON: %s", response[:200])
        return None

    # Validate required fields
    if not result.get("fix_type") or not result.get("fix_payload"):
        logger.warning("Vision diagnosis missing fix_type or fix_payload: %s", result)
        return None

    if result["fix_type"] not in {
        "selector_override", "strategy_switch", "interaction_change",
        "wait_adjustment", "field_remap",
    }:
        logger.warning("Vision diagnosis returned unknown fix_type: %s", result["fix_type"])
        return None

    return result


# ---------------------------------------------------------------------------
# Heuristic fallback (no LLM cost)
# ---------------------------------------------------------------------------

_HEURISTIC_MAP: list[tuple[re.Pattern[str], dict]] = [
    # Modal did not open after clicking apply button
    (
        re.compile(r"modal did not open|modal.*not.*open|clicked but modal", re.I),
        {
            "fix_type": "interaction_change",
            "fix_payload": {"action": "click_apply_button", "modifier": "js_click", "wait_ms": 5000,
                            "step": "click_apply_button"},
            "confidence": 0.6,
            "diagnosis": "Easy Apply modal did not open after clicking — retry with JS click and longer wait",
        },
    ),
    # Timeout / page load
    (
        re.compile(r"timeout|timed?\s*out|waiting\s+for", re.I),
        {
            "fix_type": "wait_adjustment",
            "fix_payload": {"step": "page_load", "wait_for_selector": "body", "timeout_ms": 30000},
            "confidence": 0.4,
            "diagnosis": "Page or element load timeout — increasing wait time",
        },
    ),
    # Element not found / selector broken
    (
        re.compile(r"not found|no element|selector|query_selector|null|none", re.I),
        {
            "fix_type": "selector_override",
            "fix_payload": {"original_selector": "unknown", "new_selector": "unknown"},
            "confidence": 0.3,
            "diagnosis": "Element selector not found — needs updated selector (vision diagnosis recommended)",
        },
    ),
    # Element not visible / not interactable
    (
        re.compile(r"not visible|not interactable|obscured|covered|hidden", re.I),
        {
            "fix_type": "interaction_change",
            "fix_payload": {"action": "click", "modifier": "scroll_first", "wait_ms": 2000},
            "confidence": 0.5,
            "diagnosis": "Element not visible or interactable — scroll into view first",
        },
    ),
    # Navigation / redirect
    (
        re.compile(r"navigat|redirect|url\s+changed|page\s+changed", re.I),
        {
            "fix_type": "wait_adjustment",
            "fix_payload": {"step": "navigation", "wait_for_selector": "body", "timeout_ms": 15000},
            "confidence": 0.4,
            "diagnosis": "Unexpected navigation or redirect — wait for page to settle",
        },
    ),
    # File upload issues
    (
        re.compile(r"file|upload|set_input_files|accept", re.I),
        {
            "fix_type": "strategy_switch",
            "fix_payload": {"step": "file_upload", "original_strategy": "set_input_files", "new_strategy": "drag_and_drop"},
            "confidence": 0.4,
            "diagnosis": "File upload failed — try alternative upload strategy",
        },
    ),
]


def heuristic_diagnosis(error_message: str, platform: str) -> dict | None:
    """Pattern-match the error to a heuristic fix. No LLM cost."""
    for pattern, fix in _HEURISTIC_MAP:
        if pattern.search(error_message):
            return dict(fix)
    return None
