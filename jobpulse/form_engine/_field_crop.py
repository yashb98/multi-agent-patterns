"""Per-field crop primitives for vision_verifier (S26-follow-up-M).

The verifier needs one image per filled form field showing both the
question label and the entered value so a vision model can compare the
rendered value against the filler's claim. The pre-M pipeline computed
bounding boxes in JS and cropped them out of a full-page screenshot,
which bled JD body text into the crops whenever ``get_by_label`` resolved
to the wrong DOM element (live evidence:
``data/audits/vision_verifier/1778510445_*_composite.webp``).

This module replaces that path with Playwright's
``ElementHandle.screenshot()`` on a dynamically-resolved form-row
container. The resolver walks ancestors of the input element looking for
the smallest visible container that includes both the label and the
input — universally, with no per-platform branches.

All bboxes returned in the sidecar JSON are document-relative so dedup
(Greenhouse demographic-survey duplicate labels collapse to one panel)
is invariant to scroll position.
"""
from __future__ import annotations

import io
import logging
import os
import re
import time
from dataclasses import dataclass, field as dataclass_field
from typing import Any, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle, Locator, Page

# Same required-marker stripper as vision_verifier. Structural normalization
# only — acceptable per "regex only for format normalization, not semantic
# classification" rule.
_REQUIRED_MARKER_RE = re.compile(
    r"\s*(?:\*|\(\s*required\s*\)|\brequired\b|\(\s*\*\s*\))\s*$",
    re.IGNORECASE,
)


def _strip_required_marker(label: str) -> str:
    if not label:
        return label
    return _REQUIRED_MARKER_RE.sub("", label).rstrip()


# Form-row resolution: walk ancestors until we find a container whose own
# bounding box natively encloses BOTH the label and the input. Adapter-
# agnostic; no `if platform == "X"` branches.
#
# Returns the matched DOM element directly (via evaluate_handle) — the
# caller then calls ``.screenshot()`` on the resulting ElementHandle.
# Also returns metadata via a sibling JS call so Python knows which tier
# matched.
#
# Strategy (6-tier cascade):
#   T1. The element handed in (after a visible-walk for degenerate
#       react-select 1x1 inputs). This handles inputs that already
#       represent the visible widget.
#   T2. closest('label')  — handles Ashby-style per-option radios where
#       the input is wrapped in `<label><input> option text</label>`.
#       The label is naturally small (a single option row) and includes
#       both the input + its visible text. Bounded by height ≤ 80 px to
#       avoid pathological multi-line labels.
#   T3. closest('fieldset')  — universal ATS wrapper.
#   T4. closest('[role="group"]')  — ARIA-grouping convention.
#   T5. Ancestor walk for: 40 < offsetHeight ≤ 250 AND offsetWidth > 100
#       AND textContent (normalized) contains the stripped label.
#   T6. Same walk relaxed to drop the label-containment requirement
#       (label may render in a sibling outside the chosen ancestor).
#   Last-resort: visible target itself.
#
# Tier selection always returns the SMALLEST visible container that
# satisfies the tier's constraints — bigger is more bleed-prone.
_FORM_ROW_JS = """
(el, args) => {
  const labelRaw = (args && args.label) || '';
  const labelNorm = labelRaw.toLowerCase().replace(/[\\s\\*\\(\\)\\?\\!\\.]+/g, ' ').trim();

  function rectOf(node) {
    try { return node.getBoundingClientRect(); } catch (e) { return null; }
  }
  function isVisible(node) {
    const r = rectOf(node);
    if (!r) return false;
    return r.width > 0 && r.height > 0;
  }
  function textOf(node) {
    try {
      return (node.innerText || node.textContent || '')
        .toLowerCase()
        .replace(/[\\s\\*\\(\\)\\?\\!\\.]+/g, ' ')
        .trim();
    } catch (e) { return ''; }
  }
  function containsLabel(node) {
    if (!labelNorm) return true;
    return textOf(node).includes(labelNorm);
  }
  function reportTag(node, tier) {
    const r = rectOf(node) || {height: 0, width: 0};
    return { method: tier, tag: node.tagName, role: node.getAttribute('role') || '', height: r.height, width: r.width };
  }

  // Step 0: walk to a visible ancestor if the matched element is degenerate
  // (covers react-select's 1x1 hidden input + similar combobox patterns).
  let visible = el;
  for (let i = 0; i < 5; i++) {
    if (isVisible(visible) && visible.offsetWidth >= 40 && visible.offsetHeight >= 20) break;
    if (!visible.parentElement) break;
    visible = visible.parentElement;
  }

  // T2: closest('label') — handles per-option radios where each option
  // is `<label><input> option text</label>`. Only accept if the label
  // is small (single-row, ≤ 80 px tall) and includes the option text.
  // Without this, Ashby/etc per-option radios fall through to T6
  // element_fallback (clean but label-less).
  const lbl = visible.closest && visible.closest('label');
  if (lbl && isVisible(lbl) && lbl.offsetHeight > 20 && lbl.offsetHeight <= 80
      && lbl.offsetWidth > 100 && containsLabel(lbl)) {
    return { el: lbl, meta: reportTag(lbl, 'option_label') };
  }

  // T3 + T4: closest() ancestor of a known wrapper kind.
  const fieldset = visible.closest && visible.closest('fieldset');
  if (fieldset && isVisible(fieldset) && fieldset.offsetHeight <= 400 && containsLabel(fieldset)) {
    return { el: fieldset, meta: reportTag(fieldset, 'fieldset') };
  }
  const grp = visible.closest && visible.closest('[role="group"]');
  if (grp && isVisible(grp) && grp.offsetHeight <= 400 && containsLabel(grp)) {
    return { el: grp, meta: reportTag(grp, 'role_group') };
  }

  // T4: ancestor walk for the SMALLEST container that fits the bounds AND
  // contains the label.
  let cursor = visible;
  let best = null;
  for (let depth = 0; depth < 12; depth++) {
    if (!cursor) break;
    const oh = cursor.offsetHeight || 0;
    const ow = cursor.offsetWidth || 0;
    if (oh > 40 && oh <= 250 && ow > 100 && containsLabel(cursor)) {
      best = { el: cursor, meta: reportTag(cursor, 'form_row') };
      break;  // smallest = first hit (we walk inside-out)
    }
    cursor = cursor.parentElement;
  }
  if (best) return best;

  // T5: relaxed pass — drop label-containment. Still bounded by height cap.
  cursor = visible;
  for (let depth = 0; depth < 12; depth++) {
    if (!cursor) break;
    const oh = cursor.offsetHeight || 0;
    const ow = cursor.offsetWidth || 0;
    if (oh > 40 && oh <= 250 && ow > 100) {
      return { el: cursor, meta: reportTag(cursor, 'form_row_relaxed') };
    }
    cursor = cursor.parentElement;
  }

  // Last resort: visible target itself.
  return { el: visible, meta: reportTag(visible, 'element_fallback') };
}
"""


