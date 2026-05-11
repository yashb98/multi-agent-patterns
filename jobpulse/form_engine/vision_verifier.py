"""Vision-canonical form verification + correction (Slice S26).

Treats the rendered form as the source of truth. After the form-filler
finishes a page, this module screenshots the form, asks Moonshot vision
what is actually shown for each filled field, compares against the
filler's claim, and (when correction is enabled) re-fills mismatches.

Why this exists
---------------
Metadata-pipeline patches (S22 cache cross-question, S24 options propagation,
S25 help-text missing, TP-35 third-person prompts) keep surfacing new
propagation gaps. The architectural answer is to stop reconstructing what
the form shows and just look at it. See
``docs/audits/2026-05-10-semantic-audit-verified.md`` ("Live verification
of S21" + TP-31/32/33/34/35).

Invariants
----------
- Vision sees only the ``claimed_value`` + ``page screenshot``. The profile
  is intentionally NOT in the prompt — the failure mode this layer fixes
  is "metadata wrong about what's on the page", so re-using profile data
  would just reproduce upstream failures.
- One vision call per page (not per field) — keeps cost under the
  $0.05/apply ceiling.
- Kill switch: ``VISION_VERIFICATION_ENABLED`` env var. Off by default.
- Best-effort: any failure (timeout, parse error, rate limit) returns
  ``vision_unavailable`` and never breaks the apply.
- Observe-only by default. ``correction_enabled`` is a separate flag so
  Phase B doesn't auto-run before Phase A has demonstrated ≥95% read
  accuracy on real screenshots (Outcome 1 of the S26 spec).
- Every verdict produces one row in ``data/semantic_decisions.db`` with
  ``decision_type='vision_verification'``.
- Successful corrections route through ``ai_assist_logger`` so the
  upstream caches (``screening_semantic_cache``, ``field_corrections.db``,
  ``AgentRulesDB``) are invalidated/updated — OPRAL rule 5: if it can
  recur, the fix is incomplete.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Awaitable, Callable, TYPE_CHECKING
from urllib.parse import urlparse

from shared.agents import get_openai_client
from shared.logging_config import get_logger
from shared.semantic_decisions import record_decision

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

# Default to the newer Kimi K2.6 vision model. Per Kimi docs
# (https://platform.kimi.ai/docs/guide/use-kimi-vision-model) it is the
# current recommended vision model. Live evidence during S26 RUN3/4:
# `moonshot-v1-32k-vision-preview` was sustained-overloaded (429 across
# 8+ smoke probes), while `kimi-k2.6` responded in <5s — they're served
# by different engines and have independent throttling.
_VISION_MODEL = os.environ.get(
    "VISION_VERIFIER_MODEL",
    os.environ.get("VISION_MODEL", "kimi-k2.6"),
)
_CALL_SITE = "jobpulse/form_engine/vision_verifier.py:verify_form_page"
_AGENT_NAME = "vision_verifier"
_MAX_CORRECTION_RETRIES = 1  # observe → correct → verify once; no infinite loops

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ARTIFACT_DIR = os.path.join(_PROJECT_DIR, "data", "audits", "vision_verifier")


def _is_enabled() -> bool:
    return os.environ.get("VISION_VERIFICATION_ENABLED", "").lower() in {
        "1", "true", "yes", "on",
    }


def _correction_enabled() -> bool:
    return os.environ.get("VISION_VERIFICATION_CORRECT", "").lower() in {
        "1", "true", "yes", "on",
    }


@dataclass
class FieldVerdict:
    label: str
    claimed_value: str
    observed_value: str
    matches_claim: bool
    contradicts_help_text: bool
    reason: str
    tier_reached: str  # passed | mismatch_detected | skipped_no_expected_value | vision_unavailable | correction_succeeded | correction_failed


@dataclass
class VerifierResult:
    verdicts: list[FieldVerdict] = dataclass_field(default_factory=list)
    mismatches: int = 0
    corrections_applied: int = 0
    corrections_failed: int = 0
    cost_usd: float = 0.0
    elapsed_ms: float = 0.0
    vision_unavailable: bool = False
    error: str | None = None
    artifact_path: str | None = None


# Kimi vision docs (https://platform.kimi.ai/docs/guide/use-kimi-vision-model)
# state: image resolution should not exceed 4K (4096 × 2160) for optimal
# performance, and accepted formats are PNG, JPEG, WebP, GIF. WebP is the
# format that preserves screenshot text best at a given payload size —
# it uses content-aware block prediction internally (closer to a learned
# compressor than JPEG's static DCT) and supports a true lossless mode,
# so we don't lose label-edge sharpness on small fonts the way a JPEG q82
# or a Lanczos downscale would.
_MAX_LONG_EDGE = int(os.environ.get("VISION_VERIFIER_MAX_EDGE", "4096"))
_MAX_SHORT_EDGE = int(os.environ.get("VISION_VERIFIER_MAX_SHORT_EDGE", "2160"))
_WEBP_LOSSLESS = os.environ.get("VISION_VERIFIER_WEBP_LOSSLESS", "1").lower() in {
    "1", "true", "yes", "on",
}
_WEBP_QUALITY = int(os.environ.get("VISION_VERIFIER_WEBP_QUALITY", "92"))


# Per-field margin (px) around each crop. Auto-shrinks to ``_MIN_CROP_MARGIN``
# if the composite would exceed ``_COMPOSITE_HEIGHT_CAP``.
_CROP_MARGIN = int(os.environ.get("VISION_VERIFIER_CROP_MARGIN", "12"))
_MIN_CROP_MARGIN = 4
_COMPOSITE_HEIGHT_CAP = int(os.environ.get("VISION_VERIFIER_HEIGHT_CAP", "4000"))
# Caption strip placed above each crop carrying the ordinal label so vision
# can map cleanly back to the prompt's claim list even when the crop's own
# label region is partially clipped.
_CAPTION_STRIP_PX = 28

# Required-marker stripper (same shape as
# native_form_filler._strip_required_marker). Structural normalization;
# acceptable per "regex only for format normalization, not semantic
# classification" rule.
_REQUIRED_MARKER_RE = re.compile(
    r"\s*(?:\*|\(\s*required\s*\)|\brequired\b|\(\s*\*\s*\))\s*$",
    re.IGNORECASE,
)


def _strip_required_marker(label: str) -> str:
    if not label:
        return label
    return _REQUIRED_MARKER_RE.sub("", label).rstrip()


# JS that computes the document-relative bounding box for a form-input
# element by unioning the input rect with its associated <label> rect and
# any aria-describedby help-text rect. Returns ``null`` if no rect could
# be measured (off-screen / display:none).
_FIELD_BBOX_JS = """
(el) => {
  // Discipline (S26-follow-up-K post-review): only TRUST direct DOM
  // label associations — el.labels[0] (set by <label for=>) and
  // aria-labelledby. The speculative parentElement walk had a high
  // false-positive rate on free-text textareas without proper labels
  // (Anthropic free-text fields → bbox union pulled in JD body text
  // from the nearest <h2>; ~14/19 panels in the live Anthropic
  // artifact contained "Diversity & inclusion" / "Your safety matters"
  // section headings instead of field labels). Without the walk,
  // unlabeled fields get an input-only crop — small but clean.

  // Walk up to a visibly-rendered ancestor when the input element
  // itself is degenerate (React-select / shadow-DOM combobox patterns
  // use a 1×1 hidden <input> with the actual visible widget rendered
  // as a sibling DIV). Without this, the verifier crops a 1×1 region
  // whose pixels in the full-page screenshot happen to be whatever JD
  // body text sat behind the hidden input.
  let target = el;
  for (let depth = 0; depth < 5; depth++) {
    const r = target.getBoundingClientRect();
    if (r.width >= 40 && r.height >= 20) break;
    if (!target.parentElement) break;
    target = target.parentElement;
  }
  const ir = target.getBoundingClientRect();
  let lbl = null;
  try { if (el.labels && el.labels.length) lbl = el.labels[0]; } catch (e) {}
  if (!lbl) {
    const aby = el.getAttribute('aria-labelledby');
    if (aby) {
      for (const id of aby.split(/\\s+/)) {
        const e = document.getElementById(id);
        if (e) { lbl = e; break; }
      }
    }
  }
  let lr = lbl ? lbl.getBoundingClientRect() : null;
  // Reject labels that aren't actually field labels:
  //   1. Wrapper labels that fully contain the input (Greenhouse free-
  //      text textareas have <label> blocks wrapping JD body + the
  //      textarea — label.height is several hundred px and includes
  //      paragraph text).
  //   2. Labels far away from the input top (> 80 px gap on both
  //      sides — not adjacent, so probably not associated).
  //   3. Labels taller than 60 px (real field labels are 1–2 lines;
  //      tall labels are wrappers or section blocks).
  if (lr) {
    const wraps_input = (
      lr.top <= ir.top + 2 && lr.bottom >= ir.bottom - 2
      && lr.left <= ir.left + 2 && lr.right >= ir.right - 2
    );
    const too_tall = lr.height > 60;
    const far_above = (ir.top - lr.bottom) > 80;
    const far_below = (lr.top - ir.bottom) > 80;
    if (wraps_input || too_tall || far_above || far_below) {
      lr = null;
    }
  }
  let dr = null;
  const dby = el.getAttribute('aria-describedby');
  if (dby) {
    for (const id of dby.split(/\\s+/)) {
      const e = document.getElementById(id);
      if (e) {
        const r = e.getBoundingClientRect();
        // Help-text rect must be within 100 px of input — otherwise
        // it's likely a global help block, not field-scoped.
        if (r.width > 0 && r.height > 0
            && Math.abs(r.top - ir.bottom) < 100
            && Math.abs(ir.top - r.bottom) < 100) {
          dr = r;
          break;
        }
      }
    }
  }
  const rects = [ir, lr, dr].filter(r => r && r.width > 0 && r.height > 0);
  if (rects.length === 0) return null;
  const x_min = Math.min(...rects.map(r => r.left));
  const y_min = Math.min(...rects.map(r => r.top));
  const x_max = Math.max(...rects.map(r => r.right));
  const y_max = Math.max(...rects.map(r => r.bottom));
  // Hard height cap — defends against pathological textareas / wrapper
  // divs whose computed bbox spans an entire form section. Crop the
  // input + ≤ 250 px context, no more.
  const h_raw = y_max - y_min;
  const h = Math.min(h_raw, 250);
  return {
    x: Math.max(0, x_min + window.scrollX),
    y: Math.max(0, y_min + window.scrollY),
    width: Math.max(1, x_max - x_min),
    height: Math.max(1, h)
  };
}
"""


def _encode_webp(img) -> bytes:
    import io

    buf = io.BytesIO()
    if _WEBP_LOSSLESS:
        img.save(buf, format="WEBP", lossless=True, method=6)
    else:
        img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=6)
    return buf.getvalue()


def _compress_for_vision(raw_png: bytes) -> bytes:
    """Encode a single PNG to WebP, downscaling only if width exceeds Kimi's
    4K rec. Used by callers that already have a pre-sized image (e.g. an
    individual chunk handed to ``_call_vision`` directly).

    Pre-S26-follow-up-K this function vertically chunked tall screenshots.
    The K rewrite folded chunking into the field-crop composite pipeline,
    so the chunking branch was removed (`_prepare_for_vision` no longer
    exists). Kept as a single-blob primitive for tests + direct callers.
    """
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(raw_png))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if w > _MAX_LONG_EDGE:
            scale = _MAX_LONG_EDGE / float(w)
            img = img.resize(
                (_MAX_LONG_EDGE, max(1, int(h * scale))), Image.LANCZOS,
            )
        return _encode_webp(img)
    except Exception as exc:
        logger.debug("vision_verifier: compress fallback to raw PNG: %s", exc)
        return raw_png


async def _full_page_screenshot(page: "Page") -> bytes:
    """One screenshot of the whole scrollable form, in document coords.

    Document-relative coords (from ``getBoundingClientRect() +
    window.scrollX/Y``) line up 1:1 with the pixels of a full_page
    screenshot, so each field's crop region is computable without
    per-element scroll-into-view + per-element screenshot RPCs.
    """
    return await page.screenshot(type="png", full_page=True)


async def _resolve_label_locator(
    page: "Page",
    label: str,
    field_metadata: dict | None,
):
    """Locator resolution cascade mirroring `_fill_by_label`'s primitives.

    Returns a Playwright Locator (with `.count() > 0`) or ``None``.
    The mirroring is intentional: the verifier's bbox extraction succeeds
    exactly when the filler could have re-resolved the same field, so
    unresolvable claims are also unverifiable claims — surfacing them as
    `vision_unavailable` is the right signal.
    """
    stripped = _strip_required_marker(label)

    if field_metadata:
        meta = field_metadata.get(label) or field_metadata.get(stripped)
        if meta:
            sel = meta.get("selector")
            if sel:
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        return loc
                except Exception:
                    pass
            attached = meta.get("locator")
            if attached is not None:
                try:
                    if await attached.count():
                        return attached
                except Exception:
                    pass

    for builder in (
        lambda: page.get_by_label(stripped, exact=False).first,
        lambda: page.get_by_placeholder(stripped, exact=False).first,
    ):
        try:
            loc = builder()
            if await loc.count():
                return loc
        except Exception:
            continue

    for role in ("textbox", "combobox", "spinbutton", "checkbox", "radio"):
        try:
            loc = page.get_by_role(role, name=stripped).first
            if await loc.count():
                return loc
        except Exception:
            continue
    return None


async def _extract_field_bboxes(
    page: "Page",
    claim_rows: list[tuple[str, str]],
    field_metadata: dict | None,
) -> list[dict]:
    """Resolve a locator per filled label, extract document-relative bbox.

    Returns one entry per claim in the same order, each with keys:
      - ``ordinal`` (1-indexed)
      - ``label`` (original, including any required marker)
      - ``value`` (claimed value)
      - ``bbox`` (dict {x,y,width,height}) or ``None`` if unresolvable
    """
    entries: list[dict] = []
    for idx, (label, value) in enumerate(claim_rows, start=1):
        entry = {
            "ordinal": idx,
            "label": label,
            "value": value,
            "bbox": None,
        }
        locator = await _resolve_label_locator(page, label, field_metadata)
        if locator is None:
            entries.append(entry)
            continue
        try:
            box = await locator.evaluate(_FIELD_BBOX_JS)
        except Exception as exc:
            logger.debug("vision_verifier: bbox js failed for %r: %s", label[:60], exc)
            box = None
        if isinstance(box, dict) and all(k in box for k in ("x", "y", "width", "height")):
            entry["bbox"] = {
                "x": float(box["x"]),
                "y": float(box["y"]),
                "width": float(box["width"]),
                "height": float(box["height"]),
            }
        entries.append(entry)
    return entries


def _composite_font():
    """Best-effort font for ordinal markers. ImageFont.load_default works
    everywhere but the glyph is small; we try a few common system fonts
    first."""
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


def _build_composite(
    screenshot_png: bytes,
    bbox_entries: list[dict],
) -> tuple[bytes | None, list[dict]]:
    """Crop each filled field from the full-page screenshot, stamp an
    ordinal caption, vertically tile into one WebP composite.

    Returns ``(composite_bytes, panels)`` where ``panels`` is the subset of
    ``bbox_entries`` that produced a valid crop. ``composite_bytes`` is
    ``None`` if no field could be cropped (caller falls back to the whole
    screenshot via ``_compress_for_vision``).
    """
    try:
        import io

        from PIL import Image, ImageDraw
    except Exception as exc:
        logger.debug("vision_verifier: PIL unavailable: %s", exc)
        return None, []

    try:
        img = Image.open(io.BytesIO(screenshot_png))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    except Exception as exc:
        logger.debug("vision_verifier: screenshot decode failed: %s", exc)
        return None, []

    img_w, img_h = img.size
    if img_w == 0 or img_h == 0:
        return None, []

    panels_with_bbox = [e for e in bbox_entries if e.get("bbox")]
    if not panels_with_bbox:
        return None, []

    panels_with_bbox.sort(key=lambda e: (e["bbox"]["y"], e["bbox"]["x"]))

    # First pass with full margin; shrink if the projected composite would
    # exceed the height cap (rarely fires on Greenhouse forms).
    margin = _CROP_MARGIN
    while True:
        total_h = 0
        for e in panels_with_bbox:
            b = e["bbox"]
            crop_h = max(1, int(b["height"] + 2 * margin))
            total_h += crop_h + _CAPTION_STRIP_PX + 6  # 6 px gap
        if total_h <= _COMPOSITE_HEIGHT_CAP or margin <= _MIN_CROP_MARGIN:
            break
        margin = max(_MIN_CROP_MARGIN, margin - 4)

    crops: list[dict] = []
    max_w = 0
    for e in panels_with_bbox:
        b = e["bbox"]
        x0 = max(0, int(b["x"] - margin))
        y0 = max(0, int(b["y"] - margin))
        x1 = min(img_w, int(b["x"] + b["width"] + margin))
        y1 = min(img_h, int(b["y"] + b["height"] + margin))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        crop = img.crop((x0, y0, x1, y1))
        crops.append({"entry": e, "crop": crop})
        if crop.width > max_w:
            max_w = crop.width

    if not crops:
        return None, []
    if max_w > _MAX_LONG_EDGE:
        # Edge-case: a single field wider than the Kimi 4K rec — scale just
        # the offending crops, not the whole composite, so non-wide fields
        # stay 1:1.
        for c in crops:
            if c["crop"].width > _MAX_LONG_EDGE:
                scale = _MAX_LONG_EDGE / float(c["crop"].width)
                nw = _MAX_LONG_EDGE
                nh = max(1, int(c["crop"].height * scale))
                c["crop"] = c["crop"].resize((nw, nh), Image.LANCZOS)
        max_w = min(max_w, _MAX_LONG_EDGE)

    total_h = sum(c["crop"].height + _CAPTION_STRIP_PX + 6 for c in crops)
    composite = Image.new("RGB", (max_w + 6, total_h + 6), color=(255, 255, 255))
    draw = ImageDraw.Draw(composite)
    font = _composite_font()

    y_cursor = 3
    panels_meta: list[dict] = []
    for c in crops:
        entry = c["entry"]
        crop = c["crop"]
        ordinal = entry["ordinal"]
        # Caption strip carrying the ordinal marker — kept short so vision
        # can read it without OCR drama. Stripping label here is fine:
        # the prompt already enumerates the full label per ordinal.
        caption = f"[{ordinal:02d}]"
        # Caption strip is pale-blue band so vision sees a clear panel
        # boundary even when crops have similar backgrounds.
        draw.rectangle(
            (0, y_cursor, max_w + 6, y_cursor + _CAPTION_STRIP_PX),
            fill=(228, 238, 255),
        )
        if font is not None:
            try:
                draw.text(
                    (8, y_cursor + 4),
                    caption,
                    fill=(20, 60, 140),
                    font=font,
                )
            except Exception:
                pass
        y_cursor += _CAPTION_STRIP_PX
        composite.paste(crop, (3, y_cursor))
        # Thin red border around each crop so vision can clearly tell
        # where one field ends and the next begins.
        draw.rectangle(
            (2, y_cursor - 1, 3 + crop.width + 1, y_cursor + crop.height + 1),
            outline=(220, 30, 30),
            width=1,
        )
        y_cursor += crop.height + 6
        panels_meta.append({**entry, "ordinal": ordinal})

    try:
        composite_bytes = _encode_webp(composite)
    except Exception as exc:
        logger.debug("vision_verifier: composite encode failed: %s", exc)
        return None, []

    return composite_bytes, panels_meta


def _build_prompt(claim_rows: list[tuple[str, str]], *, ordinals: bool = True) -> str:
    """Build the vision prompt. Only claim mapping + instructions — no profile.

    The whole point of vision verification is that the rendered form (in
    the screenshot) is the source of truth. Adding profile data into the
    prompt would re-introduce the metadata-pipeline failures this layer
    is meant to bypass.

    When ``ordinals=True`` (default, the S26-follow-up-K composite path),
    each claim is prefixed by a 2-digit ordinal matching the caption strip
    above each field crop in the composite image. The vision model can
    then key its verdicts by ordinal instead of full label text — robust
    against duplicate labels, trailing required markers, and long
    question text.
    """
    if ordinals:
        lines = [
            f'  [{i:02d}] "{label}": "{value}"'
            for i, (label, value) in enumerate(claim_rows, start=1)
        ]
    else:
        lines = [f'  - "{label}": "{value}"' for label, value in claim_rows]
    claim_block = "\n".join(lines) if lines else "  (no fields claimed filled)"
    if ordinals:
        return (
            "You are auditing a job-application form. The image below is a "
            "TILED EVIDENCE SHEET — vertically stacked CROPS of individual "
            "form fields the candidate's automated filler just finished "
            "filling. Each crop is one form field showing its label + filled "
            "value (and any help-text near the field). Crops are separated "
            "by thin red borders, and each crop has a pale-blue CAPTION "
            "STRIP directly above it carrying an ordinal marker like [01], "
            "[02], [03] ... read those marks to map a crop to the claim "
            "list below.\n\n"
            "Claimed fills (ordinal → label → value):\n"
            f"{claim_block}\n\n"
            "For each ordinal you can locate in the image, return one "
            "verdict object with these keys:\n"
            "  - ordinal: the integer marker shown above the crop (1..N)\n"
            "  - label: the exact label string from the claim list at that "
            "ordinal\n"
            '  - observed_value: the value visibly entered in the field, '
            'or "<empty>" if blank, or "<not found>" if you cannot locate '
            "the value within that crop\n"
            "  - matches_claim: true if observed_value semantically matches "
            "the claimed value (case/whitespace differences are fine), "
            "false otherwise\n"
            "  - contradicts_help_text: true ONLY if there is help-text or "
            "a description visible near the field whose meaning is "
            "contradicted by the claimed value (e.g. help-text says 'select "
            "Yes' and claim is 'No'), false otherwise\n"
            "  - reason: one short sentence explaining your verdict\n\n"
            'Return STRICT JSON only, in this shape: {"verdicts": [{...}, '
            '{...}]}. No prose, no markdown fences. Include one verdict per '
            "claim ordinal — if a particular crop is missing from the image, "
            'set observed_value to "<not found>".'
        )
    return (
        "You are auditing a job-application form. The screenshot below is the "
        "form a candidate's automated filler just finished filling. Below are "
        "the labels and values the filler CLAIMS it entered. Look at the "
        "screenshot and report what is ACTUALLY rendered in each field.\n\n"
        "Claimed fills:\n"
        f"{claim_block}\n\n"
        "For each claimed field that you can locate in the screenshot, return "
        "an object with these keys:\n"
        "  - label: the exact label as given above\n"
        '  - observed_value: the value visibly entered in the field, or "<empty>" if blank, or "<not found>" if you cannot locate the field\n'
        "  - matches_claim: true if observed_value semantically matches the claimed value (case/whitespace differences are fine), false otherwise\n"
        "  - contradicts_help_text: true ONLY if there is help-text or a description visible near the field whose meaning is contradicted by the claimed value (e.g. help-text says 'You must have AI experience' and claim is 'No'), false otherwise\n"
        "  - reason: one short sentence explaining your verdict\n\n"
        'Return STRICT JSON only, in this shape: {"verdicts": [{...}, {...}]}. '
        "No prose, no markdown fences. Omit fields you cannot locate."
    )


def _safe_json(raw: str) -> dict | None:
    """Parse vision output, stripping common code fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        try:
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        except Exception:
            return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _save_artifact(
    *,
    composite: bytes | None,
    fallback_screenshot: bytes | None,
    domain: str,
    page_num: int,
    verdicts: list[FieldVerdict],
    panels: list[dict],
    page_url: str,
    platform: str,
    cost_usd: float,
    elapsed_ms: float,
    chunks_used: int,
    composite_layout: dict | None,
) -> str | None:
    """Persist the image + verdicts JSON for replayable human spot-check.

    Two image blobs may be saved:
      - ``<base>_composite.webp`` — the field-evidence sheet vision saw
        (S26-follow-up-K). One per page, NEVER chunked.
      - ``<base>_fallback.png`` — only when composite couldn't be built and
        the verifier sent a whole-page screenshot to vision instead.

    The JSON sidecar records ``chunks_used`` so the SHIPPED audit doc can
    machine-check Outcome 1 ("single-shot") without re-reading the binary.
    """
    if os.environ.get("VISION_VERIFIER_SAVE_ARTIFACTS", "1").lower() in {"0", "false", "no"}:
        return None
    try:
        os.makedirs(_ARTIFACT_DIR, exist_ok=True)
        ts = int(time.time())
        safe_domain = domain.replace("/", "_") or "unknown"
        base = os.path.join(_ARTIFACT_DIR, f"{ts}_{safe_domain}_p{page_num}")
        composite_path = None
        fallback_path = None
        if composite is not None:
            composite_path = f"{base}_composite.webp"
            with open(composite_path, "wb") as fh:
                fh.write(composite)
        if fallback_screenshot is not None:
            ext = "png" if _mime_for(fallback_screenshot) == "image/png" else "webp"
            fallback_path = f"{base}_fallback.{ext}"
            with open(fallback_path, "wb") as fh:
                fh.write(fallback_screenshot)
        payload = {
            "ts": ts,
            "page_url": page_url,
            "domain": domain,
            "platform": platform,
            "page_num": page_num,
            "cost_usd": cost_usd,
            "elapsed_ms": elapsed_ms,
            "chunks_used": chunks_used,
            "composite_path": (
                os.path.basename(composite_path) if composite_path else None
            ),
            "fallback_path": (
                os.path.basename(fallback_path) if fallback_path else None
            ),
            "composite_layout": composite_layout,
            "panels": [
                {
                    "ordinal": p.get("ordinal"),
                    "label": p.get("label"),
                    "value": p.get("value"),
                    "bbox": p.get("bbox"),
                }
                for p in panels
            ],
            "verdicts": [
                {
                    "label": v.label,
                    "claimed_value": v.claimed_value,
                    "observed_value": v.observed_value,
                    "matches_claim": v.matches_claim,
                    "contradicts_help_text": v.contradicts_help_text,
                    "reason": v.reason,
                    "tier_reached": v.tier_reached,
                }
                for v in verdicts
            ],
        }
        with open(f"{base}.json", "w") as fh:
            json.dump(payload, fh, indent=2)
        return base
    except Exception as exc:
        logger.debug("vision_verifier: artifact save failed: %s", exc)
        return None


