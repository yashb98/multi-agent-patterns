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
    # S26-follow-up-M-4: scanner-seen required fields the filler didn't
    # fill (no_mapping / screening-pipeline gap). Surfaced for audit
    # purposes — these are FILLER-coverage gaps, not verifier failures.
    scanner_unfilled_required: list[dict] = dataclass_field(default_factory=list)


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


# S26-follow-up-M: per-field crops now go through
# ``_field_crop.probe_page``, which uses Playwright's native
# ``ElementHandle.screenshot()`` on a dynamically-resolved form-row
# container. Coordinate math + full-page crop are gone — see
# ``docs/audits/2026-05-11-vision-bbox-fix-prompt.md`` for the live
# evidence that drove this replacement (JD-body bleed in the M-1
# Graphcore artifact).


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


async def _try_locator_cascade(ctx, stripped: str):
    """Run the label/placeholder/role cascade against a Page or Frame context.

    The cascade is intentionally adapter-agnostic — the same primitive
    chain handles main-page forms (Greenhouse, Lever, Ashby, Generic),
    shadow-DOM forms (SmartRecruiters via the role tier piercing
    shadow boundaries), and iframe-embedded forms (iCIMS via the
    surrounding ``_resolve_label_locator`` iterating ``page.frames``).
    No ``if platform == "X"`` branches: dynamic primitives carry the
    coverage.
    """
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


