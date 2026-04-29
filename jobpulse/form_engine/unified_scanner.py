"""UnifiedFieldScanner — single scanner merging CDP a11y tree + Playwright locators + DOM evaluate.

Three-tier scanning:
  1. CDP Accessibility.getFullAXTree — pierces shadow DOM, fastest, most accurate
  2. Playwright role locators — covers all ARIA roles, no shadow DOM pierce
  3. DOM evaluate() — last resort, captures elements missed by a11y

Deduplicates by label + role + bounding-box overlap.
Returns standardized FieldInfo with full surrounding context.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from jobpulse.form_models import FieldInfo

if TYPE_CHECKING:
    from playwright.async_api import CDPSession, Page

logger = get_logger(__name__)

# All ARIA roles that can represent form inputs (primary + fallback)
_FORM_ROLES = frozenset({
    "textbox", "combobox", "spinbutton", "radio", "radiogroup", "checkbox",
    "slider", "switch", "searchbox", "listbox",
})

# Structural roles to ignore
_STRUCTURAL_ROLES = frozenset({
    "generic", "none", "presentation", "LayoutTable", "LayoutTableRow",
    "LayoutTableCell", "Section", "FooterAsNonLandmark",
    "HeaderAsNonLandmark", "InlineTextBox", "LineBreak",
})

# Navigation chrome labels to filter out
_NAV_NOISE_LABELS = (
    r"^(home|my\s*network|jobs|messaging|notifications|me|for\s*business"
    r"|more\s*options|skip\s*to|close\s*jump|privacy\s*&\s*terms"
    r"|select\s*language|compose\s*message|open\s*messenger"
    r"|reactivate\s*premium|post\s*a\s*job|save\s*the\s*job"
    r"|tap\s*to\s*toggle|you\s*are\s*on\s*the\s*messaging"
    r"|follow$|following$|i.?m\s*interested$|linkedin$"
    r"|see\s*more|show\s*all|message$)$"
)

# ARIA role → input_type mapping for FieldInfo
_ROLE_TO_INPUT_TYPE: dict[str, str] = {
    "textbox": "text",
    "searchbox": "text",
    "combobox": "combobox",
    "listbox": "select",
    "spinbutton": "number",
    "radio": "radio",
    "radiogroup": "radio",
    "checkbox": "checkbox",
    "switch": "checkbox",
    "slider": "range",
}


@dataclass
class _RawField:
    """Internal raw field before deduplication and enrichment."""

    label: str
    role: str
    input_type: str
    selector: str
    value: str = ""
    required: bool = False
    invalid: bool = False
    options: list[str] = field(default_factory=list)
    in_shadow_dom: bool = False
    in_iframe: bool = False
    iframe_index: int | None = None
    bbox: dict[str, float] | None = None
    node_id: str = ""
    source: str = ""  # "cdp", "playwright", "dom"


class UnifiedFieldScanner:
    """Scan a page for all form fields using three-tier detection."""

    def __init__(self, page: "Page") -> None:
        self._page = page

    # ── Public API ──

    async def scan(self) -> list[FieldInfo]:
        """Scan the page and return deduplicated, enriched FieldInfo list."""
        page = await self._resolve_iframe_page(self._page)

        # Tier 1: CDP a11y tree (with timeout — don't block on slow CDP)
        try:
            cdp_fields = await asyncio.wait_for(self._scan_cdp(page), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("UnifiedScanner: CDP scan timed out, skipping")
            cdp_fields = []
        logger.info("UnifiedScanner: CDP found %d fields", len(cdp_fields))

        # Tier 2: Playwright role locators
        pw_fields = await self._scan_playwright(page)
        logger.info("UnifiedScanner: Playwright found %d fields", len(pw_fields))

        # Tier 3: DOM evaluate
        dom_fields = await self._scan_dom(page)
        logger.info("UnifiedScanner: DOM found %d fields", len(dom_fields))

        # Merge and deduplicate
        all_raw = cdp_fields + pw_fields + dom_fields
        deduped = self._deduplicate_fields(all_raw)
        logger.info("UnifiedScanner: %d raw → %d deduplicated", len(all_raw), len(deduped))

        # Merge individual radio inputs into radiogroups by shared name attribute
        deduped = await self._merge_radio_groups(page, deduped)

        # Enrich with surrounding context
        enriched = await self._enrich_fields(page, deduped)

        return enriched

    # ── Tier 1: CDP Accessibility Tree ──

    async def _scan_cdp(self, page: "Page") -> list[_RawField]:
        cdp = await self._get_cdp_session(page)
        if cdp is None:
            return []

        try:
            result = await cdp.send("Accessibility.getFullAXTree")
        except Exception as exc:
            logger.debug("CDP getFullAXTree failed: %s", exc)
            return []
        finally:
            try:
                await cdp.detach()
            except Exception:
                pass

        nodes = result.get("nodes", [])
        nodes_by_id: dict[str, dict] = {}
        for node in nodes:
            nid = node.get("nodeId", "")
            if nid:
                nodes_by_id[nid] = node

        # Collect radiogroup child IDs so we don't emit individual radios
        radiogroup_child_ids: set[str] = set()
        for node in nodes:
            role = node.get("role", {}).get("value", "")
            if role == "radiogroup":
                radiogroup_child_ids.update(node.get("childIds", []))

        fields: list[_RawField] = []
        label_counts: dict[str, int] = {}

        for node in nodes:
            role, name, value, props = self._parse_ax_node(node)

            if role in _STRUCTURAL_ROLES or not name:
                continue
            if role not in _FORM_ROLES:
                continue

            node_id = node.get("nodeId", "")
            if role == "radio" and node_id in radiogroup_child_ids:
                continue
            if self._is_noise_label(name):
                continue

            # Build options for radiogroups
            options: list[str] = []
            if role == "radiogroup":
                for cid in node.get("childIds", []):
                    child = nodes_by_id.get(cid)
                    if child:
                        cr, cn, _, _ = self._parse_ax_node(child)
                        if cr == "radio" and cn:
                            options.append(cn)

            label_key = f"{role}:{name}"
            count = label_counts.get(label_key, 0) + 1
            label_counts[label_key] = count
            display_label = name if count == 1 else f"{name} #{count}"

            fields.append(_RawField(
                label=display_label,
                role=role,
                input_type=_ROLE_TO_INPUT_TYPE.get(role, role),
                selector=f'[role="{role}"][name="{name}"]',
                value=str(value) if value else "",
                required=props.get("required") is True,
                invalid=str(props.get("invalid", "")).lower() == "true",
                options=options,
                in_shadow_dom=True,  # CDP pierces shadow DOM
                node_id=node_id,
                source="cdp",
            ))

        return fields

    # ── Tier 2: Playwright Role Locators ──

    async def _scan_playwright(self, page: "Page") -> list[_RawField]:
        fields: list[_RawField] = []
        label_counts: dict[str, int] = {}

        for role_name in _FORM_ROLES:
            try:
                locators = await page.get_by_role(role_name).all()
            except Exception as exc:
                logger.debug("Playwright role locator %s failed: %s", role_name, exc)
                continue

            for loc in locators:
                try:
                    # Skip invisible elements
                    if not await loc.is_visible():
                        continue
                except Exception:
                    continue

                # Skip individual radios inside radiogroups (handled by radiogroup scan)
                if role_name == "radio":
                    try:
                        parent = await loc.evaluate(
                            "el => el.closest('[role=\"radiogroup\"]') !== null"
                        )
                        if parent:
                            continue
                    except Exception:
                        pass

                label = await self._get_accessible_name(loc)
                if not label or self._is_noise_label(label):
                    continue

                value = ""
                if role_name in ("textbox", "spinbutton", "combobox", "searchbox"):
                    try:
                        value = await loc.input_value()
                    except Exception:
                        pass
                elif role_name == "checkbox":
                    try:
                        value = "checked" if await loc.is_checked() else ""
                    except Exception:
                        pass

                options: list[str] = []
                if role_name in ("combobox", "listbox"):
                    try:
                        tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "select":
                            option_texts = await loc.locator("option").all_text_contents()
                            options = [o.strip() for o in option_texts if o.strip()]
                    except Exception:
                        pass
                elif role_name == "radiogroup":
                    try:
                        radios = await loc.get_by_role("radio").all()
                        for r in radios:
                            rl = await self._get_accessible_name(r)
                            if rl:
                                options.append(rl)
                    except Exception:
                        pass

                label_key = f"{role_name}:{label}"
                count = label_counts.get(label_key, 0) + 1
                label_counts[label_key] = count
                display_label = label if count == 1 else f"{label} #{count}"

                fields.append(_RawField(
                    label=display_label,
                    role=role_name,
                    input_type=_ROLE_TO_INPUT_TYPE.get(role_name, role_name),
                    selector=f'[role="{role_name}"]:has-text("{label}")',
                    value=value,
                    required=await loc.get_attribute("required") is not None
                        or await loc.get_attribute("aria-required") == "true",
                    options=options,
                    source="playwright",
                ))

        # Also scan textarea and file inputs via locator (not always exposed as roles)
        for sel, itype in (("textarea:visible", "textarea"), ("input[type='file']:visible", "file")):
            try:
                for loc in await page.locator(sel).all():
                    try:
                        if not await loc.is_visible():
                            continue
                    except Exception:
                        continue
                    label = await self._get_accessible_name(loc)
                    if not label:
                        label = await loc.get_attribute("placeholder") or ""
                    if not label or self._is_noise_label(label):
                        continue

                    label_key = f"{itype}:{label}"
                    count = label_counts.get(label_key, 0) + 1
                    label_counts[label_key] = count
                    display_label = label if count == 1 else f"{label} #{count}"

                    fields.append(_RawField(
                        label=display_label,
                        role=itype,
                        input_type=itype,
                        selector=sel.split(":")[0],  # crude but functional
                        source="playwright",
                    ))
            except Exception as exc:
                logger.debug("Playwright locator %s failed: %s", sel, exc)

        return fields

    # ── Tier 3: DOM evaluate() ──

    async def _scan_dom(self, page: "Page") -> list[_RawField]:
        """DOM scan as last resort — captures elements with no ARIA exposure."""
        try:
            raw = await page.evaluate("""() => {
                const out = [];
                const root = document;
                const seen = new Set();

                function getLabel(el) {
                    if (el.id) {
                        const lbl = root.querySelector('label[for="' + el.id + '"]');
                        if (lbl) return lbl.textContent.trim();
                    }
                    if (el.labels && el.labels.length) return el.labels[0].textContent.trim();
                    const aria = el.getAttribute('aria-label');
                    if (aria) return aria.trim();
                    const described = el.getAttribute('aria-describedby');
                    if (described) {
                        const desc = root.querySelector('#' + described);
                        if (desc) return desc.textContent.trim();
                    }
                    return el.getAttribute('placeholder')?.trim() || '';
                }

                function getSelector(el) {
                    if (el.id) {
                        // CSS identifiers cannot start with a digit — escape or use attribute selector
                        if (/^\d/.test(el.id)) {
                            return '[id="' + el.id + '"]';
                        }
                        return '#' + el.id;
                    }
                    if (el.name) return '[name="' + el.name + '"]';
                    return el.tagName.toLowerCase();
                }

                function getBbox(el) {
                    const r = el.getBoundingClientRect();
                    return {x: r.x, y: r.y, width: r.width, height: r.height};
                }

                for (const el of root.querySelectorAll('input, select, textarea, [contenteditable="true"]')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;
                    const label = getLabel(el);
                    if (!label) continue;
                    const key = label + '|' + el.tagName + '|' + (el.type || '');
                    if (seen.has(key)) continue;
                    seen.add(key);

                    let itype = el.type || el.tagName.toLowerCase();
                    if (el.getAttribute('contenteditable') === 'true') itype = 'richtext';

                    let opts = [];
                    if (el.tagName.toLowerCase() === 'select') {
                        opts = Array.from(el.options).map(o => o.textContent.trim()).filter(Boolean);
                    }

                    out.push({
                        label,
                        type: itype,
                        selector: getSelector(el),
                        value: el.value || '',
                        required: el.required || el.getAttribute('aria-required') === 'true',
                        options: opts,
                        bbox: getBbox(el),
                    });
                }
                return out;
            }""")
        except Exception as exc:
            logger.debug("DOM evaluate scan failed: %s", exc)
            return []

        fields: list[_RawField] = []
        for item in raw:
            fields.append(_RawField(
                label=item.get("label", ""),
                role=item.get("type", "text"),
                input_type=item.get("type", "text"),
                selector=item.get("selector", ""),
                value=item.get("value", ""),
                required=item.get("required", False),
                options=item.get("options", []),
                bbox=item.get("bbox"),
                source="dom",
            ))

        return fields

    # ── Deduplication ──

    @staticmethod
    def _normalize_input_type(t: str) -> str:
        """Normalize HTML/ARIA types to semantic types for deduplication.

        CDP uses ARIA roles (textbox → text), DOM uses HTML types
        (email, tel, url, select-one → text/select). This normalizes so
        they group together.
        """
        mapping = {
            "email": "text", "tel": "text", "url": "text",
            "number": "text", "search": "text", "password": "text",
            "textbox": "text", "textarea": "text",
            "combobox": "select", "listbox": "select",
            "select-one": "select", "select-multiple": "select",
        }
        return mapping.get(t, t)

    @staticmethod
    def _selector_quality(selector: str) -> int:
        """Score selector reliability. Higher = more likely to resolve."""
        if selector.startswith("#"):
            return 3  # ID-based — most reliable
        if selector.startswith('[id="'):
            return 3  # Attribute ID selector — same reliability as #id
        if "[name=" in selector and len(selector) < 35:
            return 2  # Short name attribute — likely real HTML name
        if ":has-text(" in selector:
            return 1  # Playwright text locator — fragile but sometimes works
        if "[name=" in selector and len(selector) > 40:
            return 0  # Long name attribute — probably using label text as name, broken
        return 1

    def _deduplicate_fields(self, fields: list[_RawField]) -> list[_RawField]:
        """Deduplicate by label + role + approximate bbox overlap.

        Prefer selectors that will actually resolve (ID > short name > text),
        with source priority as tiebreaker.

        Also merges fields with the same label but different detected types
        (e.g. DOM sees input[type=text] while Playwright sees combobox),
        keeping the version with the best selector.
        """
        source_priority = {"cdp": 0, "playwright": 1, "dom": 2}

        # --- Pass 1: group by normalized label + normalized input_type ---
        groups: dict[str, list[_RawField]] = {}
        for f in fields:
            norm_label = f.label.lower().strip().rstrip(' *#1234567890')
            norm_type = self._normalize_input_type(f.input_type)
            key = f"{norm_label}|{norm_type}"
            groups.setdefault(key, []).append(f)

        deduped: list[_RawField] = []
        for group in groups.values():
            if len(group) == 1:
                deduped.append(group[0])
                continue

            # Sort by selector quality (desc), then source priority (asc)
            group.sort(
                key=lambda f: (-self._selector_quality(f.selector), source_priority.get(f.source, 99))
            )

            # Keep the first (best selector) if bbox overlap is significant
            kept = group[0]
            for other in group[1:]:
                if self._bbox_overlap(kept.bbox, other.bbox) < 0.3:
                    # Different location — keep both
                    deduped.append(other)
            deduped.append(kept)

        # --- Pass 2: merge same-label-different-type duplicates ---
        # When CDP says "combobox" and DOM says "text" for the same element,
        # the DOM version usually has the better (ID-based) selector.
        # Use prefix-based matching to handle truncation differences across sources.
        def _label_prefix(label: str, length: int = 50) -> str:
            return label.lower().strip().rstrip(' *#1234567890')[:length]

        label_groups: dict[str, list[_RawField]] = {}
        for f in deduped:
            prefix = _label_prefix(f.label)
            matched = False
            for key in list(label_groups.keys()):
                if key == prefix or key.startswith(prefix) or prefix.startswith(key):
                    label_groups[key].append(f)
                    matched = True
                    break
            if not matched:
                label_groups.setdefault(prefix, []).append(f)

        final: list[_RawField] = []
        for group in label_groups.values():
            if len(group) == 1:
                final.append(group[0])
                continue
            # Prefer highest selector quality
            group.sort(
                key=lambda f: (-self._selector_quality(f.selector), source_priority.get(f.source, 99))
            )
            kept = group[0]
            for other in group[1:]:
                if self._bbox_overlap(kept.bbox, other.bbox) < 0.3:
                    final.append(other)
            final.append(kept)

        return final

    @staticmethod
    def _bbox_overlap(a: dict[str, float] | None, b: dict[str, float] | None) -> float:
        """Compute IoU of two bounding boxes. None = full overlap assumed."""
        if a is None or b is None:
            return 1.0

        ax1, ay1 = a["x"], a["y"]
        ax2, ay2 = ax1 + a["width"], ay1 + a["height"]
        bx1, by1 = b["x"], b["y"]
        bx2, by2 = bx1 + b["width"], by1 + b["height"]

        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)

        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0

        inter = (ix2 - ix1) * (iy2 - iy1)
        area_a = a["width"] * a["height"]
        area_b = b["width"] * b["height"]
        union = area_a + area_b - inter

        return inter / union if union > 0 else 0.0

    # ── Radio group merging ──

    async def _merge_radio_groups(self, page: "Page", fields: list[_RawField]) -> list[_RawField]:
        """Merge individual radio inputs into radiogroup fields by shared name attribute.

        When HTML uses <input type="radio" name="gender"> without role="radiogroup",
        all three scanners emit individual radio fields. This method queries the DOM
        to group them by name and merges each group into a single field with options.
        """
        radios = [f for f in fields if str(f.input_type) == "radio"]
        non_radios = [f for f in fields if str(f.input_type) != "radio"]
        if len(radios) <= 1:
            return fields

        # Build a map of ALL radio inputs on the page: name -> list of (label, selector)
        try:
            dom_radios = await page.evaluate("""() => {
                const out = {};
                document.querySelectorAll("input[type='radio']").forEach(el => {
                    const name = el.name || el.getAttribute('name') || '_unknown';
                    if (!out[name]) out[name] = [];
                    // Get label text
                    let label = '';
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) label = lbl.textContent.trim();
                    }
                    if (!label && el.labels && el.labels.length) {
                        label = el.labels[0].textContent.trim();
                    }
                    if (!label) {
                        const parent = el.closest('label');
                        if (parent) {
                            const clone = parent.cloneNode(true);
                            clone.querySelectorAll('input').forEach(i => i.remove());
                            label = clone.textContent.trim();
                        }
                    }
                    out[name].push({label: label, selector: 'input[type="radio"][name="' + name + '"]' });
                });
                return out;
            }""")
        except Exception as exc:
            logger.debug("Radio group DOM query failed: %s", exc)
            dom_radios = {}

        # Group scanner radio fields by matching them to DOM radio groups
        # Strategy: map each scanner radio to a DOM group by trying to resolve its selector
        name_to_radios: dict[str, list[_RawField]] = {}
        unmatched: list[_RawField] = []

        for rf in radios:
            matched = False
            # Try direct selector resolution first
            try:
                resolved_name = await page.evaluate(
                    """(selector) => {
                        const el = document.querySelector(selector);
                        return el ? (el.name || el.getAttribute('name') || '') : '';
                    }""",
                    rf.selector,
                )
                if resolved_name:
                    name_to_radios.setdefault(resolved_name, []).append(rf)
                    matched = True
            except Exception:
                pass

            if not matched:
                # Fallback: try to match label to a DOM radio group
                for dom_name, dom_group in (dom_radios or {}).items():
                    dom_labels = [g["label"].lower() for g in dom_group]
                    if rf.label.lower() in dom_labels:
                        name_to_radios.setdefault(dom_name, []).append(rf)
                        matched = True
                        break

            if not matched:
                # Last resort: regex extract name from selector
                import re as _re
                m = _re.search(r'\[name=["\']([^"\']+)["\']\]', rf.selector)
                if m:
                    name_to_radios.setdefault(m.group(1), []).append(rf)
                else:
                    unmatched.append(rf)

        merged: list[_RawField] = []
        for name, group in name_to_radios.items():
            if len(group) == 1:
                merged.append(group[0])
                continue

            # Build options from all radio labels
            options = [rf.label for rf in group if rf.label]

            # Find group label from DOM (legend or first radio's group context)
            group_label = group[0].label
            dom_group = (dom_radios or {}).get(name, [])
            if dom_group:
                # Use DOM to find group label
                try:
                    dom_label = await page.evaluate(
                        """(name) => {
                            const el = document.querySelector('input[type="radio"][name="' + name + '"]');
                            if (!el) return '';
                            const fieldset = el.closest('fieldset');
                            if (fieldset) {
                                const legend = fieldset.querySelector('legend');
                                if (legend) return legend.textContent.trim();
                            }
                            // Check for a label that wraps the whole group
                            const parent = el.parentElement;
                            if (parent) {
                                const prev = parent.previousElementSibling;
                                if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'P'))
                                    return prev.textContent.trim();
                            }
                            // Check preceding label in the same container
                            const container = el.closest('.field, .form-group');
                            if (container) {
                                const labels = container.querySelectorAll('label');
                                for (const lbl of labels) {
                                    if (!lbl.querySelector('input[type="radio"]')) {
                                        return lbl.textContent.trim();
                                    }
                                }
                            }
                            return '';
                        }""",
                        name,
                    )
                    if dom_label and len(dom_label) < 100 and len(dom_label) > 2:
                        group_label = dom_label
                except Exception:
                    pass

            merged.append(_RawField(
                label=group_label,
                role="radiogroup",
                input_type="radio",
                selector=f"input[type='radio'][name='{name}']",
                value="",
                required=any(r.required for r in group),
                options=options,
                bbox=group[0].bbox,
                source="merged",
            ))

        return non_radios + merged + unmatched

    # ── Enrichment ──

    async def _enrich_fields(self, page: "Page", fields: list[_RawField]) -> list[FieldInfo]:
        """Add surrounding context: group label, help text, error text, dom_context."""
        enriched: list[FieldInfo] = []

        for rf in fields:
            fi = FieldInfo(
                selector=rf.selector,
                input_type=rf.input_type,
                label=rf.label,
                required=rf.required,
                current_value=rf.value,
                options=rf.options,
                in_shadow_dom=rf.in_shadow_dom,
                in_iframe=rf.in_iframe,
                iframe_index=rf.iframe_index,
            )

            # Try to get bbox from page for context extraction
            try:
                loc = page.locator(rf.selector).first
                if await loc.count():
                    bbox = await loc.bounding_box()
                    if bbox:
                        context = await self._get_dom_context(page, bbox)
                        fi.dom_context = context.get("dom_context", "")
                        fi.group_label = context.get("group_label", "")
                        fi.help_text = context.get("help_text", "")
                        fi.error_text = context.get("error_text", "")
                        fi.fieldset_legend = context.get("fieldset_legend", "")
            except Exception as exc:
                logger.debug("Field enrichment failed for '%s': %s", rf.label, exc)

            enriched.append(fi)

        return enriched

    async def _get_dom_context(self, page: "Page", bbox: dict[str, float]) -> dict[str, str]:
        """Extract surrounding text context for a field at the given bbox."""
        try:
            result = await page.evaluate(
                """(bbox) => {
                    const el = document.elementFromPoint(bbox.x + bbox.width/2, bbox.y + bbox.height/2);
                    if (!el) return {};

                    // Walk up to find fieldset/legend
                    let fieldsetLegend = '';
                    let node = el;
                    for (let i = 0; node && i < 6; i++) {
                        if (node.tagName === 'FIELDSET') {
                            const legend = node.querySelector('legend');
                            if (legend) fieldsetLegend = legend.textContent.trim();
                            break;
                        }
                        node = node.parentElement;
                    }

                    // Find help text (aria-describedby or sibling)
                    let helpText = '';
                    let errorText = '';
                    const describedBy = el.getAttribute('aria-describedby');
                    if (describedBy) {
                        const descEl = document.getElementById(describedBy);
                        if (descEl) {
                            const text = descEl.textContent.trim();
                            if (descEl.getAttribute('role') === 'alert' ||
                                descEl.className.toLowerCase().includes('error')) {
                                errorText = text;
                            } else {
                                helpText = text;
                            }
                        }
                    }

                    // Sibling/parent error text fallback
                    const parent = el.closest('.form-group, .field-wrapper, [class*="field"]');
                    if (parent && !errorText) {
                        const errEl = parent.querySelector('[class*="error"], [role="alert"]');
                        if (errEl) errorText = errEl.textContent.trim();
                    }

                    // Group label from closest heading/label
                    let groupLabel = '';
                    node = el;
                    for (let i = 0; node && i < 4; i++) {
                        const h = node.querySelector('h1, h2, h3, h4, h5');
                        if (h) {
                            groupLabel = h.textContent.trim();
                            break;
                        }
                        node = node.parentElement;
                    }

                    // DOM context: surrounding 200 chars
                    let domContext = '';
                    node = el;
                    for (let i = 0; node && i < 3; i++) {
                        const text = (node.textContent || '').trim();
                        if (text.length > domContext.length) domContext = text;
                        node = node.parentElement;
                    }
                    if (domContext.length > 400) domContext = domContext.substring(0, 400);

                    return {
                        group_label: groupLabel,
                        fieldset_legend: fieldsetLegend,
                        help_text: helpText,
                        error_text: errorText,
                        dom_context: domContext,
                    };
                }""",
                bbox,
            )
            return result or {}
        except Exception as exc:
            logger.debug("DOM context extraction failed: %s", exc)
            return {}

    # ── Helpers ──

    @staticmethod
    async def _get_cdp_session(page: "Page") -> "CDPSession | None":
        try:
            if not hasattr(page, "context"):
                return None
            return await page.context.new_cdp_session(page)
        except Exception as exc:
            logger.debug("CDP session unavailable: %s", exc)
            return None

    @staticmethod
    def _parse_ax_node(node: dict) -> tuple[str, str, str, dict[str, Any]]:
        """Extract (role, name, value, properties) from an AX tree node."""
        role = node.get("role", {}).get("value", "")
        name = node.get("name", {}).get("value", "")
        value = node.get("value", {}).get("value", "")
        props: dict[str, Any] = {}
        for p in node.get("properties", []):
            pname = p.get("name", "")
            pval = p.get("value", {}).get("value")
            if pname in ("required", "invalid", "checked", "expanded", "disabled"):
                props[pname] = pval
        return role, name, value, props

    @staticmethod
    def _is_noise_label(label: str) -> bool:
        import re
        return bool(re.search(_NAV_NOISE_LABELS, label, re.IGNORECASE))

    @staticmethod
    async def _resolve_iframe_page(page: "Page") -> "Page":
        """If a known ATS content iframe exists, return its frame as Page-like object.

        Only switches for explicitly known ATS iframe names (e.g. iCIMS).
        Never switches to arbitrary third-party iframes (Google APIs, ads,
        analytics) — those are never the application form.
        """
        for name in ("icims_content_iframe",):
            try:
                iframe = page.frame(name=name)
                if iframe is not None:
                    logger.debug("UnifiedScanner: switching to iframe '%s'", name)
                    return iframe  # type: ignore[return-value]
            except Exception:
                pass
        return page

    @staticmethod
    async def _get_accessible_name(locator: Any) -> str:
        """Extract the accessible name for a Playwright locator."""
        try:
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
        except Exception:
            return ""