def _classify_tier(
    matches_claim: bool, contradicts_help_text: bool, observed_value: str
) -> str:
    if observed_value == "<not found>":
        return "skipped_no_expected_value"
    if matches_claim and not contradicts_help_text:
        return "passed"
    return "mismatch_detected"


def _mime_for(blob: bytes) -> str:
    if blob.startswith(b"\x89PNG"):
        return "image/png"
    if blob.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob.startswith(b"GIF8"):
        return "image/gif"
    return "image/png"


# Per-vision-call timeout (s). Moonshot's vision queue intermittently
# stalls past 60 s even on small composites (S26-follow-up-K live run
# evidence: 19/19 fields cropped to a 30 KB WebP and the call still hit
# the 180 s read timeout). A 90 s ceiling fails-fast on those stalls,
# letting the verifier surface ``vision_unavailable`` quickly instead of
# burning 9+ minutes on compound retries.
_VISION_CALL_TIMEOUT_S = float(os.environ.get("VISION_VERIFIER_CALL_TIMEOUT_S", "90"))


async def _call_vision(
    screenshot_png: bytes, prompt: str
) -> tuple[str | None, float, float]:
    """Single Moonshot vision call with bounded retry on transient errors.

    Returns ``(raw_text, cost_usd, elapsed_ms)`` on success.
    Returns ``(None, 0.0, elapsed)`` after exhausting retries — caller
    maps to ``vision_unavailable``. Cost is best-effort via
    ``record_openai_usage``.

    Retry policy (S26-follow-up-K tightening): TWO total attempts, with
    a 4 s backoff between them, and ``max_retries=0`` on the OpenAI
    client so its built-in 2× retry doesn't compound with this loop.
    Per-attempt timeout is ``_VISION_CALL_TIMEOUT_S``. Worst case wall
    clock: ``2 × _VISION_CALL_TIMEOUT_S + 4`` s (vs 36 minutes under the
    pre-K compound-retry stack).

    OPRAL rule 5: if a Moonshot stall can recur (it can, daily), the
    fix is "fail fast and emit vision_unavailable", not "wait longer".
    """
    started = time.monotonic()
    mime = _mime_for(screenshot_png)
    b64_image = base64.b64encode(screenshot_png).decode("ascii")
    try:
        client = get_openai_client(timeout=_VISION_CALL_TIMEOUT_S)
        # OpenAI client's default max_retries=2 would compound with the
        # backoff loop below into up to 4 × 3 × timeout = 36 min worst
        # case (S26-follow-up-K live evidence: Anthropic run ate 9 min
        # before falling through to vision_unavailable). Disable it so
        # this loop is the only retry layer.
        try:
            client.max_retries = 0
        except Exception:
            pass
    except Exception as exc:
        logger.warning("vision_verifier: client init failed: %s", exc)
        return None, 0.0, (time.monotonic() - started) * 1000

    backoffs = [4.0]
    response = None
    last_exc: Exception | None = None
    for attempt, backoff in enumerate(backoffs + [0.0]):
        try:
            response = client.chat.completions.create(
                model=_VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_image}"},
                        },
                    ],
                }],
            )
            break
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            # Live evidence: openai SDK raises ``APITimeoutError`` with
            # str() = "Request timed out." — note the absence of the
            # word "timeout" with the literal substring. Add "timed out"
            # to the transient list so kimi stalls retry once.
            is_transient = (
                "429" in msg or "overloaded" in msg or "rate" in msg
                or "timeout" in msg or "timed out" in msg
                or "temporarily" in msg
            )
            if not is_transient or attempt >= len(backoffs):
                logger.warning(
                    "vision_verifier: vision call failed (attempt %d, transient=%s): %s",
                    attempt + 1, is_transient, exc,
                )
                return None, 0.0, (time.monotonic() - started) * 1000
            logger.info(
                "vision_verifier: transient error on attempt %d (%s) — retrying in %.1fs",
                attempt + 1, exc.__class__.__name__, backoff,
            )
            await asyncio.sleep(backoff)

    if response is None:
        logger.warning(
            "vision_verifier: exhausted retries: %s", last_exc,
        )
        return None, 0.0, (time.monotonic() - started) * 1000

    cost = 0.0
    try:
        from shared.cost_tracker import record_openai_usage

        record_openai_usage(response, agent_name=_AGENT_NAME, model_hint=_VISION_MODEL)
        usage = getattr(response, "usage", None)
        prompt_t = getattr(usage, "prompt_tokens", 0) or 0
        completion_t = getattr(usage, "completion_tokens", 0) or 0
        # moonshot-v1-32k-vision-preview pricing from shared/model_costs/2026-04-22.json:
        # $0.30 / M input tokens, $0.30 / M output tokens (approx for the 32k variant).
        # Snapshot is the source of truth — this approximation is only for the
        # return value so callers can budget; the canonical row sits in
        # llm_usage via record_openai_usage above.
        cost = (prompt_t + completion_t) * 0.30 / 1_000_000
    except Exception:
        pass

    elapsed_ms = (time.monotonic() - started) * 1000
    raw = (response.choices[0].message.content or "").strip()
    return raw, cost, elapsed_ms