@dataclass
class FieldCrop:
    ordinal: int
    label: str
    value: str
    crop_bytes: bytes | None
    resolve_method: str  # option_label | fieldset | role_group | form_row | form_row_relaxed | element_fallback | unresolved
    dedup_with: list[int] = dataclass_field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None


async def _try_locator_cascade(ctx, stripped: str):
    """Mirror vision_verifier._try_locator_cascade — same dynamic primitives."""
    for builder in (
        lambda: ctx.get_by_label(stripped, exact=False).first,
        lambda: ctx.get_by_placeholder(stripped, exact=False).first,
    ):
        try:
            loc = builder()
            if await loc.count():
                return loc
        except Exception:
            continue
    for role in ("textbox", "combobox", "spinbutton", "checkbox", "radio"):
        try:
            loc = ctx.get_by_role(role, name=stripped).first
            if await loc.count():
                return loc
        except Exception:
            continue
    return None


async def _resolve_input_locator(
    page: "Page",
    label: str,
    field_metadata: dict | None,
):
    """Resolve (locator, owner_frame) for a filled label.

    Priority:
      1. field_metadata[label]["locator"]  — actual Playwright Locator
         the filler used at fill time. Pinned to correct frame + shadow.
      2. field_metadata[label]["selector"] — CSS selector string.
      3. Label/placeholder/role cascade on the main page.
      4. Same cascade iterated across child frames (iCIMS iframes).
    """
    stripped = _strip_required_marker(label)

    if field_metadata:
        meta = field_metadata.get(label) or field_metadata.get(stripped)
        if meta:
            attached = meta.get("locator")
            if attached is not None:
                try:
                    if await attached.count():
                        return attached, None
                except Exception:
                    pass
            sel = meta.get("selector")
            if sel:
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        return loc, None
                except Exception:
                    pass

    loc = await _try_locator_cascade(page, stripped)
    if loc is not None:
        return loc, None

    try:
        frames = page.frames
        if not isinstance(frames, list):
            frames = []
    except Exception:
        frames = []
    for frame in frames:
        try:
            if frame is page.main_frame:
                continue
        except Exception:
            pass
        try:
            loc = await _try_locator_cascade(frame, stripped)
            if loc is not None:
                return loc, frame
        except Exception:
            continue

    return None


