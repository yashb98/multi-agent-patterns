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

STRATEGIES = (
    "learned_patterns", "a11y_tree", "dom_query",
    "playwright_locators", "semantic",
)

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
        if selector:
            return selector

        # Fallback: multi-form pages where common-ancestor walks up to <body>
        # because the page has separate <form>s for page-header search,
        # cookie consent, honeypot, AND the actual apply form. Common ancestor
        # = <body>, function returns null. Confirmed live on pls-solicitors.
        # Strategy: enumerate every <form> that has a submit-like button,
        # pick the one with the most visible input/textarea fields. The apply
        # form has 5+ text inputs vs header search (1) or cookie modal (mostly
        # checkboxes). Fully dynamic — no hardcoded site selectors.
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
                    return ['submit', 'apply', 'next', 'continue', 'review', 'proceed', 'send'].some(s => text.includes(s));
                });
            }
            function countMeaningfulInputs(el) {
                // Text-style inputs are stronger apply-form signal than checkboxes
                // (cookie modals are mostly checkboxes; apply forms are mostly text/email/tel).
                const textInputs = el.querySelectorAll(
                    'input[type="text"], input[type="email"], input[type="tel"], input[type="url"], ' +
                    'input[type="number"], input[type="search"]:not([role="search"]), textarea, ' +
                    'input:not([type]), [role="textbox"], [role="combobox"]'
                );
                let count = 0;
                for (const inp of textInputs) {
                    if (inp.type === 'hidden') continue;
                    if (inp.offsetParent === null) continue;
                    if (inp.disabled || inp.readOnly) continue;
                    // Skip honeypot pattern (name contains "honeypot" or "hp" suffix)
                    const name = (inp.name || inp.id || '').toLowerCase();
                    if (name.includes('honeypot') || name.endsWith('-hp') || name.endsWith('_hp')) continue;
                    count += 1;
                }
                return count;
            }

            const allForms = [...document.querySelectorAll('form')]
                .filter(f => f.offsetParent !== null && hasSubmitButton(f));
            if (allForms.length === 0) return null;

            // Score each form by meaningful input count, pick the top
            let best = null;
            let bestScore = 0;
            for (const f of allForms) {
                const score = countMeaningfulInputs(f);
                if (score > bestScore) {
                    bestScore = score;
                    best = f;
                }
            }
            // Require at least 2 meaningful inputs to qualify (otherwise it's
            // probably a search bar or single-input newsletter signup, not an
            // apply form).
            if (!best || bestScore < 2) return null;
            return selectorFor(best);
        }""")
        if selector:
            logger.info("_detect_form_container: multi-form fallback picked %s", selector)
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
                const ariaOrPh = el.getAttribute('aria-label') || el.placeholder || '';
                if (ariaOrPh) return ariaOrPh;
                // Walk up DOM to find the nearest question/label text
                let node = el;
                for (let depth = 0; node && depth < 6; depth++) {
                    node = node.parentElement;
                    if (!node) break;
                    for (const sel of [':scope > label', ':scope > legend', ':scope > h3', ':scope > h4',
                                       ':scope > p', ':scope > span', ':scope > div > label']) {
                        for (const c of node.querySelectorAll(sel)) {
                            if (c.contains(el)) continue;
                            const t = c.textContent.trim();
                            if (t.length > 3 && t.length < 200) return t;
                        }
                    }
                }
                return el.name || '';
            }

            function fieldType(el) {
                const tag = el.tagName.toLowerCase();
                if (tag === 'select') return 'select';
                if (tag === 'textarea') return 'textarea';
                const type = (el.getAttribute('type') || 'text').toLowerCase();
                if (type === 'file') return 'file';
                if (type === 'checkbox') return 'checkbox';
                if (type === 'radio') return 'radio';
                // Combobox detection — covers four widely-used patterns:
                //   1. Native role="combobox" (correct ARIA)
                //   2. Greenhouse / many React-Select wrappers — input nested
                //      inside .select__control, css-*-control, or similar.
                //      The role attribute is set asynchronously; if the scan
                //      races React's render the role check fails. The wrapper
                //      class is set during render so it's a more stable signal.
                //   3. aria-haspopup="listbox" or "true" — declared by widgets
                //      that pop up a list of options.
                //   4. aria-autocomplete="list" or "both" — explicitly tells
                //      assistive tech this input filters a list of options.
                const role = el.getAttribute('role');
                if (role === 'combobox') return 'combobox';
                const haspopup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
                if (haspopup === 'listbox' || haspopup === 'true') return 'combobox';
                const autocomplete = (el.getAttribute('aria-autocomplete') || '').toLowerCase();
                if (autocomplete === 'list' || autocomplete === 'both') return 'combobox';
                if (el.closest && el.closest(
                    '.select__control, [class*="select__control"], '
                    + '[class*="-control"][class*="select"], '
                    + '.combobox, [class*="combobox"]'
                )) return 'combobox';
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
            // Custom React dropdowns (data-testid pattern)
            const ddSel = '[data-testid="dropdown-basic"], [data-testid="agree-data-privacy-dropdown"]';
            const allDDs = document.querySelectorAll(ddSel);
            for (let di = 0; di < allDDs.length; di++) {
                const dd = allDDs[di];
                const testId = dd.getAttribute('data-testid') || '';
                let label = '';
                const titleEl = dd.querySelector('[data-testid="dropdown-title"]');
                if (titleEl) label = titleEl.textContent.trim();
                if (!label) {
                    let node = dd;
                    for (let depth = 0; node && depth < 6; depth++) {
                        node = node.parentElement;
                        if (!node) break;
                        const qCandidates = node.querySelectorAll(':scope > label, :scope > legend, :scope > h3, :scope > h4, :scope > p, :scope > span[class*="label"]');
                        for (const c of qCandidates) {
                            const qt = c.textContent.trim();
                            if (qt.length > 5 && qt.length < 500) { label = qt; break; }
                        }
                        if (label) break;
                    }
                }
                const btn = dd.querySelector('[data-testid="dropdown-button"]') || dd.querySelector('button');
                const currentValue = btn ? btn.textContent.trim() : '';
                if (!label) label = currentValue || (testId + '_' + di);
                const ddKey = 'custom_dd:' + label;
                if (seen.has(ddKey)) continue;
                seen.add(ddKey);
                fields.push({label, type: 'custom_dropdown', value: currentValue, testId, ddIndex: di});
            }
            // Button-based custom dropdowns (Workday questionnaire pattern)
            const btnDDs = document.querySelectorAll('button');
            for (const btn of btnDDs) {
                const btnText = btn.textContent.trim();
                if (!btnText || !/^select\\s*(one|an?\\s*option)?$/i.test(btnText)) continue;
                if (btn.offsetParent === null) continue;
                const btnId = btn.id || '';
                const btnKey = 'btn_dd:' + (btnId || btnText + btn.getBoundingClientRect().y);
                if (seen.has(btnKey)) continue;
                seen.add(btnKey);
                let label = '';
                let node = btn;
                for (let depth = 0; node && depth < 6; depth++) {
                    node = node.parentElement;
                    if (!node) break;
                    for (const sel of [':scope > legend', ':scope > label', ':scope > h3',
                                       ':scope > h4', ':scope > p', ':scope > span', ':scope > div > label']) {
                        for (const c of node.querySelectorAll(sel)) {
                            if (c.contains(btn)) continue;
                            const t = c.textContent.trim().replace(/[*]$/, '').trim();
                            if (t.length > 5 && t.length < 300 && !/^select/i.test(t)) { label = t; break; }
                        }
                        if (label) break;
                    }
                    if (label) break;
                }
                if (!label) label = 'Dropdown ' + fields.length;
                fields.push({label, type: 'custom_dropdown', value: btnText, buttonId: btnId, required: true});
            }
            // Oracle HCM Yes/No widgets — <ul role="list"> + <li role="listitem"> + <button>.
            // The question label sits on the <ul> as aria-label (or aria-labelledby).
            // Selected state is NOT exposed via aria-checked/role=radio — detect via
            // CSS class (selected/active/is-selected/chosen) or fallback to non-
            // transparent computed background-color.
            // Live regression on JPMC 2026-05-05: agent missed all 4 Yes/No questions
            // on the 'Job Application Questions' page because no scan strategy queried
            // this widget pattern.
            const ulLists = document.querySelectorAll('ul[role="list"]');
            for (const ul of ulLists) {
                if (ul.offsetParent === null) continue;
                const ulLabel =
                    ul.getAttribute('aria-label') ||
                    document.getElementById(ul.getAttribute('aria-labelledby') || '')?.innerText || '';
                if (!ulLabel) continue;
                const lis = [...ul.querySelectorAll('li[role="listitem"]')];
                if (lis.length === 0 || lis.length > 8) continue;
                const buttons = lis.map(li => li.querySelector('button')).filter(Boolean);
                if (buttons.length !== lis.length) continue;
                const optionTexts = buttons.map(b => (b.innerText || '').trim()).filter(Boolean);
                if (optionTexts.length === 0) continue;
                const ulKey = 'oracle_listbtn:' + ulLabel.slice(0, 60);
                if (seen.has(ulKey)) continue;
                seen.add(ulKey);
                // Detect selected button
                let selected = '';
                for (const btn of buttons) {
                    const cls = (btn.className || '').toLowerCase();
                    const liCls = (btn.parentElement?.className || '').toLowerCase();
                    const ariaPressed = btn.getAttribute('aria-pressed');
                    const ariaCurrent = btn.getAttribute('aria-current');
                    const dataSel = btn.getAttribute('data-selected') ||
                                    btn.parentElement?.getAttribute('data-selected') || '';
                    if (ariaPressed === 'true' || ariaCurrent === 'true' ||
                        ariaCurrent === 'page' || dataSel === 'true' ||
                        /selected|active|is-selected|chosen|current/.test(cls + ' ' + liCls)) {
                        selected = (btn.innerText || '').trim();
                        break;
                    }
                }
                if (!selected) {
                    for (const btn of buttons) {
                        const bg = window.getComputedStyle(btn).backgroundColor || '';
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
                            selected = (btn.innerText || '').trim();
                            break;
                        }
                    }
                }
                fields.push({
                    label: ulLabel.trim().slice(0, 200),
                    type: 'list_button_radio',
                    options: optionTexts,
                    value: selected,
                    required: true,
                });
            }
            // ARIA toggle switches — <button role="switch" aria-checked="…">.
            // Revolut welovealfa.com 2026-05-05: 5 screening Qs each render
            // as a single switch (no Yes/No pair). The Q label is in a
            // *sibling* heading or paragraph (not aria-label / aria-labelledby
            // / wrapper label, which are all empty on this widget). We walk
            // the previous siblings + ancestors looking for question-shaped
            // text (ends with '?', under 300 chars, not "Yes"/"No"/"On"/"Off").
            const switches = document.querySelectorAll('button[role="switch"], [role="switch"]');
            for (const sw of switches) {
                if (sw.offsetParent === null) continue;
                const swKey = 'switch:' + (sw.id || sw.getBoundingClientRect().y);
                if (seen.has(swKey)) continue;
                seen.add(swKey);

                let qLabel = sw.getAttribute('aria-label') || '';
                if (!qLabel && sw.getAttribute('aria-labelledby')) {
                    const ids = sw.getAttribute('aria-labelledby').split(/\s+/);
                    qLabel = ids.map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
                }
                // Walk up to 4 ancestors looking for a question-shaped sibling
                if (!qLabel) {
                    let node = sw;
                    for (let depth = 0; node && depth < 4 && !qLabel; depth++) {
                        for (let prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) {
                            const t = (prev.innerText || prev.textContent || '').trim();
                            if (t.length > 5 && t.length < 300 && !/^(yes|no|on|off|true|false)$/i.test(t)) {
                                qLabel = t;
                                break;
                            }
                        }
                        if (!qLabel) {
                            // Try first significant text in the parent BEFORE the switch
                            const parent = node.parentElement;
                            if (parent) {
                                for (const child of parent.childNodes) {
                                    if (child === node) break;
                                    const t = ((child.innerText || child.textContent) || '').trim();
                                    if (t.length > 5 && t.length < 300 && !/^(yes|no|on|off|true|false)$/i.test(t)) {
                                        qLabel = t;
                                        // Don't break — later siblings may have the actual question
                                    }
                                }
                            }
                        }
                        node = node.parentElement;
                    }
                }
                if (!qLabel) qLabel = 'Toggle ' + fields.length;
                const checked = sw.getAttribute('aria-checked') === 'true' ||
                                sw.getAttribute('aria-pressed') === 'true';
                fields.push({
                    label: qLabel.slice(0, 250),
                    type: 'switch',
                    value: checked ? 'true' : 'false',
                    checked,
                    required: true,
                });
            }
            // Salary-context number inputs — number inputs without a label
            // whose surrounding text mentions salary/compensation/GBP/USD.
            // Revolut welovealfa.com regression: the agent filled both Min
            // and Max GBP fields with the JD's listed range £85,500-£118,000
            // because there was no label and the LLM saw only those numbers
            // on the page. Tag these explicitly so the filler routes them
            // through role_salary DB instead of LLM-from-JD-prose.
            const salaryRx = /salary|compensation|gbp|usd|gross|annual|per year|per annum/i;
            const numInputs = document.querySelectorAll('input[type="number"]');
            for (const inp of numInputs) {
                if (inp.offsetParent === null) continue;
                if (inp.disabled || inp.readOnly) continue;
                // Already covered by another scan?
                const inpKey = 'num:' + (inp.id || inp.name || inp.getBoundingClientRect().y);
                if (seen.has(inpKey)) continue;
                // Look up to 4 ancestors for "salary" context
                let salaryCtx = '';
                let node = inp.parentElement;
                for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
                    const t = (node.innerText || '').trim();
                    if (salaryRx.test(t)) {
                        salaryCtx = t.slice(0, 300);
                        break;
                    }
                }
                if (!salaryCtx) continue;
                // Existing label?
                let label = '';
                if (inp.id) {
                    label = document.querySelector(`label[for="${inp.id}"]`)?.innerText?.trim() || '';
                }
                if (!label) label = inp.getAttribute('aria-label') || inp.placeholder || '';
                // Determine min/max/single from context tokens above the input
                let role = 'salary';
                const ctxLow = salaryCtx.toLowerCase();
                // Inspect the input's preceding text within the parent
                const parent = inp.parentElement;
                let preceding = '';
                if (parent) {
                    for (const child of parent.childNodes) {
                        if (child === inp) break;
                        preceding += ((child.innerText || child.textContent) || '') + ' ';
                    }
                }
                preceding = (label + ' ' + preceding).toLowerCase();
                if (/\bmin/.test(preceding) || /minimum/.test(preceding)) role = 'min_salary';
                else if (/\bmax/.test(preceding) || /maximum/.test(preceding)) role = 'max_salary';
                seen.add(inpKey);
                if (!label) label = role === 'min_salary' ? 'Min salary' : role === 'max_salary' ? 'Max salary' : 'Salary expectation';
                fields.push({
                    label: label.slice(0, 200),
                    type: 'salary_number',
                    value: inp.value || '',
                    salary_role: role,
                    id: inp.id || '',
                    name: inp.name || '',
                    required: inp.required || false,
                });
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
        button_id = item.get("buttonId", "")
        if button_id:
            locator = page.locator(f"#{button_id}")
        elif label:
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
        if item.get("testId"):
            entry["testId"] = item["testId"]
        if "ddIndex" in item:
            entry["ddIndex"] = item["ddIndex"]
        if button_id:
            entry["buttonId"] = button_id
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
    """Merge secondary fields into primary, adding fields not already present.

    Dedup key is (label, type). Same label + different type stays as two
    entries (e.g., a Resume text input + Resume file input are distinct).

    Learned-pattern fields (learned_pattern=True) take precedence on
    (label, type) match — if a generic strategy also discovered the same
    field, the learned version replaces it because its locator was
    captured from prior corrections.
    """
    seen = {}  # (label, type) -> index in merged
    merged: list[dict] = []
    for f in primary:
        key = (f.get("label", "").lower().strip(), f.get("type", ""))
        if key[0]:
            seen[key] = len(merged)
        merged.append(f)

    for f in secondary:
        key = (f.get("label", "").lower().strip(), f.get("type", ""))
        if not key[0]:
            continue
        if key in seen:
            existing_idx = seen[key]
            existing = merged[existing_idx]
            if f.get("learned_pattern") and not existing.get("learned_pattern"):
                merged[existing_idx] = f
            continue
        seen[key] = len(merged)
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

    # Vision-augment when the scan looks sparse on a confident form.
    # Reasoner state is read from orchestrator-provided hints stamped on
    # the page object in _phase_act; defaults to assuming
    # application_form @ 0.9 (matches what _phase_act passes).
    page_type_hint = getattr(page, "_jp_page_type_hint", None)
    confidence_hint = getattr(page, "_jp_reasoner_confidence", None)
    extras = await _maybe_augment_with_vision(
        page, best_fields, page_type_hint, confidence_hint,
    )
    if extras:
        best_fields = list(best_fields) + extras
        final_count = _fillable_count(best_fields)
        logger.info(
            "scan_fields: vision augment added %d fields → %d total",
            len(extras), final_count,
        )

    _emit_scan_signal(domain, "success", winner=winner, field_count=final_count)
    return best_fields


async def _maybe_augment_with_vision(
    page: "Page",
    existing_fields: list[dict],
    page_type_hint: str | None,
    confidence_hint: float | None,
) -> list[dict]:
    """Returns vision-augmented field list (or [] if not triggered).

    Caller is responsible for merging into the primary scan result.
    """
    from jobpulse.form_engine.vision_gate import (
        should_force_vision, vision_augment_scan,
    )
    page_type = page_type_hint or "application_form"
    confidence = confidence_hint if confidence_hint is not None else 0.9
    if not should_force_vision(
        scanner_field_count=len(existing_fields),
        page_type=page_type,
        reasoner_confidence=confidence,
    ):
        return []
    return await vision_augment_scan(page, existing_fields)


async def _run_strategy(
    page: "Page", strategy_name: str, container_node_id: str | None,
) -> list[dict]:
    """Run a single scan strategy, returning [] on failure."""
    try:
        if strategy_name == "learned_patterns":
            return await _scan_learned_patterns(page)
        elif strategy_name == "a11y_tree":
            return await _scan_a11y_tree(page, container_node_id)
        elif strategy_name == "dom_query":
            return await _scan_dom_query(page)
        elif strategy_name == "playwright_locators":
            return await scan_fields_locator_fallback(page)
        elif strategy_name == "semantic":
            from jobpulse.form_engine.semantic_scanner import scan_semantic
            return await scan_semantic(page)
    except Exception as exc:
        logger.debug("Scan strategy %s failed: %s", strategy_name, exc)
    return []


async def _scan_learned_patterns(page: "Page") -> list[dict]:
    """Strategy 0: per-domain widgets learned from prior corrections.

    Queries GotchasDB.widget_patterns for the current domain, walks each
    stored selector, returns matching elements as field dicts with the
    locator pre-attached so the dispatcher uses it directly (no
    label-string re-resolution).
    """
    from urllib.parse import urlparse
    from jobpulse.form_engine.gotchas import GotchasDB

    try:
        domain = urlparse(page.url or "").netloc.lower().removeprefix("www.")
    except Exception:
        return []
    if not domain:
        return []

    try:
        patterns = GotchasDB().get_widget_patterns(domain)
    except Exception as exc:
        logger.debug("learned_patterns: GotchasDB read failed: %s", exc)
        return []

    if not patterns:
        return []

    out: list[dict] = []
    for p in patterns:
        selector = p["selector"]
        try:
            loc = page.locator(selector).first
            if not await loc.count():
                continue
        except Exception:
            continue
        out.append({
            "label": p["label"],
            "type": p["widget_type"],
            "value": "",
            "locator": loc,
            "selector": selector,
            "learned_pattern": True,
            "fix_count": p["fix_count"],
        })
    if out:
        logger.info(
            "learned_patterns: %d/%d known widgets matched on %s",
            len(out), len(patterns), domain,
        )
    return out


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