def _record_verdict_row(
    verdict: FieldVerdict,
    *,
    page_url: str,
    platform: str,
    confidence: float | None,
    elapsed_ms: float,
    trajectory_id: str | None,
) -> None:
    """Write one row per field into semantic_decisions.db."""
    try:
        record_decision(
            agent_name=_AGENT_NAME,
            call_site=_CALL_SITE,
            decision_type="vision_verification",
            mechanism="llm",
            tier_reached=verdict.tier_reached,
            input_value={
                "label": verdict.label,
                "claimed_value": verdict.claimed_value,
                "page_url": page_url,
                "platform": platform,
            },
            output_value={
                "observed_value": verdict.observed_value,
                "matches_claim": verdict.matches_claim,
                "contradicts_help_text": verdict.contradicts_help_text,
                "reason": verdict.reason,
            },
            confidence=confidence,
            field_label=verdict.label,
            elapsed_ms=elapsed_ms,
            trajectory_id=trajectory_id,
        )
    except Exception as exc:
        logger.debug("vision_verifier: decision row write failed: %s", exc)


def _learn_correction(
    *,
    label: str,
    old_value: str,
    new_value: str,
    domain: str,
    platform: str,
    reason: str,
) -> None:
    """Route a successful vision-driven correction through ai_assist_logger.

    This single call cascades to CorrectionCapture (``field_corrections.db``),
    AgentRulesDB, and ``screening_semantic_cache`` — so the upstream caches
    that produced the wrong answer are invalidated/updated. Without this
    routing the same wrong answer regenerates on the next apply, violating
    OPRAL "if it can recur, the fix is incomplete."
    """
    try:
        from jobpulse.ai_assist_logger import get_ai_assist_logger

        ai_logger = get_ai_assist_logger()
        session = ai_logger.start_session(
            "vision_verifier", domain=domain, platform=platform,
        )
        ai_logger.record_fix(
            session.session_id,
            field_label=label,
            old_value=old_value,
            new_value=new_value,
            reasoning=f"vision_verifier: {reason}",
            confidence=0.85,
        )
        ai_logger.finalize_session(session.session_id, push_to_learning=True)
    except Exception as exc:
        logger.debug("vision_verifier: ai_assist_logger routing failed: %s", exc)


