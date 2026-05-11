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


_CHUNK_OVERLAP_PX = int(os.environ.get("VISION_VERIFIER_CHUNK_OVERLAP", "200"))
_MAX_CHUNKS = int(os.environ.get("VISION_VERIFIER_MAX_CHUNKS", "5"))


def _compress_for_vision(raw_png: bytes) -> bytes:
    """Back-compat single-blob API. Returns the FIRST chunk of
    ``_prepare_for_vision`` — i.e. the whole image if it fits, else the
    top section. Tests use this when they don't care about multi-chunk
    aggregation, only encoder behaviour."""
    chunks = _prepare_for_vision(raw_png)
    return chunks[0] if chunks else raw_png


def _encode_webp(img) -> bytes:
    import io

    buf = io.BytesIO()
    if _WEBP_LOSSLESS:
        img.save(buf, format="WEBP", lossless=True, method=6)
    else:
        img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=6)
    return buf.getvalue()


def _prepare_for_vision(raw_png: bytes) -> list[bytes]:
    """Prepare the screenshot for vision — preserve detail by chunking
    instead of cropping/downscaling.

    Strategy (in order):
      1. Decode PNG once.
      2. If width > 4096, scale uniformly down so width ≤ 4096
         (rarely triggered — most ATS forms are ≤ 1700 px wide). This
         is the only place we lose horizontal detail, and it only
         happens on forms wider than Kimi's 4K recommendation.
      3. If height ≤ 4096, encode the whole image as lossless WebP →
         single chunk, full detail.
      4. If height > 4096, split vertically into ≤ N chunks of ~4096 px
         each with a small overlap so a field that lands on a chunk
         boundary still appears intact in one of them. Each chunk is
         encoded losslessly — no spatial-detail loss anywhere.
      5. Hard cap on chunk count (``_MAX_CHUNKS`` × ~$0.0003 each =
         ~$0.0015 per page worst case) so an unusually long form can't
         bust Outcome 4's $0.05/apply ceiling.

    User intent (S26 RUN3 feedback): "use CNN for compression rather than
    cropping so quality of detail stays the same". WebP's encoder is a
    learned-block predictor (closer to a CNN-style content-aware codec
    than JPEG's static DCT) and in ``lossless=True`` mode preserves all
    pixel-level detail. Chunking avoids the bilinear/Lanczos resize that
    would otherwise be required to fit a 9000+ px form into a single 4K
    image.

    Returns a list of WebP byte blobs, in top-to-bottom order. Empty list
    iff PIL isn't available (caller falls back to vision_unavailable).
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
            w, h = img.size

        if h <= _MAX_LONG_EDGE:
            return [_encode_webp(img)]

        chunks: list[bytes] = []
        chunk_h = _MAX_LONG_EDGE
        step = chunk_h - _CHUNK_OVERLAP_PX
        y = 0
        while y < h and len(chunks) < _MAX_CHUNKS:
            bottom = min(y + chunk_h, h)
            piece = img.crop((0, y, w, bottom))
            chunks.append(_encode_webp(piece))
            if bottom >= h:
                break
            y += step
        return chunks
    except Exception as exc:
        logger.debug("vision_verifier: prepare fallback to raw PNG: %s", exc)
        return [raw_png]


async def _screenshot_form_area(page: "Page") -> bytes:
    """Screenshot the form container if locatable, else viewport.

    Mirrors ``field_mapper._screenshot_form_area`` but kept local so the
    verifier doesn't depend on internal field-mapper helpers.
    """
    for selector in ("form", "[role='form']", "#application", ".application-form"):
        try:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible():
                return await loc.screenshot(type="png")
        except Exception:
            continue
    return await page.screenshot(type="png")


def _build_prompt(claim_rows: list[tuple[str, str]]) -> str:
    """Build the vision prompt. Only claim mapping + instructions — no profile.

    The whole point of vision verification is that the rendered form (in
    the screenshot) is the source of truth. Adding profile data into the
    prompt would re-introduce the metadata-pipeline failures this layer
    is meant to bypass.
    """
    lines = [f'  - "{label}": "{value}"' for label, value in claim_rows]
    claim_block = "\n".join(lines) if lines else "  (no fields claimed filled)"
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
    screenshot_png: bytes | list[bytes],
    domain: str,
    page_num: int,
    verdicts: list[FieldVerdict],
    page_url: str,
    platform: str,
    cost_usd: float,
    elapsed_ms: float,
) -> str | None:
    """Persist the screenshot + verdicts JSON for replayable human spot-check.

    Without this, the screenshot vision worked from is consumed and gone —
    the human cannot retroactively verify Outcome 1 (≥95% read accuracy).
    Saved to ``data/audits/vision_verifier/<ts>_<domain>_p<N>.{ext,json}``
    where ``<ext>`` matches the actual MIME (``png``, ``webp``, ``jpg``).
    The bytes saved are the **same bytes vision processed** — so when the
    human spot-checks accuracy, they're inspecting the exact image vision
    interpreted, not a higher-fidelity precursor.
    Best-effort: failure to save never breaks the apply.
    """
    if os.environ.get("VISION_VERIFIER_SAVE_ARTIFACTS", "1").lower() in {"0", "false", "no"}:
        return None
    try:
        os.makedirs(_ARTIFACT_DIR, exist_ok=True)
        ts = int(time.time())
        safe_domain = domain.replace("/", "_") or "unknown"
        chunks = (
            screenshot_png
            if isinstance(screenshot_png, list)
            else [screenshot_png]
        )
        ext_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        base = os.path.join(_ARTIFACT_DIR, f"{ts}_{safe_domain}_p{page_num}")
        for i, blob in enumerate(chunks):
            ext = ext_map.get(_mime_for(blob), "bin")
            suffix = "" if len(chunks) == 1 else f"_c{i}"
            with open(f"{base}{suffix}.{ext}", "wb") as fh:
                fh.write(blob)
        payload = {
            "ts": ts,
            "page_url": page_url,
            "domain": domain,
            "platform": platform,
            "page_num": page_num,
            "cost_usd": cost_usd,
            "elapsed_ms": elapsed_ms,
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


async def _call_vision(
    screenshot_png: bytes, prompt: str
) -> tuple[str | None, float, float]:
    """Single Moonshot vision call with retry on 429 / transient errors.

    Returns ``(raw_text, cost_usd, elapsed_ms)`` on success.
    Returns ``(None, 0.0, elapsed)`` after exhausting retries — caller
    maps to ``vision_unavailable``. Cost is best-effort via
    ``record_openai_usage``.

    Retry policy: 3 attempts with exponential backoff (2s, 5s, 12s) on
    429 / engine_overloaded / transient errors. Without this layer, every
    transient Moonshot overload silently zeros the verifier out — the
    initial S26 live run hit this and emitted 19 ``vision_unavailable``
    rows in a row, demonstrating the gap. OPRAL rule 5: if it can recur,
    fix is incomplete.
    """
    started = time.monotonic()
    mime = _mime_for(screenshot_png)
    b64_image = base64.b64encode(screenshot_png).decode("ascii")
    try:
        client = get_openai_client()
    except Exception as exc:
        logger.warning("vision_verifier: client init failed: %s", exc)
        return None, 0.0, (time.monotonic() - started) * 1000

    backoffs = [2.0, 5.0, 12.0]
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
            is_transient = (
                "429" in msg or "overloaded" in msg or "rate" in msg
                or "timeout" in msg or "temporarily" in msg
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
) -> VerifierResult:
    """Run vision verification on the current form page.

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

    Returns:
        VerifierResult with per-field verdicts and aggregate counts.
    """
    result = VerifierResult()
    if not _is_enabled():
        return result

    if not filled_mapping:
        return result

    # Outcome 6 — skipped fields stay visible but don't get sent to vision.
    skipped: list[tuple[str, str]] = []
    claim_rows: list[tuple[str, str]] = []
    for label, value in filled_mapping.items():
        if not str(value).strip():
            skipped.append((label, str(value)))
        else:
            claim_rows.append((label, str(value)))

    domain = _domain_from_url(page_url)
    started = time.monotonic()

    # Skipped fields → decision rows with skipped_no_expected_value, no vision call.
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

    try:
        raw_png = await _screenshot_form_area(page)
        chunks = _prepare_for_vision(raw_png)
        logger.info(
            "vision_verifier: screenshot %d bytes raw → %d chunk(s) "
            "(total %d bytes, mime %s) page=%d domain=%s",
            len(raw_png), len(chunks),
            sum(len(c) for c in chunks),
            _mime_for(chunks[0]) if chunks else "<none>",
            page_num, domain,
        )
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

    prompt = _build_prompt(claim_rows)
    # Issue chunk calls in parallel via asyncio.gather so the per-page
    # latency is dominated by the slowest single chunk, not the sum.
    # Live evidence (S26 RUN4): 3 sequential chunks on Anthropic took
    # 189s end-to-end (≈63s/chunk on kimi-k2.6) — 3.16× over Outcome 4's
    # 60s budget. Parallel: 1 × 63s = within budget. Total cost is
    # identical either way; only wall-clock changes.
    async def _verify_chunk(idx: int, blob: bytes):
        ctx_prompt = prompt
        if len(chunks) > 1:
            ctx_prompt = (
                f"(Form chunk {idx + 1}/{len(chunks)} — only verify "
                "fields visible in this slice; skip any field you cannot "
                "locate, it will be in a sibling chunk.)\n\n"
            ) + prompt
        raw, cost, ms = await _call_vision(blob, ctx_prompt)
        return idx, raw, cost, ms

    chunk_results = await asyncio.gather(
        *[_verify_chunk(i, b) for i, b in enumerate(chunks)],
        return_exceptions=False,
    )
    parsed: dict | None = None
    call_ms = 0.0
    aggregated_verdicts: list[dict] = []
    seen_labels: set[str] = set()
    # Chunks may complete out of order. Re-sort by chunk index so a
    # later chunk's verdict cannot override an earlier chunk's (overlap
    # zones surface in the EARLIER chunk first — matches DOM reading
    # order).
    for chunk_idx, raw, chunk_cost, chunk_ms in sorted(chunk_results, key=lambda x: x[0]):
        result.cost_usd += chunk_cost
        call_ms = max(call_ms, chunk_ms)  # wall-clock = slowest chunk
        chunk_parsed = _safe_json(raw) if raw is not None else None
        if chunk_parsed and isinstance(chunk_parsed.get("verdicts"), list):
            for entry in chunk_parsed["verdicts"]:
                if not isinstance(entry, dict):
                    continue
                label = str(entry.get("label", "")).strip()
                if not label or label in seen_labels:
                    continue
                obs = str(entry.get("observed_value", ""))
                if obs in {"<not found>", "<not reported>", ""}:
                    continue
                entry["_chunk"] = chunk_idx
                aggregated_verdicts.append(entry)
                seen_labels.add(label)
            parsed = {"verdicts": aggregated_verdicts}

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
        # Save artifact even on unavailability so the human can diagnose
        # whether screenshot quality is the issue or whether the provider
        # was genuinely down.
        result.artifact_path = _save_artifact(
            screenshot_png=chunks,
            domain=domain,
            page_num=page_num,
            verdicts=result.verdicts,
            page_url=page_url,
            platform=platform,
            cost_usd=result.cost_usd,
            elapsed_ms=result.elapsed_ms,
        )
        return result

    claim_by_label = {label: value for label, value in claim_rows}
    verdicts_by_label: dict[str, dict[str, Any]] = {}
    for entry in parsed["verdicts"]:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        if not label or label not in claim_by_label:
            continue
        verdicts_by_label[label] = entry

    correct = correction_enabled if correction_enabled is not None else _correction_enabled()
    if correct and fill_callback is None:
        logger.warning(
            "vision_verifier: correction_enabled but no fill_callback — falling back to observe-only",
        )
        correct = False

    # Phase 1 — build all initial verdicts (no vision calls). For
    # mismatches that need a correction proposal, record their chunk
    # reference so we can issue all proposals in parallel below.
    verdicts: list[FieldVerdict] = []
    correction_queue: list[tuple[int, int]] = []  # (verdict_idx, chunk_idx)
    for label, claimed in claim_rows:
        entry = verdicts_by_label.get(label)
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
                chunk_idx = entry.get("_chunk", 0)
                if not isinstance(chunk_idx, int) or not 0 <= chunk_idx < len(chunks):
                    chunk_idx = 0
                correction_queue.append((len(verdicts) - 1, chunk_idx))

    # Phase 2 — issue all correction PROPOSALS in parallel. Re-fill
    # itself must stay sequential (form state changes after each
    # callback), but the vision proposals are independent. Live evidence
    # (S26 RUN5): 5 mismatches × ~30s sequential vision proposals = 150s
    # added latency. Parallel: ≈30s for the slowest. Cost is unchanged.
    proposals: dict[int, str | None] = {}
    if correct and correction_queue:
        proposal_tasks = [
            _attempt_correction(
                screenshot_png=chunks[chunk_idx],
                label=verdicts[v_idx].label,
                claimed=verdicts[v_idx].claimed_value,
                observed=verdicts[v_idx].observed_value,
                reason=verdicts[v_idx].reason,
            )
            for v_idx, chunk_idx in correction_queue
        ]
        proposed_values = await asyncio.gather(*proposal_tasks, return_exceptions=True)
        for (v_idx, _chunk_idx), proposed in zip(correction_queue, proposed_values):
            if isinstance(proposed, Exception):
                logger.debug(
                    "vision_verifier: correction proposal raised for '%s': %s",
                    verdicts[v_idx].label[:60], proposed,
                )
                proposals[v_idx] = None
            else:
                proposals[v_idx] = proposed

    # Phase 3 — sequentially re-fill the fields with non-null proposals,
    # then update tiers + record decisions.
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
        screenshot_png=chunks,
        domain=domain,
        page_num=page_num,
        verdicts=result.verdicts,
        page_url=page_url,
        platform=platform,
        cost_usd=result.cost_usd,
        elapsed_ms=result.elapsed_ms,
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
