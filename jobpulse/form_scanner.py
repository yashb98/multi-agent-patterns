"""FormScanner — Accessibility-tree-first form field discovery.

Uses CDP ``Accessibility.getFullAXTree`` to pierce shadow DOM boundaries
and discover every form field (label, role, value, options, required,
invalid) in a single ~200ms call.  Falls back to Playwright role-based
locators when CDP is unavailable.

Three-step workflow used by NativeFormFiller and all ATS adapters:

1. **scan_form()** — discover fields + metadata (no filling).
2. **scan_combobox_options()** — open a combobox, read its options via
   a11y tree, close it.  Never guess option text.
3. **select_combobox_option()** — pick the best match from scanned
   options and click it.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import CDPSession, Page

logger = get_logger(__name__)

_STRUCTURAL_ROLES = frozenset({
    "generic", "none", "presentation", "LayoutTable", "LayoutTableRow",
    "LayoutTableCell", "Section", "FooterAsNonLandmark",
    "HeaderAsNonLandmark", "InlineTextBox", "LineBreak",
})

_FORM_ROLES = frozenset({
    "textbox", "combobox", "spinbutton", "radio", "radiogroup", "checkbox",
    "button", "slider", "switch",
})

_COOKIE_BUTTON_PATTERNS = re.compile(
    r"^(manage\s*cookies?|reject\s*all|allow\s*all|accept\s*(all\s*)?(cookies?)?"
    r"|cookie\s*(settings|preferences)|customize\s*cookies?"
    r"|alle\s*akzeptieren|alle\s*ablehnen|cookies?\s*verwalten"
    r"|tout\s*accepter|tout\s*refuser|g[eé]rer\s*les\s*cookies?)$",
    re.IGNORECASE,
)


@dataclass
class FormField:
    """Single form field discovered by the accessibility tree."""

    label: str
    role: str
    value: str = ""
    required: bool = False
    invalid: bool = False
    options: list[str] = field(default_factory=list)
    node_id: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.value

    @property
    def needs_fill(self) -> bool:
        return self.required and self.is_empty

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "role": self.role,
            "value": self.value,
            "required": self.required,
            "invalid": self.invalid,
            "options": self.options,
        }


@dataclass
class FormScanResult:
    """Complete scan of a form page."""

    fields: list[FormField]
    page_title: str = ""
    page_url: str = ""
    headings: list[str] = field(default_factory=list)

    @property
    def required_empty(self) -> list[FormField]:
        return [f for f in self.fields if f.needs_fill]

    @property
    def invalid_fields(self) -> list[FormField]:
        return [f for f in self.fields if f.invalid]

    @property
    def field_types(self) -> list[str]:
        return sorted({f.role for f in self.fields})

    @property
    def screening_questions(self) -> list[str]:
        return [f.label for f in self.fields if f.role in ("textbox", "combobox", "radio")]

    def summary(self) -> str:
        lines = [f"FormScan: {len(self.fields)} fields, "
                 f"{len(self.required_empty)} required-empty, "
                 f"{len(self.invalid_fields)} invalid"]
        for f in self.fields:
            status = "EMPTY" if f.is_empty else f'"{f.value[:40]}"'
            req = " *" if f.required else ""
            inv = " !INVALID" if f.invalid else ""
            lines.append(f"  [{f.role}] {f.label[:60]}{req}{inv} = {status}")
        return "\n".join(lines)


async def _get_cdp_session(page: Page) -> CDPSession | None:
    try:
        if not hasattr(page, 'context'):
            return None
        return await page.context.new_cdp_session(page)
    except Exception as exc:
        logger.debug("FormScanner: CDP session unavailable: %s", exc)
        return None


def _parse_ax_node(node: dict) -> tuple[str, str, str, dict[str, Any]]:
    """Extract role, name, value, and properties from an AX tree node."""
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


async def _resolve_iframe_page(page: Page) -> Page:
    """If a content iframe exists (iCIMS, etc.), return its frame as Page-like object."""
    # Named iframes first
    for name in ("icims_content_iframe",):
        try:
            iframe = page.frame(name=name)
            if iframe is not None:
                logger.debug("FormScanner: switching to iframe '%s'", name)
                return iframe  # type: ignore[return-value]
        except Exception:
            pass
    # Fallback: large content iframe covering most of the viewport
    try:
        frames = page.frames
        for frame in frames:
            if frame == page.main_frame:
                continue
            if frame.url and "about:blank" not in frame.url:
                logger.debug("FormScanner: found content frame %s", frame.url[:80])
                return frame  # type: ignore[return-value]
    except Exception:
        pass
    return page


async def scan_form(page: Page, *, container_backend_node_id: str | None = None) -> FormScanResult:
    """Discover all form fields using CDP Accessibility tree.

    When container_backend_node_id is provided, uses getPartialAXTree to
    scope the scan to that container, falling back to getFullAXTree on failure.
    Pierces shadow DOM completely.  Returns structured FormScanResult
    with every field's label, role, value, required/invalid state.
    Falls back to Playwright role locators if CDP is unavailable.
    """
    page = await _resolve_iframe_page(page)

    cdp = await _get_cdp_session(page)
    if cdp is None:
        return await _scan_form_fallback(page)

    nodes: list[dict] = []
    if container_backend_node_id is not None:
        try:
            result = await cdp.send(
                "Accessibility.getPartialAXTree",
                {"backendNodeId": int(container_backend_node_id), "fetchRelatives": True},
            )
            nodes = result.get("nodes", [])
        except Exception as exc:
            logger.debug("FormScanner: getPartialAXTree failed, falling back: %s", exc)

    if not nodes:
        try:
            result = await cdp.send("Accessibility.getFullAXTree")
            nodes = result.get("nodes", [])
        except Exception as exc:
            logger.debug("FormScanner: getFullAXTree failed: %s", exc)
            return await _scan_form_fallback(page)

    fields: list[FormField] = []
    headings: list[str] = []
    page_title = ""
    label_counts: dict[str, int] = {}

    nodes_by_id: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("nodeId", "")
        if nid:
            nodes_by_id[nid] = node

    radiogroup_child_ids: set[str] = set()
    for node in nodes:
        role = node.get("role", {}).get("value", "")
        if role == "radiogroup":
            for cid in node.get("childIds", []):
                radiogroup_child_ids.add(cid)

    for node in nodes:
        role, name, value, props = _parse_ax_node(node)

        if role == "RootWebArea":
            page_title = name
            continue

        if role == "heading" and name:
            headings.append(name)
            continue

        if role in _STRUCTURAL_ROLES or not name:
            continue

        if role not in _FORM_ROLES:
            continue

        # Filter out cookie/overlay buttons — never treat these as form fields
        if role == "button" and _COOKIE_BUTTON_PATTERNS.search(name):
            logger.debug("FormScanner: skipping cookie button '%s'", name)
            continue

        node_id = node.get("nodeId", "")
        if role == "radio" and node_id in radiogroup_child_ids:
            continue

        options: list[str] = []
        if role == "radiogroup":
            for cid in node.get("childIds", []):
                child = nodes_by_id.get(cid)
                if not child:
                    continue
                cr, cn, _, _ = _parse_ax_node(child)
                if cr == "radio" and cn:
                    options.append(cn)

        label_key = f"{role}:{name}"
        count = label_counts.get(label_key, 0) + 1
        label_counts[label_key] = count
        display_label = name if count == 1 else f"{name} #{count}"

        ff = FormField(
            label=display_label,
            role=role,
            value=str(value) if value else "",
            required=props.get("required") is True,
            invalid=str(props.get("invalid", "")).lower() == "true",
            node_id=str(node_id),
            options=options,
        )
        fields.append(ff)

    try:
        await cdp.detach()
    except Exception:
        pass

    scan = FormScanResult(
        fields=fields,
        page_title=page_title,
        page_url=page.url,
        headings=headings,
    )
    logger.info("FormScanner: %d fields, %d required-empty, %d invalid",
                len(fields), len(scan.required_empty), len(scan.invalid_fields))
    return scan


async def _scan_form_fallback(page: Page) -> FormScanResult:
    """Fallback scanner using Playwright role locators (no shadow DOM pierce)."""
    fields: list[FormField] = []
    label_counts: dict[str, int] = {}

    for role_name in ("textbox", "combobox", "spinbutton", "checkbox"):
        for loc in await page.get_by_role(role_name).all():
            try:
                label = await loc.get_attribute("aria-label") or ""
                if not label:
                    label = await loc.evaluate(
                        "el => el.labels?.[0]?.textContent?.trim() || "
                        "el.placeholder || ''"
                    )
                value = ""
                if role_name in ("textbox", "spinbutton", "combobox"):
                    value = await loc.input_value()
                elif role_name == "checkbox":
                    value = "checked" if await loc.is_checked() else ""

                label_key = f"{role_name}:{label}"
                count = label_counts.get(label_key, 0) + 1
                label_counts[label_key] = count
                display_label = label if count == 1 else f"{label} #{count}"

                fields.append(FormField(label=display_label, role=role_name, value=value))
            except Exception:
                continue

    title = await page.title()
    return FormScanResult(fields=fields, page_title=title, page_url=page.url)


async def scan_combobox_options(
    page: Page,
    field_label: str,
    *,
    search_text: str = "",
) -> list[str]:
    """Open a combobox, read all available options via a11y tree, close it.

    This is the ONLY reliable way to read shadow DOM dropdown options.
    Standard ``text_content()`` / ``inner_text()`` return empty strings
    for ``spl-*`` web components.

    Args:
        page: Playwright Page.
        field_label: Exact label from FormField.label (a11y name).
        search_text: Optional text to type before reading options
                     (for filtered/autocomplete combos).

    Returns:
        List of option text strings in display order.
    """
    combo = page.get_by_role("combobox", name=field_label)
    if not await combo.count():
        logger.warning("scan_combobox_options: no combobox '%s'", field_label)
        return []

    try:
        await combo.click(timeout=5000)
    except Exception as exc:
        logger.debug("scan_combobox_options: click failed for '%s': %s",
                     field_label, exc)
        existing = await _try_clear_combobox(page, field_label)
        if not existing:
            return []
        try:
            await combo.click(timeout=5000)
        except Exception:
            return []

    await asyncio.sleep(0.4)
    if search_text:
        await combo.fill(search_text)
        await asyncio.sleep(0.5)
    else:
        await combo.fill("")
        await asyncio.sleep(0.5)

    cdp = await _get_cdp_session(page)
    if cdp is None:
        options = await _read_options_fallback(page)
        await combo.press("Escape")
        return options

    try:
        result = await cdp.send("Accessibility.getFullAXTree")
        options = [
            n["name"]["value"]
            for n in result.get("nodes", [])
            if n.get("role", {}).get("value") == "option"
            and n.get("name", {}).get("value")
        ]
    except Exception as exc:
        logger.debug("scan_combobox_options: a11y tree failed: %s", exc)
        options = []
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass

    await combo.press("Escape")
    await asyncio.sleep(0.2)

    logger.info("scan_combobox_options('%s'): %d options", field_label[:40], len(options))
    return options


async def _try_clear_combobox(page: Page, field_label: str) -> bool:
    """Try to clear an existing combobox value via its Clear button."""
    try:
        clear_btn = page.get_by_role("button", name=f"Clear {field_label} value")
        if await clear_btn.count():
            await clear_btn.click(timeout=3000)
            await asyncio.sleep(0.3)
            return True
        clear_btn = page.get_by_role("button", name="Clear value")
        if await clear_btn.count():
            for i in range(await clear_btn.count()):
                btn = clear_btn.nth(i)
                bbox = await btn.bounding_box()
                combo = page.get_by_role("combobox", name=field_label)
                combo_bbox = await combo.bounding_box()
                if bbox and combo_bbox and abs(bbox["y"] - combo_bbox["y"]) < 50:
                    await btn.click(timeout=2000)
                    await asyncio.sleep(0.3)
                    return True
    except Exception:
        pass
    return False


async def _read_options_fallback(page: Page) -> list[str]:
    """Read dropdown options via Playwright (works for non-shadow DOM)."""
    options: list[str] = []
    for loc in await page.get_by_role("option").all():
        try:
            text = await loc.text_content()
            if text and text.strip():
                options.append(text.strip())
        except Exception:
            continue
    return options


def best_option_match(
    desired_value: str,
    available_options: list[str],
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> str | None:
    """Find the best matching option for a desired value.

    Match priority:
    1. Exact match (case-insensitive)
    2. Alias match (e.g. "He/Him" → "Him/His/Himself")
    3. Substring containment
    4. None if no match

    Returns the exact option text to click.
    """
    if not available_options:
        return None

    desired_lower = desired_value.strip().lower()
    opts_lower = {o.strip().lower(): o for o in available_options}

    if desired_lower in opts_lower:
        return opts_lower[desired_lower]

    if aliases:
        for alias in aliases.get(desired_lower, ()):
            if alias.lower() in opts_lower:
                return opts_lower[alias.lower()]

    for opt_lower, opt_original in opts_lower.items():
        if desired_lower in opt_lower or opt_lower in desired_lower:
            return opt_original

    return None


def best_range_match(
    numeric_value: float,
    available_options: list[str],
) -> str | None:
    """Find the best range option for a numeric value.

    Handles options like "£40,000 - £50,000" or "25 - 34".
    """
    range_pat = re.compile(
        r"[£$€]?\s*([\d,]+)\s*[-–—]\s*[£$€]?\s*([\d,]+)",
    )
    for opt in available_options:
        m = range_pat.search(opt)
        if m:
            low = float(m.group(1).replace(",", ""))
            high = float(m.group(2).replace(",", ""))
            if low <= numeric_value <= high:
                return opt

    return None


async def select_combobox_option(
    page: Page,
    field_label: str,
    desired_value: str,
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
    numeric_value: float | None = None,
) -> dict[str, Any]:
    """Scan a combobox's options and select the best match.

    Complete workflow: scan → match → click.  Never guesses option text.

    Args:
        page: Playwright Page.
        field_label: Exact a11y label from scan_form().
        desired_value: What we want to select (e.g. "He/Him").
        aliases: Optional {value: (alias1, alias2)} for fuzzy matching.
        numeric_value: If set, try range matching (e.g. salary brackets).

    Returns:
        {"success": bool, "selected": str, "options": list[str]}
    """
    options = await scan_combobox_options(page, field_label)
    if not options:
        return {"success": False, "error": "no options found", "options": []}

    match = best_option_match(desired_value, options, aliases=aliases)

    if match is None and numeric_value is not None:
        match = best_range_match(numeric_value, options)

    if match is None:
        logger.warning(
            "select_combobox_option: no match for '%s' in %s",
            desired_value, options,
        )
        return {"success": False, "error": "no match", "options": options}

    combo = page.get_by_role("combobox", name=field_label)
    await combo.click(timeout=5000)
    await asyncio.sleep(0.3)
    await combo.fill("")
    await asyncio.sleep(0.4)

    try:
        option_loc = page.get_by_role("option", name=match, exact=True)
        await option_loc.click(timeout=5000)
    except Exception:
        try:
            await combo.fill(match[:20])
            await asyncio.sleep(0.5)
            await combo.press("ArrowDown")
            await asyncio.sleep(0.2)
            await combo.press("Enter")
        except Exception as exc:
            return {"success": False, "error": str(exc), "options": options}

    await asyncio.sleep(0.3)
    logger.info("select_combobox_option: '%s' = '%s'", field_label[:40], match)
    return {"success": True, "selected": match, "options": options}