async def verify_form_page(
    page: "Page",
    filled_mapping: dict[str, str],
    *,
    page_url: str,
    platform: str,
    page_num: int = 1,
    correction_enabled: bool | None = None,
    fill_callback: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    trajectory_id: str | None = None,
    field_metadata: dict[str, dict] | None = None,
) -> VerifierResult:
    """Run vision verification on the current form page.

    Field-evidence pipeline (S26-follow-up-K, single-shot):
      1. Take ONE full-page screenshot.
      2. Resolve each filled label to its DOM input element (via the same
         locator cascade `_fill_by_label` uses).
      3. JS-evaluate each input's document-relative bbox (input + label +
         help-text rects, unioned).
      4. Crop each field from the screenshot and tile into ONE composite
         WebP-lossless image with ordinal caption strips.
      5. ONE kimi vision call with ordinal-indexed claim list.
      6. Parse verdicts keyed by ordinal back into the claim row order.

    When no field can be resolved, the verifier falls back to a single
    whole-page screenshot + single call (still chunks_used=1), so live
    visibility doesn't regress.

    Args:
        page: Playwright Page positioned on the just-filled form page.
        filled_mapping: ``{label: value}`` the filler claims it entered.
            Empty-value entries are treated as ``skipped_no_expected_value``
            and not sent to vision (Outcome 6: don't hallucinate skipped
            fields into existence).
        page_url: Current page URL (used for domain key + decision row).
        platform: ATS platform key (e.g. "greenhouse").
        page_num: Current form page number (for logging).
        correction_enabled: Override env. None → read VISION_VERIFICATION_CORRECT.
        fill_callback: Async ``(label, value) -> result_dict`` used to re-fill
            mismatched fields. Required when correction_enabled is True.
            Typically ``NativeFormFiller._fill_by_label``.
        trajectory_id: Optional trajectory ID for decision-row linkage.
        field_metadata: Optional per-label metadata dict (e.g. native_form
            filler's ``_fields_by_label``). When supplied, the verifier
            consults attached selectors before falling back to
            ``page.get_by_label``. Speeds locator resolution + handles
            shadow-DOM-attached fields (SmartRecruiters, react-select)
            that ``get_by_label`` misses.

    Returns:
        VerifierResult with per-field verdicts and aggregate counts.
    """
    result = VerifierResult()
    if not _is_enabled():
        return result

    if not filled_mapping:
        return result

    skipped: list[tuple[str, str]] = []
    claim_rows: list[tuple[str, str]] = []
    for label, value in filled_mapping.items():
        if not str(value).strip():
            skipped.append((label, str(value)))
        else:
            claim_rows.append((label, str(value)))

    domain = _domain_from_url(page_url)
    started = time.monotonic()

    for label, value in skipped:
        verdict = FieldVerdict(
            label=label,
            claimed_value=value,
            observed_value="<empty>",
            matches_claim=False,
            contradicts_help_text=False,
            reason="filler did not produce a value for this label",
            tier_reached="skipped_no_expected_value",
        )
        result.verdicts.append(verdict)
        _record_verdict_row(
            verdict,
            page_url=page_url,
            platform=platform,
            confidence=None,
            elapsed_ms=0.0,
            trajectory_id=trajectory_id,
        )

    if not claim_rows:
        result.elapsed_ms = (time.monotonic() - started) * 1000
        return result

    # 1) Screenshot the whole scrollable form once.
    try:
        screenshot_png = await _full_page_screenshot(page)
    except Exception as exc:
        logger.warning("vision_verifier: screenshot failed: %s", exc)
        result.vision_unavailable = True
        result.error = f"screenshot_failed: {exc}"
        for label, value in claim_rows:
            verdict = FieldVerdict(
                label=label,
                claimed_value=value,
                observed_value="<unknown>",
                matches_claim=False,
                contradicts_help_text=False,
                reason="screenshot capture failed",
                tier_reached="vision_unavailable",
            )
            result.verdicts.append(verdict)
            _record_verdict_row(
                verdict,
                page_url=page_url,
                platform=platform,
                confidence=None,
                elapsed_ms=0.0,
                trajectory_id=trajectory_id,
            )
        result.elapsed_ms = (time.monotonic() - started) * 1000
        return result

    # 2-3) Resolve locators + extract bboxes per filled field.
    bbox_entries = await _extract_field_bboxes(page, claim_rows, field_metadata)
    resolved = sum(1 for e in bbox_entries if e.get("bbox"))
    logger.info(
        "vision_verifier: bbox resolution %d/%d fields  page=%d domain=%s",
        resolved, len(bbox_entries), page_num, domain,
    )

    # 4) Build the composite. If no bbox was resolvable, fall back to
    #    sending the whole-page screenshot (still single-shot).
    composite_bytes, panels = _build_composite(screenshot_png, bbox_entries)
    used_composite = composite_bytes is not None
    if used_composite:
        vision_input = composite_bytes
        ordered_for_prompt = [(p["label"], p["value"]) for p in panels]
        prompt = _build_prompt(ordered_for_prompt, ordinals=True)
        composite_layout = {
            "panels_total": len(panels),
            "panels_unresolved": len(bbox_entries) - len(panels),
            "image_bytes": len(composite_bytes),
        }
    else:
        # Fallback: whole page, no ordinals — same shape as pre-K verifier
        # but still single-call. Vision keys verdicts by label text.
        vision_input = _compress_for_vision(screenshot_png)
        ordered_for_prompt = list(claim_rows)
        prompt = _build_prompt(ordered_for_prompt, ordinals=False)
        composite_layout = {
            "panels_total": 0,
            "panels_unresolved": len(bbox_entries),
            "image_bytes": len(vision_input),
            "fallback_reason": "no_field_bboxes_resolved",
        }
        panels = []

    logger.info(
        "vision_verifier: %s %d bytes (composite=%s) → 1 chunk(s) page=%d domain=%s",
        "composite" if used_composite else "fallback_full_page",
        len(vision_input), used_composite, page_num, domain,
    )

    # 5) ONE kimi call.
    raw, cost, call_ms = await _call_vision(vision_input, prompt)
    result.cost_usd += cost

    parsed = _safe_json(raw) if raw is not None else None
    if parsed is None or not isinstance(parsed.get("verdicts"), list):
        logger.warning(
            "vision_verifier: vision unavailable or unparseable on page %d (domain=%s)",
            page_num, domain,
        )
        result.vision_unavailable = True
        result.error = "vision call failed or returned unparseable JSON"
        for label, value in claim_rows:
            verdict = FieldVerdict(
                label=label,
                claimed_value=value,
                observed_value="<unknown>",
                matches_claim=False,
                contradicts_help_text=False,
                reason="vision unavailable",
                tier_reached="vision_unavailable",
            )
            result.verdicts.append(verdict)
            _record_verdict_row(
                verdict,
                page_url=page_url,
                platform=platform,
                confidence=None,
                elapsed_ms=call_ms,
                trajectory_id=trajectory_id,
            )
        result.elapsed_ms = (time.monotonic() - started) * 1000
        result.artifact_path = _save_artifact(
            composite=composite_bytes,
            fallback_screenshot=None if used_composite else vision_input,
            domain=domain,
            page_num=page_num,
            verdicts=result.verdicts,
            panels=panels,
            page_url=page_url,
            platform=platform,
            cost_usd=result.cost_usd,
            elapsed_ms=result.elapsed_ms,
            chunks_used=1,
            composite_layout=composite_layout,
        )
        return result

    # 6) Map verdicts back to claim rows.
    # Composite path: key by ordinal first (most robust), else by label.
    # Fallback path: key by label text only.
    by_ordinal: dict[int, dict] = {}
    by_label: dict[str, dict] = {}
    for entry in parsed["verdicts"]:
        if not isinstance(entry, dict):
            continue
        ordinal = entry.get("ordinal")
        if isinstance(ordinal, int):
            by_ordinal[ordinal] = entry
        elif isinstance(ordinal, str) and ordinal.strip().isdigit():
            by_ordinal[int(ordinal.strip())] = entry
        label = str(entry.get("label", "")).strip()
        if label:
            by_label.setdefault(label, entry)
            stripped = _strip_required_marker(label)
            if stripped != label:
                by_label.setdefault(stripped, entry)

    panel_ordinal_by_label: dict[str, int] = {
        p["label"]: p["ordinal"] for p in panels
    }

    correct = correction_enabled if correction_enabled is not None else _correction_enabled()
    if correct and fill_callback is None:
        logger.warning(
            "vision_verifier: correction_enabled but no fill_callback — falling back to observe-only",
        )
        correct = False

    verdicts: list[FieldVerdict] = []
    correction_queue: list[int] = []
    for label, claimed in claim_rows:
        entry: dict | None = None
        if used_composite and label in panel_ordinal_by_label:
            entry = by_ordinal.get(panel_ordinal_by_label[label])
        if entry is None:
            entry = by_label.get(label) or by_label.get(_strip_required_marker(label))
        if entry is None:
            verdict = FieldVerdict(
                label=label,
                claimed_value=claimed,
                observed_value="<not reported>",
                matches_claim=False,
                contradicts_help_text=False,
                reason="vision did not include this field in its verdict list",
                tier_reached="vision_unavailable",
            )
            verdicts.append(verdict)
            continue

        observed_raw = entry.get("observed_value", "<unknown>")
        observed_value = "<unknown>" if observed_raw is None else str(observed_raw)
        matches_claim = bool(entry.get("matches_claim", False))
        contradicts_help_text = bool(entry.get("contradicts_help_text", False))
        reason = str(entry.get("reason", ""))[:240]
        tier = _classify_tier(matches_claim, contradicts_help_text, observed_value)
        verdict = FieldVerdict(
            label=label,
            claimed_value=claimed,
            observed_value=observed_value,
            matches_claim=matches_claim,
            contradicts_help_text=contradicts_help_text,
            reason=reason,
            tier_reached=tier,
        )
        verdicts.append(verdict)
        if tier == "mismatch_detected":
            result.mismatches += 1
            if correct and fill_callback is not None:
                correction_queue.append(len(verdicts) - 1)

    # Correction proposals — feed the composite (vision can still see all
    # fields in context, but the prompt names ONE specific ordinal/label).
    proposals: dict[int, str | None] = {}
    if correct and correction_queue:
        proposal_tasks = [
            _attempt_correction(
                screenshot_png=vision_input,
                label=verdicts[v_idx].label,
                claimed=verdicts[v_idx].claimed_value,
                observed=verdicts[v_idx].observed_value,
                reason=verdicts[v_idx].reason,
            )
            for v_idx in correction_queue
        ]
        proposed_values = await asyncio.gather(*proposal_tasks, return_exceptions=True)
        for v_idx, proposed in zip(correction_queue, proposed_values):
            if isinstance(proposed, Exception):
                logger.debug(
                    "vision_verifier: correction proposal raised for '%s': %s",
                    verdicts[v_idx].label[:60], proposed,
                )
                proposals[v_idx] = None
            else:
                proposals[v_idx] = proposed

    for v_idx, verdict in enumerate(verdicts):
        if (
            verdict.tier_reached == "mismatch_detected"
            and v_idx in proposals
            and fill_callback is not None
        ):
            corrected_value = proposals[v_idx]
            if corrected_value and corrected_value.strip():
                try:
                    fill_result = await fill_callback(verdict.label, corrected_value)
                except Exception as exc:
                    logger.warning(
                        "vision_verifier: fill_callback raised for '%s': %s",
                        verdict.label[:60], exc,
                    )
                    fill_result = {"success": False, "error": str(exc)}

                if fill_result.get("success") and fill_result.get("value_verified", True):
                    verdict.tier_reached = "correction_succeeded"
                    verdict.observed_value = corrected_value
                    verdict.matches_claim = True
                    verdict.reason = f"corrected to '{corrected_value}': {verdict.reason}"
                    result.corrections_applied += 1
                    _learn_correction(
                        label=verdict.label,
                        old_value=verdict.claimed_value,
                        new_value=corrected_value,
                        domain=domain,
                        platform=platform,
                        reason=verdict.reason,
                    )
                else:
                    verdict.tier_reached = "correction_failed"
                    verdict.reason = f"correction to '{corrected_value}' did not verify: {verdict.reason}"
                    result.corrections_failed += 1
            else:
                verdict.tier_reached = "correction_failed"
                verdict.reason = f"vision did not propose a corrected value: {verdict.reason}"
                result.corrections_failed += 1

        result.verdicts.append(verdict)
        confidence = 1.0 if verdict.tier_reached == "passed" else (
            0.85 if verdict.tier_reached == "correction_succeeded" else 0.5
        )
        _record_verdict_row(
            verdict,
            page_url=page_url,
            platform=platform,
            confidence=confidence,
            elapsed_ms=call_ms,
            trajectory_id=trajectory_id,
        )

    result.elapsed_ms = (time.monotonic() - started) * 1000
    result.artifact_path = _save_artifact(
        composite=composite_bytes,
        fallback_screenshot=None if used_composite else vision_input,
        domain=domain,
        page_num=page_num,
        verdicts=result.verdicts,
        panels=panels,
        page_url=page_url,
        platform=platform,
        cost_usd=result.cost_usd,
        elapsed_ms=result.elapsed_ms,
        chunks_used=1,
        composite_layout=composite_layout,
    )
    logger.info(
        "vision_verifier: page %d (%s) — verified=%d mismatches=%d "
        "corrections=%d cost=$%.4f elapsed=%.0fms artifact=%s",
        page_num, domain,
        sum(1 for v in result.verdicts if v.tier_reached == "passed"),
        result.mismatches,
        result.corrections_applied,
        result.cost_usd,
        result.elapsed_ms,
        result.artifact_path or "(not saved)",
    )
    return result


