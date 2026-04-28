"""Field scanner — multi-strategy form field discovery with per-domain learning.

Three scan strategies run competitively:
  1. a11y_tree — CDP Accessibility tree (pierces shadow DOM, rich metadata)
  2. dom_query — querySelectorAll on standard form elements (hydration-resilient)
  3. playwright_locators — Playwright get_by_role (pierces shadow DOM, clean API)

The scanner picks the strategy that returns the most valid fields, stores the
winner per domain in FormExperienceDB, and uses the preferred strategy first
on subsequent visits.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

STRATEGIES = ("a11y_tree", "dom_query", "playwright_locators")

_HYDRATION_RETRY_MS = 2000
_MAX_HYDRATION_RETRIES = 2


async def get_accessible_name(locator: Any) -> str:
    """Extract the label a screen reader would announce for this element."""
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
    """Auto-detect form container via common ancestor of visible form elements.

    After finding the common ancestor, walks up to the nearest <form> ancestor
    if one exists and contains a submit-like button — prevents picking a narrow
    <div> child when a proper <form> tag wraps the fields.
    """
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

            function hasSubmitButton(el) {
                const buttons = el.querySelectorAll('button, [role="button"], input[type="submit"]');
                return Array.from(buttons).some(b => {
                    const text = (b.textContent || b.value || b.getAttribute('aria-label') || '').toLowerCase();
                    return ['submit', 'apply', 'next', 'continue', 'review', 'save', 'proceed'].some(s => text.includes(s));
                });
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

            // Prefer a <form> ancestor over a narrow <div> child
            if (commonAncestor.tagName !== 'FORM') {
                let walk = commonAncestor.parentElement;
                while (walk && walk !== document.body && walk !== document.documentElement) {
                    if (walk.tagName === 'FORM' && hasSubmitButton(walk)) {
                        commonAncestor = walk;
                        break;
                    }
                    walk = walk.parentElement;
                }
            }

            if (!hasSubmitButton(commonAncestor)) return null;
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


# ---------------------------------------------------------------------------
# Individual scan strategies
# ---------------------------------------------------------------------------


async def _scan_a11y_tree(
    page: "Page", container_node_id: str | None = None,
) -> list[dict]:
    """Strategy 1: CDP Accessibility tree scan."""
    from jobpulse.form_scanner import scan_form

    scan = await scan_form(page, container_backend_node_id=container_node_id)
    if not scan.fields:
        return []
    return ax_scan_to_field_dicts(page, scan)


async def _scan_dom_query(page: "Page") -> list[dict]:
    """Strategy 2: DOM querySelectorAll for standard form elements.

    Resilient to incomplete hydration — finds raw HTML elements even before
    React/Angular frameworks have finished rendering.
    """
    try:
        raw = await page.evaluate("""() => {
            const fields = [];
            const seen = new Set();

            function labelFor(el) {
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                    if (lbl) return lbl.textContent.trim();
                }
                const parent = el.closest('label');
                if (parent) return parent.textContent.trim();
                return el.getAttribute('aria-label') || el.placeholder || el.name || '';
            }

            function fieldType(el) {
                const tag = el.tagName.toLowerCase();
                if (tag === 'select') return 'select';
                if (tag === 'textarea') return 'textarea';
                const type = (el.getAttribute('type') || 'text').toLowerCase();
                if (type === 'file') return 'file';
                if (type === 'checkbox') return 'checkbox';
                if (type === 'radio') return 'radio';
                const role = el.getAttribute('role');
                if (role === 'combobox') return 'combobox';
                return 'text';
            }

            const selector = [
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"])',
                'select', 'textarea',
                '[role="combobox"]', '[role="textbox"]',
                '[role="radiogroup"]', '[role="checkbox"]',
            ].join(', ');

            for (const el of document.querySelectorAll(selector)) {
                if (el.offsetParent === null && !el.closest('[role="radiogroup"]')) continue;
                const key = el.id || el.name || (el.getAttribute('aria-label') || '') + fieldType(el);
                if (seen.has(key) && key) continue;
                if (key) seen.add(key);

                const label = labelFor(el);
                const ft = fieldType(el);
                const entry = {label: label, type: ft, value: el.value || ''};

                if (el.hasAttribute('required') || el.getAttribute('aria-required') === 'true') {
                    entry.required = true;
                }
                if (ft === 'select') {
                    entry.options = Array.from(el.options).map(o => o.textContent.trim());
                }
                if (ft === 'checkbox') {
                    entry.checked = el.checked;
                }
                if (ft === 'radio') {
                    const name = el.name;
                    if (seen.has('radio:' + name)) continue;
                    seen.add('radio:' + name);
                    const radios = document.querySelectorAll('input[name="' + CSS.escape(name) + '"]');
                    entry.options = Array.from(radios).map(r => labelFor(r));
                    entry.label = label || name;
                    entry.name = name;
                    // Walk up to find the question text — skip option labels
                    const optTexts = new Set(entry.options.map(o => o.toLowerCase()));
                    let node = el;
                    for (let depth = 0; node && depth < 6; depth++) {
                        node = node.parentElement;
                        if (!node) break;
                        const qCandidates = node.querySelectorAll(':scope > label, :scope > legend, :scope > h3, :scope > h4, :scope > p, :scope > span[class*="label"]');
                        for (const c of qCandidates) {
                            const qt = c.textContent.trim();
                            if (qt.length > 10 && qt.length < 500 && !optTexts.has(qt.toLowerCase())) {
                                entry.question = qt;
                                break;
                            }
                        }
                        if (entry.question) break;
                    }
                }
                if (el.tagName === 'DIV' || el.tagName === 'SPAN') {
                    const role = el.getAttribute('role');
                    if (role === 'radiogroup') {
                        const radios = el.querySelectorAll('[role="radio"]');
                        entry.options = Array.from(radios).map(r => r.textContent.trim());
                        entry.type = 'radio';
                    }
                }
                fields.push(entry);
            }
            return fields;
        }""")
    except Exception as exc:
        logger.debug("DOM query scan failed: %s", exc)
        return []

    fields: list[dict] = []
    for item in (raw or []):
        label = item.get("label", "")
        ftype = item.get("type", "text")
        if label:
            locator = page.get_by_label(label, exact=False).first
        else:
            locator = None
        entry: dict = {"label": label, "type": ftype, "locator": locator, "value": item.get("value", "")}
        if item.get("required"):
            entry["required"] = True
        if item.get("options"):
            entry["options"] = item["options"]
        if "checked" in item:
            entry["checked"] = item["checked"]
        if item.get("name"):
            entry["name"] = item["name"]
        if item.get("question"):
            entry["label"] = item["question"]
            entry["name"] = item.get("name", "")
        fields.append(entry)
    return fields


async def scan_fields_locator_fallback(page: "Page") -> list[dict]:
    """Strategy 3: Playwright role locators (pierces shadow DOM)."""
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


# ---------------------------------------------------------------------------
# Multi-strategy orchestrator
# ---------------------------------------------------------------------------


def _merge_fields(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Merge secondary fields into primary, adding fields not already present."""
    seen = set()
    for f in primary:
        key = (f.get("label", "").lower().strip(), f.get("type", ""))
        if key[0]:
            seen.add(key)

    merged = list(primary)
    for f in secondary:
        key = (f.get("label", "").lower().strip(), f.get("type", ""))
        if key[0] and key not in seen:
            seen.add(key)
            merged.append(f)
    return merged


