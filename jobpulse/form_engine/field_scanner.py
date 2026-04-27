"""Field scanner — discovers interactive form fields via a11y tree and Playwright locators."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


async def get_accessible_name(locator: Any) -> str:
    """Extract the label a screen reader would announce for this element.

    Excludes aria-hidden children (e.g. required-field asterisks) so
    the returned text matches what Playwright's get_by_label() sees.
    """
    return await locator.evaluate(
        "el => {"
        "  const lbl = el.labels?.[0];"
        "  if (lbl) {"
        "    const clone = lbl.cloneNode(true);"
        "    clone.querySelectorAll('[aria-hidden]').forEach(n => n.remove());"
        "    const t = clone.textContent.trim();"
        "    if (t) return t;"
        "  }"
        "  return el.getAttribute('aria-label') || el.placeholder || '';"
        "}"
    )


async def resolve_form_container(
    page: "Page",
    strategy,
    form_experience_db=None,
) -> str | None:
    """Resolve the CSS selector for the form container.

    Three-tier detection:
    1. Learned — stored selector from FormExperienceDB
    2. Auto-detect — common ancestor of form elements via JS
    3. Strategy hint — optional CSS selector from platform strategy
    """
    from urllib.parse import urlparse

    url = getattr(page, "url", "") or ""
    domain = urlparse(url).netloc.lower().removeprefix("www.") if url else ""

    # Tier 1: Learned selector
    if form_experience_db and domain:
        stored = form_experience_db.get_container(domain)
        if stored:
            try:
                container = page.locator(stored)
                if await container.count():
                    logger.info("Container Tier 1 (learned): %s for %s", stored, domain)
                    return stored
            except Exception:
                pass
            form_experience_db.delete_container(domain)
            logger.info("Container Tier 1: stale selector '%s' deleted for %s", stored, domain)

    # Tier 2: Auto-detect via common ancestor of form elements
    detected = await _detect_form_container(page)
    if detected:
        logger.info("Container Tier 2 (auto-detect): %s for %s", detected, domain)
        return detected

    # Tier 3: Strategy hint
    hint = strategy.form_container_hint()
    if hint:
        try:
            container = page.locator(hint)
            if await container.count():
                logger.info("Container Tier 3 (strategy hint): %s for %s", hint, domain)
                return hint
        except Exception:
            pass

    logger.info("Container resolution: no container found for %s, full-page scan", domain)
    return None


async def _detect_form_container(page: "Page") -> str | None:
    """Auto-detect form container via common ancestor of visible form elements."""
    try:
        selector = await page.evaluate("""() => {
            function selectorFor(el) {
                if (el.id) return '#' + CSS.escape(el.id);
                if (el === document.body) return 'body';
                const tag = el.tagName.toLowerCase();
                const parent = el.parentElement;
                if (!parent) return tag;
                const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
                if (siblings.length === 1) return selectorFor(parent) + ' > ' + tag;
                const idx = siblings.indexOf(el) + 1;
                return selectorFor(parent) + ' > ' + tag + ':nth-of-type(' + idx + ')';
            }

            const formEls = Array.from(document.querySelectorAll(
                'input:not([type="hidden"]), select, textarea, [role="combobox"], [role="textbox"], [role="radio"], [role="checkbox"]'
            )).filter(el => el.offsetParent !== null);
            if (formEls.length < 3) return null;

            let commonAncestor = formEls[0].parentElement;
            for (const el of formEls.slice(1)) {
                while (commonAncestor && !commonAncestor.contains(el)) {
                    commonAncestor = commonAncestor.parentElement;
                }
            }
            if (!commonAncestor || commonAncestor === document.body || commonAncestor === document.documentElement) {
                return null;
            }
            const buttons = commonAncestor.querySelectorAll('button, [role="button"], input[type="submit"]');
            const hasSubmit = Array.from(buttons).some(b => {
                const text = (b.textContent || b.value || b.getAttribute('aria-label') || '').toLowerCase();
                return ['submit', 'apply', 'next', 'continue', 'review', 'save', 'proceed'].some(s => text.includes(s));
            });
            if (!hasSubmit) return null;
            return selectorFor(commonAncestor);
        }""")
        return selector
    except Exception as exc:
        logger.debug("Auto-detect form container failed: %s", exc)
        return None


def validate_field_scan(
    fields: list[dict],
    strategy,
    form_experience: dict | None = None,
) -> dict:
    """Validate a field scan result for obvious problems."""
    from collections import Counter

    expected_min, expected_max = strategy.expected_field_range()

    if form_experience and form_experience.get("field_count"):
        expected_max = int(form_experience["field_count"] * 1.5)

    if len(fields) == 0:
        return {"valid": False, "reason": "zero_fields", "count": 0}

    if len(fields) > expected_max:
        return {"valid": False, "reason": "too_many_fields", "count": len(fields)}

    label_counts = Counter(f.get("label", "") for f in fields)
    max_dup = max(label_counts.values()) if label_counts else 0
    if max_dup > 3:
        return {"valid": False, "reason": "duplicate_labels", "count": max_dup}

    return {"valid": True, "reason": "", "count": len(fields)}


async def scan_fields(
    page: "Page",
    *,
    strategy=None,
    form_experience_db=None,
) -> list[dict]:
    """Scan visible form fields — container-scoped a11y tree first, fallback second."""
    from jobpulse.form_scanner import scan_form

    container_selector = None
    container_node_id = None

    if strategy or form_experience_db:
        from jobpulse.ats_adapters.generic import GenericStrategy
        _strategy = strategy or GenericStrategy()
        container_selector = await resolve_form_container(
            page, _strategy, form_experience_db,
        )

    if container_selector:
        try:
            cdp = await page.context.new_cdp_session(page)
            try:
                dom_result = await cdp.send("DOM.getDocument")
                query_result = await cdp.send(
                    "DOM.querySelector",
                    {"nodeId": dom_result["root"]["nodeId"], "selector": container_selector},
                )
                if query_result.get("nodeId"):
                    describe = await cdp.send(
                        "DOM.describeNode", {"nodeId": query_result["nodeId"]},
                    )
                    container_node_id = str(describe["node"]["backendNodeId"])
            finally:
                await cdp.detach()
        except Exception as exc:
            logger.debug("Container node ID resolution failed: %s", exc)

    scan = await scan_form(page, container_backend_node_id=container_node_id)
    if scan.fields:
        return ax_scan_to_field_dicts(page, scan)

    fields = await scan_fields_locator_fallback(page)
    return fields


def ax_scan_to_field_dicts(page: "Page", scan) -> list[dict]:
    """Convert FormScanResult to the legacy field-dict format."""
    _ROLE_TO_TYPE = {
        "textbox": "text", "combobox": "combobox", "spinbutton": "text",
        "radio": "radio", "radiogroup": "radio", "checkbox": "checkbox",
        "button": "button",
    }
    fields: list[dict] = []
    for ff in scan.fields:
        ftype = _ROLE_TO_TYPE.get(ff.role, ff.role)
        locator = page.get_by_role(ff.role, name=ff.label)
        entry: dict = {
            "label": ff.label,
            "type": ftype,
            "locator": locator,
            "value": ff.value,
            "required": ff.required,
        }
        if ff.role == "checkbox":
            entry["checked"] = ff.value == "checked" or ff.value == "true"
        if ff.options:
            entry["options"] = ff.options
        fields.append(entry)
    return fields


async def scan_fields_locator_fallback(page: "Page") -> list[dict]:
    """Legacy scanner using Playwright role locators (no shadow DOM)."""
    fields: list[dict] = []

    for loc in await page.get_by_role("textbox").all():
        label = await get_accessible_name(loc)
        fields.append({
            "label": label, "type": "text", "locator": loc,
            "value": await loc.input_value(),
            "required": await loc.get_attribute("required") is not None,
        })

    for loc in await page.get_by_role("combobox").all():
        label = await get_accessible_name(loc)
        tag = await loc.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            options = await loc.locator("option").all_text_contents()
            fields.append({
                "label": label, "type": "select", "locator": loc,
                "options": options, "value": await loc.input_value(),
            })
        else:
            fields.append({
                "label": label, "type": "combobox", "locator": loc,
                "value": await loc.input_value(),
            })

    for loc in await page.get_by_role("radiogroup").all():
        label = await get_accessible_name(loc)
        radios = await loc.get_by_role("radio").all()
        option_labels = [await get_accessible_name(r) for r in radios]
        fields.append({
            "label": label, "type": "radio", "options": option_labels,
            "locator": loc,
        })

    for loc in await page.get_by_role("checkbox").all():
        label = await get_accessible_name(loc)
        fields.append({
            "label": label, "type": "checkbox", "locator": loc,
            "checked": await loc.is_checked(),
        })

    for loc in await page.locator("textarea:visible").all():
        label = await get_accessible_name(loc)
        fields.append({
            "label": label, "type": "textarea", "locator": loc,
            "value": await loc.input_value(),
        })

    for loc in await page.locator("input[type='file']").all():
        label = await get_accessible_name(loc)
        fields.append({"label": label, "type": "file", "locator": loc})

    return fields