async def _resolve_label_locator(
    page: "Page",
    label: str,
    field_metadata: dict | None,
):
    """Locator resolution cascade mirroring `_fill_by_label`'s primitives.

    Returns a tuple ``(locator, owner_frame)`` (``owner_frame`` is ``None``
    for main-page locators; otherwise it is the Frame object the locator
    was resolved inside). Returns ``None`` when no context produced a
    match.

    The mirroring is intentional: the verifier's bbox extraction succeeds
    exactly when the filler could have re-resolved the same field, so
    unresolvable claims are also unverifiable claims — surfacing them as
    ``vision_unavailable`` is the right signal.

    Iframe handling (S26-follow-up-L5): after the main-page cascade
    fails, we iterate ``page.frames`` and try the same cascade in each
    child frame. This is adapter-agnostic — the iCIMS strategy declares
    an ``icims_content_iframe`` but the same primitive serves any future
    iframe-based ATS. No platform branches.
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
                        return loc, None
                except Exception:
                    pass
            attached = meta.get("locator")
            if attached is not None:
                try:
                    if await attached.count():
                        return attached, None
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


async def _extract_field_crops(
    page: "Page",
    claim_rows: list[tuple[str, str]],
    field_metadata: dict | None,
) -> list:
    """Capture one per-field crop per filled label via locator.screenshot().

    Replaces the pre-M ``_extract_field_bboxes`` + JS bbox-math + PIL.crop
    pipeline. Each entry returned is a ``_field_crop.FieldCrop`` carrying
    PNG bytes for the form-row container around the input. See
    ``_field_crop.py`` for the resolver cascade (fieldset → role=group →
    form-row → relaxed → element-fallback) — no per-platform branches.

    Greenhouse-style duplicate-bbox claims (the same widget rendered with
    a required + optional label) collapse to one panel here; the
    ``dedup_with`` list on the returned crop records the collapsed
    ordinals so the verdict-mapping step still emits one ``FieldVerdict``
    per original claim row.
    """
    from jobpulse.form_engine._field_crop import _capture_field_crop, _dedup_crops

    crops = []
    for idx, (label, value) in enumerate(claim_rows, start=1):
        crop = await _capture_field_crop(
            page, label, value, idx, field_metadata,
        )
        crops.append(crop)
    return _dedup_crops(crops)


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
    crops: list,
) -> tuple[bytes | None, list[dict]]:
    """Vertically tile per-field crops into one WebP-lossless composite.

    Takes the ``_field_crop.FieldCrop`` objects produced by
    ``_extract_field_crops`` and lays them out with pale-blue caption
    strips carrying the ordinal marker (``[01]``, ``[02]``, ...).
    Returns ``(composite_bytes, panels_meta)`` — ``panels_meta`` carries
    one entry per panel in the composite (post-dedup), so the verdict-
    mapping step can key by ordinal back to the original claim rows.
    """
    try:
        import io

        from PIL import Image, ImageDraw
    except Exception as exc:
        logger.debug("vision_verifier: PIL unavailable: %s", exc)
        return None, []

    panels = [c for c in crops if c.crop_bytes is not None]
    if not panels:
        return None, []

    pil_crops: list = []
    max_w = 0
    for c in panels:
        try:
            img = Image.open(io.BytesIO(c.crop_bytes))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
        except Exception as exc:
            logger.debug("vision_verifier: crop decode failed for ord %d: %s",
                         c.ordinal, exc)
            continue
        pil_crops.append((c, img))
        if img.width > max_w:
            max_w = img.width

    if not pil_crops:
        return None, []

    # Edge-case: any crop wider than Kimi 4K rec → scale that crop only.
    if max_w > _MAX_LONG_EDGE:
        for i, (c, img) in enumerate(pil_crops):
            if img.width > _MAX_LONG_EDGE:
                scale = _MAX_LONG_EDGE / float(img.width)
                nw = _MAX_LONG_EDGE
                nh = max(1, int(img.height * scale))
                pil_crops[i] = (c, img.resize((nw, nh), Image.LANCZOS))
        max_w = min(max_w, _MAX_LONG_EDGE)

    total_h = sum(img.height + _CAPTION_STRIP_PX + 6 for _c, img in pil_crops)
    composite = Image.new("RGB", (max_w + 6, total_h + 6), color=(255, 255, 255))
    draw = ImageDraw.Draw(composite)
    font = _composite_font()

    # S26-follow-up-M-5: caption + prompt ordinals must align. After dedup
    # the FieldCrop's `ordinal` is non-contiguous (e.g. [1,2,3,4,5,7,9,11,13]
    # when 5 pairs collapsed), but `_build_prompt` enumerates the panel list
    # 1..N contiguously. If we stamped `c.ordinal` on the caption, the
    # vision model would key its verdicts to the captions it sees in the
    # image, while the verifier's lookup keyed them to the prompt indices —
    # causing a one-row shift for every dedup collapse upstream. Use the
    # contiguous panel position for BOTH the caption stamp AND the
    # panels_meta `ordinal` so the prompt index, caption marker, and
    # verdict-mapping key agree. `original_ordinal` + `dedup_with` carry the
    # claim-row mapping for downstream consumers.
    y_cursor = 3
    panels_meta: list[dict] = []
    for panel_pos, (c, img) in enumerate(pil_crops, start=1):
        caption = f"[{panel_pos:02d}]"
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
        composite.paste(img, (3, y_cursor))
        draw.rectangle(
            (2, y_cursor - 1, 3 + img.width + 1, y_cursor + img.height + 1),
            outline=(220, 30, 30),
            width=1,
        )
        y_cursor += img.height + 6
        panels_meta.append({
            "ordinal": panel_pos,           # caption + prompt index
            "original_ordinal": c.ordinal,  # original claim-row index
            "label": c.label,
            "value": c.value,
            "resolve_method": c.resolve_method,
            "bbox": list(c.bbox) if c.bbox else None,
            "dedup_with": list(c.dedup_with),
        })

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


def _compute_coverage_realtime(
    field_metadata: dict[str, dict] | None,
    filled_mapping: dict[str, str],
    session_state,  # SessionFillState | None
    scanner_unfilled_required: list[dict],
    scanner_unfilled_optional: list[dict],
    scanner_noise_excluded: list[dict],
) -> dict | None:
    """S26-follow-up-O-4: per-field outcome bucketing keyed by WHERE
    verification happened (fill-time DOM vs deferred-to-vision).

    Buckets:
      - filled_verified_at_fill_time: session_state.was_verified == True
      - filled_deferred_to_vision   : session_state has the label but
        wasn't verified (e.g. combobox — DOM couldn't decide)
      - scanner_saw_filler_skipped_required / *_optional / scanner_noise_excluded:
        carried forward from the M-4 / N-1 surfacing.

    Returns None when no session_state is supplied (legacy callers) or
    when field_metadata is empty (fallback code paths). The sidecar
    invariant ``sum(buckets) == scanner_coverage.total`` is what the
    audit doc machine-checks.
    """
    if session_state is None or not field_metadata:
        return None
    verified_at_fill_time: list[dict] = []
    deferred_to_vision: list[dict] = []
    for label, value in filled_mapping.items():
        bucket = {"label": label, "claimed_value": value}
        try:
            was_verified = session_state.was_verified(label)
        except Exception:
            was_verified = False
        if was_verified:
            verified_at_fill_time.append(bucket)
        else:
            deferred_to_vision.append(bucket)
    return {
        "filled_verified_at_fill_time": verified_at_fill_time,
        "filled_deferred_to_vision": deferred_to_vision,
        "scanner_saw_filler_skipped_required": list(scanner_unfilled_required),
        "scanner_saw_filler_skipped_optional": list(scanner_unfilled_optional),
        "scanner_noise_excluded": list(scanner_noise_excluded),
    }


def _persist_verified_fills_cache(
    verdicts: list[FieldVerdict],
    field_metadata: dict[str, dict] | None,
    domain: str,
    dom_match_labels: set[str],
) -> None:
    """Write/invalidate verified_fills rows from the verifier's verdicts.

    Only ``passed`` records a row (strong-evidence verdict tier).
    ``mismatch_detected`` invalidates any existing row for the label so
    the next dry-run does not trust a stale cache hit. Other tiers
    (``correction_succeeded``, ``vision_unavailable``,
    ``skipped_no_expected_value``) are intentionally skipped — a
    correction means the original claim was wrong, and the other tiers
    don't constitute evidence of correctness.

    ``dom_match_labels`` provides the provenance: passed verdicts whose
    label is in this set were confirmed by DOM read, the rest by vision.
    """
    if not domain:
        return
    try:
        from jobpulse.form_engine.verified_fills_db import VerifiedFillsDB
        db = VerifiedFillsDB()
    except Exception as exc:
        logger.debug("verified_fills: DB init failed: %s", exc)
        return
    for v in verdicts:
        meta = None
        if field_metadata:
            meta = (
                field_metadata.get(v.label)
                or field_metadata.get(_strip_required_marker(v.label))
            )
        ftype = ""
        if isinstance(meta, dict):
            ftype = str(meta.get("type") or "")
        if v.tier_reached == "passed":
            method = "dom_match" if v.label in dom_match_labels else "vision"
            db.record(domain, v.label, ftype, v.claimed_value, method)
        elif v.tier_reached == "mismatch_detected":
            db.invalidate(domain, v.label)


def _compute_scanner_coverage(
    field_metadata: dict[str, dict] | None,
    verdicts: list[FieldVerdict],
    scanner_unfilled_required: list[dict],
    scanner_unfilled_optional: list[dict],
    scanner_noise_excluded: list[dict],
) -> dict | None:
    """Build the sidecar's scanner_coverage block (S26-follow-up-N-1).

    Enumerates every scanner-discovered field in exactly one bucket so a
    cross-adapter audit can check ``sum(buckets) == total`` without
    re-reading filler logs. Returns None when the verifier had no
    field_metadata to bucket (fallback / non-scanner code paths) so
    callers can emit ``scanner_coverage: null``.
    """
    if not field_metadata:
        return None
    filled_passed: list[dict] = []
    filled_mismatch: list[dict] = []
    filled_vision_unavailable: list[dict] = []
    for v in verdicts:
        bucket_entry = {"label": v.label, "claimed_value": v.claimed_value}
        if v.tier_reached == "passed":
            filled_passed.append(bucket_entry)
        elif v.tier_reached == "correction_succeeded":
            # Treat correction_succeeded as a passed-after-fix; sidecar
            # already records the original mismatch via the verdict list.
            filled_passed.append(bucket_entry)
        elif v.tier_reached in ("mismatch_detected", "correction_failed"):
            filled_mismatch.append(bucket_entry)
        elif v.tier_reached in (
            "vision_unavailable", "skipped_no_expected_value",
        ):
            filled_vision_unavailable.append(bucket_entry)
        else:
            # Future tiers fall here — surface so coverage isn't lost.
            filled_vision_unavailable.append(
                {**bucket_entry, "tier_reached": v.tier_reached},
            )
    total = len(field_metadata)
    return {
        "total": total,
        "filled_verified_passed": filled_passed,
        "filled_verified_mismatch": filled_mismatch,
        "filled_vision_unavailable": filled_vision_unavailable,
        "scanner_saw_filler_skipped_required": list(scanner_unfilled_required),
        "scanner_saw_filler_skipped_optional": list(scanner_unfilled_optional),
        "scanner_noise_excluded": list(scanner_noise_excluded),
    }


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
    scanner_unfilled_required: list[dict] | None = None,
    scanner_coverage: dict | None = None,
    coverage_realtime: dict | None = None,
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
                    "ordinal": p.get("ordinal"),  # contiguous panel position (post-M-5)
                    "original_ordinal": p.get("original_ordinal"),  # original claim-row index
                    "label": p.get("label"),
                    "value": p.get("value"),
                    "bbox": p.get("bbox"),
                    "resolve_method": p.get("resolve_method"),
                    "dedup_with": p.get("dedup_with"),
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
            # M-4: silent-drop required fields the scanner saw but the
            # filler didn't attempt (no_mapping / screening-pipeline gap).
            # These are FILLER-coverage gaps; surfacing here lets the
            # cross-adapter audit flag them without re-scraping logs.
            "scanner_unfilled_required": scanner_unfilled_required or [],
            # N-1: full scanner-field coverage report. Every scanner field
            # lands in exactly one bucket; ``total`` matches the scanner's
            # field count so the assertion ``sum(buckets) == total``
            # machine-checks coverage from the sidecar alone.
            "scanner_coverage": scanner_coverage,
            # O-4: same field set, bucketed by WHERE verification
            # happened (fill-time DOM vs end-of-page vision). The
            # sidecar invariant is ``sum(coverage_realtime buckets) ==
            # scanner_coverage.total``.
            "coverage_realtime": coverage_realtime,
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


# Per-vision-call timeout (s). S26-follow-up-L probe evidence:
# Moonshot kimi-k2.6 returns at ~290 s on verifier-shaped prompts and
# ~10 s on tiny prompts — i.e. for the verifier's actual workload it
# almost always blows the G3 ≤ 60 s budget. The slice's fix is to keep
# Moonshot as primary (per spec — backwards compat for the day kimi's
# vision queue recovers) but FAIL FAST so the fallback path has budget
# remaining within G3. 25 s per attempt × 1 attempt = 25 s primary
# worst-case; fallback then has up to ~35 s of the G3 budget left.
_VISION_CALL_TIMEOUT_S = float(os.environ.get("VISION_VERIFIER_CALL_TIMEOUT_S", "25"))

# Fallback vision endpoint — engaged ONLY after the primary (Moonshot
# via ``get_openai_client``) exhausts its retry budget. Backed by an
# OpenAI-SDK-compatible endpoint so the same ``chat.completions.create``
# call works with no special-cased branches.
#
# Default values target local Ollama with qwen3-vl:4b — Ollama exposes an
# OpenAI-compat API at /v1, the model is multimodal (vision + text), and
# it has no rate limits / network jitter. Override either:
#   - ``VISION_VERIFIER_FALLBACK_MODEL`` — model name (e.g. "gpt-4o", "qwen3-vl:8b").
#     Set to empty string or "none" to disable fallback entirely.
#   - ``VISION_VERIFIER_FALLBACK_BASE_URL`` — base URL. Default localhost:11434/v1.
#     For OpenAI cloud: set to "https://api.openai.com/v1".
#
# Adapter-agnostic: the fallback hits the SAME ``_call_provider`` helper
# as the primary, so all of the verifier's prompt + parse + tier
# classification logic applies unchanged. Per S26-follow-up-L Gate G6,
# the fallback never inspects ``platform`` / ``ats`` / ``domain``.
_FALLBACK_MODEL = os.environ.get("VISION_VERIFIER_FALLBACK_MODEL", "qwen3-vl:4b").strip()
_FALLBACK_BASE_URL = os.environ.get(
    "VISION_VERIFIER_FALLBACK_BASE_URL", "http://localhost:11434/v1",
).strip()
# Fallback latency budget — measured behaviour of qwen3-vl on Mac via
# Ollama on the actual verifier prompt + 10–30 KB composite:
#   * 4b nothink + minimal schema, 11-field Graphcore composite (10 KB):
#     161 s elapsed, ct=8933 — PARSEABLE 11 verdicts (bleed-contaminated).
#   * 4b verbose prompt, same composite:
#     269 s elapsed, ct=13675 — PARSEABLE 11 verdicts.
#   * 8b nothink + minimal schema, same composite:
#     169 s elapsed, ct=5019 — PARSEABLE 11 verdicts.
#   * Trial B (full 30 KB composite + TINY prompt): 3 s, ct=92.
#     i.e. qwen vision encoding is fast; the slowness is response
#     generation including ~5000–13000 hidden "thinking" tokens that
#     ``enable_thinking=False`` does NOT suppress.
#
# G3-budget consequence: ``primary 25 s + fallback 90 s = 115 s`` worst
# case — over the G3 ≤ 60 s bar. SHIPPED-PARTIAL framing: the verifier
# architecture, vendor-fallback wiring, iframe support (L5), and decision-
# row writes all land; latency remains the dominant gap pending faster
# vision endpoints (OpenAI quota refresh, larger Ollama model, or future
# distilled qwen variant). Filed as **S26-follow-up-L-2** in the audit.
#
# 90 s is the default ceiling: enough budget for many real qwen calls
# to actually return (we've measured 161–268 s, but 8b on smaller forms
# can come in below 90 s); short enough that the verifier surfaces
# ``vision_unavailable`` deterministically instead of stalling the apply.
_FALLBACK_CALL_TIMEOUT_S = float(
    os.environ.get("VISION_VERIFIER_FALLBACK_TIMEOUT_S", "90")
)


def _fallback_enabled() -> bool:
    return bool(_FALLBACK_MODEL) and _FALLBACK_MODEL.lower() != "none"


def _get_fallback_client(timeout: float):
    """Construct the fallback vision client (OpenAI SDK).

    Returns the constructed client, or ``None`` if the fallback is
    disabled / mis-configured. Never raises.

    Authentication strategy:
      * Ollama (``localhost`` in base_url): pass api_key="ollama" (the
        SDK requires a non-empty key but Ollama ignores its value).
      * OpenAI cloud (``api.openai.com`` in base_url): pull
        ``OPENAI_API_KEY``; return None if absent so the verifier
        surfaces vision_unavailable cleanly rather than crashing.
    """
    if not _fallback_enabled():
        return None
    try:
        from openai import OpenAI
    except Exception as exc:
        logger.debug("vision_verifier: openai SDK import failed: %s", exc)
        return None
    try:
        base_lower = _FALLBACK_BASE_URL.lower()
        if "localhost" in base_lower or "127.0.0.1" in base_lower or "ollama" in base_lower:
            return OpenAI(api_key="ollama", base_url=_FALLBACK_BASE_URL, timeout=timeout)
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            logger.debug(
                "vision_verifier: fallback OPENAI_API_KEY missing for base_url=%s",
                _FALLBACK_BASE_URL,
            )
            return None
        return OpenAI(api_key=api_key, base_url=_FALLBACK_BASE_URL, timeout=timeout)
    except Exception as exc:
        logger.debug("vision_verifier: fallback client init failed: %s", exc)
        return None


async def _call_provider(
    client,
    model: str,
    prompt: str,
    screenshot_png: bytes,
    *,
    provider_name: str,
    timeout: float,
) -> tuple[str | None, float, float, Exception | None]:
    """Call ONE OpenAI-SDK-compatible vision endpoint with bounded retry.

    Returns ``(raw_text, cost_usd, elapsed_ms, last_exception)`` —
    ``raw_text`` is None on failure (caller decides whether to fall
    back), ``last_exception`` carries the final failure for logging.

    Retry policy: TWO attempts with a 4 s backoff between, transient-
    only retries (429 / overloaded / timeout / rate). The OpenAI client
    is built with ``max_retries=0`` so its internal retry doesn't
    compound with this loop (S26-follow-up-K live evidence: a 9-min
    burn on the pre-K compound stack). Worst-case wall-clock per
    provider: ``2 × timeout + 4`` s.
    """
    started = time.monotonic()
    mime = _mime_for(screenshot_png)
    b64_image = base64.b64encode(screenshot_png).decode("ascii")

    try:
        client.max_retries = 0  # belt-and-suspenders; some clients already 0
    except Exception:
        pass

    # S26-follow-up-L: SINGLE attempt per provider. The pre-L policy of
    # "1 attempt + 4 s backoff + 1 attempt" was a hedge against Moonshot
    # transient queue stalls — but the probe evidence shows the stalls
    # are not transient (kimi reliably needs ~290 s on the verifier
    # prompt class). Retrying on transient errors just doubles the
    # primary's wall-clock before the fallback can fire. With a
    # fallback provider available, the second attempt's value is
    # captured by the fallback instead — better signal, different
    # vendor, fresh latency profile.
    response = None
    last_exc: Exception | None = None
    try:
        response = client.chat.completions.create(
            model=model,
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
    except Exception as exc:
        last_exc = exc
        msg = str(exc).lower()
        is_transient = (
            "429" in msg or "overloaded" in msg or "rate" in msg
            or "timeout" in msg or "timed out" in msg
            or "temporarily" in msg
        )
        logger.warning(
            "vision_verifier[%s]: call failed (transient=%s): %s",
            provider_name, is_transient, exc,
        )
        return None, 0.0, (time.monotonic() - started) * 1000, exc

    if response is None:
        return None, 0.0, (time.monotonic() - started) * 1000, last_exc

    cost = 0.0
    try:
        from shared.cost_tracker import record_openai_usage

        record_openai_usage(response, agent_name=_AGENT_NAME, model_hint=model)
        usage = getattr(response, "usage", None)
        prompt_t = getattr(usage, "prompt_tokens", 0) or 0
        completion_t = getattr(usage, "completion_tokens", 0) or 0
        cost = (prompt_t + completion_t) * 0.30 / 1_000_000
    except Exception:
        pass

    elapsed_ms = (time.monotonic() - started) * 1000
    try:
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        raw = ""
    return raw, cost, elapsed_ms, None


async def _call_vision(
    screenshot_png: bytes, prompt: str
) -> tuple[str | None, float, float]:
    """Two-provider vision call: Moonshot (primary) → fallback on exhaustion.

    Returns ``(raw_text, cost_usd, elapsed_ms)`` on success.
    Returns ``(None, 0.0, elapsed)`` after primary + fallback both fail.

    Primary: Moonshot via ``get_openai_client()`` (the production Kimi
    endpoint). Per S26-follow-up-K, the primary path has retry
    policy = 2 attempts × ``_VISION_CALL_TIMEOUT_S`` per attempt + 4 s
    backoff, max_retries=0 on the SDK to prevent compound retries.

    Fallback: triggered ONLY when the primary returns None (failed
    after retries). Controlled by ``VISION_VERIFIER_FALLBACK_MODEL`` —
    defaults to ``qwen3-vl:4b`` via local Ollama (free, no rate limits,
    predictable latency vs Moonshot's queue jitter). Set to "none" to
    disable fallback. Adapter-agnostic — never inspects platform.

    OPRAL rule 5: a Moonshot stall is a recurring failure mode (S26-
    follow-up-L probe evidence: 290 s on full-schema prompts, 124 s
    timeouts on tiny-schema retries). The fallback satisfies "if it
    can recur, the fix is incomplete" by providing a second-source of
    truth without trading off the primary path.
    """
    started = time.monotonic()

    try:
        primary_client = get_openai_client(timeout=_VISION_CALL_TIMEOUT_S)
    except Exception as exc:
        logger.warning("vision_verifier: primary client init failed: %s", exc)
        primary_client = None

    if primary_client is not None:
        raw, cost, ms, exc = await _call_provider(
            primary_client, _VISION_MODEL, prompt, screenshot_png,
            provider_name="primary", timeout=_VISION_CALL_TIMEOUT_S,
        )
        if raw is not None:
            return raw, cost, ms

    if not _fallback_enabled():
        return None, 0.0, (time.monotonic() - started) * 1000

    fb_client = _get_fallback_client(timeout=_FALLBACK_CALL_TIMEOUT_S)
    if fb_client is None:
        return None, 0.0, (time.monotonic() - started) * 1000

    logger.info(
        "vision_verifier: primary exhausted — trying fallback model=%s base_url=%s",
        _FALLBACK_MODEL, _FALLBACK_BASE_URL,
    )
    fb_raw, fb_cost, fb_ms, fb_exc = await _call_provider(
        fb_client, _FALLBACK_MODEL, prompt, screenshot_png,
        provider_name="fallback", timeout=_FALLBACK_CALL_TIMEOUT_S,
    )
    total_ms = (time.monotonic() - started) * 1000
    return fb_raw, fb_cost, total_ms


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
    session_state: Any = None,
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

    # S26-follow-up-M-4 + N-1: scanner ↔ filler coverage surfacing.
    # The verifier only sends to vision the fields the filler claimed to
    # fill. Scanner-discovered fields that weren't claimed are bucketed
    # here so the sidecar JSON enumerates every scanner field in exactly
    # one of: scanner_saw_filler_skipped_required, *_optional, or
    # scanner_noise_excluded (button/file). The verdict-derived buckets
    # (filled_*) are assembled at sidecar-write time from the final
    # verdicts list.
    scanner_unfilled_required: list[dict] = []
    scanner_unfilled_optional: list[dict] = []
    scanner_noise_excluded: list[dict] = []
    if field_metadata:
        claimed_labels = set(filled_mapping.keys())
        claimed_stripped = {_strip_required_marker(lbl) for lbl in claimed_labels}
        for label, meta in field_metadata.items():
            if not isinstance(meta, dict):
                continue
            if label in claimed_labels:
                continue
            if _strip_required_marker(label) in claimed_stripped:
                continue
            ftype = meta.get("type", "")
            if ftype in ("button", "file"):
                scanner_noise_excluded.append({
                    "label": label,
                    "type": ftype,
                    "reason": "noise_excluded",
                })
                continue
            entry = {
                "label": label,
                "type": ftype,
                "reason": "scanner_saw_filler_skipped",
            }
            if meta.get("required"):
                scanner_unfilled_required.append(entry)
            else:
                scanner_unfilled_optional.append(entry)
    if scanner_unfilled_required:
        logger.warning(
            "vision_verifier: %d required field(s) visible to scanner but not filled — page=%d domain=%s labels=%s",
            len(scanner_unfilled_required), page_num, domain,
            [e["label"][:50] for e in scanner_unfilled_required[:5]],
        )

    # 2-3) Resolve locators + capture per-field crops (S26-follow-up-M).
    #      Uses ElementHandle.screenshot() on a dynamically-resolved
    #      form-row container — no coordinate math, no full-page crop.
    field_crops = await _extract_field_crops(page, claim_rows, field_metadata)

    # S26-follow-up-N-2: split crops into DOM-matched (short-circuit to
    # passed without a vision call) and the residue that still needs a
    # vision verdict. ``_capture_field_crop`` flags DOM-matched crops
    # with ``resolve_method='dom_match'`` and leaves ``crop_bytes=None``
    # so the composite naturally excludes them.
    dom_match_by_ordinal: dict[int, FieldCrop] = {
        c.ordinal: c for c in field_crops if c.resolve_method == "dom_match"
    }
    dom_match_labels: set[str] = {
        c.label for c in field_crops if c.resolve_method == "dom_match"
    }
    non_dom_match_crops = [
        c for c in field_crops if c.resolve_method != "dom_match"
    ]
    captured = sum(1 for c in non_dom_match_crops if c.crop_bytes is not None)
    logger.info(
        "vision_verifier: per-field crops %d/%d (dom_match=%d)  page=%d domain=%s",
        captured, len(non_dom_match_crops), len(dom_match_by_ordinal),
        page_num, domain,
    )

    # If every filled field was DOM-matched, vision has nothing to do.
    if not non_dom_match_crops:
        for label, value in claim_rows:
            verdict = FieldVerdict(
                label=label,
                claimed_value=value,
                observed_value=value,
                matches_claim=True,
                contradicts_help_text=False,
                reason="DOM input value matched claim (no vision needed)",
                tier_reached="passed",
            )
            result.verdicts.append(verdict)
            _record_verdict_row(
                verdict,
                page_url=page_url,
                platform=platform,
                confidence=1.0,
                elapsed_ms=0.0,
                trajectory_id=trajectory_id,
            )
        result.elapsed_ms = (time.monotonic() - started) * 1000
        result.scanner_unfilled_required = list(scanner_unfilled_required)
        composite_layout = {
            "panels_total": 0,
            "claims_collapsed_via_dedup": 0,
            "claims_unresolved": 0,
            "image_bytes": 0,
            "dom_match_count": len(dom_match_by_ordinal),
            "fallback_reason": "all_fields_dom_matched",
        }
        _persist_verified_fills_cache(
            result.verdicts, field_metadata, domain, dom_match_labels,
        )
        result.artifact_path = _save_artifact(
            composite=None,
            fallback_screenshot=None,
            domain=domain,
            page_num=page_num,
            verdicts=result.verdicts,
            panels=[],
            page_url=page_url,
            platform=platform,
            cost_usd=result.cost_usd,
            elapsed_ms=result.elapsed_ms,
            chunks_used=0,
            composite_layout=composite_layout,
            scanner_unfilled_required=scanner_unfilled_required,
            scanner_coverage=_compute_scanner_coverage(
                field_metadata, result.verdicts,
                scanner_unfilled_required,
                scanner_unfilled_optional,
                scanner_noise_excluded,
            ),
            coverage_realtime=_compute_coverage_realtime(
                field_metadata, filled_mapping, session_state,
                scanner_unfilled_required,
                scanner_unfilled_optional,
                scanner_noise_excluded,
            ),
        )
        logger.info(
            "vision_verifier: page %d (%s) — all %d field(s) DOM-matched, vision skipped",
            page_num, domain, len(claim_rows),
        )
        return result

    # 4) Build the composite. If no crop could be captured, fall back to
    #    the whole-page screenshot (still single-shot).
    composite_bytes, panels = _build_composite(non_dom_match_crops)
    used_composite = composite_bytes is not None
    if used_composite:
        vision_input = composite_bytes
        ordered_for_prompt = [(p["label"], p["value"]) for p in panels]
        prompt = _build_prompt(ordered_for_prompt, ordinals=True)
        # M-5 naming: `claims_collapsed_via_dedup` is the count of claim
        # rows that share a panel with another claim row (Greenhouse-style
        # duplicate-labeled widgets). They ARE represented in the
        # composite, sharing a panel — not failed captures.
        collapsed = sum(len(p.get("dedup_with") or []) for p in panels)
        unresolved_count = (
            len(claim_rows) - len(panels) - collapsed
            - len(dom_match_by_ordinal)
        )
        composite_layout = {
            "panels_total": len(panels),
            "claims_collapsed_via_dedup": collapsed,
            "claims_unresolved": max(0, unresolved_count),
            "image_bytes": len(composite_bytes),
            "dom_match_count": len(dom_match_by_ordinal),
        }
    else:
        # Fallback: whole page, no ordinals — same shape as pre-K verifier
        # but still single-call. Vision keys verdicts by label text.
        vision_input = _compress_for_vision(screenshot_png)
        # The DOM-matched fields don't need vision; only ask vision about
        # the residue so the prompt list matches what's visible.
        ordered_for_prompt = [
            (label, value) for label, value in claim_rows
            if label not in dom_match_labels
        ] or list(claim_rows)
        prompt = _build_prompt(ordered_for_prompt, ordinals=False)
        composite_layout = {
            "panels_total": 0,
            "claims_collapsed_via_dedup": 0,
            "claims_unresolved": len(non_dom_match_crops),
            "image_bytes": len(vision_input),
            "dom_match_count": len(dom_match_by_ordinal),
            "fallback_reason": "no_field_crops_captured",
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
        result.scanner_unfilled_required = list(scanner_unfilled_required)
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
            scanner_unfilled_required=scanner_unfilled_required,
            scanner_coverage=_compute_scanner_coverage(
                field_metadata, result.verdicts,
                scanner_unfilled_required,
                scanner_unfilled_optional,
                scanner_noise_excluded,
            ),
            coverage_realtime=_compute_coverage_realtime(
                field_metadata, filled_mapping, session_state,
                scanner_unfilled_required,
                scanner_unfilled_optional,
                scanner_noise_excluded,
            ),
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

    # Map every original claim-row ordinal → the panel-position the row
    # was rendered into. Panel positions are contiguous 1..N matching the
    # caption strip + prompt enumeration (S26-follow-up-M-5).
    # Greenhouse-style required + optional copies of the same widget
    # collapse into one panel; ``dedup_with`` carries the original
    # claim-row ordinals collapsed into the kept panel so each original
    # claim row still receives a verdict from the panel's vision output.
    panel_pos_by_claim_ordinal: dict[int, int] = {}
    panel_pos_by_label: dict[str, int] = {}
    for p in panels:
        panel_pos = p["ordinal"]  # post-M-5: this IS the contiguous panel position
        original_claim = p.get("original_ordinal", panel_pos)
        panel_pos_by_claim_ordinal[original_claim] = panel_pos
        for collapsed in p.get("dedup_with", []) or []:
            panel_pos_by_claim_ordinal[collapsed] = panel_pos
        if p.get("label"):
            panel_pos_by_label[p["label"]] = panel_pos

    correct = correction_enabled if correction_enabled is not None else _correction_enabled()
    if correct and fill_callback is None:
        logger.warning(
            "vision_verifier: correction_enabled but no fill_callback — falling back to observe-only",
        )
        correct = False

    verdicts: list[FieldVerdict] = []
    correction_queue: list[int] = []
    for claim_idx, (label, claimed) in enumerate(claim_rows, start=1):
        # S26-follow-up-N-2: DOM pre-check short-circuit. When
        # ``_capture_field_crop`` already confirmed via the DOM that the
        # rendered value matches the claim, vision was never asked about
        # this field — emit a passed verdict directly so the row stays
        # in the sidecar and decision log.
        if claim_idx in dom_match_by_ordinal:
            verdict = FieldVerdict(
                label=label,
                claimed_value=claimed,
                observed_value=claimed,
                matches_claim=True,
                contradicts_help_text=False,
                reason="DOM input value matched claim (no vision needed)",
                tier_reached="passed",
            )
            verdicts.append(verdict)
            continue
        entry: dict | None = None
        if used_composite:
            panel_pos = panel_pos_by_claim_ordinal.get(claim_idx)
            if panel_pos is not None:
                entry = by_ordinal.get(panel_pos)
            if entry is None and label in panel_pos_by_label:
                entry = by_ordinal.get(panel_pos_by_label[label])
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
    result.scanner_unfilled_required = list(scanner_unfilled_required)
    _persist_verified_fills_cache(
        result.verdicts, field_metadata, domain, dom_match_labels,
    )
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
        scanner_unfilled_required=scanner_unfilled_required,
        chunks_used=1,
        composite_layout=composite_layout,
        scanner_coverage=_compute_scanner_coverage(
            field_metadata, result.verdicts,
            scanner_unfilled_required,
            scanner_unfilled_optional,
            scanner_noise_excluded,
        ),
        coverage_realtime=_compute_coverage_realtime(
            field_metadata, filled_mapping, session_state,
            scanner_unfilled_required,
            scanner_unfilled_optional,
            scanner_noise_excluded,
        ),
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
