"""LLM-driven widget recovery — last-resort Playwright fallback.

When a custom widget (date picker, signature pad, custom dropdown, etc.)
fails through all standard filler paths and vision_tier returns nothing,
this module asks an LLM to produce a sequence of Playwright actions given
the widget's HTML snippet and the target value, then executes them.

Architecture mirrors intent_healing.py: LLM called lazily, all exceptions
swallowed with logging, returns a structured result dict.

Return contract (always):
    {
        "status": "success" | "failed" | "skipped",
        "reason": str,
        "actions_executed": int,
    }

Skip conditions (no LLM call made):
    - OPENAI_API_KEY not set in environment
    - html_snippet is empty / None
    - value is empty / None

Action plan schema the LLM is instructed to return:
    [
        {"type": "click",         "selector": "<css>"},
        {"type": "fill",          "selector": "<css>", "value": "<text>"},
        {"type": "press",         "selector": "<css>", "key": "<key>"},
        {"type": "select_option", "selector": "<css>", "value": "<option>"},
    ]
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

# ── Prompt ──

_RECOVERY_PROMPT = (
    "You are an expert at Playwright browser automation.\n\n"
    "A form field could not be filled by standard methods. "
    "Given the widget's HTML and the target value, produce a minimal sequence "
    "of Playwright actions to set the value.\n\n"
    "Field label: {label}\n"
    "Field role: {field_role}\n"
    "Target value: {value}\n\n"
    "Widget HTML (truncated to 2000 chars):\n"
    "{html_snippet}\n\n"
    "Return ONLY a JSON array of action objects. Each action must have a "
    '"type" key, a "selector" key, and optionally a "value" or "key" key.\n'
    "Supported action types:\n"
    '  {{"type": "click",         "selector": "<css>"}}\n'
    '  {{"type": "fill",          "selector": "<css>", "value": "<text>"}}\n'
    '  {{"type": "press",         "selector": "<css>", "key": "<key>"}}\n'
    '  {{"type": "select_option", "selector": "<css>", "value": "<option>"}}\n\n'
    "Return [] if the field cannot be filled.\n"
    "Return ONLY the JSON array — no explanation, no markdown fences."
)


def _call_llm_for_actions(
    label: str,
    value: str,
    html_snippet: str,
    field_role: str,
) -> list[dict[str, str]]:
    """Ask the LLM for a Playwright action plan. Returns [] on any failure.

    Imports are lazy so the module has zero import-time overhead and tests
    can patch this function directly without mocking deep LangChain internals.
    """
    try:
        from shared.agents import get_llm, smart_llm_call  # noqa: PLC0415
        from langchain_core.messages import HumanMessage  # noqa: PLC0415

        prompt = _RECOVERY_PROMPT.format(
            label=label[:80],
            field_role=field_role[:40],
            value=value[:200],
            html_snippet=html_snippet[:2000],
        )
        llm = get_llm(temperature=0, max_tokens=400, agent_name="widget_llm_recovery")
        response = smart_llm_call(llm, [HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()

        # Isolate the JSON array
        if "[" in text:
            text = text[text.index("["):text.rindex("]") + 1]

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            logger.debug("widget_llm_recovery: LLM returned non-list JSON")
            return []
        return parsed

    except Exception as exc:
        logger.debug("widget_llm_recovery: LLM call failed: %s", exc)
        return []


async def _execute_action(page: "Page", action: dict[str, Any]) -> None:
    """Execute a single Playwright action dict.

    Raises on failure so the caller can count partial successes.
    """
    action_type = action.get("type", "")
    selector = action.get("selector", "")

    if not selector:
        raise ValueError(f"Action missing selector: {action!r}")

    if action_type == "click":
        await page.locator(selector).first.click()

    elif action_type == "fill":
        val = action.get("value", "")
        await page.locator(selector).first.fill(str(val))

    elif action_type == "press":
        key = action.get("key", "")
        if not key:
            raise ValueError(f"press action missing key: {action!r}")
        await page.locator(selector).first.press(key)

    elif action_type == "select_option":
        val = action.get("value", "")
        await page.locator(selector).first.select_option(str(val))

    else:
        raise ValueError(f"Unknown action type: {action_type!r}")


async def recover_widget_via_llm(
    *,
    page: "Page",
    label: str,
    value: str,
    html_snippet: str,
    field_role: str = "unknown",
) -> dict[str, Any]:
    """Attempt to fill a custom widget via an LLM-generated action plan.

    Called after widget_detector returns "unknown" and all standard fillers
    have failed. This is the last resort before the field is left unfilled.

    Args:
        page:         Playwright Page object.
        label:        Visible label of the field (for prompt context).
        value:        Target value to set.
        html_snippet: Outer HTML of the widget element (truncated internally).
        field_role:   ARIA role or detected widget type hint.

    Returns:
        {"status": "success"|"failed"|"skipped", "reason": str, "actions_executed": int}
    """
    # ── Skip conditions — no LLM call made ──
    if not os.environ.get("OPENAI_API_KEY"):
        return {
            "status": "skipped",
            "reason": "OPENAI_API_KEY not set",
            "actions_executed": 0,
        }

    if not html_snippet or not html_snippet.strip():
        return {
            "status": "skipped",
            "reason": "html_snippet is empty",
            "actions_executed": 0,
        }

    if not value or not str(value).strip():
        return {
            "status": "skipped",
            "reason": "value is empty",
            "actions_executed": 0,
        }

    # ── Ask LLM for action plan ──
    try:
        actions = _call_llm_for_actions(
            label=label,
            value=str(value),
            html_snippet=html_snippet,
            field_role=field_role,
        )
    except Exception as exc:
        logger.warning("widget_llm_recovery: unexpected error from _call_llm_for_actions: %s", exc)
        return {
            "status": "failed",
            "reason": f"LLM call error: {exc}",
            "actions_executed": 0,
        }

    if not actions:
        return {
            "status": "skipped",
            "reason": "LLM returned empty action plan",
            "actions_executed": 0,
        }

    # ── Execute actions one by one ──
    actions_executed = 0
    for action in actions:
        try:
            await _execute_action(page, action)
            actions_executed += 1
        except Exception as exc:
            logger.warning(
                "widget_llm_recovery: action %d/%d failed for %r: %s",
                actions_executed + 1, len(actions), label[:40], exc,
            )
            return {
                "status": "failed",
                "reason": f"action {actions_executed + 1}/{len(actions)} failed: {exc}",
                "actions_executed": actions_executed,
            }

    logger.info(
        "widget_llm_recovery: successfully executed %d action(s) for %r",
        actions_executed, label[:40],
    )
    return {
        "status": "success",
        "reason": f"executed {actions_executed} action(s)",
        "actions_executed": actions_executed,
    }