async def _resolve_row_handle(
    input_locator: "Locator", label: str,
) -> tuple["ElementHandle | None", str]:
    """Resolve the form-row ElementHandle for an input.

    Returns ``(handle, method)``. Handle is the DOM element to call
    ``.screenshot()`` on; method names the tier that matched.
    """
    stripped = _strip_required_marker(label)
    try:
        result_handle = await input_locator.evaluate_handle(
            _FORM_ROW_JS, {"label": stripped},
        )
    except Exception as exc:
        logger.debug("m_probe: evaluate_handle failed for %r: %s", label[:60], exc)
        return None, "unresolved"

    # The JS returns { el, meta }. Pull both out as separate handles so we
    # can use el as ElementHandle and meta as a regular dict.
    try:
        meta_handle = await result_handle.get_property("meta")
        meta = await meta_handle.json_value()
        await meta_handle.dispose()
    except Exception:
        meta = {}
    method = (meta or {}).get("method", "element_fallback")

    try:
        el_handle = await result_handle.get_property("el")
        el = el_handle.as_element()
        if el is None:
            await el_handle.dispose()
            return None, method
        return el, method
    except Exception as exc:
        logger.debug("m_probe: el property failed for %r: %s", label[:60], exc)
        return None, method
    finally:
        try:
            await result_handle.dispose()
        except Exception:
            pass


async def _capture_field_crop(
    page: "Page",
    label: str,
    value: str,
    ordinal: int,
    field_metadata: dict | None,
) -> FieldCrop:
    """Capture one per-field crop using ElementHandle.screenshot()."""
    crop = FieldCrop(
        ordinal=ordinal, label=label, value=value, crop_bytes=None,
        resolve_method="unresolved",
    )

    resolved = await _resolve_input_locator(page, label, field_metadata)
    if resolved is None:
        return crop
    input_locator, _owner_frame = resolved

    row_handle, method = await _resolve_row_handle(input_locator, label)
    crop.resolve_method = method

    target_handle = row_handle
    if target_handle is None:
        # Fall back to the input element itself
        try:
            target_handle = await input_locator.element_handle(timeout=2000)
        except Exception:
            target_handle = None
        crop.resolve_method = "element_fallback" if target_handle is not None else "unresolved"

    if target_handle is None:
        return crop

    # Bbox for dedup keying — use document-relative coords so two ordinals
    # pointing at the same DOM widget yield identical keys regardless of
    # scroll position. Playwright's bounding_box() is viewport-relative, so
    # we compute the doc-relative bbox in JS once.
    try:
        doc_box = await target_handle.evaluate(
            """
            (node) => {
              const r = node.getBoundingClientRect();
              return {
                x: r.left + (window.scrollX || 0),
                y: r.top + (window.scrollY || 0),
                w: r.width, h: r.height,
              };
            }
            """,
        )
        if isinstance(doc_box, dict):
            crop.bbox = (
                round(float(doc_box.get("x", 0)), 1),
                round(float(doc_box.get("y", 0)), 1),
                round(float(doc_box.get("w", 0)), 1),
                round(float(doc_box.get("h", 0)), 1),
            )
    except Exception:
        pass

    try:
        await target_handle.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass

    try:
        crop.crop_bytes = await target_handle.screenshot(
            type="png", animations="disabled", timeout=3000,
        )
    except Exception as exc:
        logger.debug("m_probe: screenshot failed for %r (%s): %s", label[:60], method, exc)
        # Try input as last resort if we were on a row
        if target_handle is not None and method != "element_fallback":
            try:
                input_handle = await input_locator.element_handle(timeout=2000)
                if input_handle is not None:
                    crop.crop_bytes = await input_handle.screenshot(
                        type="png", animations="disabled", timeout=2000,
                    )
                    crop.resolve_method = "element_fallback"
                    try:
                        await input_handle.dispose()
                    except Exception:
                        pass
            except Exception as exc2:
                logger.debug("m_probe: input-fallback screenshot failed: %s", exc2)

    try:
        await target_handle.dispose()
    except Exception:
        pass

    return crop


def _dedup_crops(crops: list[FieldCrop]) -> list[FieldCrop]:
    """Collapse crops with the same bbox into a single panel.

    Greenhouse-style required + optional copies of the same widget share
    bboxes; we keep the first and record collapsed ordinals in
    ``dedup_with``.
    """
    by_bbox: dict[tuple, FieldCrop] = {}
    out: list[FieldCrop] = []
    for c in crops:
        if c.bbox is None or c.crop_bytes is None:
            out.append(c)
            continue
        key = c.bbox
        if key in by_bbox:
            by_bbox[key].dedup_with.append(c.ordinal)
        else:
            by_bbox[key] = c
            out.append(c)
    return out


