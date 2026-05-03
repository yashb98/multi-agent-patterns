"""Intent-based locator self-healing for DOM rotation.

When a stored CSS/XPath selector returns 0 elements, this module re-resolves
the field's *semantic intent* against the live a11y tree. Closes the
DOM-rotation gap: sites that regenerate IDs/class names mid-session no
longer break stored selectors permanently.

Architecture (per 2026 self-healing research — Mabl/Momentic intent-based
healing, 75–90% heal rate vs 40–70% for rule-based fallback):

1. Each locator stores an INTENT (label + role + neighborhood hints).
2. On lookup, try the stored selector first — if found, done.
3. If 0 elements, try Playwright's role-based fallback:
   page.get_by_role(role, name=label) — Playwright's accessibility-tree
   targeting handles most React-Select/dynamic-class re-renders.
4. If still 0, ask an LLM to resolve the intent against the live a11y tree.
   Cache the new selector if found.

This is platform-agnostic. Works on any page with an accessible DOM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FieldIntent:
    """Semantic description of a field — stable across DOM mutations."""
    label: str
    role: str = "textbox"  # ARIA role: textbox, combobox, button, checkbox, radio
    neighborhood: str = ""  # nearby text for disambiguation (e.g. "section: Personal info")
    field_type: str = "text"  # text/email/phone/select/file/checkbox

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label, "role": self.role,
            "neighborhood": self.neighborhood, "field_type": self.field_type,
        }


_HEAL_PROMPT = (
    "You are healing a stale browser locator.\n\n"
    "A stored CSS/XPath selector returned 0 elements after a DOM re-render. "
    "Given the field's semantic intent and a snapshot of the live a11y tree, "
    "return a fresh CSS selector that targets the same field.\n\n"
    "Return ONLY a JSON object with a single key: {\"selector\": \"<css>\"}\n"
    "If you cannot identify the field, return: {\"selector\": null}\n\n"
    "Intent:\n"
    "  label: {label}\n"
    "  role: {role}\n"
    "  field_type: {field_type}\n"
    "  neighborhood: {neighborhood}\n\n"
    "Live a11y fields (label | role | input_type | id):\n"
    "{a11y_summary}\n"
)


def _build_a11y_summary(snapshot_fields: list[dict], limit: int = 30) -> str:
    """Serialize the live a11y tree's field list into a compact summary."""
    if not snapshot_fields:
        return "(no fields scanned)"
    lines = []
    for f in snapshot_fields[:limit]:
        label = (f.get("label") or "").strip()[:60]
        role = (f.get("role") or f.get("input_type") or "?")[:20]
        input_type = (f.get("input_type") or f.get("type") or "?")[:20]
        elem_id = (f.get("id") or f.get("element_id") or "")[:40]
        lines.append(f"  - {label!r} | {role} | {input_type} | id={elem_id}")
    return "\n".join(lines)


def _call_llm_for_selector(intent: FieldIntent, snapshot_fields: list[dict]) -> str | None:
    """Call LLM to resolve intent → CSS selector. Returns None on failure."""
    try:
        from shared.agents import get_llm, smart_llm_call
        from langchain_core.messages import HumanMessage

        prompt = _HEAL_PROMPT.format(
            label=intent.label[:80],
            role=intent.role,
            field_type=intent.field_type,
            neighborhood=intent.neighborhood[:200],
            a11y_summary=_build_a11y_summary(snapshot_fields),
        )
        llm = get_llm(temperature=0, max_tokens=200, agent_name="intent_healing")
        response = smart_llm_call(llm, [HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else str(response)

        # Extract JSON object
        text = text.strip()
        if "{" in text:
            text = text[text.index("{"):text.rindex("}") + 1]
        parsed = json.loads(text)
        selector = parsed.get("selector")
        return selector if isinstance(selector, str) and selector.strip() else None
    except Exception as exc:
        logger.debug("intent_healing: LLM call failed: %s", exc)
        return None


async def heal_locator(
    page: Any,
    *,
    stored_selector: str | None,
    intent: FieldIntent,
    snapshot_fields: list[dict] | None = None,
) -> Any | None:
    """Resolve the field via stored selector → role fallback → LLM intent resolution.

    Returns:
        A Playwright Locator pointing at the resolved element, or None if all
        three resolution paths failed.

    The three paths in order:
        1. stored_selector (free) — Playwright auto-resolves on every call
        2. role-based locator (free) — page.get_by_role(intent.role, name=intent.label)
        3. LLM intent resolution (~$0.001) — only on failure of (1) and (2)
    """
    # Path 1: stored selector — Playwright re-resolves on every call already
    if stored_selector:
        try:
            loc = page.locator(stored_selector)
            count = await loc.count()
            if count > 0:
                return loc
        except Exception as exc:
            logger.debug("intent_healing: stored selector errored: %s", exc)

    # Path 2: role-based fallback — accessibility-tree targeting
    if intent.label:
        try:
            loc = page.get_by_role(intent.role, name=intent.label, exact=False)
            count = await loc.count()
            if count > 0:
                logger.info(
                    "intent_healing: role-based fallback resolved %r as %s",
                    intent.label[:40], intent.role,
                )
                return loc
        except Exception as exc:
            logger.debug("intent_healing: role fallback errored: %s", exc)

        # Try get_by_label too — common for form fields
        try:
            loc = page.get_by_label(intent.label, exact=False)
            count = await loc.count()
            if count > 0:
                logger.info(
                    "intent_healing: label-based fallback resolved %r",
                    intent.label[:40],
                )
                return loc
        except Exception as exc:
            logger.debug("intent_healing: label fallback errored: %s", exc)

    # Path 3: LLM intent resolution against live a11y tree
    if snapshot_fields:
        new_selector = _call_llm_for_selector(intent, snapshot_fields)
        if new_selector:
            try:
                loc = page.locator(new_selector)
                count = await loc.count()
                if count > 0:
                    logger.info(
                        "intent_healing: LLM resolved %r → %s",
                        intent.label[:40], new_selector[:80],
                    )
                    return loc
            except Exception as exc:
                logger.debug("intent_healing: LLM-suggested selector errored: %s", exc)

    logger.debug(
        "intent_healing: all resolution paths failed for %r",
        intent.label[:40],
    )
    return None