def _fillable_count(fields: list[dict]) -> int:
    """Count fields that are actually fillable (exclude buttons)."""
    return sum(1 for f in fields if f.get("type") not in ("button",))


async def _resolve_container_node_id(
    page: "Page", container_selector: str | None,
) -> str | None:
    if not container_selector:
        return None
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
                return str(describe["node"]["backendNodeId"])
        finally:
            await cdp.detach()
    except Exception as exc:
        logger.debug("Container node ID resolution failed: %s", exc)
    return None


async def _run_all_strategies_parallel(
    page: "Page",
    container_node_id: str | None,
    domain: str,
    preferred: str | None,
) -> dict[str, list[dict]]:
    """Run all scan strategies concurrently via asyncio.gather.

    On a known domain with a preferred strategy, runs it alongside the others
    so we still validate the preference without extra latency.  On a new domain,
    all 3 race and the best result wins.  Hydration retries also run in parallel.
    """
    results: dict[str, list[dict]] = {}

    # All 3 strategies run concurrently — zero sequential overhead
    gathered = await asyncio.gather(
        *[_run_strategy(page, s, container_node_id) for s in STRATEGIES],
        return_exceptions=True,
    )
    for strat, fields in zip(STRATEGIES, gathered):
        if isinstance(fields, BaseException):
            logger.debug("Scan strategy %s raised: %s", strat, fields)
            continue
        fc = _fillable_count(fields)
        if fc > 0:
            results[strat] = fields
            tag = " (preferred)" if strat == preferred else ""
            logger.info("Scan strategy %s%s found %d fields for %s", strat, tag, fc, domain)

    # Hydration retry: if all returned 0, wait and retry in parallel
    if not results:
        for retry in range(_MAX_HYDRATION_RETRIES):
            logger.info(
                "All strategies returned 0 fields — hydration retry %d/%d (waiting %dms)",
                retry + 1, _MAX_HYDRATION_RETRIES, _HYDRATION_RETRY_MS,
            )
            await asyncio.sleep(_HYDRATION_RETRY_MS / 1000)
            gathered = await asyncio.gather(
                *[_run_strategy(page, s, container_node_id) for s in STRATEGIES],
                return_exceptions=True,
            )
            for strat, fields in zip(STRATEGIES, gathered):
                if isinstance(fields, BaseException):
                    continue
                fc = _fillable_count(fields)
                if fc > 0:
                    results[strat] = fields
                    logger.info("Scan strategy %s found %d fields after hydration retry", strat, fc)
            if results:
                break

    return results