def _composite_font():
    try:
        from PIL import ImageFont
        for candidate in (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ):
            try:
                return ImageFont.truetype(candidate, 16)
            except Exception:
                continue
        return ImageFont.load_default()
    except Exception:
        return None


def _build_composite(crops: list[FieldCrop]) -> bytes | None:
    """Vertically tile crops into one WebP composite with ordinal captions.

    Mirrors ``vision_verifier._build_composite`` output shape so the
    Phase-1 promotion drops in without disturbing verdict-parsing.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        logger.debug("m_probe: PIL unavailable: %s", exc)
        return None

    panels = [c for c in crops if c.crop_bytes is not None]
    if not panels:
        return None

    pil_crops: list[tuple[FieldCrop, Any]] = []
    max_w = 0
    for c in panels:
        try:
            img = Image.open(io.BytesIO(c.crop_bytes))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
        except Exception as exc:
            logger.debug("m_probe: PIL decode failed for crop %d: %s", c.ordinal, exc)
            continue
        pil_crops.append((c, img))
        if img.width > max_w:
            max_w = img.width

    if not pil_crops:
        return None

    caption_h = 28
    gap = 6
    total_h = sum(img.height + caption_h + gap for _, img in pil_crops)
    composite = Image.new("RGB", (max_w + 6, total_h + 6), color=(255, 255, 255))
    draw = ImageDraw.Draw(composite)
    font = _composite_font()

    y = 3
    for c, img in pil_crops:
        marker_parts = [f"[{c.ordinal:02d}]"]
        if c.dedup_with:
            extra = ",".join(f"{o:02d}" for o in sorted(c.dedup_with))
            marker_parts.append(f"+[{extra}]")
        marker_parts.append(f"({c.resolve_method})")
        caption = " ".join(marker_parts)
        draw.rectangle((0, y, max_w + 6, y + caption_h), fill=(228, 238, 255))
        if font is not None:
            try:
                draw.text((8, y + 4), caption, fill=(20, 60, 140), font=font)
            except Exception:
                pass
        y += caption_h
        composite.paste(img, (3, y))
        draw.rectangle(
            (2, y - 1, 3 + img.width + 1, y + img.height + 1),
            outline=(220, 30, 30), width=1,
        )
        y += img.height + gap

    buf = io.BytesIO()
    composite.save(buf, format="WEBP", lossless=True, method=6)
    return buf.getvalue()


async def probe_page(
    page: "Page",
    filled_mapping: dict[str, str],
    *,
    field_metadata: dict[str, dict] | None = None,
) -> tuple[bytes | None, list[FieldCrop]]:
    """Capture per-field crops + tile a composite for the current page.

    Returns ``(composite_bytes, crops)``. ``composite_bytes`` is None
    when nothing could be captured. ``crops`` includes one entry per
    claim row (post-dedup by bbox).
    """
    if not filled_mapping:
        return None, []

    crops: list[FieldCrop] = []
    for idx, (label, value) in enumerate(filled_mapping.items(), start=1):
        crop = await _capture_field_crop(
            page, label, str(value), idx, field_metadata,
        )
        crops.append(crop)

    deduped = _dedup_crops(crops)
    composite = _build_composite(deduped)
    return composite, deduped


async def save_probe_artifact(
    page: "Page",
    filled_mapping: dict[str, str],
    *,
    artifact_dir: str,
    adapter_key: str,
    page_url: str,
    field_metadata: dict[str, dict] | None = None,
) -> str | None:
    """Run a probe and persist composite + sidecar JSON for offline review."""
    import json

    composite, crops = await probe_page(
        page, filled_mapping, field_metadata=field_metadata,
    )
    if not crops:
        return None

    os.makedirs(artifact_dir, exist_ok=True)
    ts = int(time.time())
    base = os.path.join(artifact_dir, f"{ts}_{adapter_key}")

    if composite is not None:
        with open(f"{base}.webp", "wb") as fh:
            fh.write(composite)

    sidecar = {
        "ts": ts,
        "page_url": page_url,
        "adapter_key": adapter_key,
        "panels_total": sum(1 for c in crops if c.crop_bytes is not None),
        "panels_unresolved": sum(1 for c in crops if c.crop_bytes is None),
        "composite_path": (
            os.path.basename(f"{base}.webp") if composite is not None else None
        ),
        "claims": [
            {
                "ordinal": c.ordinal,
                "label": c.label,
                "value": c.value,
                "resolve_method": c.resolve_method,
                "bbox": list(c.bbox) if c.bbox else None,
                "dedup_with": c.dedup_with,
                "captured": c.crop_bytes is not None,
            }
            for c in crops
        ],
    }
    with open(f"{base}.json", "w") as fh:
        import json as _json
        _json.dump(sidecar, fh, indent=2)
    return base
