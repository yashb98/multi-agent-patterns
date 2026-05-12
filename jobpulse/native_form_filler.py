"""NativeFormFiller — Playwright native form-filling orchestrator.

Thin coordinator that delegates to focused modules in jobpulse/form_engine/:
- field_scanner: a11y tree + Playwright field discovery
- field_resolver: lookup tables + deterministic answer resolution
- field_mapper: LLM mapping, screening, recovery, vision fallback
- file_uploader: CV/CL uploads, consent, modal CV handling
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from typing import TYPE_CHECKING, Any

from shared.agents import get_openai_client, get_model_name
from shared.logging_config import get_logger
from shared.pii import assert_prompt_has_wrapped_pii

from jobpulse.content_hasher import compute_content_hash
from jobpulse.form_engine.field_resolver import (
    _best_option_match,
    _build_option_aliases,
    _canonicalize_country_value,
    _COUNTRY_DATA,
    _country_from_location,
    _ensure_label_db,
    _FIELD_LABEL_TO_PROFILE_KEY,
    _fuzzy_label_to_profile_key,
    _get_field_gap,
    _normalize_match_text,
    _persist_label_mapping,
    _profile_prompt_json,
    _screening_prompt_background,
    _screening_prompt_profile,
)
from jobpulse.form_engine.field_scanner import (
    get_accessible_name,
    scan_fields,
)
from jobpulse.form_engine.field_mapper import (
    clean_mapping,
    is_screening_like_field,
    learn_field_mapping,
    map_fields,
    map_fields_with_confidence,
    recover_failed_fields_with_llm,
    recover_failed_fields_with_vision,
    review_form,
    screen_questions,
    seed_mapping,
    try_cached_mapping,
    vision_map_unlabeled_fields,
)
from jobpulse.form_engine.file_uploader import (
    check_consent,
    handle_modal_cv_upload,
    upload_files,
    upload_pdf,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

# Re-export for backward compatibility (tests import these from native_form_filler)
__all__ = [
    "NativeFormFiller",
    "emit_form_fill_failures",
    "_best_option_match",
    "_build_option_aliases",
    "_canonicalize_country_value",
    "_fuzzy_label_to_profile_key",
    "_screening_prompt_background",
    "_screening_prompt_profile",
]

MAX_FORM_PAGES = 20

_SKIP_FILL_LABELS = frozenset({"middle name"})

_SELECT_PLACEHOLDER_RE = re.compile(
    r"^(|—.*—|please select.*|select\b.*|choose\b.*|-999|not applicable)$",
    re.IGNORECASE,
)


def _is_select_placeholder(value: str) -> bool:
    return bool(_SELECT_PLACEHOLDER_RE.match(value.strip()))


async def _resolve_listbox_scope(page, el):
    """Return a Playwright Page-or-Locator scoped to *this* combobox's own
    listbox so option-lookup queries don't match unrelated open dropdowns
    elsewhere on the page.

    Resolution order (most precise first):
    1. ``aria-controls`` / ``aria-owns`` on the input → ``#<id>`` locator.
       This is the WAI-ARIA contract for combobox→listbox association,
       so it fires for any spec-compliant component (Greenhouse React
       Select sets ``aria-controls`` to the menu's React-generated id).
    2. ``.select__control`` ancestor → following ``.select__menu`` sibling
       (React Select's portal-or-sibling pattern when aria-controls is
       missing on a re-rendered input).
    3. Closest ancestor with ``role='listbox'`` or class containing
       ``select__menu`` (other component libraries).
    4. Last resort: the page itself, returned as-is. Calling code logs a
       warning when this happens.

    Live evidence (Anthropic Greenhouse, 2026-05-09 run): the visa
    Yes/No combobox and an iti-0 phone country picker were both open at
    fill time. Without this scoping the option click matched
    ``[role='option']`` inside ``iti__country`` and selected ``Norway``
    for the visa field.
    """

    try:
        listbox_id = await el.get_attribute("aria-controls")
        if not listbox_id:
            listbox_id = await el.get_attribute("aria-owns")
    except Exception:
        listbox_id = None
    if listbox_id:
        scoped = page.locator(f"#{listbox_id}")
        try:
            if await scoped.count():
                return scoped
        except Exception:
            pass

    # React Select standard pattern: listbox id is `react-select-{input_id}-listbox`
    # — Anthropic / other Greenhouse forms don't set aria-controls but follow this
    # naming. Verified live on 2026-05-10: visa combobox input has id
    # 'question_4089394008', listbox has id 'react-select-question_4089394008-listbox'.
    try:
        input_id = await el.get_attribute("id")
    except Exception:
        input_id = None
    if input_id:
        rs_listbox_id = f"react-select-{input_id}-listbox"
        rs_scoped = page.locator(f"#{rs_listbox_id}")
        try:
            if await rs_scoped.count():
                return rs_scoped
        except Exception:
            pass

    # React Select wraps both .select__control and .select__menu inside the
    # same .select-shell container, but they are NOT siblings — the menu is
    # a separate child of .select-shell rendered when open. The previous
    # following-sibling XPath returned count=0 on Anthropic's structure;
    # descend from .select-shell to find the menu.
    try:
        descendant_menu = el.locator(
            "xpath=ancestor::*[contains(@class,'select-shell')][1]"
            "//*[contains(@class,'select__menu')][1]"
        )
        if await descendant_menu.count():
            return descendant_menu
    except Exception:
        pass

    # Older fallback path (kept for non-shell React Select variants where
    # the menu IS a following sibling of the .select__control).
    try:
        sibling = el.locator(
            "xpath=ancestor::*[contains(@class,'select__control')][1]"
            "/following-sibling::*[contains(@class,'select__menu')][1]"
        )
        if await sibling.count():
            return sibling
    except Exception:
        pass

    try:
        role_listbox = el.locator(
            "xpath=ancestor-or-self::*"
            "[@role='listbox' or contains(@class,'select__menu')][1]"
        )
        if await role_listbox.count():
            return role_listbox
    except Exception:
        pass

    return page


def _align_screening_to_options(
    answer: str, field: dict, label_for_log: str = "",
) -> str:
    """Validate a screening answer against the field's known options.

    Returns the option-aligned answer if it fits, or "" when it doesn't (which
    the caller treats as "fall through to the next tier"). For free-text
    fields or fields without options the answer is returned unchanged.

    Why this exists: the legacy `screening_answers.COMMON_ANSWERS` regex map
    and other early-tier resolvers were written before the form-fill engine
    could see the dropdown options at decision time. They emit fixed strings
    like "Yes, within the UK" / "No" / "Norway" without knowing whether the
    current field has those choices. The form filler then types those values
    into a closed-set picker and accidentally selects the first autocomplete
    match — visa fields end up filled with country names, EEO fields end up
    filled with "No". This helper lets every screening tier fail closed.
    """
    from shared.db_observability import (
        DROP_OPTION_MISALIGNMENT,
        mark_fill_outcome,
    )

    options = field.get("options") or []
    ftype = (field.get("true_type") or field.get("type") or "").lower()
    field_label = label_for_log or field.get("label", "")
    if not options or ftype not in {
        "select", "combobox", "radio", "checkbox", "custom_dropdown",
        "multiselect",
    }:
        # Free-text path: any prior DB lookup that produced this answer is
        # considered consumed (no alignment shaved it).
        if answer:
            mark_fill_outcome(field_label, intended=answer, actual=answer)
        return answer
    try:
        from jobpulse.screening_option_aligner import OptionAligner
        aligner = OptionAligner()
        aligned = aligner.align_answer(str(answer), options, ftype)
    except Exception:
        if answer:
            mark_fill_outcome(field_label, intended=answer, actual=answer)
        return answer
    opts_lower = {(o or "").lower().strip() for o in options}
    if (aligned or "").lower().strip() in opts_lower:
        # If alignment changed the value (e.g. "Yes, within the UK" → "Yes"),
        # mark the underlying lookup as dropped with reason
        # option_misalignment. If alignment kept the value, mark consumed.
        if str(answer).strip().casefold() != (aligned or "").strip().casefold():
            mark_fill_outcome(
                field_label, intended=answer, actual=aligned,
                drop_reason=DROP_OPTION_MISALIGNMENT,
            )
        else:
            mark_fill_outcome(field_label, intended=answer, actual=aligned)
        return aligned
    logger.warning(
        "screening answer %r did not align to any option for %r — dropping "
        "(opts=%s)",
        str(answer)[:60], (field_label)[:60],
        [o[:25] for o in options[:5]],
    )
    # The aligner couldn't resolve the value at all. Tag the underlying
    # lookup as dropped — caller will fall through to the next tier.
    mark_fill_outcome(
        field_label, intended=answer, actual="",
        drop_reason=DROP_OPTION_MISALIGNMENT,
    )
    return ""


_REQUIRED_MARKER_RE = re.compile(
    r"\s*(?:\*|\(\s*required\s*\)|\brequired\b|\(\s*\*\s*\))\s*$",
    re.IGNORECASE,
)


def _strip_required_marker(label: str) -> str:
    """Remove trailing required-field markers from a label.

    Examples:
        'Email*'              -> 'Email'
        'Phone *'             -> 'Phone'
        'LinkedIn URL (required)' -> 'LinkedIn URL'
        'Name Required'       -> 'Name'

    Markers are rendered visually via CSS pseudo-elements or adjacent <span>s
    on most ATSs (Greenhouse, Lever, Ashby). Playwright's get_by_label
    matches the underlying text, so the literal asterisk in our planned label
    prevents the match. Strip it here so all downstream matchers see the
    canonical label.

    Format-validation regex is acceptable per the no-regex-for-classification
    rule — this is structural normalization, not semantic routing.
    """
    if not label:
        return label
    return _REQUIRED_MARKER_RE.sub("", label).rstrip()


def emit_form_fill_failures(
    failures: list[dict], *, domain: str,
) -> None:
    """Emit OptimizationEngine 'failure' signals for unverified fills.

    Mirrors action_executor.emit_fill_failures but with source='form_filler'
    so the learning DBs see corrections from BOTH paths (navigator and the
    main NativeFormFiller form-fill loop).

    failures: list of {"label": str, "expected": str, "actual": str}
    """
    if not failures:
        return
    try:
        from datetime import UTC, datetime
        from shared.optimization import get_optimization_engine
        engine = get_optimization_engine()
        session_id = f"nff_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        for f in failures:
            engine.emit(
                signal_type="failure",
                source_loop="form_filler",
                domain=domain,
                agent_name="native_form_filler",
                payload={
                    "field": f.get("label", ""),
                    "expected": (f.get("expected") or "")[:60],
                    "actual": (f.get("actual") or "")[:60],
                    "kind": "fill_mismatch",
                },
                session_id=session_id,
            )
    except Exception as exc:
        logger.debug("emit_form_fill_failures: signal failed: %s", exc)


def _resolve_dropdown_from_profile(question: str, options: list[str]) -> str | None:
    """Resolve dropdown option using applicant profile context (WORK_AUTH).

    Handles work-auth dropdowns where options describe visa/sponsorship status
    rather than simple Yes/No.
    """
    q_lower = question.lower()
    opts_lower = [o.lower() for o in options]
    has_sponsorship_opts = any("sponsorship" in o or "visa" in o for o in opts_lower)
    if not has_sponsorship_opts:
        return None
    if not ("work" in q_lower or "right" in q_lower or "visa" in q_lower or "sponsor" in q_lower):
        return None
    try:
        from jobpulse.config import WORK_AUTH
        needs_sponsorship = bool(WORK_AUTH.get("requires_sponsorship"))
        visa_status = str(WORK_AUTH.get("visa_status", "")).lower()

        for opt, opt_lower in zip(options, opts_lower):
            if needs_sponsorship and "require sponsorship" in opt_lower and "not" not in opt_lower:
                return opt
            if not needs_sponsorship and "not requiring sponsorship" in opt_lower:
                return opt
            if not needs_sponsorship and "without sponsorship" in opt_lower:
                return opt

        if not needs_sponsorship:
            for opt, opt_lower in zip(options, opts_lower):
                if "visa" in opt_lower and "not requiring" in opt_lower:
                    return opt
                if "obtain" in opt_lower and "visa" in opt_lower:
                    return opt
        if "permanent" in visa_status or "citizen" in visa_status or "settled" in visa_status:
            for opt, opt_lower in zip(options, opts_lower):
                if "permanent" in opt_lower:
                    return opt
    except Exception:
        pass
    return None


def _get_adaptive_page_delay(platform: str, timing_data: dict | None) -> float:
    """Return adaptive page delay based on measured timing data.
    Returns 0 when FAST_FILL=true (Claude Code assisted mode).
    """
    if os.environ.get("FAST_FILL"):
        return 0.0

    if timing_data:
        measured = timing_data.get("avg_fill_ms", 5000) / 1000.0
        return max(measured * 1.1, 3.0)

    _STRATEGY_DEFAULTS = {
        "workday": 8.0,
        "linkedin": 3.0,
        "greenhouse": 5.0,
        "lever": 5.0,
        "indeed": 8.0,
    }
    return _STRATEGY_DEFAULTS.get(platform, 5.0)


def _log_field_trajectory(
    job_id: str, domain: str, field_label: str, field_type: str,
    strategy: str, value: str, confidence: float, time_ms: int,
    page_index: int = 0,
) -> None:
    """Log a field fill to the TrajectoryStore. Non-blocking."""
    try:
        from jobpulse.trajectory_store import get_trajectory_store
        get_trajectory_store().log_field(
            job_id=job_id, domain=domain, field_label=field_label,
            strategy=strategy, value_filled=value,
            field_type=field_type, confidence=confidence,
            time_ms=time_ms, page_index=page_index,
        )
    except Exception as exc:
        logger.debug("trajectory log_field failed: %s", exc)


def _load_field_overrides(domain: str) -> dict[str, dict]:
    """Load agent rule overrides for this domain. Non-blocking."""
    try:
        from jobpulse.agent_rules import AgentRulesDB
        return AgentRulesDB().get_field_overrides(domain=domain)
    except Exception as exc:
        logger.debug("agent_rules override load failed: %s", exc)
        return {}


def _load_heuristics(domain: str, platform: str) -> str:
    """Load heuristics context for LLM prompts. Non-blocking."""
    try:
        from jobpulse.trajectory_store import load_heuristics_for_application
        result = load_heuristics_for_application(domain, platform=platform)
        context = result.get("prompt_context", "")
        if context:
            logger.info("Loaded %d domain + %d platform heuristics for %s",
                        len(result["domain_heuristics"]),
                        len(result["platform_heuristics"]), domain)
        return context
    except Exception as exc:
        logger.debug("heuristic loading failed: %s", exc)
        return ""


def _classify_fill_failure(result: dict) -> str:
    """Classify why a field fill failed to route to correct recovery."""
    error = (result.get("error") or "").lower()
    if "no field" in error or "not found" in error or "no fillable" in error:
        return "no_field"
    if "intercept" in error or "pointer" in error or "click" in error:
        return "blocked"
    if result.get("value_mismatch"):
        return "wrong_value"
    if "readonly" in error or "disabled" in error:
        return "readonly"
    return "unknown"


# Audit 2026-05-10 / Slice S12 / TP-24 — silent field-drop invariant.
# Surfaced live on Graphcore: a required combobox was scanned but never
# attempted, with no fill ✓ / fill ✗ log emission. Apply still routed to
# `queued_for_review` because the fill loop's success accounting only
# counts attempted fields. This helper computes the diff between scanned
# fields and attempted fields so callers can emit `fill ⊘` log lines and
# include the count in agent_fill_stats.
#
# `radio` and `custom_dropdown` types are filled by separate loops
# (_fill_radio_groups / _fill_custom_dropdowns) so absence from the main
# `attempted_labels` set is expected and not a silent drop.
def _compute_silent_drops(
    visible_fields: list[dict],
    attempted_labels: set[str],
) -> list[dict]:
    """Return fields visible to the scanner but never touched by the fill loop.

    Args:
        visible_fields: scanned field dicts (`{label, type, options, required}`)
            from `field_scanner.scan(...)`. May omit `type` / `required`.
        attempted_labels: labels the main fill loop tried (whether they
            succeeded or failed). Radio/custom_dropdown labels are excluded
            from this set even when filled — those are handled by their
            own emission loops downstream.

    Returns:
        list of `{label, type, required, reason}` dicts for each silent drop.
        Empty list if every visible field was either attempted or routed to
        a separate fill loop.
    """
    drops: list[dict] = []
    for field in visible_fields:
        label = field.get("label", "")
        if not label or label in attempted_labels:
            continue
        ftype = field.get("type", "")
        # Radio + custom_dropdown have dedicated fill loops; not silent drops.
        if ftype in ("radio", "custom_dropdown"):
            continue
        drops.append({
            "label": label,
            "type": ftype,
            "required": bool(field.get("required", False)),
            "reason": "no_mapping",
        })
    return drops


class NativeFormFiller:
    """Playwright-native form filler using locators and LLM calls."""

    def __init__(self, page: "Page", driver: Any) -> None:
        self._page = page
        self._driver = driver
        self._correction_warning: str = ""
        self._llm_fallback_count: int = 0
        self._profile_store: Any = None
        self._known_domain: bool = False
        self._platform_strategy: dict[str, Any] | None = None
        self._domain_field_mappings: dict[str, str] = {}
        self._cached_screening: dict[str, str] = {}
        self._iframe_resolved: bool = False
        self._strategy: Any = None
        self._fe_db: Any = None
        self._container_selector: str | None = None
        self._platform: str = ""
        # Per-page live form state captured right before each Next/Continue click.
        # Surfaces user mid-flow edits to live_review_applicator for correction
        # capture. Without this, only the read-only review page is scanned and
        # screening-page edits are lost (live regression on Forge 2026-05-05).
        self._per_page_live_snapshots: list[dict[str, str]] = []
        self._intelligence: Any = getattr(driver, "intelligence", None)
        self._signal_interpreter: Any = None
        if self._intelligence:
            try:
                from jobpulse.signal_interpreter import SignalInterpreter
                self._signal_interpreter = SignalInterpreter()
            except Exception:
                pass

    # ── Platform Strategy + Domain Knowledge ──

    def _load_platform_strategy(self, platform: str) -> None:
        from jobpulse.config import DATA_DIR
        strategy_path = DATA_DIR / "platform_strategies" / f"{platform}.json"
        if strategy_path.exists():
            try:
                with open(strategy_path) as f:
                    self._platform_strategy = json.load(f)
                logger.info("Loaded platform strategy for %s (%d quirks)",
                            platform, len(self._platform_strategy.get("quirks", [])))
            except Exception as exc:
                logger.debug("Could not load platform strategy: %s", exc)

    def _load_domain_field_mappings(self) -> None:
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if not url:
                return
            db = FormExperienceDB()
            self._domain_field_mappings = db.get_field_mappings(url)
            global_mappings = db.get_field_mappings("_global")
            for label, key in global_mappings.items():
                self._domain_field_mappings.setdefault(label, key)
            if self._domain_field_mappings:
                logger.info("Loaded %d field mappings for %s (%d global)",
                            len(self._domain_field_mappings),
                            FormExperienceDB.normalize_domain(url),
                            len(global_mappings))
                logger.info(
                    "DIAG field_mapping_keys (first 15): %s",
                    list(self._domain_field_mappings.keys())[:15],
                )
        except Exception as exc:
            logger.debug("Could not load domain field mappings: %s", exc)

    def _load_cached_screening_answers(self) -> None:
        try:
            from jobpulse.job_db import JobDB
            self._cached_screening = JobDB().get_all_cached_answers()
            if self._cached_screening:
                logger.info("Loaded %d cached screening answers", len(self._cached_screening))
        except Exception as exc:
            logger.debug("Could not load cached screening answers: %s", exc)

    async def _resolve_page_context(self) -> None:
        if self._iframe_resolved:
            return
        self._iframe_resolved = True

        page = self._page
        iframe_names = []
        if self._platform_strategy:
            for quirk in self._platform_strategy.get("quirks", []):
                if isinstance(quirk, str) and quirk.startswith("iframe:"):
                    iframe_names.append(quirk.split(":", 1)[1].strip())

        iframe_names.append("icims_content_iframe")

        for name in iframe_names:
            try:
                frame = page.frame(name=name)
                if frame is not None:
                    self._page = frame  # type: ignore[assignment]
                    logger.info("Switched to iframe '%s' for form filling", name)
                    return
            except Exception:
                pass

    async def _fill_by_element_ids(
        self, profile: dict[str, str], custom_answers: dict[str, Any],
    ) -> dict[str, str]:
        if not self._domain_field_mappings:
            return {}

        from jobpulse.applicator import PROFILE, WORK_AUTH
        profile_flat = {**PROFILE, **profile}

        # Filter to keys that could plausibly be HTML element IDs.
        # _domain_field_mappings is polluted by _global label-keyed mappings
        # that get merged into the same dict — those labels (with spaces,
        # '*', '?', '(', '@') will always fail document.getElementById and
        # waste the JS evaluate budget. Per HTML5 spec an ID just can't
        # contain whitespace; we also reject obvious label artefacts.
        def _looks_like_html_id(key: str) -> bool:
            if not key or len(key) > 64:
                return False
            for ch in key:
                # whitespace, asterisk, question mark, parens, at-sign,
                # newline, etc. all disqualify an HTML id
                if ch.isspace() or ch in "*?()@!":
                    return False
            return True

        fills: dict[str, str] = {}
        skipped_label_keys: list[str] = []
        for element_id, profile_key in self._domain_field_mappings.items():
            if not _looks_like_html_id(element_id):
                skipped_label_keys.append(element_id)
                continue
            value = profile_flat.get(profile_key, "")
            if not value:
                value = custom_answers.get(profile_key, "")
            if value:
                fills[element_id] = str(value)

        if skipped_label_keys:
            logger.debug(
                "_fill_by_element_ids: skipped %d non-ID-shaped keys (labels merged "
                "from _global mappings — handled by label path instead): %s",
                len(skipped_label_keys), skipped_label_keys[:5],
            )

        if not fills:
            return {}

        page = self._page
        results = await page.evaluate("""(fills) => {
            const out = {};
            for (const [id, val] of Object.entries(fills)) {
                const el = document.getElementById(id);
                if (!el) { out[id] = 'NOT_FOUND'; continue; }
                const tag = el.tagName.toLowerCase();
                if (tag === 'select') {
                    let found = false;
                    for (let i = 0; i < el.options.length; i++) {
                        if (el.options[i].value === val || el.options[i].textContent.trim() === val) {
                            el.selectedIndex = i;
                            found = true;
                            break;
                        }
                    }
                    out[id] = found ? 'SET' : 'NO_OPTION';
                } else if (tag === 'input' || tag === 'textarea') {
                    const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    out[id] = 'SET';
                } else {
                    out[id] = 'UNKNOWN_TAG';
                }
            }
            return out;
        }""", fills)

        filled = {k: fills[k] for k, v in results.items() if v == "SET"}
        failed = {k: v for k, v in results.items() if v != "SET"}
        if filled:
            logger.info("DIRECT ID FILL: %d/%d fields set in single evaluate()",
                        len(filled), len(fills))
        if failed:
            logger.warning("DIRECT ID FILL: %d fields failed: %s", len(failed), failed)
        return filled

    # ── Label Extraction (delegates to field_scanner) ──

    async def _get_accessible_name(self, locator: Any) -> str:
        return await get_accessible_name(locator)

    # ── Field Scanning (delegates to field_scanner) ──

    async def _scan_fields(self) -> list[dict]:
        fields = await scan_fields(
            self._page,
            strategy=self._strategy,
            form_experience_db=self._fe_db,
            container_selector=self._container_selector,
        )
        # Stash per-label metadata so _fill_by_label can consult e.g.
        # semantic-scanner attached selectors without re-scanning.
        self._fields_by_label = {
            f["label"]: f for f in (fields or []) if f.get("label")
        }
        return fields

    # ── Auto-Gotcha Learning ──

    def _save_gotcha(self, label: str, problem: str, solution: str) -> None:
        try:
            from urllib.parse import urlparse
            from jobpulse.form_engine.gotchas import GotchasDB

            url = getattr(self._page, 'url', '') or ''
            if not url:
                return
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            db = GotchasDB()
            db.store(domain, label, problem, solution, engine="playwright")
        except Exception as exc:
            logger.debug("Could not save gotcha: %s", exc)

    @staticmethod
    def _fingerprint_fields(fields: list[dict]) -> str:
        parts = sorted(f"{f.get('type', '')}:{f.get('label', '')}" for f in fields)
        return "|".join(parts)

    async def _try_cognitive_unstuck(
        self, fields: list[dict], platform: str, page_url: str
    ) -> bool:
        """Use cognitive reasoning to escape a stuck form page.

        Called when the same field fingerprint appears for consecutive pages.
        Asks the LLM for a structured action, then executes it.
        Returns True if the action changed the page state.
        """
        try:
            from shared.cognitive import get_cognitive_engine

            engine = get_cognitive_engine("form_filler")
            if not engine:
                return False

            field_summary = "\n".join(
                f"- {f.get('label', 'unknown')} ({f.get('type', 'unknown')})"
                + (f" [required]" if f.get("required") else "")
                + (f" [value: {f.get('value', '')}]" if f.get("value") else " [empty]")
                for f in fields[:15]
            )
            task = (
                f"Platform: {platform}\n"
                f"URL: {page_url}\n"
                f"Current form fields:\n{field_summary}\n\n"
                "The form appears stuck — the same page keeps appearing after clicking Next/Continue. "
                "Common causes: required field empty, validation error, wrong button clicked, unchecked consent.\n\n"
                'Return ONLY a JSON object: {{"action": "click_button"|"check_required"|"scroll_down", '
                '"target": "button text or field label", "reason": "why this should work"}}'
            )
            result = engine.think_sync(
                task=task,
                domain="form_navigation",
                stakes="medium",
            )
            # Score is None when the classifier picks L1 with no scorer
            # (the dominant case here). Treat unscored answers as below
            # threshold so they don't bypass the gate. S6 audit B-1.
            if not result or (result.score or 0.0) < 5.0:
                return False

            suggestion = result.answer.strip()
            logger.info("Cognitive unstuck suggestion (score=%.1f): %s", result.score, suggestion[:200])

            acted = await self._execute_unstuck_action(suggestion)

            try:
                from shared.optimization import get_optimization_engine
                get_optimization_engine().emit(
                    signal_type="adaptation",
                    source_loop="native_form_filler",
                    domain=platform,
                    agent_name="form_filler",
                    payload={
                        "param": "stuck_recovery",
                        "old_value": "abort",
                        "new_value": suggestion,
                        "acted": acted,
                        "reason": f"Cognitive unstuck for {platform}",
                    },
                )
            except Exception:
                pass
            return acted
        except Exception as exc:
            # With the score-coalesce fix above, a real exception here is
            # an actual bug, not the routine None-score path. Surface at
            # warning so it shows up in default logs. S6 audit B-1.
            logger.warning("Cognitive unstuck failed: %s", exc)
        return False

    async def _execute_unstuck_action(self, suggestion: str) -> bool:
        """Parse and execute the cognitive engine's unstuck suggestion."""
        page = self._page

        try:
            cleaned = suggestion
            if "{" in cleaned:
                cleaned = cleaned[cleaned.index("{"):cleaned.rindex("}") + 1]
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            data = {"action": "click_button", "target": suggestion[:80]}

        action = data.get("action", "")
        target = data.get("target", "")

        if action == "click_button" and target:
            for role in ("button", "link"):
                loc = page.get_by_role(role, name=target, exact=False)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    await asyncio.sleep(2)
                    logger.info("Unstuck: clicked %s '%s'", role, target)
                    return True

        if action == "check_required":
            unchecked = page.get_by_role("checkbox").filter(has_not=page.locator(":checked"))
            for i in range(min(await unchecked.count(), 5)):
                cb = unchecked.nth(i)
                name = await get_accessible_name(cb) or ""
                from jobpulse.form_engine.semantic_matcher import checkbox_intent
                if checkbox_intent(name) is True or checkbox_intent(name) is None:
                    await cb.check()
                    logger.info("Unstuck: checked checkbox '%s'", name[:60])
            return True

        if action == "scroll_down":
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(1)
            logger.info("Unstuck: scrolled down")
            return True

        return False

    # ── Browser Signal Intelligence ──

    async def _check_browser_signals(
        self, field_label: str, field_locator: Any, fill_timestamp_ms: float,
    ) -> Any:
        if not self._intelligence or not self._signal_interpreter:
            return None
        try:
            return await self._signal_interpreter.check_after_fill(
                self._intelligence, field_label, field_locator,
                fill_timestamp_ms, self._page,
            )
        except Exception as exc:
            logger.debug("Browser signal check failed: %s", exc)
            return None

    def _pre_fill_transform(self, domain: str, field_label: str, value: str) -> str:
        if not self._fe_db:
            return value
        try:
            corrections = self._fe_db.get_signal_corrections(domain, field_label)
            if corrections:
                from jobpulse.signal_interpreter import TRANSFORMS
                transform_name = corrections[0]["transform"]
                transform_fn = TRANSFORMS.get(transform_name)
                if transform_fn:
                    transformed = transform_fn(value)
                    if transformed != value:
                        logger.info(
                            "Pre-fill transform '%s' on '%s': '%s' -> '%s'",
                            transform_name, field_label, value[:30], transformed[:30],
                        )
                    return transformed
        except Exception as exc:
            logger.debug("Pre-fill transform lookup failed: %s", exc)
        return value

    # ── Human-Like Behavior (delegates to driver) ──

    async def _smart_scroll(self, el: Any) -> None:
        if hasattr(self._driver, '_smart_scroll'):
            await self._driver._smart_scroll(el)
        else:
            # 5s timeout (was Playwright default 30s). When element is
            # technically visible but obscured (cookie modal, sticky
            # header, off-viewport honeypot), 30s blocks the entire fill
            # chain. 5s is enough for any legitimate scroll animation.
            try:
                await el.scroll_into_view_if_needed(timeout=5000)
            except Exception as exc:
                logger.debug("_smart_scroll: scroll timeout — proceeding anyway: %s", exc)

    async def _move_mouse_to(self, el: Any) -> None:
        if hasattr(self._driver, '_move_mouse_to'):
            await self._driver._move_mouse_to(el)

    async def _normalize_phone_value(self, label: str, value: str) -> str:
        if "phone" not in _normalize_match_text(label):
            return value

        digits = re.sub(r"\D+", "", value)
        if not digits:
            return value

        phone_code = "44"
        store = getattr(self, "_profile_store", None)
        if store:
            country = _country_from_location(store.identity().location or "")
            if country:
                country_info = _COUNTRY_DATA.get(country, ())
                for alias in country_info:
                    if alias.startswith("+"):
                        phone_code = alias.lstrip("+")
                        break

        has_split_country_code = False
        try:
            country_hint = self._page.get_by_text(f"+{phone_code}", exact=False)
            has_split_country_code = bool(await country_hint.count())
        except Exception:
            has_split_country_code = False

        if has_split_country_code:
            if digits.startswith(phone_code):
                digits = digits[len(phone_code):]
            if digits.startswith("0") and len(digits) >= 10:
                digits = digits[1:]
            return digits

        if digits.startswith("0") and len(digits) >= 10:
            return f"+{phone_code}{digits[1:]}"
        if digits.startswith(phone_code):
            return f"+{digits}"
        return value

    # ── Fill By Label ──

    async def _escalate_fill(
        self, *, label: str, value: str, failure_tier: str,
    ) -> dict:
        """Plan E: route stuck-field cases through the CognitiveEngine.

        Called when _fill_by_label has exhausted label-string lookup,
        placeholder, role fallback, intent_healing, and LLM-recovery.
        The engine sees the page snapshot, the label, the intended
        value, and the failure tier; it returns a structured plan
        which is executed via _fill_resolved_widget.

        On success: the executed action's selector + widget_type land
        in GotchasDB.widget_patterns (via ai_assist_logger.record_fix
        with dom_signature) so future visits pick up the widget via
        _scan_learned_patterns and skip escalation entirely.

        On failure: the caller logs and returns; existing Telegram
        approval-request bypass is the human floor (unchanged path).
        """
        try:
            from urllib.parse import urlparse
            from shared.agents import cognitive_llm_call

            page = self._page
            if page is None:
                return {"success": False, "error": "no page"}
            url = page.url or ""
            domain = urlparse(url).netloc.lower().removeprefix("www.")

            # Compact failure context for the engine. Cheap snapshot —
            # the existing scan_fields cache should serve this.
            try:
                fields = await self._scan_fields()
            except Exception:
                fields = []
            visible_buttons: list[dict] = []
            try:
                visible_buttons = await page.evaluate(
                    """() => [...document.querySelectorAll('button, [role="button"]')]
                        .filter(b => b.offsetParent !== null)
                        .slice(0, 30)
                        .map(b => ({
                            text: ((b.innerText || b.getAttribute('aria-label') || '') + '').trim().slice(0, 60),
                            id: b.id || null,
                            role: b.getAttribute('role') || '',
                            haspopup: b.getAttribute('aria-haspopup') || '',
                        }))
                        .filter(x => x.text)"""
                )
            except Exception:
                pass

            # Live regression on Revolut welovealfa.com 2026-05-06:
            # the engine returned `select[name='visa_sponsorship']` on
            # all 3 retry attempts — a hallucinated selector that
            # doesn't exist on the page. The fix: include the SCANNER'S
            # ACTUAL SELECTORS in the field summary so the engine picks
            # from real candidates, not from prior knowledge of standard
            # form names.
            field_summary = "\n".join(
                f"- label={(f.get('label') or '')[:80]!r} "
                f"type={f.get('type', '?')} "
                f"selector={(f.get('selector') or 'unknown')[:100]!r}"
                for f in (fields or [])[:25]
            )
            button_summary = "\n".join(
                f"- text={b.get('text', '')[:60]!r} "
                f"id={b.get('id') or '(none)'!r} "
                f"role={b.get('role') or 'button'} "
                f"haspopup={b.get('haspopup') or 'none'}"
                for b in visible_buttons[:20]
            )

            base_prompt = (
                f"You are recovering a stuck form-fill on {domain}. The agent "
                f"could not find or fill the following field after exhausting "
                f"label lookup, placeholder, role fallback, intent_healing, and "
                f"LLM recovery.\n\n"
                f"Field label: {label!r}\n"
                f"Intended value: {value!r}\n"
                f"Failure tier reached: {failure_tier!r}\n\n"
                f"IMPORTANT: pick a selector ONLY from the lists below. Do NOT "
                f"invent standard form names like select[name='visa_sponsorship'] "
                f"— React forms rarely use plain HTML name attributes. If none "
                f"of the listed selectors matches the field, return action='abort'.\n\n"
                f"Visible fields on page ({len(fields or [])}):\n{field_summary}\n\n"
                f"Visible buttons ({len(visible_buttons)}):\n{button_summary}\n\n"
                f"Return ONLY a JSON object describing one executable action:\n"
                f'{{"action": "click_then_select" | "click_toggle" | "fill_text",\n'
                f'  "selector": "<CSS selector for the widget>",\n'
                f'  "widget_type": "switch | combobox | select | text | '
                f'rich_text | range | date_native",\n'
                f'  "option_text": "<exact option text to click after opening, '
                f'   if click_then_select>",\n'
                f'  "reasoning": "<one short sentence>"}}\n\n'
                f"If no recovery is possible, return "
                f'{{"action": "abort", "reasoning": "<why>"}}.'
            )

            # Plan F6: retry loop. After each failed plan, re-prompt the
            # engine with the failure context appended ("the previous
            # selector returned 0 elements; try a different one"). Caps
            # at 3 attempts so we don't burn cognitive-engine budget on
            # an unsolvable case.
            import json as _json
            attempt_history: list[dict] = []
            last_result: dict = {"success": False, "error": "no attempts"}
            for attempt in range(3):
                if attempt_history:
                    history_summary = "\n".join(
                        f"  attempt {i+1}: selector={h.get('selector','?')[:80]!r} "
                        f"widget={h.get('widget_type','?')!r} "
                        f"failed_with={h.get('error','?')[:80]!r}"
                        for i, h in enumerate(attempt_history)
                    )
                    prompt = (
                        f"{base_prompt}\n\n"
                        f"Previous attempts that failed:\n{history_summary}\n\n"
                        f"Try a different selector or widget_type — the prior "
                        f"plan did not work."
                    )
                else:
                    prompt = base_prompt

                raw = cognitive_llm_call(
                    task=prompt,
                    domain="form_recovery",
                    stakes="high",
                )
                if not raw:
                    last_result = {"success": False, "error": "engine returned no plan"}
                    break
                if raw.strip().startswith("```"):
                    raw = raw.strip().strip("`")
                    if raw.lower().startswith("json"):
                        raw = raw[4:].lstrip()
                try:
                    plan = _json.loads(raw)
                except Exception as exc:
                    logger.debug("_escalate_fill: bad JSON (attempt %d): %s", attempt + 1, exc)
                    last_result = {"success": False, "error": "engine plan unparseable"}
                    attempt_history.append({"error": "unparseable_json"})
                    continue

                if plan.get("action") == "abort":
                    logger.info(
                        "_escalate_fill: engine aborted on %r (attempt %d) — %s",
                        label, attempt + 1, plan.get("reasoning", "?"),
                    )
                    return {"success": False, "error": "engine_abort"}

                selector = (plan.get("selector") or "").strip()
                widget_type = (plan.get("widget_type") or "text").strip()
                option_text = plan.get("option_text") or value
                logger.info(
                    "_escalate_fill: attempt %d plan for %r — action=%s, "
                    "widget=%s, selector=%r",
                    attempt + 1, label, plan.get("action"), widget_type,
                    selector[:120],
                )
                if not selector:
                    last_result = {"success": False, "error": "engine plan missing selector"}
                    attempt_history.append({"selector": "", "widget_type": widget_type,
                                             "error": "missing_selector"})
                    continue

                try:
                    loc = page.locator(selector).first
                    if not await loc.count():
                        last_result = {"success": False, "error": "engine selector not on page"}
                        attempt_history.append({"selector": selector, "widget_type": widget_type,
                                                 "error": "selector_not_on_page"})
                        continue
                except Exception as exc:
                    last_result = {"success": False, "error": f"engine selector errored: {exc}"}
                    attempt_history.append({"selector": selector, "widget_type": widget_type,
                                             "error": f"selector_errored: {exc}"})
                    continue

                exec_result = await self._fill_resolved_widget(
                    loc, label, option_text, widget_type,
                )
                last_result = exec_result

                if exec_result.get("success"):
                    logger.info(
                        "_escalate_fill: ✓ recovered %r on attempt %d "
                        "(widget=%s, selector=%s)",
                        label, attempt + 1, widget_type, selector[:80],
                    )
                    try:
                        from jobpulse.ai_assist_logger import get_ai_assist_logger
                        sess_id = getattr(self, "_ai_assist_session_id", None)
                        if not sess_id:
                            sess = get_ai_assist_logger().start_session(
                                "claude",
                                domain=domain,
                                platform=getattr(self, "_platform", "generic"),
                            )
                            self._ai_assist_session_id = sess.session_id
                            sess_id = sess.session_id
                        get_ai_assist_logger().record_fix(
                            sess_id,
                            field_label=label,
                            old_value="",
                            new_value=str(option_text),
                            reasoning=plan.get("reasoning", "cognitive_escalation"),
                            fix_category="value_correction",
                            confidence=0.9,
                            dom_signature={
                                "selector": selector,
                                "widget_type": widget_type,
                                "ancestor_classes": "",
                                "aria_label": "",
                            },
                        )
                    except Exception as exc:
                        logger.debug("_escalate_fill: record_fix failed: %s", exc)
                    return exec_result

                attempt_history.append({
                    "selector": selector,
                    "widget_type": widget_type,
                    "error": str(exec_result.get("error", "unverified"))[:80],
                })
                logger.info(
                    "_escalate_fill: attempt %d plan executed but didn't verify on %r — %s",
                    attempt + 1, label, exec_result.get("error", "?"),
                )

            return last_result
        except Exception as exc:
            logger.warning("_escalate_fill crashed for %r: %s", label, exc)
            return {"success": False, "error": f"escalation crash: {exc}"}

    async def _fill_resolved_widget(
        self, loc: Any, label: str, value: str, input_type: str,
    ) -> dict:
        """Click-based dispatch for widgets the semantic scanner or
        learned-patterns strategy resolved directly.

        Routes by input_type rather than tag: Revolut-style React
        comboboxes render as `<button role="combobox">` and switches as
        `<button role="switch">` — both are click-only, so page.fill()
        on the resolved locator errors out. This helper handles that
        gap by clicking the button, scanning [role="option"] for a
        match, then clicking the matching option (combobox/select), or
        toggling on (switch/checkbox) when value implies "yes".
        """
        page = self._page
        try:
            await self._smart_scroll(loc)
        except Exception:
            pass

        v_norm = (value or "").strip()
        truthy = v_norm.lower() in ("yes", "true", "on", "1", "checked")
        falsy = v_norm.lower() in ("no", "false", "off", "0", "unchecked")

        if input_type == "switch":
            try:
                await loc.click(timeout=4000)
                # Verify aria-checked / aria-pressed flipped to match intent
                checked = await loc.evaluate(
                    "el => el.getAttribute('aria-checked') === 'true' || "
                    "el.getAttribute('aria-pressed') === 'true'"
                )
                if (truthy and checked) or (falsy and not checked):
                    return {"success": True, "value_set": value,
                            "value_verified": True, "actual_value": str(checked),
                            "expected_value": value}
                # Click again to flip if we landed on the wrong state
                if (truthy and not checked) or (falsy and checked):
                    await loc.click(timeout=4000)
                    checked = await loc.evaluate(
                        "el => el.getAttribute('aria-checked') === 'true' || "
                        "el.getAttribute('aria-pressed') === 'true'"
                    )
                return {"success": (truthy == bool(checked)),
                        "value_set": value, "actual_value": str(checked),
                        "expected_value": value}
            except Exception as exc:
                return {"success": False, "error": f"switch click failed: {exc}"}

        if input_type == "checkbox":
            try:
                state = await loc.is_checked()
                if (truthy and not state) or (falsy and state):
                    await loc.click(timeout=4000)
                state2 = await loc.is_checked()
                return {"success": (truthy == bool(state2)),
                        "value_set": value, "actual_value": str(state2),
                        "expected_value": value, "value_verified": True}
            except Exception as exc:
                return {"success": False, "error": f"checkbox click failed: {exc}"}

        if input_type in ("combobox", "custom_select", "select",
                          "multiselect", "radio_group"):
            # Native <select> takes a different path
            try:
                tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            except Exception:
                tag = ""
            if tag == "select":
                try:
                    await loc.select_option(label=value, timeout=4000)
                    return {"success": True, "value_set": value,
                            "value_verified": True, "expected_value": value}
                except Exception as exc:
                    return {"success": False,
                            "error": f"select_option failed: {exc}"}

            # Click to open the dropdown / option list
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.15)
                await loc.click(timeout=4000)
                await asyncio.sleep(0.5)
            except Exception as exc:
                return {"success": False, "error": f"open click failed: {exc}"}

            options = await page.evaluate(
                """() => {
                    return Array.from(document.querySelectorAll(
                        '[role="option"], [role="radio"], [role="menuitemcheckbox"], li[role="option"]'
                    ))
                        .filter(o => o.offsetParent !== null)
                        .map(o => o.textContent.trim())
                        .filter(t => t && !/^select\\s*(one|an?\\s*option)?$/i.test(t)
                                       && !/^loading/i.test(t));
                }"""
            )
            if not options:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return {"success": False, "error": "no options surfaced",
                        "options_seen": []}

            match = _best_option_match(
                label, value, options, store=self._profile_store,
            )
            if not match:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return {"success": False, "error": "no option matched",
                        "options_seen": options[:8]}

            clicked = await page.evaluate(
                """(target) => {
                    const sel = '[role="option"], [role="radio"], [role="menuitemcheckbox"], li[role="option"]';
                    for (const o of document.querySelectorAll(sel)) {
                        if (o.offsetParent !== null && o.textContent.trim() === target) {
                            o.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                match,
            )
            if not clicked:
                return {"success": False, "error": "option click failed",
                        "options_seen": options[:8]}

            await asyncio.sleep(0.3)
            return {"success": True, "value_set": match,
                    "value_verified": True, "expected_value": value,
                    "options_seen": options[:8]}

        if input_type in ("range", "split_numeric", "range_slider",
                          "salary_range"):
            # Plan F3-1: split a "min-max" string into two numeric
            # inputs that share a parent. Pure feature detection — walk
            # the locator's ancestors looking for a scope containing
            # exactly two <input type=number> with aria-valuemin /
            # aria-valuemax (the standard range-pair signal).
            try:
                parts = [
                    p.strip()
                    for p in v_norm.replace(",", "").split("-")
                    if p.strip()
                ]
                if len(parts) < 2:
                    return {"success": False,
                            "error": f"range value not splittable: {v_norm!r}"}
                min_val, max_val = parts[0], parts[-1]
                pair_ids = await loc.evaluate(
                    """(el) => {
                        let scope = el.parentElement;
                        for (let i = 0; scope && i < 4; i++, scope = scope.parentElement) {
                            const inputs = Array.from(scope.querySelectorAll(
                                'input[type="number"]'
                            )).filter(x => x.offsetParent !== null);
                            if (inputs.length >= 2) {
                                return inputs.slice(0, 2).map(n => {
                                    if (n.id) return '#' + n.id;
                                    if (n.name) return `input[name="${n.name}"]`;
                                    return null;
                                });
                            }
                        }
                        return null;
                    }"""
                )
                if not pair_ids or len(pair_ids) < 2 or not all(pair_ids):
                    return {"success": False,
                            "error": "no sibling number-input pair"}
                min_loc = page.locator(pair_ids[0]).first
                max_loc = page.locator(pair_ids[1]).first
                await min_loc.fill(min_val, timeout=4000)
                await max_loc.fill(max_val, timeout=4000)
                actual_min = ""
                actual_max = ""
                try:
                    actual_min = await min_loc.input_value()
                    actual_max = await max_loc.input_value()
                except Exception:
                    pass
                verified = bool(actual_min) and bool(actual_max) and (
                    actual_min == min_val and actual_max == max_val
                )
                return {"success": True,
                        "value_set": f"{min_val}-{max_val}",
                        "value_verified": verified,
                        "actual_value": f"{actual_min}-{actual_max}",
                        "expected_value": value}
            except Exception as exc:
                return {"success": False, "error": f"range fill failed: {exc}"}

        if input_type in ("rich_text", "rich_text_editor", "contenteditable"):
            # Plan F3-4: contenteditable widgets (TipTap, Lexical, Quill)
            # ignore page.fill(). Use focus + pressSequentially so each
            # input event fires individually — React/TipTap state updates
            # require it.
            try:
                await loc.click(timeout=2000)
                await loc.press_sequentially(value, delay=10, timeout=10000)
                actual = await loc.evaluate(
                    "el => (el.innerText || el.textContent || '').trim()"
                )
                return {"success": (value.strip() in actual),
                        "value_set": value, "actual_value": actual[:200],
                        "value_verified": (value.strip() in actual),
                        "expected_value": value}
            except Exception as exc:
                return {"success": False,
                        "error": f"rich_text fill failed: {exc}"}

        if input_type in ("date_native", "date"):
            # Plan F3-5: <input type=date> takes ISO YYYY-MM-DD via .fill().
            # Format the incoming value if it's not already ISO.
            try:
                from jobpulse.form_engine.date_filler import (
                    _format_date,
                )
                iso_value = _format_date(value, fmt="YYYY-MM-DD")
            except Exception:
                iso_value = value
            try:
                await loc.fill(iso_value, timeout=4000)
                actual = await loc.input_value()
                return {"success": (actual == iso_value),
                        "value_set": iso_value, "actual_value": actual,
                        "value_verified": (actual == iso_value),
                        "expected_value": value}
            except Exception as exc:
                return {"success": False,
                        "error": f"date_native fill failed: {exc}"}

        if input_type in ("text", "textarea", "number", "email", "tel", "url"):
            # Direct fill for text-class widgets. The cognitive engine
            # may classify a missed widget as text/textarea (e.g.
            # contenteditable divs that the shape detectors don't flag
            # as fillable). Try fill() first; fall back to type() for
            # widgets that synthesize input events differently.
            try:
                await loc.fill(value, timeout=4000)
                actual = ""
                try:
                    actual = await loc.input_value()
                except Exception:
                    pass
                # When input_value() fails, we have no proof the value
                # landed — default to NOT verified so the caller can
                # re-fill rather than silently leaving the form empty.
                return {"success": True, "value_set": value,
                        "value_verified": (actual == value) if actual else False,
                        "actual_value": actual, "expected_value": value}
            except Exception:
                try:
                    await loc.click(timeout=2000)
                    await loc.type(value, delay=20, timeout=4000)
                    actual = ""
                    try:
                        actual = await loc.input_value()
                    except Exception:
                        pass
                    return {"success": True, "value_set": value,
                            "value_verified": (actual == value) if actual else False,
                            "actual_value": actual, "expected_value": value}
                except Exception as exc:
                    return {"success": False, "error": f"text fill failed: {exc}"}

        return {"success": False, "error": f"unsupported input_type {input_type!r}"}

    async def _try_verified_fills_skip(
        self, label: str, value: str,
    ) -> dict | None:
        """Return a success-skipped dict if cache + DOM say the field is
        already correctly filled, or None to proceed with the fill.

        Two gates: the verified-fills cache must have a row for
        ``(domain, label_norm, value)`` AND the DOM must currently show
        that same value. The DOM re-check protects against
        cross-page/cross-session drift (the cached value's option may
        have been removed, the field may have been reset, etc.).

        Types where DOM state is unreliable (combobox, custom_dropdown,
        multiselect) bypass the short-circuit entirely; ``read_dom_value``
        returns None for those and we fall through to the normal fill.
        """
        if os.environ.get("VERIFIED_FILLS_CACHE_ENABLED", "1").lower() in {
            "0", "false", "no",
        }:
            return None
        try:
            url = getattr(self._page, "url", "") or ""
            if not url:
                return None
            from jobpulse.form_experience_db import FormExperienceDB
            from jobpulse.form_engine.verified_fills_db import VerifiedFillsDB
            from jobpulse.form_engine._field_crop import (
                read_dom_value, dom_value_matches_claim,
            )
            domain = FormExperienceDB.normalize_domain(url)
            if not domain:
                return None
            db = VerifiedFillsDB()
            hit = db.lookup(domain, label, value)
            if hit is None:
                return None

            meta = (
                getattr(self, "_fields_by_label", {}).get(label)
                or getattr(self, "_fields_by_label", {}).get(
                    _strip_required_marker(label)
                )
            )
            ftype = ""
            if isinstance(meta, dict):
                ftype = str(meta.get("type") or "")
            if not ftype:
                ftype = hit.get("field_type") or ""

            attached = None
            if isinstance(meta, dict):
                attached = meta.get("locator")
                if attached is None and meta.get("selector"):
                    try:
                        attached = self._page.locator(meta["selector"]).first
                    except Exception:
                        attached = None
            if attached is None:
                stripped = _strip_required_marker(label)
                attached = self._page.get_by_label(stripped, exact=False).first
            try:
                if not await attached.count():
                    return None
            except Exception:
                return None

            observed = await read_dom_value(attached, ftype)
            if not dom_value_matches_claim(observed, value, ftype):
                return None

            logger.info(
                "fill ⊘ %r reason=already_verified (cache hit, DOM confirms)",
                label[:60],
            )
            return {
                "success": True,
                "skipped": "already_verified",
                "value_set": value,
                "value_verified": True,
                "actual_value": observed,
                "expected_value": value,
            }
        except Exception as exc:
            logger.debug("verified_fills_skip failed for %r: %s", label[:60], exc)
            return None

    async def _fill_by_label(self, label: str, value: str) -> dict:
        page = self._page

        # S26-follow-up-N-3: verified-fills cache short-circuit. If a
        # previous run already verified this (domain, label, value)
        # AND the DOM still shows that value, we don't need to re-issue
        # the fill at all. ``_try_verified_fills_skip`` returns the
        # success-skipped dict on hit, or None to proceed with the
        # normal fill path.
        skip_result = await self._try_verified_fills_skip(label, value)
        if skip_result is not None:
            return skip_result

        if not os.environ.get("FAST_FILL"):
            await asyncio.sleep(_get_field_gap(label))

        special_result = await self._fill_special_widget(label, value)
        if special_result is not None:
            return special_result

        nth_index = 0
        base_label = label
        dup_match = re.match(r"^(.+?)\s+#(\d+)$", label)
        if dup_match:
            base_label = dup_match.group(1)
            nth_index = int(dup_match.group(2)) - 1

        # Strip required-field markers ('*', '(required)', '(Required)') —
        # Playwright matchers compare against the rendered <label> text, but
        # required markers are typically rendered via CSS pseudo-elements or
        # adjacent <span class="required"> nodes that don't appear in the
        # label's text. Without this, "Email*" never matches the actual
        # label "Email" on Greenhouse / many ATSs.
        base_label = _strip_required_marker(base_label)

        # Semantic-scanner / learned-pattern short-circuit. When the
        # matched field came from scan_semantic or _scan_learned_patterns
        # with a selector + widget_type attached, dispatch directly to
        # the per-widget handler — avoids label-string resolution that
        # fails when the label is a free-form question without a paired
        # <label>, AND avoids page.fill() on click-only widgets like
        # <button role="switch"> or <button role="combobox">.
        _meta = (
            getattr(self, "_fields_by_label", {}).get(label)
            or getattr(self, "_fields_by_label", {}).get(base_label)
        )
        _has_attached_selector = bool(_meta and (
            (_meta.get("semantic_match") and _meta.get("selector"))
            or (_meta.get("learned_pattern") and (
                _meta.get("selector") or _meta.get("locator")
            ))
        ))
        if _has_attached_selector:
            _input_type = (_meta.get("type") or "text").lower()
            try:
                _attached_loc = _meta.get("locator")
                if _attached_loc is None:
                    _attached_loc = page.locator(_meta["selector"]).first
                if await _attached_loc.count():
                    if _input_type in ("switch", "combobox", "select",
                                        "multiselect", "custom_select",
                                        "radio_group", "checkbox"):
                        _dispatch = await self._fill_resolved_widget(
                            _attached_loc, label, value, _input_type,
                        )
                        if _dispatch.get("success"):
                            return _dispatch
                        logger.debug(
                            "_fill_resolved_widget for %r returned %s — falling through",
                            label, _dispatch.get("error", "?"),
                        )
                    locator = _attached_loc
                else:
                    locator = page.get_by_label(base_label, exact=False)
            except Exception as exc:
                logger.debug(
                    "semantic/learned selector resolve failed for %r: %s",
                    label, exc,
                )
                locator = page.get_by_label(base_label, exact=False)
        else:
            locator = page.get_by_label(base_label, exact=False)

        if not await locator.count():
            locator = page.get_by_placeholder(base_label, exact=False)

        _from_role_fallback = False
        if not await locator.count():
            for _role in ("combobox", "textbox", "spinbutton"):
                _fallback = page.get_by_role(_role, name=base_label)
                if await _fallback.count():
                    locator = _fallback
                    _from_role_fallback = True
                    logger.debug("Shadow DOM fallback: found '%s' via get_by_role('%s')", base_label, _role)
                    break

        if not await locator.count():
            # Intent-healing fallback: re-resolve via a11y snapshot + optional LLM
            try:
                from jobpulse.form_engine.intent_healing import FieldIntent, heal_locator
                from jobpulse.form_engine.field_scanner import scan_fields
                _snapshot_fields = await scan_fields(
                    self._page,
                    strategy=getattr(self, "_strategy", None),
                    form_experience_db=getattr(self, "_fe_db", None),
                    container_selector=getattr(self, "_container_selector", None),
                )
                _intent = FieldIntent(
                    label=base_label,
                    role="textbox",
                    field_type="text",
                )
                _healed = await heal_locator(
                    self._page,
                    stored_selector=None,
                    intent=_intent,
                    snapshot_fields=_snapshot_fields or None,
                )
                if _healed is not None and await _healed.count():
                    locator = _healed
                    _from_role_fallback = False
                    logger.info("intent_healing: healed locator for '%s'", base_label)
                else:
                    logger.warning("No field found for label '%s'", base_label)
                    _esc = await self._escalate_fill(
                        label=label, value=value,
                        failure_tier="no_field_after_intent_healing",
                    )
                    if _esc.get("success"):
                        return _esc
                    return {"success": False, "error": f"No field for '{base_label}'"}
            except Exception as _heal_err:
                logger.debug("intent_healing error for '%s': %s", base_label, _heal_err)
                logger.warning("No field found for label '%s'", base_label)
                _esc = await self._escalate_fill(
                    label=label, value=value,
                    failure_tier="intent_healing_crash",
                )
                if _esc.get("success"):
                    return _esc
                return {"success": False, "error": f"No field for '{base_label}'"}

        _FILLABLE_TAGS = {"input", "textarea", "select"}
        el = None
        if _from_role_fallback:
            el = locator.nth(nth_index) if await locator.count() > nth_index else locator.first
        else:
            fillable_idx = 0
            try:
                for i in range(await locator.count()):
                    candidate = locator.nth(i)
                    t = await candidate.evaluate("el => el.tagName.toLowerCase()")
                    if t in _FILLABLE_TAGS or await candidate.get_attribute("contenteditable"):
                        if fillable_idx == nth_index:
                            el = candidate
                            break
                        fillable_idx += 1
            except Exception as exc:
                logger.debug("Fillable element scan failed for '%s': %s", base_label, exc)
        if el is None:
            for _role in ("combobox", "textbox", "spinbutton"):
                _fb = page.get_by_role(_role, name=base_label)
                if await _fb.count():
                    el = _fb.nth(nth_index) if await _fb.count() > nth_index else _fb.first
                    role = _role
                    logger.debug("Shadow DOM element: '%s' via get_by_role('%s')", base_label, _role)
                    break
        if el is None:
            locator = page.get_by_placeholder(base_label, exact=False)
            if not await locator.count():
                logger.warning("No fillable field found for label '%s'", base_label)
                _esc = await self._escalate_fill(
                    label=label, value=value,
                    failure_tier="no_fillable_element",
                )
                if _esc.get("success"):
                    return _esc
                return {"success": False, "error": f"No fillable field for '{base_label}'"}
            el = locator.first

        await self._smart_scroll(el)
        await self._move_mouse_to(el)

        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        input_type = await el.get_attribute("type") or ""
        role = await el.get_attribute("role") or ""

        fill_value = _canonicalize_country_value(label, value, store=self._profile_store)
        options_seen: list[str] = []
        expected_value = fill_value
        fill_technique = "direct_fill"

        if tag == "select":
            fill_technique = "select_option"
            selected = False
            options = await el.locator("option").all_text_contents()
            options_stripped = [o.strip() for o in options]
            meaningful = [o for o in options_stripped if o and not re.match(
                r"^(|—.*—|please select.*|select\.{0,3}|choose\.{0,3}|-999|not applicable)$",
                o, re.IGNORECASE,
            )]
            if not meaningful:
                try:
                    await el.click()
                    await asyncio.sleep(0.8)
                    options = await el.locator("option").all_text_contents()
                    options_stripped = [o.strip() for o in options]
                except Exception:
                    pass
            options_seen = options_stripped
            try:
                await el.select_option(label=fill_value, timeout=5000)
                selected = True
            except Exception:
                pass
            if not selected:
                matched_option = _best_option_match(label, fill_value, options_stripped, store=self._profile_store)
                if matched_option is not None:
                    try:
                        await el.select_option(label=matched_option, timeout=5000)
                        selected = True
                        expected_value = matched_option
                        self._save_gotcha(
                            label, "select_exact_failed",
                            f"Use option '{matched_option}' for value '{fill_value}'",
                        )
                    except Exception:
                        pass
            if not selected:
                try:
                    await el.select_option(value=fill_value, timeout=5000)
                    selected = True
                except Exception:
                    pass
            if not selected:
                logger.warning("Could not select '%s' for '%s' — options: %s", fill_value, label, options_stripped)
        elif input_type == "checkbox":
            if fill_value.lower() in ("true", "yes", "1"):
                await el.check()
            else:
                await el.uncheck()
        elif input_type == "radio":
            name_attr = await el.get_attribute("name") or ""
            if name_attr:
                group = await page.query_selector_all(f'input[name="{name_attr}"]')
                radio_pairs: list[tuple[Any, str]] = []
                for radio_el in group:
                    lbl = await radio_el.evaluate("""el => {
                        if (el.id) {
                            const lbl = document.querySelector('label[for="' + el.id + '"]');
                            if (lbl) return lbl.textContent.trim();
                        }
                        return el.getAttribute('aria-label')
                            || (el.parentElement ? el.parentElement.textContent.trim() : '')
                            || el.value || '';
                    }""")
                    radio_pairs.append((radio_el, (lbl or "").strip()))

                radio_labels = [p[1] for p in radio_pairs if p[1]]
                # Use the same 5-tier semantic matcher the dispatcher uses
                # everywhere else. Exact-equality on lowercased text broke
                # for free-form radio labels ("Asian / Indian" vs "Indian",
                # "Yes — sponsored" vs "Yes") and silently left the radio
                # group unfilled.
                matched_label = _best_option_match(
                    label, fill_value, radio_labels, store=self._profile_store,
                )
                matched = False
                if matched_label:
                    for radio_el, lbl in radio_pairs:
                        if lbl == matched_label:
                            await radio_el.scroll_into_view_if_needed()
                            await radio_el.click()
                            matched = True
                            break
                if not matched:
                    logger.warning(
                        "No radio in group '%s' matches '%s' (options: %s)",
                        name_attr, fill_value, radio_labels[:8],
                    )
            else:
                radio = page.get_by_role("radio", name=fill_value, exact=True)
                if await radio.count() == 1:
                    await radio.first.check()
                else:
                    logger.warning("Radio '%s' matched %d elements — skipping unscoped click", fill_value, await radio.count())
        elif input_type == "switch":
            # ARIA toggle switches — <button role="switch" aria-checked="…">.
            # User answer "Yes"/"true" → click to flip OFF→ON. "No"/"false"
            # → leave as-is (these widgets default to OFF). If already in
            # the desired state, no-op.
            target_on = (fill_value or "").strip().lower() in {
                "yes", "true", "on", "agreed", "1", "y",
            }
            try:
                state = await el.evaluate(
                    "el => el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-pressed') === 'true'"
                )
            except Exception:
                state = False
            if bool(state) == target_on:
                return {"success": True, "value_set": "yes" if target_on else "no",
                        "value_verified": True}
            try:
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=5000)
            except Exception as exc:
                return {"success": False, "value_verified": False,
                        "error": f"switch click failed: {exc}"}
            await asyncio.sleep(0.4)
            try:
                new_state = await el.evaluate(
                    "el => el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-pressed') === 'true'"
                )
            except Exception:
                new_state = state
            return {"success": bool(new_state) == target_on,
                    "value_set": "yes" if new_state else "no",
                    "value_verified": True}
        elif input_type == "salary_number":
            # Salary number input — never let LLM read JD prose for these.
            # Always pull from role_salary DB by job title (substring +
            # token Jaccard fallback). For min_salary use lookup as-is;
            # for max_salary add a 5k buffer; otherwise use lookup.
            from jobpulse.screening_answers import lookup_user_salary
            job_title = ""
            try:
                job_title = (self._job_context or {}).get("title") or ""
            except Exception:
                job_title = ""
            base = lookup_user_salary(job_title)
            salary_role = "salary"
            try:
                # The field metadata is on the matched field dict; read it
                # back via DOM in a best-effort way (label hint).
                lower = (label or "").lower()
                if "min" in lower or "minimum" in lower:
                    salary_role = "min_salary"
                elif "max" in lower or "maximum" in lower:
                    salary_role = "max_salary"
            except Exception:
                pass
            if salary_role == "max_salary":
                value_to_set = str(base + 5000)
            else:
                value_to_set = str(base)
            try:
                await el.fill(value_to_set, timeout=5000)
                return {"success": True, "value_set": value_to_set,
                        "value_verified": True}
            except Exception as exc:
                return {"success": False, "value_verified": False,
                        "error": f"salary_number fill failed: {exc}"}
        elif input_type == "list_button_radio":
            # Oracle HCM ul[role=list]+li[role=listitem]+button widget.
            # Locator points to the <ul>; find the option button whose text
            # matches `fill_value` and click it. The widget toggles via class
            # change, not aria-checked, so verification reads back the state
            # via field_scanner's same detection logic on the next scan.
            target = (fill_value or "").strip().lower()
            if not target:
                return {"success": False, "value_verified": False,
                        "error": "list_button_radio: empty fill value"}
            clicked = await el.evaluate(r"""(ul, target) => {
                const buttons = [...ul.querySelectorAll('li[role="listitem"] > button')];
                const match = buttons.find(b => (b.innerText || '').trim().toLowerCase() === target);
                if (!match) return null;
                match.scrollIntoView({block: 'center'});
                match.click();
                return (match.innerText || '').trim();
            }""", target)
            if clicked:
                return {"success": True, "value_set": clicked, "value_verified": True}
            return {"success": False, "value_verified": False,
                    "error": f"list_button_radio: no option matches {fill_value!r}"}
        elif await self._is_combobox_widget(el):
            strategy = getattr(self, "_strategy", None)
            if strategy and hasattr(strategy, "fill_combobox"):
                override_result = await strategy.fill_combobox(self._page, el, fill_value, label)
                if override_result is not None:
                    return {"success": True, "value_set": override_result, "value_verified": True}
            fill_value = _canonicalize_country_value(label, fill_value, store=self._profile_store)
            stored_technique = None
            try:
                page_url = getattr(self._page, "url", "") or ""
                if page_url and self._fe_db:
                    techniques = self._fe_db.get_fill_techniques(page_url)
                    stored_technique = techniques.get(label, {}).get("technique")
                    if not stored_technique and self._platform:
                        platform_techniques = self._fe_db.get_platform_fill_techniques(self._platform)
                        field_type_prefix = f"{tag}:{input_type or role}"
                        for pt in platform_techniques:
                            if pt["field_type"] == field_type_prefix and pt["success"]:
                                stored_technique = pt["technique"]
                                break
            except Exception:
                pass
            # Country-suffix preference for ambiguous option lists. When the
            # user lives in the UK and Greenhouse's autocomplete returns
            # multiple "Dundee, ..." cities, prefer the one with "United
            # Kingdom" in the option text — without this, the picker silently
            # picked Dundee, Florida or Dundee, Michigan based on render order.
            _prefer_country: tuple[str, ...] = ()
            try:
                if self._profile_store:
                    _country = (self._profile_store.sensitive("country") or "").strip()
                    if not _country:
                        _loc = (self._profile_store.identity().location or "").strip()
                        if "," in _loc:
                            _country = _loc.rsplit(",", 1)[-1].strip()
                        elif _loc:
                            _country = _loc
                    if _country:
                        _prefer_country = (_country,)
            except Exception:
                _prefer_country = ()
            from jobpulse.form_scanner import (
                best_option_match as ax_best_match,
                best_range_match,
                scan_combobox_options,
            )
            # React-Select primary strategy — works on Greenhouse / Lever /
            # Ashby / many other ATS forms that wrap an <input type="text">
            # in `.select__control`. Existing `scan_combobox_options` and the
            # type-to-search path both rely on `get_by_role("combobox", name=...)`
            # which fails when the role attribute isn't rendered yet (React
            # async render race). This path uses the wrapper class instead
            # and reads options from `.select__menu` / `.select__option`,
            # which the styled component always renders.
            react_select_options: list[str] = []
            react_select_chosen: str | None = None
            # Pre-initialise so the failure paths inside the React-Select
            # block (option click missed, exception, etc.) don't surface
            # as an UnboundLocalError at the `if ax_options:` check below.
            # Live evidence (2026-05-09 Anthropic Greenhouse run):
            #   "Field fill failed for 'Are you Hispanic/Latino?': cannot
            #    access local variable 'ax_options' where it is not
            #    associated with a value"
            # masked the actual click-timeout error.
            ax_options: list[str] = []
            matched_option: str | None = None
            try:
                is_react_select = await el.evaluate("""(node) => {
                    if (!node || !node.closest) return false;
                    return !!node.closest(
                        '.select__control, [class*="select__control"], '
                        + '[class*="-control"][class*="select"]'
                    );
                }""")
            except Exception:
                is_react_select = False

            if is_react_select and stored_technique != "combobox_type_to_search":
                try:
                    parent = el.locator(
                        "xpath=ancestor::*[contains(@class,'select__control')][1]"
                    ).first
                    if await parent.count():
                        await parent.scroll_into_view_if_needed()
                        await parent.click()
                    else:
                        await el.click()
                    await asyncio.sleep(0.6)
                    react_select_options = await page.evaluate("""() => {
                        const items = document.querySelectorAll(
                            '.select__menu .select__option, '
                            + '.select__menu [role="option"], '
                            + '[class*="select__menu"] [class*="select__option"]'
                        );
                        return Array.from(items)
                            .map(o => (o.textContent || '').trim())
                            .filter(Boolean);
                    }""")
                    if react_select_options:
                        react_select_chosen = ax_best_match(
                            fill_value, react_select_options,
                            aliases=_build_option_aliases(),
                            prefer_substrings=_prefer_country,
                        )
                        if react_select_chosen is None:
                            # Autocomplete city/location: type to filter,
                            # then read the freshly rendered options.
                            await el.fill("")
                            await el.type(fill_value, delay=80)
                            await asyncio.sleep(0.9)
                            react_select_options = await page.evaluate("""() => {
                                const items = document.querySelectorAll(
                                    '.select__menu .select__option, '
                                    + '.select__menu [role="option"]'
                                );
                                return Array.from(items)
                                    .map(o => (o.textContent || '').trim())
                                    .filter(Boolean);
                            }""")
                            if react_select_options:
                                react_select_chosen = (
                                    ax_best_match(
                                        fill_value, react_select_options,
                                        aliases=_build_option_aliases(),
                                        prefer_substrings=_prefer_country,
                                    )
                                    or react_select_options[0]
                                )
                        if react_select_chosen:
                            _scope = await _resolve_listbox_scope(page, el)
                            opt_locator = _scope.locator(
                                ".select__option, [role='option']"
                            ).filter(has_text=react_select_chosen).first
                            if await opt_locator.count():
                                await opt_locator.click()
                                await asyncio.sleep(0.25)
                                # Gap 1: per-fill verification + retry —
                                # confirm the click actually updated the
                                # React-Select state by reading
                                # .select__single-value. On mismatch retry
                                # via force-click then keyboard-nav.
                                # Wrapped in try/except so any failure
                                # gracefully falls through to legacy.
                                _displayed = ""
                                try:
                                    _displayed = await el.evaluate(
                                        "(node) => {"
                                        " let p = node.parentElement;"
                                        " for (let i = 0; p && i < 5; i++, p = p.parentElement) {"
                                        "   if (p.classList && p.classList.contains('select__control')) {"
                                        "     const sv = p.querySelector('.select__single-value');"
                                        "     return sv ? (sv.textContent || '').trim() : '';"
                                        "   }"
                                        " }"
                                        " return '';"
                                        "}"
                                    )
                                except Exception:
                                    _displayed = ""

                                _target_lc = react_select_chosen.strip().lower()
                                if _displayed.strip().lower() != _target_lc:
                                    # Retry: force-click via JS dispatch
                                    try:
                                        _parent2 = el.locator(
                                            "xpath=ancestor::*[contains(@class,'select__control')][1]"
                                        ).first
                                        if await _parent2.count():
                                            await _parent2.click()
                                        await asyncio.sleep(0.4)
                                        _scope2 = await _resolve_listbox_scope(page, el)
                                        _opt2 = _scope2.locator(
                                            ".select__option, [role='option']"
                                        ).filter(has_text=react_select_chosen).first
                                        if await _opt2.count():
                                            await _opt2.click(force=True)
                                            await asyncio.sleep(0.25)
                                        _displayed = await el.evaluate(
                                            "(node) => {"
                                            " let p = node.parentElement;"
                                            " for (let i = 0; p && i < 5; i++, p = p.parentElement) {"
                                            "   if (p.classList && p.classList.contains('select__control')) {"
                                            "     const sv = p.querySelector('.select__single-value');"
                                            "     return sv ? (sv.textContent || '').trim() : '';"
                                            "   }"
                                            " }"
                                            " return '';"
                                            "}"
                                        )
                                    except Exception:
                                        pass

                                fill_technique = "react_select_click_option"
                                expected_value = react_select_chosen
                                options_seen = react_select_options[:20]
                                # Skip the legacy strategies — we filled it
                                ax_options = []
                                matched_option = react_select_chosen
                                if _displayed.strip().lower() == _target_lc:
                                    logger.info(
                                        "react_select_click_option ✓ '%s' = %r",
                                        label[:60], react_select_chosen[:60],
                                    )
                                else:
                                    logger.warning(
                                        "react_select_click_option ✗ '%s' "
                                        "(intended=%r, displayed=%r) — "
                                        "click(s) succeeded but state didn't update",
                                        label[:60], react_select_chosen[:60],
                                        _displayed[:60],
                                    )
                                    # Mark as not-verified so the outer return
                                    # logs failure. value_verified is set later
                                    # from the actual.lower()==expected.lower()
                                    # check; help it by leaving expected/actual
                                    # in sync with the verification result.
                            else:
                                await page.keyboard.press("Escape")
                except Exception as exc:
                    logger.debug(
                        "react_select_click_option failed for '%s': %s — falling through",
                        label, exc,
                    )
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

            if stored_technique == "combobox_type_to_search":
                ax_options = []
            elif not react_select_chosen:
                ax_options = await scan_combobox_options(page, label)
            if ax_options:
                options_seen = ax_options
                matched_option = ax_best_match(
                    fill_value, ax_options,
                    aliases=_build_option_aliases(),
                    prefer_substrings=_prefer_country,
                )
                if matched_option is None:
                    try:
                        num = float(fill_value.replace(",", "").replace("£", "").replace("$", ""))
                        matched_option = best_range_match(num, ax_options)
                    except (ValueError, TypeError):
                        pass
                if matched_option:
                    fill_technique = "combobox_prescanned_match"
                    expected_value = matched_option
                    await el.click(timeout=3000)
                    await asyncio.sleep(0.3)
                    await el.fill("")
                    await asyncio.sleep(0.4)
                    _scope = await _resolve_listbox_scope(page, el)
                    option = _scope.get_by_role("option", name=matched_option, exact=True)
                    if await option.count():
                        await option.first.click()
                    else:
                        await el.press("ArrowDown")
                        await asyncio.sleep(0.2)
                        await el.press("Enter")
            if not ax_options or not matched_option:
                fill_technique = "combobox_type_to_search"
                await el.click(timeout=3000)
                await asyncio.sleep(0.3)
                await el.fill("")
                await el.type(fill_value, delay=80)
                await asyncio.sleep(1.2)
                _scope = await _resolve_listbox_scope(page, el)
                option_group = _scope.get_by_role("option")
                option_texts: list[str] = []
                try:
                    for i in range(await option_group.count()):
                        text = (await option_group.nth(i).text_content() or "").strip()
                        if text:
                            option_texts.append(text)
                except Exception:
                    option_texts = []
                options_seen = option_texts
                matched_option = _best_option_match(label, fill_value, option_texts, store=self._profile_store)
                if matched_option:
                    expected_value = matched_option
                    option = _scope.get_by_role("option", name=matched_option, exact=False)
                    if await option.count():
                        await option.first.click()
                    else:
                        await el.press("Enter")
        else:
            if input_type == "tel":
                fill_value = await self._normalize_phone_value(label, fill_value)
            elif input_type == "date":
                import re as _re
                if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", fill_value):
                    store = getattr(self, "_profile_store", None)
                    dob = store.sensitive("date_of_birth") if store else ""
                    if dob and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
                        fill_value = dob
            await el.fill(fill_value)

        # Post-fill verification
        if tag == "select":
            actual = await el.evaluate(
                "el => el.options[el.selectedIndex]?.text?.trim() || ''"
            )
        elif input_type in ("checkbox", "radio"):
            actual = str(await el.is_checked())
        elif role == "combobox":
            actual = await el.evaluate(
                """el => {
                    const own = (el.value || '').trim();
                    if (own) return own;
                    let node = el.parentElement;
                    for (let i = 0; node && i < 5; i += 1, node = node.parentElement) {
                        const display = node.querySelector('.select__single-value, [class*="singleValue"]');
                        const text = display?.textContent?.trim();
                        if (text) return text;
                    }
                    return '';
                }"""
            )
        else:
            actual = await el.input_value()

        norm_expected = _normalize_match_text(expected_value)
        norm_actual = _normalize_match_text(actual)
        verified = bool(norm_expected and norm_actual) and (
            norm_expected == norm_actual
            or norm_expected in norm_actual
            or norm_actual in norm_expected
        )

        if verified:
            try:
                page_url = getattr(self._page, "url", "") or ""
                if page_url and self._fe_db:
                    self._fe_db.record_fill_technique(
                        domain_or_url=page_url,
                        field_label=label,
                        field_type=f"{tag}:{input_type or role}",
                        technique=fill_technique,
                        value_used=actual or fill_value,
                        success=True,
                    )
            except Exception:
                pass
        else:
            try:
                page_url = getattr(self._page, "url", "") or ""
                if page_url and fill_technique and self._fe_db:
                    existing = self._fe_db.get_fill_techniques(page_url)
                    if label not in existing:
                        self._fe_db.record_fill_technique(
                            domain_or_url=page_url, field_label=label,
                            field_type=f"{tag}:{input_type or role}",
                            technique=fill_technique, value_used=fill_value,
                            success=False,
                        )
            except Exception:
                pass

        # Gap 4: explicit per-field log signal so failures aren't invisible.
        # Without this the only evidence of a failed fill is the empty visible
        # state in the browser — every debug session required a CDP inspection.
        if verified:
            logger.info(
                "fill ✓ '%s' = %r [tech=%s, expected=%r]",
                label[:60], (fill_value or "")[:60],
                fill_technique, (expected_value or fill_value)[:60],
            )
        else:
            logger.warning(
                "fill ✗ '%s' (intended=%r, actual=%r) [tech=%s, options_seen=%d]",
                label[:60], (expected_value or fill_value)[:60],
                (actual or "")[:60], fill_technique,
                len(options_seen) if options_seen else 0,
            )
        try:
            from shared.db_observability import (
                DROP_VALIDATION_FAILED,
                mark_fill_outcome,
            )
            intended_for_outcome = expected_value or fill_value or ""
            actual_for_outcome = actual or ""
            mark_fill_outcome(
                label,
                intended=intended_for_outcome,
                actual=actual_for_outcome,
                drop_reason=None if verified else DROP_VALIDATION_FAILED,
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("db_observability mark_fill_outcome failed: %s", exc)
        return {
            "success": True,
            "value_set": fill_value,
            "value_verified": verified,
            "actual_value": actual,
            "options_seen": options_seen,
            "expected_value": expected_value,
            "fill_technique": fill_technique,
        }

    async def _fill_special_widget(self, label: str, value: str) -> dict[str, Any] | None:
        norm_label = _normalize_match_text(label)
        if "country options" not in norm_label:
            return None

        button = self._page.locator("button.iti__selected-country").first
        if not await button.count():
            return {"success": False, "error": "No phone country widget found"}

        search_term = "United Kingdom"
        phone_code = "+44"
        store = getattr(self, "_profile_store", None)
        if store:
            country = _country_from_location(store.identity().location or "")
            if country and country in _COUNTRY_DATA:
                search_term = country
                for alias in _COUNTRY_DATA[country]:
                    if alias.startswith("+"):
                        phone_code = alias
                        break

        await self._smart_scroll(button)
        await self._move_mouse_to(button)
        await button.click()
        search = self._page.locator("#iti-0__search-input").first
        await search.fill("")
        await search.fill(search_term)
        await asyncio.sleep(0.5)

        expected = f"{search_term} ({phone_code})"
        option = self._page.locator("#iti-0__country-listbox li", has_text=expected).first
        if await option.count():
            await option.click()
        else:
            await search.press("ArrowDown")
            await asyncio.sleep(0.2)
            await search.press("Enter")

        actual = (await button.get_attribute("aria-label")) or ""
        verified = search_term.lower() in actual.lower() and phone_code in actual
        return {
            "success": True,
            "value_set": expected,
            "value_verified": verified,
            "actual_value": actual,
            "options_seen": [expected],
            "expected_value": expected,
        }

    async def _overwrite_experience_descriptions(self) -> None:
        """Overwrite auto-parsed experience descriptions with structured versions.

        PII compliance: experience text comes from `user_profile.db.experience`
        via `ProfileStore.experience()`, not from a hardcoded config dict.
        Each row's `bullets` JSON array is joined into a single description
        for the form's textarea fill. Falls back to legacy
        `config.EXPERIENCE_DESCRIPTIONS` only when the DB is empty (fresh
        install before profile-sync has populated experience rows).
        """
        # Build {role_key: description} from ProfileStore.experience() rows.
        # ExperienceEntry is a dataclass with .title / .bullets / etc.
        descriptions: dict[str, str] = {}
        try:
            from shared.profile_store import get_profile_store
            import re as _re
            for entry in get_profile_store().experience() or []:
                title = (getattr(entry, "title", "") or "").strip()
                bullets = list(getattr(entry, "bullets", []) or [])
                if not title or not bullets:
                    continue
                # Strip HTML tags so the form gets plain text
                joined = " ".join(_re.sub(r"<[^>]+>", "", b).strip() for b in bullets)
                descriptions[title] = joined
        except Exception as exc:
            logger.debug("experience() unavailable, using legacy fallback: %s", exc)
        if not descriptions:
            try:
                from jobpulse.config import EXPERIENCE_DESCRIPTIONS as _legacy
                descriptions = dict(_legacy)
            except Exception:
                descriptions = {}
        if not descriptions:
            return
        page = self._page
        all_btns = await page.locator("button").all()
        for role_key, desc in descriptions.items():
            for btn in all_btns:
                try:
                    label = await btn.get_attribute("aria-label") or ""
                    if "Edit experience" not in label or role_key not in label:
                        continue
                    await btn.click()
                    await asyncio.sleep(1.5)
                    textareas = await page.locator("textarea:visible").all()
                    for ta in textareas:
                        val = await ta.input_value()
                        if len(val) > 30:
                            await ta.fill(desc)
                            logger.info("Overwrote experience description for '%s'", role_key)
                            break
                    save_btns = await page.get_by_role("button", name="Save").all()
                    for sb in save_btns:
                        if await sb.is_visible():
                            await sb.click()
                            break
                    await asyncio.sleep(1.5)
                    break
                except Exception as exc:
                    logger.debug("Experience overwrite failed for '%s': %s", role_key, exc)

    async def _fill_toggle_buttons(
        self, mapping: dict[str, str], custom_answers: dict[str, Any] | None,
    ) -> int:
        """Click YES/NO toggle buttons matched by screening answers. Returns count filled."""
        page = self._page
        groups = await page.evaluate("""() => {
            const allBtns = Array.from(document.querySelectorAll('button'));
            const yesBtns = allBtns.filter(b => /^yes$/i.test(b.textContent.trim()));
            const results = [];
            for (const yBtn of yesBtns) {
                const parent = yBtn.parentElement;
                if (!parent) continue;
                const noBtn = Array.from(parent.querySelectorAll('button'))
                    .find(b => /^no$/i.test(b.textContent.trim()) && b !== yBtn);
                if (!noBtn) continue;
                let questionText = '';
                let node = parent;
                for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                    const candidates = node.querySelectorAll(
                        'label, legend, h3, h4, p, [class*="question"], [class*="label"]'
                    );
                    for (const c of candidates) {
                        const t = (c.textContent || '').trim();
                        if (t.length > 10 && t.length < 500 && !/^(yes|no)$/i.test(t)) {
                            questionText = t;
                            break;
                        }
                    }
                    if (questionText) break;
                }
                if (!questionText) continue;
                results.push({
                    question: questionText,
                    yesIdx: allBtns.indexOf(yBtn),
                    noIdx: allBtns.indexOf(noBtn),
                });
            }
            return results;
        }""")

        if not groups:
            return 0

        filled = 0
        for group in groups:
            question = group["question"]
            q_norm = _normalize_match_text(question)
            answer = None
            for label, value in mapping.items():
                l_norm = _normalize_match_text(label)
                if l_norm and q_norm and (l_norm in q_norm or q_norm in l_norm):
                    answer = str(value).strip()
                    break

            if not answer:
                from jobpulse.screening_answers import try_instant_answer
                _job_ctx_raw = (custom_answers or {}).get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                cached = try_instant_answer(question, _job_ctx)
                if cached:
                    answer = str(cached).strip()

            if not answer:
                continue

            is_yes = answer.lower() in ("yes", "true", "1")
            is_no = answer.lower() in ("no", "false", "0")
            if not is_yes and not is_no:
                continue

            idx = group["yesIdx"] if is_yes else group["noIdx"]
            btn = page.locator("button").nth(idx)
            try:
                await self._smart_scroll(btn)
                await btn.click()
                filled += 1
                logger.info("Toggle button: '%s' → %s", question[:80], "YES" if is_yes else "NO")
            except Exception as exc:
                logger.warning("Toggle button click failed for '%s': %s", question[:80], exc)

        return filled

    async def _fill_radio_groups(
        self, mapping: dict[str, str], custom_answers: dict[str, Any] | None,
        fields: list[dict] | None = None,
    ) -> int:
        """Fill Yes/No radio groups extracted from visible page text.

        Shadow DOM (SmartRecruiters spl-*) blocks both DOM queries and CDP
        a11y labels for radiogroups. Instead we:
        1. Extract question text from page.inner_text (works across shadow DOM)
        2. Parse "question ... Yes No" patterns to pair questions with radio indices
        3. Match questions against screening answers
        4. Click the correct radio via CDP-indexed locator
        """
        page = self._page

        radio_fields = [
            f for f in (fields or [])
            if f.get("type") == "radio" and f.get("options")
        ]
        if radio_fields:
            return await self._fill_radio_groups_from_scan(
                radio_fields, mapping, custom_answers,
            )

        all_radios = page.get_by_role("radio")
        radio_count = await all_radios.count()
        if radio_count < 2:
            return 0

        radio_info: list[dict] = []
        for ri in range(radio_count):
            r = all_radios.nth(ri)
            info = await r.evaluate("""el => {
                const label = el.getAttribute("aria-label") || el.labels?.[0]?.textContent?.trim() || el.value || "";
                let q = "";
                let node = el;
                for (let i = 0; node && i < 15; i++) {
                    const next = node.parentElement || (node.getRootNode && node.getRootNode() !== node ? node.getRootNode().host : null);
                    if (!next) break;
                    node = next;
                    const txt = (node.textContent || "").trim();
                    if (txt.length > 20 && txt.length < 500) { q = txt; break; }
                }
                return {label, question: q, checked: el.checked};
            }""")
            radio_info.append({"index": ri, **info})

        seen_questions: dict[str, list[dict]] = {}
        for ri in radio_info:
            q = ri["question"]
            if q:
                seen_questions.setdefault(q, []).append(ri)

        filled = 0
        for question, radios_in_group in seen_questions.items():
            if any(r["checked"] for r in radios_in_group):
                continue

            answer: str | None = None
            q_norm = _normalize_match_text(question)
            for label, value in mapping.items():
                l_norm = _normalize_match_text(label)
                if l_norm and q_norm and (l_norm in q_norm or q_norm in l_norm):
                    answer = str(value).strip()
                    break

            if not answer:
                from jobpulse.screening_answers import try_instant_answer
                _job_ctx_raw = (custom_answers or {}).get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                cached = try_instant_answer(question, _job_ctx)
                if cached:
                    answer = str(cached).strip()

            if not answer:
                logger.debug("Radio group unanswered: '%s'", question[:80])
                continue

            is_yes = answer.lower() in ("yes", "true", "1")
            is_no = answer.lower() in ("no", "false", "0")
            if not is_yes and not is_no:
                continue

            target = None
            for r in radios_in_group:
                lbl = str(r["label"]).lower()
                if is_yes and lbl in ("1", "yes", "true"):
                    target = r
                    break
                if is_no and lbl in ("0", "no", "false"):
                    target = r
                    break

            if target is None:
                continue

            try:
                radio = all_radios.nth(target["index"])
                await self._smart_scroll(radio)
                await radio.check(force=True)
                filled += 1
                logger.info("Radio group: '%s' → %s", question[:80], "Yes" if is_yes else "No")
            except Exception as exc:
                logger.warning("Radio group fill failed for '%s': %s", question[:80], exc)

        return filled

    async def _fill_radio_groups_from_scan(
        self, radio_fields: list[dict], mapping: dict[str, str],
        custom_answers: dict[str, Any] | None,
    ) -> int:
        """Fill radiogroup fields scoped by name attribute or parent container."""
        page = self._page
        filled = 0
        for field in radio_fields:
            question = field["label"]
            options = field.get("options", [])
            name_attr = field.get("name", "")
            if not question or len(question) < 5 or not options:
                continue

            answer: str | None = None
            q_norm = _normalize_match_text(question)
            for label, value in mapping.items():
                l_norm = _normalize_match_text(label)
                if l_norm and q_norm and (l_norm in q_norm or q_norm in l_norm):
                    answer = str(value).strip()
                    break

            if not answer:
                from jobpulse.screening_answers import try_instant_answer
                _job_ctx_raw = (custom_answers or {}).get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                cached = try_instant_answer(question, _job_ctx)
                if cached:
                    answer = str(cached).strip()

            if not answer:
                continue

            target = None
            answer_lower = answer.lower()
            for opt in options:
                if opt.lower() == answer_lower:
                    target = opt
                    break
            if not target:
                if answer_lower in ("yes", "true", "1"):
                    target = next((o for o in options if o.lower() == "yes"), None)
                elif answer_lower in ("no", "false", "0"):
                    target = next((o for o in options if o.lower() == "no"), None)
            if not target:
                # Consent-to-provide questions: pick "agree" if user has the data
                q_lower = question.lower()
                if any(k in q_lower for k in ("date of birth", "dob", "provide your")):
                    agree_opt = next(
                        (o for o in options if "agree" in o.lower()),
                        None,
                    )
                    if agree_opt:
                        target = agree_opt
            if not target:
                target = _best_option_match(question, answer, options, store=self._profile_store)
            if not target:
                from jobpulse.screening_answers import try_screening_v2
                _job_ctx_raw = (custom_answers or {}).get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                retry_answer = try_screening_v2(
                    question, _job_ctx,
                    field={"type": "radio", "options": options},
                )
                if retry_answer:
                    retry_str = str(retry_answer).strip()
                    for opt in options:
                        if opt.lower() == retry_str.lower():
                            target = opt
                            break
                    if not target:
                        target = _best_option_match(question, retry_str, options, store=self._profile_store)
            if not target:
                continue

            try:
                clicked = False
                if name_attr:
                    group = await page.query_selector_all(f'input[name="{name_attr}"]')
                    for radio_el in group:
                        lbl = await radio_el.evaluate("""el => {
                            if (el.id) {
                                const lbl = document.querySelector('label[for="' + el.id + '"]');
                                if (lbl) return lbl.textContent.trim();
                            }
                            return el.getAttribute('aria-label')
                                || (el.parentElement ? el.parentElement.textContent.trim() : '')
                                || el.value || '';
                        }""")
                        if lbl.strip().lower() == target.strip().lower():
                            await radio_el.scroll_into_view_if_needed()
                            await radio_el.click()
                            clicked = True
                            break
                if not clicked:
                    radio = page.get_by_role("radio", name=target, exact=True)
                    if await radio.count() == 1:
                        await self._smart_scroll(radio.first)
                        await radio.first.check(force=True)
                        clicked = True
                if clicked:
                    filled += 1
                    logger.info("Radio group [%s]: '%s' → '%s'", name_attr or "role", question[:80], target)
            except Exception as exc:
                logger.warning("Radio group fill failed for '%s': %s", question[:80], exc)
        return filled

    # ── Custom React Dropdowns ──

    async def _fill_custom_dropdowns(
        self, mapping: dict[str, str], custom_answers: dict[str, Any] | None,
        fields: list[dict] | None = None,
    ) -> int:
        """Fill custom React dropdowns (data-testid="dropdown-basic" pattern)."""
        custom_fields = [
            f for f in (fields or [])
            if f.get("type") == "custom_dropdown"
        ]
        if not custom_fields:
            return 0

        filled = 0
        for field in custom_fields:
            question = field["label"]
            test_id = field.get("testId", "")

            if any(kw in test_id.lower() for kw in ("privacy", "consent", "agree")):
                continue

            answer: str | None = mapping.get(question)
            if not answer:
                q_norm = _normalize_match_text(question)
                for label, value in mapping.items():
                    l_norm = _normalize_match_text(label)
                    if l_norm and q_norm and (l_norm in q_norm or q_norm in l_norm):
                        answer = str(value).strip()
                        break

            if not answer:
                from jobpulse.screening_answers import try_instant_answer
                _job_ctx_raw = (custom_answers or {}).get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                cached = try_instant_answer(question, _job_ctx)
                if cached:
                    answer = str(cached).strip()

            if not answer:
                logger.debug("Custom dropdown unanswered: '%s'", question[:80])
                continue

            try:
                button_id = field.get("buttonId", "")
                dd_index = field.get("ddIndex", -1)
                if button_id:
                    result = await self._fill_button_dropdown(button_id, question, answer)
                else:
                    result = await self._click_custom_dropdown_option(question, answer, dd_index)
                if result is True:
                    filled += 1
                    logger.info("Custom dropdown [%s]: '%s' → '%s'", button_id or test_id or dd_index, question[:80], answer)
                elif isinstance(result, list) and result:
                    picked = _resolve_dropdown_from_profile(question, result)
                    if not picked:
                        from jobpulse.screening_answers import try_screening_v2
                        _job_ctx_raw = (custom_answers or {}).get("_job_context")
                        _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                        retry_answer = try_screening_v2(
                            question, _job_ctx,
                            field={"type": "select", "options": result},
                        )
                        if retry_answer:
                            retry_answer = str(retry_answer).strip()
                            picked = _best_option_match(question, retry_answer, result, store=self._profile_store)
                    if not picked:
                        picked = _best_option_match(question, answer, result, store=self._profile_store)
                    if picked:
                        if button_id:
                            re_clicked = await self._fill_button_dropdown(button_id, question, picked)
                        else:
                            re_clicked = await self._click_custom_dropdown_option(question, picked, dd_index)
                        if re_clicked is True:
                            filled += 1
                            logger.info("Custom dropdown [%s]: '%s' → '%s' (retry matched)", test_id or dd_index, question[:80], picked)
                        else:
                            logger.warning("Custom dropdown [%s]: click failed for matched option '%s'", test_id or dd_index, picked[:60])
                    else:
                        logger.warning("Custom dropdown [%s]: no option match for '%s' among %d options", test_id or dd_index, question[:60], len(result))
            except Exception as exc:
                logger.warning("Custom dropdown fill failed for '%s': %s", question[:80], exc)

        return filled

    async def _click_custom_dropdown_option(
        self, question: str, answer: str, dd_index: int = -1,
    ) -> bool:
        """Find a custom dropdown by question text and select the matching option."""
        page = self._page
        q_norm = _normalize_match_text(question)

        dd_selector = '[data-testid="dropdown-basic"], [data-testid="agree-data-privacy-dropdown"]'
        containers = await page.locator(dd_selector).all()
        if not containers:
            return False

        target = None
        if 0 <= dd_index < len(containers):
            target = containers[dd_index]
        else:
            for c in containers:
                try:
                    ctx = await c.evaluate("""el => {
                        const title = el.querySelector('[data-testid="dropdown-title"]');
                        if (title) return title.textContent.trim();
                        let node = el;
                        for (let i = 0; node && i < 6; i++) {
                            node = node.parentElement;
                            if (!node) break;
                            for (const sel of [':scope > label', ':scope > legend', ':scope > h3', ':scope > h4', ':scope > p']) {
                                const found = node.querySelector(sel);
                                if (found) {
                                    const t = found.textContent.trim();
                                    if (t.length > 5 && t.length < 500) return t;
                                }
                            }
                        }
                        return '';
                    }""")
                    if ctx and _normalize_match_text(ctx) == q_norm:
                        target = c
                        break
                except Exception:
                    continue

        if not target:
            return False

        btn = target.locator('[data-testid="dropdown-button"], button').first
        if not await btn.count():
            return False

        current = (await btn.text_content() or "").strip()
        if current and _normalize_match_text(current) == _normalize_match_text(answer):
            return True

        # Dismiss any stale dropdown overlay before opening
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.15)

        await btn.scroll_into_view_if_needed()
        try:
            await btn.click(timeout=5000)
        except Exception:
            await btn.evaluate("el => el.click()")
        await asyncio.sleep(0.5)

        # Verify dropdown actually opened; retry click if toggle state was stale
        has_visible = await page.evaluate("""() => {
            const candidates = [
                ...document.querySelectorAll('[role="listbox"] [role="option"]'),
                ...document.querySelectorAll('[role="listbox"] li'),
                ...document.querySelectorAll('ul[class*="dropdown"] li'),
                ...document.querySelectorAll('[data-testid*="dropdown-option"]'),
            ];
            return [...new Set(candidates)].some(c => c.offsetParent !== null && c.textContent.trim());
        }""")
        if not has_visible:
            try:
                await btn.click(timeout=5000)
            except Exception:
                await btn.evaluate("el => el.click()")
            await asyncio.sleep(0.5)

        # Read all visible options and try direct JS match first
        option_data = await page.evaluate("""(answer) => {
            const norm = answer.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
            const isShort = norm.length <= 4;
            const candidates = [
                ...document.querySelectorAll('[role="listbox"] [role="option"]'),
                ...document.querySelectorAll('[role="listbox"] li'),
                ...document.querySelectorAll('ul[class*="dropdown"] li'),
                ...document.querySelectorAll('[data-testid*="dropdown-option"]'),
            ];
            const unique = [...new Set(candidates)];
            const texts = [];
            const visible = [];
            for (const c of unique) {
                if (c.offsetParent === null) continue;
                const text = c.textContent.trim();
                if (!text) continue;
                texts.push(text);
                visible.push(c);
            }
            // Pass 1: exact match (always safe)
            for (let i = 0; i < visible.length; i++) {
                const textNorm = texts[i].toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
                if (textNorm === norm) {
                    visible[i].click();
                    return {matched: texts[i], options: texts};
                }
            }
            // Pass 2: substring match (skip for short answers to avoid "No" matching "not")
            if (!isShort) {
                for (let i = 0; i < visible.length; i++) {
                    const textNorm = texts[i].toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
                    if (textNorm.includes(norm) || norm.includes(textNorm)) {
                        visible[i].click();
                        return {matched: texts[i], options: texts};
                    }
                }
            }
            return {matched: null, options: texts};
        }""", answer)

        if option_data.get("matched"):
            return True

        # Fuzzy match: use _best_option_match against visible options
        visible_options = option_data.get("options", [])
        if visible_options:
            fuzzy = _best_option_match(question, answer, visible_options, store=self._profile_store)
            if fuzzy:
                # Click the fuzzy-matched option via JS
                clicked = await page.evaluate("""(target) => {
                    const candidates = [
                        ...document.querySelectorAll('[role="listbox"] [role="option"]'),
                        ...document.querySelectorAll('[role="listbox"] li'),
                        ...document.querySelectorAll('ul[class*="dropdown"] li'),
                        ...document.querySelectorAll('[data-testid*="dropdown-option"]'),
                    ];
                    for (const c of [...new Set(candidates)]) {
                        if (c.offsetParent === null) continue;
                        if (c.textContent.trim() === target) {
                            c.click();
                            return true;
                        }
                    }
                    return false;
                }""", fuzzy)
                if clicked:
                    logger.info("Custom dropdown fuzzy matched: '%s' → '%s'", answer[:40], fuzzy)
                    return True

        await page.keyboard.press("Escape")
        if visible_options:
            logger.info(
                "Custom dropdown no match: question='%s' answer='%s' options=%s",
                question[:60], answer[:40], visible_options[:5],
            )
            return visible_options
        return False

    async def _fill_button_dropdown(
        self, button_id: str, question: str, answer: str,
    ) -> bool | list[str]:
        """Fill a button-based custom dropdown (e.g. Workday questionnaire)."""
        page = self._page
        btn = page.locator(f"#{button_id}")
        if not await btn.count():
            return False

        current = (await btn.text_content() or "").strip()
        if current and _normalize_match_text(current) == _normalize_match_text(answer):
            return True

        await page.keyboard.press("Escape")
        await asyncio.sleep(0.15)
        await btn.scroll_into_view_if_needed()
        await btn.click(timeout=5000)
        await asyncio.sleep(0.6)

        options = await page.evaluate(r"""() => {
            return Array.from(document.querySelectorAll('[role="option"]'))
                .filter(o => o.offsetParent !== null)
                .map(o => o.textContent.trim())
                .filter(t => t && !/^select\s*(one|an?\s*option)?$/i.test(t));
        }""")

        if not options:
            await page.keyboard.press("Escape")
            return False

        match = _best_option_match(question, answer, options, store=self._profile_store)
        if match:
            clicked = await page.evaluate("""(target) => {
                for (const o of document.querySelectorAll('[role="option"]')) {
                    if (o.offsetParent !== null && o.textContent.trim() === target) {
                        o.click();
                        return true;
                    }
                }
                return false;
            }""", match)
            if clicked:
                await asyncio.sleep(0.3)
                return True

        await page.keyboard.press("Escape")
        logger.info("Button dropdown no match: '%s' answer='%s' options=%s",
                     question[:60], answer[:40], options[:5])
        return options

    # ── Page Detection ──

    async def _recover_if_navigated(self, expected_url: str) -> bool:
        """Detect and recover from unexpected SPA navigation.

        Some SPAs navigate away from the form when JS change events fire
        (e.g. direct ID fill triggering client-side routing).  If the
        current URL no longer matches, navigate back and re-resolve the
        container.
        """
        if not expected_url or not isinstance(expected_url, str):
            return False
        current_url = getattr(self._page, "url", "") or ""
        if not current_url or not isinstance(current_url, str):
            return False

        from urllib.parse import urlparse
        expected_parsed = urlparse(expected_url)
        current_parsed = urlparse(current_url)

        same_path = (
            expected_parsed.netloc == current_parsed.netloc
            and expected_parsed.path == current_parsed.path
        )
        if same_path:
            return False

        logger.warning(
            "SPA navigation detected: expected %s, got %s — navigating back",
            expected_url[:120], current_url[:120],
        )
        try:
            await self._page.goto(expected_url, wait_until="domcontentloaded", timeout=15000)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            # Re-resolve container after navigation recovery
            from jobpulse.form_engine.field_scanner import resolve_form_container
            self._container_selector = await resolve_form_container(
                self._page, self._strategy, self._fe_db,
            )
            logger.info("SPA recovery: navigated back, container=%s", self._container_selector)
            return True
        except Exception as exc:
            logger.error("SPA recovery failed: %s", exc)
            return False

    async def _detect_page_type_quick(self) -> str:
        """Lightweight page-type check using DOM classifier after page transitions.

        Returns a page type string: 'application_form', 'verification_wall',
        'login_form', 'session_expired', 'confirmation', or 'unknown'.
        Only used for high-confidence detections (>= 0.8) to catch obvious
        non-form pages. Low-confidence results return 'application_form' so
        the fill loop continues normally.
        """
        try:
            snapshot = await self._driver.get_snapshot(force_refresh=True)
            if hasattr(snapshot, "model_dump"):
                snapshot = snapshot.model_dump()
            from jobpulse.page_analysis.classifier import PageTypeClassifier
            clf = PageTypeClassifier()
            page_type, confidence = clf.classify(snapshot)
            if confidence >= 0.8 and page_type.value != "application_form":
                logger.info(
                    "Post-nav page type: %s (confidence=%.2f)",
                    page_type.value, confidence,
                )
                return page_type.value
        except Exception as exc:
            logger.debug("Quick page type detection failed: %s", exc)
        return "application_form"

    async def _is_confirmation_page(self) -> bool:
        body = await self._page.locator("body").text_content()
        body_lower = (body or "").lower()[:2000]
        return any(phrase in body_lower for phrase in (
            "thank you for applying",
            "application has been received",
            "application submitted",
            "successfully submitted",
        ))

    async def _dismiss_stale_dialogs(self) -> None:
        """Dismiss LinkedIn 'Save this application?' and similar blocking overlays."""
        page = self._page
        # Check for the overlay container first — if it exists, we MUST dismiss it
        overlay = page.locator('[data-test-easy-apply-discard-confirmation]')
        try:
            if await overlay.count():
                logger.info("Detected LinkedIn discard-confirmation overlay — dismissing")
                # Click Discard inside the overlay with force to bypass pointer-events
                discard_btn = overlay.locator('button:has-text("Discard")')
                if await discard_btn.count():
                    await discard_btn.first.click(force=True)
                    await asyncio.sleep(1)
                    logger.info("Dismissed discard-confirmation overlay via Discard button")
                    return
                # Fallback: any button with "discard" in the overlay
                any_btn = overlay.get_by_role("button").last
                if await any_btn.count():
                    await any_btn.click(force=True)
                    await asyncio.sleep(1)
                    logger.info("Dismissed discard-confirmation overlay via last button")
                    return
        except Exception as exc:
            logger.debug("Overlay dismiss attempt failed: %s", exc)

        # Broader selectors for other dialog types
        for selector in (
            'button[data-control-name="discard_application_confirm_btn"]',
            'div[data-test-modal-container] button:has-text("Discard")',
            'button:has-text("Discard")',
        ):
            try:
                btn = page.locator(selector)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(force=True)
                    await asyncio.sleep(0.5)
                    logger.info("Dismissed stale dialog via selector: %s", selector)
                    return
            except Exception as exc:
                logger.debug("Dialog dismiss selector %s failed: %s", selector, exc)

    async def _is_submit_page(self) -> bool:
        """Plan D: a page is the final submit page iff the reasoner
        emits action='done'. No string matching against button names —
        every page on a job portal has an "Apply" or "Submit"-flavored
        button somewhere (header, sidebar), so text-matching produces
        constant false positives. The reasoner already classifies the
        page intent; consume that.
        """
        pa = getattr(self, "_planned_action", None)
        if pa and pa.get("action"):
            return pa.get("action") == "done"
        # Fallback: no planned_action threaded in (tests, cron path).
        # Ask the reasoner now — its cache makes repeated calls cheap.
        try:
            from jobpulse.page_analysis.page_reasoner import get_page_reasoner
            snap = await self._driver.get_snapshot()
            return get_page_reasoner().reason_sync(snap).action == "done"
        except Exception as exc:
            logger.debug("_is_submit_page fallback reasoner failed: %s", exc)
            return False

    # ── Navigation ──

    @staticmethod
    async def _is_combobox_widget(el) -> bool:
        """Return True if the element behaves as a combobox/listbox picker,
        not a free-text input.

        Mirrors field_scanner.fieldType()'s combobox detection so the FILL
        path treats Greenhouse / Lever / Ashby React-Select widgets as
        comboboxes even when their ARIA role hasn't rendered by scan time.
        Without this, the elif at fill-time fell through to a generic text
        fill — which on React-Select fills the search input but never picks
        an option.
        """
        try:
            return await el.evaluate("""(node) => {
                if (!node) return false;
                if (node.getAttribute('role') === 'combobox') return true;
                const hp = (node.getAttribute('aria-haspopup') || '').toLowerCase();
                if (hp === 'listbox' || hp === 'true') return true;
                const ac = (node.getAttribute('aria-autocomplete') || '').toLowerCase();
                if (ac === 'list' || ac === 'both') return true;
                if (node.closest && node.closest(
                    '.select__control, [class*="select__control"], '
                    + '[class*="-control"][class*="select"], '
                    + '.combobox, [class*="combobox"]'
                )) return true;
                return false;
            }""")
        except Exception:
            return False

    async def _record_final_state_before_submit(self) -> None:
        """Snapshot every filled form field and persist its label + type +
        options + user-final value to the learning DBs.

        Why this exists: per-field record_fill() calls during the agent's
        fill loop capture the agent's *planned* answer. If the user (or a
        manual correction step) edits the field afterwards, those changes
        aren't seen — the cache holds a stale answer for next time.
        Calling this immediately before submit closes that gap by reading
        what's actually about to be sent to the ATS.

        Triggers four downstream learning paths:
        - JobDB.cache_answer (global Q→A cache)
        - ScreeningOutcomeRecorder.record_fill (Qdrant semantic cache)
        - FormExperienceDB.record_fill_technique (per-domain technique log)
        - FormExperienceDB.save_field_mappings (label → profile_key)
        """
        page = self._page
        url = getattr(page, "url", "") or ""
        if not url:
            return

        # Read the full final state — including React-Select displayed values
        try:
            fields = await page.evaluate("""() => {
                const form = document.querySelector('#application-form, form') || document.body;
                const out = [];
                form.querySelectorAll('input, textarea, select').forEach(el => {
                    if (el.type === 'hidden') return;
                    if (el.id && el.id.startsWith('iti-')) return;
                    const tag = el.tagName.toLowerCase();
                    const type = el.type || '';
                    const role = el.getAttribute('role') || '';
                    let label = '';
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) label = lbl.textContent.trim().replace(/\\s+/g, ' ');
                    }
                    if (!label) label = el.getAttribute('aria-label') || '';
                    let value = el.value || '';
                    let displayed = null;
                    if (role === 'combobox') {
                        let p = el.parentElement;
                        for (let i = 0; p && i < 5; i++, p = p.parentElement) {
                            if (p.classList && p.classList.contains('select__control')) {
                                const sv = p.querySelector('.select__single-value');
                                displayed = sv ? sv.textContent.trim() : null;
                                break;
                            }
                        }
                    }
                    if (type === 'checkbox' || type === 'radio') value = el.checked ? 'true' : '';
                    if (type === 'file') value = (el.files?.length > 0)
                        ? `[FILE: ${el.files[0].name}]` : '';
                    out.push({id: el.id, tag, type, role, label,
                              raw_value: value, displayed_value: displayed});
                });
                return out;
            }""")
        except Exception as exc:
            logger.debug("record_final_state: page.evaluate failed: %s", exc)
            return

        if not fields:
            return

        from jobpulse.form_experience_db import FormExperienceDB
        fe_db = FormExperienceDB()
        domain = FormExperienceDB.normalize_domain(url)

        try:
            from jobpulse.screening_outcome_recorder import get_screening_outcome_recorder
            recorder = get_screening_outcome_recorder()
        except Exception:
            recorder = None
        try:
            from jobpulse.job_db import JobDB
            jdb = JobDB()
        except Exception:
            jdb = None

        recorded = 0
        for f in fields:
            label = (f.get("label") or "").strip()
            answer = (f.get("displayed_value") or f.get("raw_value") or "").strip()
            if not label or not answer or answer.startswith("[FILE:"):
                continue
            field_type = "combobox" if f.get("role") == "combobox" else (
                f.get("type") or f.get("tag") or "text"
            )
            technique = "react_select_click_option" if field_type == "combobox" else (
                "textarea_fill" if field_type == "textarea" else "direct_fill"
            )

            try:
                fe_db.record_fill_technique(
                    domain, label, field_type, technique,
                    value_used=answer[:200], success=True,
                )
            except Exception:
                pass

            if jdb is not None:
                try:
                    jdb.cache_answer(label, answer)
                except Exception:
                    pass

            if recorder is not None:
                try:
                    recorder.record_fill(
                        question=label, answer=answer,
                        field_options=None, field_type=field_type,
                    )
                except Exception:
                    pass
            recorded += 1

        logger.info(
            "record_final_state_before_submit: captured %d/%d filled fields "
            "for %s",
            recorded, len(fields), domain,
        )

    async def _snapshot_live_form_state(self) -> dict[str, Any]:
        """Snapshot every visible form input's current value.

        Used right before clicking Next/Continue so user mid-flow edits
        on screening pages survive into the correction-capture diff.
        Indeed's review-module is read-only — without this, screening-page
        edits are lost (live regression on Forge 2026-05-05). The reader
        mirrors live_review_applicator._capture_final_mapping_async.

        Also emits per-field DOM signatures under ``"<label>__dom"`` keys
        so downstream confirm_application can route them to
        GotchasDB.widget_patterns when the user corrects the field.
        """
        page = self._page
        if page is None:
            return {}
        snapshot: dict[str, Any] = {}

        async def _read(loc: Any, label: str, kind: str) -> None:
            if not label:
                return
            try:
                if kind in ("text", "textarea"):
                    snapshot[label] = (await loc.input_value()) or ""
                elif kind == "select":
                    snapshot[label] = await loc.evaluate(
                        "el => el.options[el.selectedIndex]?.text?.trim() || ''"
                    )
                elif kind == "combobox":
                    snapshot[label] = await loc.evaluate(
                        """el => {
                            const own = (el.value || '').trim();
                            if (own) return own;
                            let node = el.parentElement;
                            for (let i = 0; node && i < 5; i += 1, node = node.parentElement) {
                                const display = node.querySelector('.select__single-value, [class*="singleValue"]');
                                const text = display?.textContent?.trim();
                                if (text) return text;
                            }
                            return '';
                        }"""
                    )
                elif kind == "checkbox":
                    snapshot[label] = "true" if await loc.is_checked() else "false"
                elif kind == "radio_group":
                    selected = ""
                    for radio in await loc.get_by_role("radio").all():
                        try:
                            if await radio.is_checked():
                                selected = await self._get_accessible_name(radio)
                                break
                        except Exception:
                            continue
                    snapshot[label] = selected
            except Exception as exc:
                logger.debug(
                    "_snapshot_live_form_state: read failed for %r: %s",
                    label, exc,
                )

        try:
            for loc in await page.get_by_role("textbox").all():
                label = await self._get_accessible_name(loc)
                await _read(loc, label, "text")
            for loc in await page.get_by_role("combobox").all():
                label = await self._get_accessible_name(loc)
                try:
                    tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                except Exception:
                    tag = "combobox"
                await _read(loc, label, "select" if tag == "select" else "combobox")
            for loc in await page.get_by_role("radiogroup").all():
                label = await self._get_accessible_name(loc)
                await _read(loc, label, "radio_group")
            for loc in await page.get_by_role("checkbox").all():
                label = await self._get_accessible_name(loc)
                await _read(loc, label, "checkbox")
            for loc in await page.locator("textarea:visible").all():
                label = await self._get_accessible_name(loc)
                await _read(loc, label, "textarea")
        except Exception as exc:
            logger.warning("_snapshot_live_form_state: crashed: %s", exc)
            return {}

        # Best-effort DOM signature capture — every visible field that
        # has a stable selector contributes a "<label>__dom" entry. The
        # filter below preserves these alongside the value entries.
        try:
            sigs = await page.evaluate(
                """() => {
                    const out = {};
                    const els = document.querySelectorAll(
                        'input, select, textarea, [role="switch"], [role="combobox"]'
                    );
                    els.forEach(el => {
                        if (el.offsetParent === null && el.type !== 'radio') return;
                        const lblNode = el.id
                            ? document.querySelector(`label[for="${el.id}"]`)
                            : null;
                        const label = (
                            (lblNode && lblNode.innerText) ||
                            el.getAttribute('aria-label') || ''
                        ).trim().slice(0, 200);
                        if (!label) return;
                        let sel = '';
                        if (el.id) sel = `#${el.id}`;
                        else if (el.name) sel = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                        else if (el.getAttribute('data-qa')) sel = `[data-qa="${el.getAttribute('data-qa')}"]`;
                        else return;
                        const role = el.getAttribute('role');
                        const tag = el.tagName.toLowerCase();
                        let widget_type = 'text';
                        if (role === 'switch') widget_type = 'switch';
                        else if (tag === 'select') widget_type = 'select';
                        else if (tag === 'textarea') widget_type = 'textarea';
                        else if (el.type === 'number') widget_type = 'number';
                        out[label] = {
                            selector: sel,
                            widget_type: widget_type,
                            ancestor_classes: (el.parentElement && el.parentElement.className) || '',
                            aria_label: el.getAttribute('aria-label') || '',
                        };
                    });
                    return out;
                }"""
            )
        except Exception:
            sigs = {}
        for label, sig in (sigs or {}).items():
            if label and sig:
                snapshot[label + "__dom"] = sig

        return {k: v for k, v in snapshot.items() if k and v}

    async def _click_navigation(self, dry_run: bool) -> str:
        page = self._page
        # Plan D: consume the reasoner's PageAction. The reasoner's
        # prompt produces `advance_button` (the exact text of the
        # Continue/Submit button to click) and `action` (which is
        # "done" iff this is the final submit page). No string-based
        # button-text lists — that ran headlong into the
        # "Apply" false-positive bug on welovealfa.com 2026-05-06.
        pa = getattr(self, "_planned_action", None) or {}
        target_text = (pa.get("advance_button") or "").strip()
        is_submit = pa.get("action") == "done"

        # Fallback: no PageAction threaded in (tests, cron, direct
        # callers). Ask the reasoner with a fresh snapshot — it's
        # cached so the next consumer reuses the result.
        if not target_text:
            try:
                from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                snap = await self._driver.get_snapshot()
                act = get_page_reasoner().reason_sync(snap)
                target_text = (act.advance_button or "").strip()
                is_submit = act.action == "done"
            except Exception as exc:
                logger.debug("_click_navigation reasoner fallback failed: %s", exc)

        if target_text:
            btn = page.get_by_role("button", name=target_text, exact=True)
            if not await btn.count():
                btn = page.get_by_role("button", name=target_text, exact=False)
            if await btn.count() and await btn.first.is_visible():
                if is_submit and dry_run:
                    return "dry_run_stop"
                if is_submit:
                    try:
                        await self._record_final_state_before_submit()
                    except Exception as exc:
                        logger.warning("record_final_state_before_submit failed: %s", exc)
                await self._move_mouse_to(btn.first)
                await btn.first.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    await asyncio.sleep(2)
                logger.info(
                    "nav: clicked %s via reasoner-named %r",
                    "submit" if is_submit else "next", target_text,
                )
                return "submitted" if is_submit else "next"
            else:
                logger.debug(
                    "nav: reasoner-named button %r not on page — "
                    "trying structural-selector fallbacks",
                    target_text,
                )

        # S10: consult platform-strategy selectors before generic CSS fallback.
        # `WorkdayStrategy.next_page_selectors()` returns the
        # `button[data-automation-id='bottom-navigation-next-button']`
        # selector that used to live hardcoded inline here; Greenhouse,
        # Lever, Ashby, etc. each contribute their own `:has-text(...)`
        # selectors. Pre-S10 these methods were only consumed by the
        # deleted `UNIFIED_FORM_ENGINE` path — NativeFormFiller duplicated
        # the same strings inline. The order (submit-first vs next-first)
        # follows the planned-action's `is_submit`, so we don't accidentally
        # click "Submit Application" on a multi-page form's intermediate page.
        try:
            from jobpulse.ats_adapters.strategy import (
                get_strategy as _get_platform_strategy,
            )
            _strategy = _get_platform_strategy(self._platform)
            if is_submit:
                _ordered = [
                    ("submit", _strategy.submit_selectors(), "submitted"),
                    ("next", _strategy.next_page_selectors(), "next"),
                ]
            else:
                _ordered = [
                    ("next", _strategy.next_page_selectors(), "next"),
                    ("submit", _strategy.submit_selectors(), "submitted"),
                ]
            for _kind, _selectors, _retval in _ordered:
                for _sel in _selectors:
                    try:
                        _btn = page.locator(_sel).first
                        if not await _btn.count() or not await _btn.is_visible():
                            continue
                        if _kind == "submit" and dry_run:
                            return "dry_run_stop"
                        if _kind == "submit":
                            try:
                                await self._record_final_state_before_submit()
                            except Exception as exc:
                                logger.warning(
                                    "record_final_state_before_submit failed: %s",
                                    exc,
                                )
                        await self._move_mouse_to(_btn)
                        await _btn.click()
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            await asyncio.sleep(2)
                        logger.info(
                            "nav: clicked %s via platform strategy selector %r "
                            "(platform=%s)",
                            _kind, _sel, self._platform,
                        )
                        return _retval
                    except Exception as exc:
                        logger.debug(
                            "nav: strategy %s selector %r errored: %s",
                            _kind, _sel, exc,
                        )
                        continue
        except Exception as exc:
            logger.debug("nav: strategy consultation failed: %s", exc)

        # CSS-selector fallback: get_by_role can miss buttons with extra
        # aria-describedby text or non-standard accessible names. Match the
        # type=submit attribute directly — no name-string fragility. Live bug:
        # Contentful's Greenhouse <button type="submit">Submit application</button>
        # was missed by get_by_role on 2026-05-04, forcing manual-help loop.
        css_submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button.submit-application",
            "button[data-qa*='submit']",
        ]
        for sel in css_submit_selectors:
            try:
                btn = page.locator(sel)
                count = await btn.count()
                for i in range(count):
                    candidate = btn.nth(i)
                    if not await candidate.is_visible():
                        continue
                    if await candidate.is_disabled():
                        continue
                    if dry_run:
                        return "dry_run_stop"
                    try:
                        await self._record_final_state_before_submit()
                    except Exception as exc:
                        logger.warning("record_final_state_before_submit failed: %s", exc)
                    await self._move_mouse_to(candidate)
                    await candidate.click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        await asyncio.sleep(2)
                    logger.info("nav: clicked submit via CSS fallback %r", sel)
                    return "submitted"
            except Exception as exc:
                logger.debug("nav: CSS fallback %r errored: %s", sel, exc)
                continue

        # All matchers exhausted — log every visible button so future debug
        # sessions don't need a CDP inspection trip.
        try:
            visible_btns = await page.evaluate(
                """() => [...document.querySelectorAll('button, input[type="submit"], [role="button"]')]
                    .filter(b => b.offsetParent !== null)
                    .map(b => ({
                        text: ((b.innerText || b.value || b.getAttribute('aria-label') || '') + '').trim().slice(0, 80),
                        tag: b.tagName, type: b.type || '', disabled: !!b.disabled
                    }))
                    .filter(x => x.text)
                    .slice(0, 20)"""
            )
            logger.warning(
                "nav: NO submit/next button matched. %d visible buttons on page: %s",
                len(visible_btns), visible_btns,
            )
        except Exception as exc:
            logger.warning("nav: NO submit/next button matched (snapshot failed: %s)", exc)
        return ""

    # ── Public Interface ──

    async def fill(
        self,
        platform: str,
        cv_path: str | None,
        cl_path: str | None,
        profile: dict,
        custom_answers: dict,
        dry_run: bool,
        planned_action: dict | None = None,
    ) -> dict:
        # Stash job context so per-input handlers (e.g. salary_number)
        # can consult it without needing custom_answers passed through
        # every sub-method signature.
        _job_ctx_raw = (custom_answers or {}).get("_job_context")
        self._job_context = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None

        # Plan D: stash the reasoner's PageAction (action +
        # advance_button + expected_outcome) so _is_submit_page and
        # _click_navigation can consume it instead of running their own
        # hardcoded button-text lookups. The orchestrator passes this
        # through; tests / cron / direct callers may omit it (None →
        # _click_navigation falls back to a fresh reasoner call with
        # the current snapshot).
        self._planned_action = planned_action

        # 0. Build correction warning from form hints
        hints = custom_answers.get("_form_hints")
        if hints and hints.get("correction_accuracy") is not None:
            acc = hints["correction_accuracy"]
            if acc < 0.9:
                bad_fields = hints.get("frequently_corrected_fields", [])
                self._correction_warning = (
                    f"\n\nWARNING: This domain has {acc*100:.0f}% historical accuracy. "
                    f"Fields often corrected by user: {', '.join(bad_fields) if bad_fields else 'unknown'}. "
                    f"Double-check these fields — prefer user-corrected values from Known answers."
                )
            else:
                self._correction_warning = ""
        else:
            self._correction_warning = ""

        self._timing_data = None
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if url:
                self._timing_data = FormExperienceDB().get_timing(url)
        except Exception:
            self._timing_data = None

        try:
            from shared.profile_store import get_profile_store
            self._profile_store = get_profile_store()
        except Exception:
            self._profile_store = None

        from jobpulse.ats_adapters.strategy import get_strategy
        self._strategy = get_strategy(platform)

        if self._strategy:
            try:
                pre_result = await self._strategy.pre_fill(self._page, cv_path, profile, custom_answers)
                if pre_result.get("cv_uploaded"):
                    custom_answers["_cv_pre_uploaded"] = True
                    await self._overwrite_experience_descriptions()
            except Exception as exc:
                logger.debug("Strategy pre_fill failed: %s", exc)

        self._container_selector: str | None = None
        self._fe_db = None
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            self._fe_db = FormExperienceDB()
            from jobpulse.form_engine.field_scanner import resolve_form_container
            self._container_selector = await resolve_form_container(
                self._page, self._strategy, self._fe_db,
            )
            if self._container_selector:
                logger.info("Form container resolved: %s", self._container_selector)
        except Exception as exc:
            logger.debug("Container resolution failed: %s", exc)

        self._load_platform_strategy(platform)
        self._platform = platform
        await self._resolve_page_context()

        self._stored_exp = None
        try:
            url = getattr(self._page, 'url', '') or ''
            if url and self._fe_db:
                self._stored_exp = self._fe_db.lookup(url)
        except Exception:
            pass

        self._load_domain_field_mappings()
        self._load_cached_screening_answers()

        _raw_url = getattr(self._page, 'url', '') or ''
        _expected_url = _raw_url if isinstance(_raw_url, str) else ''

        if self._domain_field_mappings:
            direct_filled = await self._fill_by_element_ids(profile, custom_answers)
            if direct_filled:
                logger.info("DIRECT ID FILL: pre-filled %d fields before page loop", len(direct_filled))
            await self._recover_if_navigated(_expected_url)

        await handle_modal_cv_upload(self._page, cv_path)

        await self._dismiss_stale_dialogs()

        _job_ctx = custom_answers.get("_job_context") or {}
        _job_id = _job_ctx.get("job_id", "")
        # Normalize to netloc so signal/correction keys match the navigator path
        # (which calls extract_domain) — otherwise OptimizationEngine bucketizes
        # form-filler signals per full-URL while navigator signals are per-domain.
        _raw_page_url = getattr(self._page, 'url', '') or ''
        if isinstance(_raw_page_url, str) and _raw_page_url:
            from jobpulse.application_orchestrator_pkg._navigator import extract_domain
            _page_domain = extract_domain(_raw_page_url)
        else:
            _page_domain = ''

        _field_overrides = _load_field_overrides(_page_domain)
        if _field_overrides:
            logger.info("Loaded %d field overrides from agent rules", len(_field_overrides))

        self._heuristics_context = _load_heuristics(_page_domain, platform)

        seen_field_types: list[str] = []
        seen_screening: list[dict[str, Any]] = []
        _outcome_recorder = None
        all_agent_mappings: dict[str, str] = {}
        total_fields_attempted = 0
        total_fields_filled = 0
        # Audit 2026-05-10 / Slice S12 / TP-24 — silent field-drop accounting.
        # Tracks fields visible to the scanner that no fill loop attempted.
        # Without this, an apply can succeed (queued_for_review) on a form
        # with an unfilled required field (Graphcore legal-name regression).
        total_fields_silently_dropped = 0
        silently_dropped_labels: list[dict] = []
        total_fill_failures: list[str] = []
        t0 = time.monotonic()
        _prev_fingerprint = ""
        _stuck_count = 0
        page_timings_list: list[tuple[int, int, int]] = []

        def _result(base: dict) -> dict:
            base.setdefault("field_types", seen_field_types)
            base.setdefault("screening_results", seen_screening)
            base.setdefault("time_seconds", round(time.monotonic() - t0, 1))
            base.setdefault("agent_mapping", all_agent_mappings)
            base["agent_fill_stats"] = {
                "fields_attempted": total_fields_attempted,
                "fields_filled": total_fields_filled,
                "fields_failed": len(total_fill_failures),
                "fields_silently_dropped": total_fields_silently_dropped,
                "silently_dropped_labels": silently_dropped_labels,
                "failed_labels": total_fill_failures,
                "llm_fallback_count": self._llm_fallback_count,
            }
            if base.get("success") and self._container_selector and self._fe_db:
                try:
                    page_url = getattr(self._page, "url", "") or ""
                    if page_url:
                        self._fe_db.store_container(page_url, self._container_selector)
                except Exception:
                    pass
            if page_timings_list and self._fe_db:
                _page_url = getattr(self._page, "url", "") or ""
                if _page_url:
                    avg_h = sum(h for h, _, _ in page_timings_list) // len(page_timings_list)
                    avg_f = sum(f for _, f, _ in page_timings_list) // len(page_timings_list)
                    transitions = [t for _, _, t in page_timings_list if t > 0]
                    avg_t = sum(transitions) // len(transitions) if transitions else 0
                    try:
                        self._fe_db.store_timing(_page_url, avg_h, avg_f, avg_t)
                    except Exception:
                        pass
            return base

        page_url = getattr(self._page, 'url', '') or ''

        for page_num in range(1, MAX_FORM_PAGES + 1):
            # 0. Detect unexpected SPA navigation (e.g. change events routing away)
            if page_num == 1:
                await self._recover_if_navigated(_expected_url)

            # 0a. Re-detect page type after page transition — catch walls,
            # login redirects, session expiry, and confirmation pages instead
            # of blindly assuming we're still on an application form.
            if page_num > 1:
                _post_nav_type = await self._detect_page_type_quick()
                if _post_nav_type == "verification_wall":
                    logger.warning("Page %d: verification wall detected after navigation", page_num)
                    return _result({"success": False, "error": "verification_wall_after_nav"})
                if _post_nav_type == "session_expired":
                    logger.warning("Page %d: session expired after navigation", page_num)
                    return _result({"success": False, "error": "session_expired"})
                if _post_nav_type == "login_form":
                    logger.warning("Page %d: login form detected — session likely expired", page_num)
                    return _result({"success": False, "error": "session_expired"})
                if _post_nav_type == "confirmation":
                    logger.info("Page %d: confirmation page detected", page_num)
                    return _result({"success": True, "pages_filled": page_num - 1})

            await self._dismiss_stale_dialogs()

            # 0.4. Clear browser signal buffer between pages
            if self._intelligence:
                self._intelligence.clear()
                await self._intelligence.inject_on_new_page()

            # 0.5. Dismiss cookie banners before scanning (defense-in-depth)
            try:
                from jobpulse.cookie_dismisser import dismiss_cookie_banner_playwright
                await dismiss_cookie_banner_playwright(self._page, timeout_ms=2000)
            except Exception:
                pass

            # 1. Scan fields (measure hydration time, with hydration wait for empty scans)
            t_hydration = time.monotonic()
            fields = await self._scan_fields()
            if not fields and page_num == 1:
                logger.info("Page 1: 0 fields — waiting for SPA hydration (up to 8s)")
                for _poll in range(4):
                    await asyncio.sleep(2.0)
                    fields = await self._scan_fields()
                    if fields:
                        logger.info("SPA hydration complete: %d fields after %.1fs",
                                    len(fields), (time.monotonic() - t_hydration))
                        break
            if not fields and page_num > 1 and self._container_selector:
                logger.info("Page %d: 0 fields in container %s — re-resolving container",
                            page_num, self._container_selector)
                old_container = self._container_selector
                try:
                    from jobpulse.form_engine.field_scanner import resolve_form_container
                    self._container_selector = await resolve_form_container(
                        self._page, self._strategy, self._fe_db,
                    )
                    if self._container_selector != old_container:
                        logger.info("Container changed: %s → %s", old_container, self._container_selector)
                    fields = await self._scan_fields()
                    if not fields:
                        self._container_selector = None
                        logger.info("Re-resolved container still empty — scanning full page")
                        fields = await self._scan_fields()
                except Exception as exc:
                    logger.debug("Container re-resolution failed: %s", exc)
                    self._container_selector = None
                    fields = await self._scan_fields()
            hydration_ms = int((time.monotonic() - t_hydration) * 1000)

            # Reasoning-LLM field analyser (cache-llm Step C):
            # The static a11y scan misses dynamic widgets — React Select
            # comboboxes report options=[], custom radio groups look like
            # buttons, hidden file inputs hide behind drag-drop divs. A
            # reasoning model corrects this per-page (cached by
            # (domain, page_signature) so the same form re-uses the
            # analysis). Augments each field with `true_type`,
            # `analyzed_options`, `fill_method`, `analyzer_reasoning`,
            # AND backfills the canonical `options` key when the scanner
            # came back empty so downstream semantic_matcher /
            # OptionAligner code keeps working unchanged.
            try:
                from jobpulse.form_engine.field_analyzer import analyze_fields
                page_text_for_analysis = ""
                try:
                    page_text_for_analysis = (await self._page.inner_text("body"))[:1500]
                except Exception:
                    pass
                fields = analyze_fields(
                    fields, url=page_url, page_text=page_text_for_analysis,
                )
            except Exception as exc:
                logger.debug("field_analyzer call failed (continuing): %s", exc)

            # Step C2: click-and-extract for analyzer-flagged comboboxes.
            # The analyzer correctly identifies fields like 'Country',
            # 'Gender', 'Veteran Status' as comboboxes but its options
            # are unreliable for closed-enum fields — Veteran Status and
            # Disability Status get hallucinated as Yes/No when the real
            # Greenhouse / Workday EEO enums are 3-item ('I am a protected
            # veteran' / 'I am not …' / 'I do not wish to answer').
            #
            # Therefore: ALWAYS click-extract for analyzer-flagged
            # comboboxes. DOM is ground truth; LLM-inferred options are
            # only a fallback when click-extract fails (field hidden,
            # listbox renders in unsupported portal, etc.).
            #
            # Two click strategies tried in order:
            #   1. Field's stored ``selector`` (when scanner populated it)
            #   2. Playwright's ``get_by_label(label)`` (works for any
            #      properly-labelled <input role="combobox">, which is
            #      most ATS dropdowns including Greenhouse / Workday /
            #      Ashby). The scanner often forgets to store selectors
            #      for these fields, so the label fallback is what
            #      actually catches Country / Gender / Veteran Status.
            try:
                from jobpulse.form_engine.field_scanner import _scan_combobox_options
                empty_combos = [
                    f for f in fields
                    if (f.get("true_type") or f.get("type") or "").lower() == "combobox"
                    and (f.get("label") or "").strip()
                ]
                if empty_combos:
                    logger.info(
                        "field_analyzer: opening %d combobox(es) to extract real options "
                        "from DOM (analyzer-inferred options can hallucinate on "
                        "EEO / closed-enum fields)",
                        len(empty_combos),
                    )
                    for f in empty_combos:
                        label = (f.get("label") or "").strip()
                        selector = (f.get("selector") or "").strip()
                        opts: list[str] = []
                        # 1. Try the scanner-supplied selector first.
                        if selector:
                            try:
                                opts = await _scan_combobox_options(
                                    self._page, selector, timeout_ms=2000,
                                )
                            except Exception as exc:
                                logger.debug(
                                    "selector click-extract failed for %r: %s",
                                    label[:40], exc,
                                )
                        # 2. Fall back to label-based locator.
                        if not opts and label:
                            label_for_fallback = label.rstrip("*").strip()
                            try:
                                loc = self._page.get_by_label(
                                    label_for_fallback, exact=False,
                                ).first
                                if await loc.count():
                                    await loc.click(timeout=2500)
                                    options = await self._page.evaluate(
                                        """() => {
                                            const sel = '[role="option"], '
                                                      + '[role="radio"], '
                                                      + '[role="menuitemcheckbox"], '
                                                      + 'li[role="option"]';
                                            return Array.from(
                                                document.querySelectorAll(sel)
                                            )
                                                .filter(o => o.offsetParent !== null)
                                                .map(o => (o.textContent || '').trim())
                                                .filter(t => t
                                                    && !/^select\\s*(one|an?\\s*option)?$/i.test(t)
                                                    && !/^loading/i.test(t));
                                        }"""
                                    )
                                    try:
                                        await self._page.keyboard.press("Escape")
                                        await self._page.wait_for_function(
                                            """() => document.querySelectorAll(
                                                '[role="option"], [role="radio"]'
                                            ).length === 0""",
                                            timeout=1000,
                                        )
                                    except Exception:
                                        pass
                                    opts = options or []
                            except Exception as exc:
                                logger.debug(
                                    "get_by_label click-extract failed for %r: %s",
                                    label[:40], exc,
                                )
                        if opts:
                            f["options"] = opts
                            f["analyzed_options"] = opts
                            logger.info(
                                "  ✓ %r → %d options: %s%s",
                                label[:50], len(opts),
                                opts[:5], "…" if len(opts) > 5 else "",
                            )
            except Exception as exc:
                logger.debug("combobox option-extraction skipped: %s", exc)

            _cur_fingerprint = self._fingerprint_fields(fields)
            if _cur_fingerprint == _prev_fingerprint and page_num > 1:
                _stuck_count += 1
                if _stuck_count >= 1:
                    logger.warning(
                        "Stuck: identical page fingerprint for %d consecutive pages", _stuck_count + 1,
                    )
                    # Cognitive fallback: try to reason our way out of the stuck state
                    cognitive_unstuck = await self._try_cognitive_unstuck(
                        fields, platform, page_url
                    )
                    if cognitive_unstuck:
                        logger.info("Cognitive unstuck succeeded — continuing fill")
                        _stuck_count = 0
                        continue
                    if self._fe_db:
                        try:
                            self._fe_db.record_failure_reason(
                                domain=page_url, platform=self._platform,
                                failure_type="stuck_page", field_label="",
                                details=f"Identical page fingerprint on page {page_num}",
                            )
                        except Exception:
                            pass
                    return _result({"success": False, "error": f"Stuck on identical page (page {page_num})"})
                logger.info(
                    "Page %d fingerprint matches previous — possible stuck (count=%d)", page_num, _stuck_count,
                )
            else:
                _stuck_count = 0
            _prev_fingerprint = _cur_fingerprint

            for f in fields:
                ft = f"{f['type']}:{f['label'].lower().replace(' ', '_')[:40]}"
                seen_field_types.append(ft)

            if page_num == 1 and self._stored_exp and self._stored_exp.get("success") and self._fe_db:
                from jobpulse.form_experience_db import FormExperienceDB as _FE
                validation = self._fe_db.validate_against_live(
                    page_url, seen_field_types, live_page_count=None,
                )
                if validation["trusted"]:
                    self._known_domain = True
                    logger.info(
                        "FAST PATH: domain %s validated (%.0f%% match, %d prior applies)",
                        _FE.normalize_domain(page_url),
                        validation["match_ratio"] * 100,
                        self._stored_exp.get("apply_count", 0),
                    )
                else:
                    self._known_domain = False
                    logger.warning(
                        "DRIFT DETECTED on %s — match %.0f%%, diverged: %s. Using full LLM path.",
                        _FE.normalize_domain(page_url),
                        validation["match_ratio"] * 100,
                        validation["diverged_fields"][:5],
                    )

            fields_by_label = {f["label"]: f for f in fields}

            try:
                from jobpulse.form_interaction_log import FormInteractionLog
                from urllib.parse import urlparse
                domain = urlparse(page_url).netloc.lower().removeprefix("www.")
                if domain:
                    FormInteractionLog().log_page_structure(
                        domain=domain,
                        platform=platform,
                        page_num=page_num,
                        page_title=await self._page.title(),
                        field_labels=[f["label"] for f in fields],
                        field_types=[f["type"] for f in fields],
                        has_file_upload=any(f["type"] == "file" for f in fields),
                    )
            except Exception as exc:
                logger.debug("form_interaction_log: %s", exc)

            # 2. Confirmation page?
            if await self._is_confirmation_page():
                return _result({"success": True, "pages_filled": page_num})

            # 3. Map fields — deterministic first, LLM only when needed
            mapping, llm_calls = await map_fields(
                page_url, fields, profile, custom_answers, platform,
                self._known_domain, self._correction_warning,
                self._domain_field_mappings, self._cached_screening,
            )
            self._llm_fallback_count += llm_calls

            # 3b. Vision fallback — SKIP for known domains
            if not self._known_domain:
                vision_unlabeled, v_calls = await vision_map_unlabeled_fields(
                    self._page, fields, profile, custom_answers, platform,
                )
                self._llm_fallback_count += v_calls
                if vision_unlabeled:
                    mapping.update(vision_unlabeled)
                    for lbl in vision_unlabeled:
                        if lbl not in fields_by_label:
                            fields_by_label[lbl] = {"label": lbl, "type": "text"}

            # 4. Screening: DB cache → pattern → V2 pipeline → LLM
            #    Skip text/select fields already pre-filled (by direct ID fill or form defaults)
            #    Radio/select fields always have a non-empty HTML value — include them
            #    unless the select has a genuinely pre-filled (non-placeholder) value
            unresolved = [
                f for f in fields
                if f["label"] not in mapping and f["type"] != "file"
                and (
                    f["type"] in ("radio", "custom_dropdown")
                    or not f.get("value")
                    or (f["type"] == "select" and _is_select_placeholder(f.get("value", "")))
                )
                and not any(s in _normalize_match_text(f["label"]) for s in _SKIP_FILL_LABELS)
            ]
            if unresolved:
                from jobpulse.screening_answers import try_instant_answer, try_screening_v2
                from jobpulse.screening_outcome_recorder import get_screening_outcome_recorder
                _outcome_recorder = get_screening_outcome_recorder()
                _job_ctx_raw = custom_answers.get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                still_unresolved = []
                for f in unresolved:
                    db_answer = self._cached_screening.get(f["label"].lower().strip())
                    # Validate the cached answer against the field's CURRENT
                    # options before using it (Step C2 follow-up: stale
                    # cache entries served wrong-shape answers because this
                    # fast path didn't re-align). For combobox/select
                    # fields with a known options list, the cached answer
                    # must either match an option exactly OR fuzzy-align
                    # to one — otherwise treat it as a cache miss and let
                    # the screening pipeline regenerate.
                    if db_answer:
                        field_options_now = f.get("options") or []
                        field_type_now = (f.get("true_type") or f.get("type") or "").lower()
                        accept_db_answer = True
                        if field_options_now and field_type_now in (
                            "combobox", "select", "radio", "custom_dropdown"
                        ):
                            try:
                                from jobpulse.screening_option_aligner import OptionAligner
                                aligner = OptionAligner()
                                aligned = aligner.align_answer(
                                    db_answer, field_options_now, field_type_now,
                                )
                                opts_lower = {
                                    (o or "").lower().strip()
                                    for o in field_options_now
                                }
                                if aligned.lower().strip() not in opts_lower:
                                    logger.info(
                                        "_cached_screening: stale entry for %r "
                                        "(answer=%r doesn't fit options=%s) — "
                                        "skipping cache, falling through to "
                                        "screening pipeline",
                                        f["label"][:60], db_answer[:60],
                                        [o[:30] for o in field_options_now[:5]],
                                    )
                                    accept_db_answer = False
                                else:
                                    db_answer = aligned
                            except Exception as exc:
                                logger.debug(
                                    "_cached_screening: alignment check failed "
                                    "for %r (%s) — accepting cached answer as-is",
                                    f["label"][:60], exc,
                                )
                        if accept_db_answer:
                            mapping[f["label"]] = db_answer
                            seen_screening.append({
                                "question": f["label"], "answer": db_answer,
                                "field_type": f.get("type", "text"), "field_options": f.get("options"),
                                "intent": "unknown", "strategy": "db_cache",
                            })
                            _outcome_recorder.record_fill(
                                question=f["label"], answer=db_answer,
                                field_options=f.get("options"), field_type=f.get("type", "text"),
                            )
                            continue
                    cached = try_instant_answer(
                        f["label"], _job_ctx,
                        input_type=f.get("type"), platform=platform,
                    )
                    if cached:
                        cached_text = str(cached).strip()
                        # Validate the regex/dictionary answer fits the field's
                        # current options. screening_answers.COMMON_ANSWERS is
                        # a hardcoded regex map that fires "Yes, within the UK"
                        # for any "open.*relocation" label, "No" for "veteran"
                        # / "disability" — none of those values exist in the
                        # actual EEO 3-item / Yes-No dropdown options on
                        # Greenhouse / Workday / Anthropic forms. Without
                        # alignment, the form gets wrong-shape free text typed
                        # into a closed-set picker, which selects the first
                        # accidental autocomplete match.
                        if cached_text:
                            cached_text = _align_screening_to_options(
                                cached_text, f, label_for_log=f["label"],
                            )
                        if cached_text:
                            mapping[f["label"]] = cached_text
                            seen_screening.append({
                                "question": f["label"], "answer": cached_text,
                                "field_type": f.get("type", "text"), "field_options": f.get("options"),
                                "intent": "unknown", "strategy": "pattern_match",
                            })
                            _outcome_recorder.record_fill(
                                question=f["label"], answer=cached_text,
                                field_options=f.get("options"), field_type=f.get("type", "text"),
                            )
                            continue
                        else:
                            still_unresolved.append(f)
                        continue

                    # V2 pipeline: semantic cache → intent → regex → rules → LLM
                    v2_answer = try_screening_v2(
                        f["label"], _job_ctx,
                        field={"type": f.get("type"), "options": f.get("options")},
                    )
                    if v2_answer:
                        v2_text = str(v2_answer).strip()
                        # Same alignment guard: the V2 pipeline's intent
                        # resolver and pattern fallback can also emit values
                        # that don't fit the current field's options
                        # (relocation defaults written before the form's
                        # options were known).
                        if v2_text:
                            v2_text = _align_screening_to_options(
                                v2_text, f, label_for_log=f["label"],
                            )
                        if v2_text:
                            mapping[f["label"]] = v2_text
                            seen_screening.append({
                                "question": f["label"], "answer": v2_text,
                                "field_type": f.get("type", "text"), "field_options": f.get("options"),
                                "intent": "unknown", "strategy": "screening_v2",
                            })
                            _outcome_recorder.record_fill(
                                question=f["label"], answer=v2_text,
                                field_options=f.get("options"), field_type=f.get("type", "text"),
                            )
                        else:
                            still_unresolved.append(f)
                    else:
                        still_unresolved.append(f)

                if still_unresolved:
                    _enriched_warning = self._correction_warning
                    if getattr(self, '_heuristics_context', ''):
                        _enriched_warning += f"\n\nLearned heuristics:\n{self._heuristics_context}"
                    screening, s_calls = await screen_questions(
                        still_unresolved, custom_answers.get("_job_context"),
                        self._profile_store, _enriched_warning,
                    )
                    self._llm_fallback_count += s_calls
                    screening = clean_mapping(screening)
                    mapping.update(screening)
                    for q, a in screening.items():
                        seen_screening.append({
                            "question": q, "answer": str(a),
                            "field_type": "text", "field_options": None,
                            "intent": "unknown", "strategy": "llm_fallback",
                        })
                        _outcome_recorder.record_fill(
                            question=q, answer=str(a),
                            field_options=None, field_type="text",
                        )

            # Merge caller-provided custom_answers into mapping (overrides screening)
            _known_labels = {f["label"] for f in fields}
            _known_norms = {_normalize_match_text(l): l for l in _known_labels}
            for k, v in custom_answers.items():
                if k.startswith("_") or not isinstance(v, str):
                    continue
                if k in _known_labels:
                    mapping[k] = v
                else:
                    k_norm = _normalize_match_text(k)
                    matched_label = _known_norms.get(k_norm)
                    if matched_label:
                        mapping[matched_label] = v

            all_agent_mappings.update({k: str(v) for k, v in mapping.items()})

            # 5. Fill each field by label (skip radio — handled by _fill_radio_groups)
            fill_failures = []
            pending_retries: list[dict[str, Any]] = []
            # Audit 2026-05-10 / S12 — per-page tracker for silent-drop detection.
            # Every visible field must end up either in `attempted_labels` (we
            # tried to fill it via this loop, a secondary loop, or LLM recovery)
            # or in the silent-drops report at end of page.
            attempted_labels: set[str] = set()
            for label, value in mapping.items():
                value_text = str(value).strip()
                if not value_text:
                    # Empty value = nothing to fill, but still emit so the skip
                    # is observable. (Was a silent `continue` before S12.)
                    logger.info(
                        "fill ⊘ '%s' reason=empty_value", label[:60],
                    )
                    attempted_labels.add(label)
                    continue
                if fields_by_label.get(label, {}).get("type") in ("radio", "custom_dropdown"):
                    # Routed to _fill_radio_groups / _fill_custom_dropdowns
                    # below; not silently dropped, just handled elsewhere.
                    attempted_labels.add(label)
                    continue
                attempted_labels.add(label)
                # Apply agent rule overrides from correction history
                override = _field_overrides.get(label.lower().strip())
                if override and override["action"] == "override_answer":
                    value_text = override["value"]
                    logger.info("Agent rule override: '%s' -> '%s'", label, value_text)
                # Pre-fill transform from prior signal corrections
                value_text = self._pre_fill_transform(_page_domain, label, value_text)
                total_fields_attempted += 1
                _fill_ts = time.monotonic() * 1000
                try:
                    result = await self._fill_by_label(label, value_text)
                    if result.get("success") and result.get("value_verified", True):
                        total_fields_filled += 1
                        _log_field_trajectory(
                            job_id=_job_id, domain=_page_domain,
                            field_label=label, field_type=fields_by_label.get(label, {}).get("type", "text"),
                            strategy="pattern_match", value=value_text,
                            confidence=0.9, time_ms=0, page_index=page_num,
                        )
                    else:
                        # Browser signal correction before adding to pending_retries
                        # Resolve locator using same fallback chain as _fill_by_label
                        _field_locator = self._page.get_by_label(label, exact=False)
                        if not await _field_locator.count():
                            _field_locator = self._page.get_by_placeholder(label, exact=False)
                        if not await _field_locator.count():
                            for _role in ("combobox", "textbox", "spinbutton"):
                                _rl = self._page.get_by_role(_role, name=label)
                                if await _rl.count():
                                    _field_locator = _rl
                                    break
                        _correction = await self._check_browser_signals(label, _field_locator, _fill_ts)
                        if _correction and _correction.transform != "none":
                            from jobpulse.signal_interpreter import TRANSFORMS
                            _tfn = TRANSFORMS.get(_correction.transform)
                            if _tfn:
                                _corrected = _tfn(value_text)
                                if _corrected != value_text:
                                    logger.info("Signal correction on '%s': %s('%s') -> '%s'",
                                                label, _correction.transform, value_text[:30], _corrected[:30])
                                    _retry = await self._fill_by_label(label, _corrected)
                                    if _retry.get("success"):
                                        _verified = await self._signal_interpreter.verify_correction(
                                            _field_locator, self._page,
                                        )
                                        if _verified and self._fe_db:
                                            self._fe_db.store_signal_correction(
                                                domain=_page_domain, field_label=label,
                                                signal_type=_correction.signal_type,
                                                error_message=_correction.error_message[:200],
                                                original_value=value_text, corrected_value=_corrected,
                                                transform=_correction.transform,
                                            )
                                        total_fields_filled += 1
                                        continue
                        pending_retries.append({
                            "field": fields_by_label.get(label, {"label": label, "type": "text"}),
                            "attempted_value": value_text,
                            "result": result,
                        })
                except Exception as fill_err:
                    err_str = str(fill_err).lower()
                    if "intercept" in err_str or "pointer" in err_str:
                        logger.warning("Overlay blocking '%s' — dismissing and retrying", label)
                        await self._dismiss_stale_dialogs()
                        try:
                            result = await self._fill_by_label(label, value_text)
                            if result.get("success") and result.get("value_verified", True):
                                total_fields_filled += 1
                                continue
                        except Exception:
                            pass
                    logger.warning("Field fill failed for '%s': %s", label, fill_err)
                    pending_retries.append({
                        "field": fields_by_label.get(label, {"label": label, "type": "text"}),
                        "attempted_value": value_text,
                        "result": {"success": False, "error": str(fill_err)},
                    })

            # 5b. Toggle buttons (YES/NO pairs not handled by _fill_by_label)
            toggle_filled = await self._fill_toggle_buttons(mapping, custom_answers)
            total_fields_filled += toggle_filled

            # 5c. Radio groups (pierces shadow DOM via get_by_role)
            radio_filled = await self._fill_radio_groups(mapping, custom_answers, fields)
            total_fields_filled += radio_filled

            # 5d. Custom React dropdowns (data-testid pattern)
            custom_dd_filled = await self._fill_custom_dropdowns(mapping, custom_answers, fields)
            total_fields_filled += custom_dd_filled

            # 6. File uploads (always run — page may have file inputs even
            # when scan returned 0 form fields, e.g. CV-only landing pages
            # like Revolut welovealfa.com /apply/upload-cv).
            logger.info("native_form_filler: invoking upload_files (cv_path=%s)",
                        bool(cv_path))
            await upload_files(self._page, cv_path, cl_path, custom_answers, self._get_accessible_name)

            # 7. Consent boxes
            await check_consent(self._page, self._get_accessible_name)

            # 7b. Second-chance recovery — LLM for all domains, vision only for unknown
            if pending_retries:
                retry_candidates = [
                    item for item in pending_retries
                    if item["field"].get("type") != "checkbox"
                ]
                recovered, r_calls = await recover_failed_fields_with_llm(
                    page_url, retry_candidates, profile, custom_answers, platform,
                    heuristics_context=getattr(self, '_heuristics_context', ''),
                )
                self._llm_fallback_count += r_calls
                if recovered:
                    learn_field_mapping(recovered, profile)

                still_failing: list[dict[str, Any]] = []
                for item in pending_retries:
                    label = item["field"]["label"]
                    retry_value = str(recovered.get(label, "")).strip() if recovered else ""
                    if retry_value and retry_value != item["attempted_value"]:
                        total_fields_attempted += 1
                        try:
                            retry_result = await self._fill_by_label(label, retry_value)
                        except Exception as exc:
                            logger.warning("LLM recovery fill failed for '%s': %s", label, exc)
                            still_failing.append(item)
                            continue
                        if retry_result.get("success") and retry_result.get("value_verified", True):
                            total_fields_filled += 1
                            _log_field_trajectory(
                                job_id=_job_id, domain=_page_domain,
                                field_label=label, field_type=item["field"].get("type", "text"),
                                strategy="llm_recovery", value=retry_value,
                                confidence=0.7, time_ms=0, page_index=page_num,
                            )
                            mapping[label] = retry_value
                            all_agent_mappings[label] = retry_value
                            if is_screening_like_field(item["field"]):
                                seen_screening.append({
                                    "question": label, "answer": retry_value,
                                    "field_type": item["field"].get("type", "text"),
                                    "field_options": item["field"].get("options"),
                                    "intent": "unknown", "strategy": "llm_recovery",
                                })
                                if _outcome_recorder is not None:
                                    _outcome_recorder.record_fill(
                                        question=label, answer=retry_value,
                                        field_options=item["field"].get("options"),
                                        field_type=item["field"].get("type", "text"),
                                    )
                            continue
                    still_failing.append(item)

                for item in still_failing:
                    if self._fe_db:
                        try:
                            self._fe_db.record_failure_reason(
                                domain=page_url, platform=self._platform,
                                failure_type=_classify_fill_failure(item["result"]),
                                field_label=item["field"]["label"],
                                selector=item["field"].get("selector", ""),
                                details=item["result"].get("error", ""),
                            )
                        except Exception:
                            pass

                # 7c. Vision recovery — SKIP for known domains
                if still_failing and not self._known_domain:
                    vision_recovered, vr_calls = await recover_failed_fields_with_vision(
                        self._page, still_failing, profile, custom_answers, platform,
                    )
                    self._llm_fallback_count += vr_calls
                    final_failed_labels: list[str] = []
                    for item in still_failing:
                        label = item["field"]["label"]
                        v_value = str(vision_recovered.get(label, "")).strip() if vision_recovered else ""
                        if v_value and v_value != item["attempted_value"]:
                            total_fields_attempted += 1
                            v_result = await self._fill_by_label(label, v_value)
                            if v_result.get("success") and v_result.get("value_verified", True):
                                total_fields_filled += 1
                                _log_field_trajectory(
                                    job_id=_job_id, domain=_page_domain,
                                    field_label=label, field_type=item["field"].get("type", "text"),
                                    strategy="vision_recovery", value=v_value,
                                    confidence=0.6, time_ms=0, page_index=page_num,
                                )
                                mapping[label] = v_value
                                all_agent_mappings[label] = v_value
                                self._save_gotcha(
                                    label, "vision_recovery_success",
                                    f"Vision suggested '{v_value}' after text LLM failed",
                                )
                                continue
                        final_failed_labels.append(label)
                        if self._fe_db:
                            try:
                                self._fe_db.record_failure_reason(
                                    domain=page_url, platform=self._platform,
                                    failure_type=_classify_fill_failure(item["result"]),
                                    field_label=label,
                                    selector=item["field"].get("selector", ""),
                                    details=item["result"].get("error", ""),
                                )
                            except Exception:
                                pass
                else:
                    final_failed_labels = []

                fill_failures.extend(final_failed_labels)
                total_fill_failures.extend(final_failed_labels)

                # Emit failure signals for persistently-unverified fills so the
                # OptimizationEngine learning loop sees them — same signal stream
                # as the navigator path's emit_fill_failures (source='form_filler').
                # For known domains vision is skipped so still_failing is definitive;
                # for unknown domains final_failed_labels is the persistent set.
                if self._known_domain:
                    _persistent = still_failing
                else:
                    _persistent_labels = set(final_failed_labels)
                    _persistent = [
                        item for item in still_failing
                        if item["field"].get("label") in _persistent_labels
                    ]
                if _persistent:
                    _failure_records = []
                    for _item in _persistent:
                        _field = _item.get("field", {})
                        _res = _item.get("result") or {}
                        _failure_records.append({
                            "label": _field.get("label", "") if isinstance(_field, dict) else str(_field),
                            "expected": str(_item.get("attempted_value", "")),
                            "actual": str(_res.get("actual_value", "") if isinstance(_res, dict) else ""),
                        })
                    emit_form_fill_failures(_failure_records, domain=_page_domain)

            # 7d. Post-fill rescan — catch conditionally-revealed fields
            await asyncio.sleep(0.5)
            rescan_fields = await self._scan_fields()
            original_labels = {f["label"] for f in fields}
            new_fields = [f for f in rescan_fields if f["label"] not in original_labels]
            if new_fields:
                logger.info(
                    "Post-fill rescan found %d new field(s): %s",
                    len(new_fields), [f["label"] for f in new_fields],
                )
                new_mapping, still_unresolved_rescan = seed_mapping(
                    new_fields, profile, custom_answers,
                    strategy=getattr(self, "_strategy", None),
                )
                for nf in still_unresolved_rescan:
                    lbl = nf["label"]
                    store = getattr(self, "_profile_store", None)
                    if nf.get("type") == "text":
                        el_type = nf.get("input_type", "")
                        lbl_lower = lbl.lower()
                        if store and (el_type == "date" or "date of birth" in lbl_lower or "dob" in lbl_lower):
                            dob = store.sensitive("date_of_birth")
                            if dob and re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
                                new_mapping[lbl] = dob
                                continue
                    from jobpulse.screening_answers import try_screening_v2
                    # Scan options for combobox/select fields surfaced via post-fill
                    # rescan (conditionally-revealed fields). The initial scan_fields
                    # call doesn't probe combobox dropdowns — without this, the LLM
                    # gets an unconstrained prompt and produces free-text paragraphs
                    # for what should be option-pick answers (e.g. "Please identify
                    # your race" with 6 options scanned-but-empty).
                    _nf_type = (nf.get("type") or "").lower()
                    _opts = nf.get("options") or []
                    if not _opts and _nf_type in ("combobox", "select", "radio", "custom_dropdown"):
                        try:
                            from jobpulse.form_scanner import scan_combobox_options
                            scanned = await scan_combobox_options(self._page, lbl)
                            if scanned:
                                _opts = scanned
                                nf["options"] = scanned
                                logger.info(
                                    "post_fill_rescan: scanned %d options for '%s' (was empty)",
                                    len(scanned), lbl[:60],
                                )
                        except Exception as exc:
                            logger.debug(
                                "post_fill_rescan: scan_combobox_options failed for '%s': %s",
                                lbl[:60], exc,
                            )
                    v2_answer = try_screening_v2(
                        lbl, _job_ctx,
                        field={"type": nf.get("type"), "options": _opts or None},
                    )
                    if v2_answer:
                        new_mapping[lbl] = str(v2_answer).strip()

                for lbl, val in new_mapping.items():
                    if not val:
                        continue
                    try:
                        res = await self._fill_by_label(lbl, val)
                        if res.get("success") and res.get("value_verified", True):
                            total_fields_filled += 1
                            mapping[lbl] = val
                            all_agent_mappings[lbl] = val
                            logger.info("Post-fill rescan filled: '%s' → '%s'", lbl, val)
                    except Exception as exc:
                        logger.warning("Post-fill rescan fill failed for '%s': %s", lbl, exc)

            # 7c. Silent-drop accounting — Audit 2026-05-10 / Slice S12 / TP-24.
            # `fields` is the original page scan; `attempted_labels` tracks every
            # label the main fill loop touched (filled, skipped-empty, or routed
            # to radio/custom_dropdown loops). Anything visible to the scanner
            # but never touched is a silent drop — the bug surfaced live on
            # Graphcore where a required combobox `'Have you added your full
            # legal name…?*'` was scanned but never filled, yet the apply still
            # routed to `queued_for_review` because the success accounting only
            # counted attempted fields.
            _page_silent_drops = _compute_silent_drops(fields, attempted_labels)
            for _drop in _page_silent_drops:
                logger.warning(
                    "fill ⊘ '%s' reason=%s required=%s type=%s — visible to scanner, not attempted",
                    _drop["label"][:60], _drop["reason"],
                    _drop["required"], _drop["type"],
                )
            total_fields_silently_dropped += len(_page_silent_drops)
            silently_dropped_labels.extend(_page_silent_drops)

            # 8. Timing measurement + anti-detection delay
            page_fill_ms = int((time.monotonic() - t_hydration) * 1000) - hydration_ms
            page_timings_list.append((hydration_ms, page_fill_ms, 0))

            page_delay = _get_adaptive_page_delay(platform, self._timing_data)
            if page_delay > 0:
                await asyncio.sleep(page_delay * random.uniform(0.8, 1.2))

            # 8b. Vision-canonical verification — Audit 2026-05-11 / Slice S26.
            # Treats the rendered form as the source of truth. Reads what is
            # actually visible in the screenshot and compares against the
            # filler's claim. Runs every page (not just submit page) so
            # mid-form mismatches are caught before the candidate clicks
            # Next. Best-effort; verifier failures never break the apply.
            # Kill switch: VISION_VERIFICATION_ENABLED.
            try:
                from jobpulse.form_engine.vision_verifier import verify_form_page
                vv_result = await verify_form_page(
                    self._page,
                    dict(mapping),
                    page_url=page_url,
                    platform=platform,
                    page_num=page_num,
                    fill_callback=self._fill_by_label,
                    field_metadata=getattr(self, "_fields_by_label", None),
                )
                if vv_result.mismatches or vv_result.vision_unavailable:
                    logger.warning(
                        "vision_verifier: page %d mismatches=%d corrections_ok=%d "
                        "corrections_fail=%d unavailable=%s",
                        page_num, vv_result.mismatches,
                        vv_result.corrections_applied,
                        vv_result.corrections_failed,
                        vv_result.vision_unavailable,
                    )
            except Exception as exc:
                logger.debug("vision_verifier hook skipped: %s", exc)

            # 9. Pre-submit review — SKIP for known domains
            if await self._is_submit_page():
                if dry_run:
                    return _result({
                        "success": True, "dry_run": True,
                        "pages_filled": page_num,
                    })
                if not self._known_domain:
                    review_result, rev_calls = await review_form(self._page)
                    self._llm_fallback_count += rev_calls
                    if not review_result.get("pass"):
                        logger.warning(
                            "Pre-submit review failed: %s", review_result.get("issues"),
                        )

            # 10. Snapshot live state — user mid-flow edits survive correction capture
            try:
                pre_nav_snapshot = await self._snapshot_live_form_state()
                if pre_nav_snapshot:
                    self._per_page_live_snapshots.append(pre_nav_snapshot)
                    logger.debug(
                        "snapshot_live_form_state: captured %d fields on page %d",
                        len(pre_nav_snapshot), page_num,
                    )
            except Exception as exc:
                logger.debug("snapshot_live_form_state: skipped: %s", exc)

            # 11. Click next/submit
            clicked = await self._click_navigation(dry_run)
            if clicked == "submitted":
                return _result({"success": True, "pages_filled": page_num})
            if clicked == "dry_run_stop":
                return _result({
                    "success": True, "dry_run": True,
                    "pages_filled": page_num,
                })
            if not clicked:
                return _result({
                    "success": False,
                    "error": f"No navigation button on page {page_num}",
                })

        return _result({
            "success": False,
            "error": f"Exhausted {MAX_FORM_PAGES} form pages",
        })