async def scan_fields(
    page: "Page",
    *,
    strategy=None,
    form_experience_db=None,
    container_selector: str | None = None,
) -> list[dict]:
    """Multi-strategy field scanner with per-domain learning.

    Tries up to 3 scan strategies, picks the one with the most fillable fields,
    merges unique fields from runners-up, and stores the winning strategy for
    future visits.

    When *container_selector* is provided it is reused directly — avoids
    re-resolving the container on every page scan.
    """
    from urllib.parse import urlparse

    url = getattr(page, "url", "") or ""
    domain = urlparse(url).netloc.lower().removeprefix("www.") if isinstance(url, str) and url else ""

    # Resolve container if needed
    if container_selector is None and (strategy or form_experience_db):
        from jobpulse.ats_adapters.generic import GenericStrategy
        _strategy = strategy or GenericStrategy()
        container_selector = await resolve_form_container(
            page, _strategy, form_experience_db,
        )

    container_node_id = await _resolve_container_node_id(page, container_selector)

    # Check for preferred strategy from prior successful scans
    preferred: str | None = None
    if form_experience_db and domain:
        pref = form_experience_db.get_scan_strategy(domain)
        if pref:
            preferred = pref["preferred_strategy"]

    results = await _run_all_strategies_parallel(
        page, container_node_id, domain, preferred,
    )

    if not results:
        logger.warning("All scan strategies returned 0 fields for %s", domain)
        _emit_scan_signal(domain, "failure", winner="none", field_count=0)
        return []

    # Pick the winner: strategy with most fillable fields
    winner = max(results, key=lambda s: _fillable_count(results[s]))
    best_fields = results[winner]

    # Merge unique fields from other strategies
    for strat, fields in results.items():
        if strat != winner:
            best_fields = _merge_fields(best_fields, fields)

    final_count = _fillable_count(best_fields)
    logger.info(
        "Scan winner: %s with %d fields (%d after merge) for %s",
        winner, _fillable_count(results[winner]), final_count, domain,
    )

    # Store winning strategy for future visits
    if form_experience_db and domain:
        try:
            form_experience_db.store_scan_strategy(domain, winner, final_count)
        except Exception as exc:
            logger.debug("Failed to store scan strategy: %s", exc)

    _emit_scan_signal(domain, "success", winner=winner, field_count=final_count)
    return best_fields


async def _run_strategy(
    page: "Page", strategy_name: str, container_node_id: str | None,
) -> list[dict]:
    """Run a single scan strategy, returning [] on failure."""
    try:
        if strategy_name == "a11y_tree":
            return await _scan_a11y_tree(page, container_node_id)
        elif strategy_name == "dom_query":
            return await _scan_dom_query(page)
        elif strategy_name == "playwright_locators":
            return await scan_fields_locator_fallback(page)
    except Exception as exc:
        logger.debug("Scan strategy %s failed: %s", strategy_name, exc)
    return []


def _emit_scan_signal(
    domain: str, outcome: str, winner: str, field_count: int,
) -> None:
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type=outcome,
            source_loop="field_scanner",
            domain=domain,
            agent_name="field_scanner",
            payload={"action": "multi_strategy_scan", "winner": winner, "field_count": field_count},
            session_id=f"scan_{domain}",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