async def _attempt_correction(
    *,
    screenshot_png: bytes,
    label: str,
    claimed: str,
    observed: str,
    reason: str,
) -> str | None:
    """Ask vision for the correct value for one specific mismatched field.

    Important scoping (S26 RUN4 lesson): a mismatch verdict means
    ``claim != observed``, but that does NOT always mean the form is
    wrong. Two failure modes both surface as ``mismatch_detected``:

      1. **Silent fill failure** — filler attempted the right value, the
         click didn't stick (e.g. AI Policy combobox stuck on
         ``Select...``). Help-text usually gives a clear directive
         ("select Yes"), so vision can propose the correct value.

      2. **Wrong upstream intent** — filler attempted the wrong value
         (e.g. stale UK-context cache produced "No" for visa
         sponsorship on a US job where the right answer is "Yes"). The
         form may already show the correct value; help-text doesn't
         disambiguate without profile + JD context.

    Vision sees the screenshot only — no profile, no JD. So this
    function can only fix mode 1. For mode 2, vision should return
    ``null``, the verdict stays ``mismatch_detected``, and the surfaced
    row is a learning signal to the upstream cache (handled by the
    audit doc + the human + future cache-key work — not auto-corrected
    here). That's the right scope discipline: do less, do it right.

    Bounded: at most ``_MAX_CORRECTION_RETRIES`` correction proposals per
    field — no infinite loops.
    """
    prompt = (
        "You are fixing ONE specific field in a job-application form. An "
        "automated filler and the rendered form disagree on this field's "
        "value. Look at the screenshot, locate the field below, and decide "
        "whether you can determine the CORRECT value FROM THE SCREENSHOT "
        "ALONE.\n\n"
        f"Field label: {label!r}\n"
        f"Filler's claimed value: {claimed!r}\n"
        f"Currently observed value: {observed!r}\n"
        f"Verifier reason: {reason}\n\n"
        "Rules:\n"
        "- If the field's help-text or instructions explicitly direct a "
        'specific answer (e.g. "confirm by selecting \'Yes\'"), return '
        "that answer.\n"
        "- If the field is a Yes/No combobox or radio AND the help-text "
        "gives a clear directive, return the exact option text shown.\n"
        "- If the help-text is just a question (e.g. 'Do you require visa "
        "sponsorship?') with no directive, you CANNOT determine the "
        "correct value from the screenshot alone — return null. The right "
        "answer depends on candidate profile + job context, which you do "
        "not have access to.\n"
        "- If the field appears unfilled (placeholder text like 'Select...' "
        "is visible) and the help-text directs an answer, propose that "
        "answer.\n"
        "- If you cannot determine the correct value, ALWAYS return null. "
        "A null is safer than a guess.\n\n"
        'Return STRICT JSON only: {"corrected_value": "..."} or '
        '{"corrected_value": null}. No prose, no markdown fences.'
    )
    raw, _, _ = await _call_vision(screenshot_png, prompt)
    if raw is None:
        return None
    parsed = _safe_json(raw)
    if parsed is None:
        return None
    proposed = parsed.get("corrected_value")
    if proposed is None or not str(proposed).strip():
        return None
    return str(proposed).strip()
