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

        fills: dict[str, str] = {}
        for element_id, profile_key in self._domain_field_mappings.items():
            value = profile_flat.get(profile_key, "")
            if not value:
                value = custom_answers.get(profile_key, "")
            if value:
                fills[element_id] = str(value)

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
        return await scan_fields(
            self._page,
            strategy=self._strategy,
            form_experience_db=self._fe_db,
            container_selector=self._container_selector,
        )

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
        Returns True if the cognitive engine suggested a viable next action.
        """
        try:
            from shared.cognitive import get_cognitive_engine

            engine = get_cognitive_engine("form_filler")
            if not engine:
                return False

            field_summary = "\n".join(
                f"- {f.get('label', 'unknown')} ({f.get('type', 'unknown')})"
                for f in fields[:10]
            )
            task = (
                f"Platform: {platform}\n"
                f"URL: {page_url}\n"
                f"Current fields:\n{field_summary}\n\n"
                "The form appears stuck — the same page keeps appearing after clicking Next. "
                "What is the most likely cause and what single action should be taken to proceed? "
                "Answer in ONE sentence with a concrete action."
            )
            result = engine.think_sync(
                task=task,
                domain="form_navigation",
                stakes="medium",
            )
            if result and result.score >= 5.0:
                suggestion = result.answer.strip()
                logger.info("Cognitive unstuck suggestion (score=%.1f): %s", result.score, suggestion[:200])
                # Emit an optimization signal so the system learns
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
                            "reason": f"Cognitive unstuck for {platform}",
                        },
                    )
                except Exception:
                    pass
                return True
        except Exception as exc:
            logger.debug("Cognitive unstuck failed: %s", exc)
        return False

    # ── Human-Like Behavior (delegates to driver) ──

    async def _smart_scroll(self, el: Any) -> None:
        if hasattr(self._driver, '_smart_scroll'):
            await self._driver._smart_scroll(el)
        else:
            await el.scroll_into_view_if_needed()

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

    async def _fill_by_label(self, label: str, value: str) -> dict:
        page = self._page
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
            logger.warning("No field found for label '%s'", base_label)
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
                matched = False
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
                    if lbl.strip().lower() == fill_value.strip().lower():
                        await radio_el.scroll_into_view_if_needed()
                        await radio_el.click()
                        matched = True
                        break
                if not matched:
                    logger.warning("No radio in group '%s' matches '%s'", name_attr, fill_value)
            else:
                radio = page.get_by_role("radio", name=fill_value, exact=True)
                if await radio.count() == 1:
                    await radio.first.check()
                else:
                    logger.warning("Radio '%s' matched %d elements — skipping unscoped click", fill_value, await radio.count())
        elif await el.get_attribute("role") == "combobox":
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
            from jobpulse.form_scanner import (
                best_option_match as ax_best_match,
                best_range_match,
                scan_combobox_options,
            )
            if stored_technique == "combobox_type_to_search":
                ax_options = []
            else:
                ax_options = await scan_combobox_options(page, label)
            if ax_options:
                options_seen = ax_options
                matched_option = ax_best_match(
                    fill_value, ax_options,
                    aliases=_build_option_aliases(),
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
                    option = page.get_by_role("option", name=matched_option, exact=True)
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
                option_group = page.get_by_role("option")
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
                    option = page.get_by_role("option", name=matched_option, exact=False)
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
        """Overwrite auto-parsed experience descriptions with structured versions."""
        from jobpulse.config import EXPERIENCE_DESCRIPTIONS
        if not EXPERIENCE_DESCRIPTIONS:
            return
        page = self._page
        all_btns = await page.locator("button").all()
        for role_key, desc in EXPERIENCE_DESCRIPTIONS.items():
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
                dd_index = field.get("ddIndex", -1)
                result = await self._click_custom_dropdown_option(question, answer, dd_index)
                if result is True:
                    filled += 1
                    logger.info("Custom dropdown [%s]: '%s' → '%s'", test_id or dd_index, question[:80], answer)
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
        for name in ["Submit Application", "Submit", "Apply"]:
            btn = self._page.get_by_role("button", name=name, exact=False)
            if await btn.count() and await btn.first.is_visible():
                return True
        return False

    # ── Navigation ──

    async def _click_navigation(self, dry_run: bool) -> str:
        page = self._page
        button_names = [
            ("submit", ["Submit Application", "Submit", "Apply"]),
            ("next", ["Review", "Save & Continue", "Continue", "Next", "Proceed"]),
        ]

        for action, names in button_names:
            for name in names:
                btn = page.get_by_role("button", name=name, exact=False)
                if await btn.count() and await btn.first.is_visible():
                    if action == "submit" and dry_run:
                        return "dry_run_stop"
                    await self._move_mouse_to(btn.first)
                    await btn.first.click()
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=5000,
                        )
                    except Exception:
                        await asyncio.sleep(2)
                    return "submitted" if action == "submit" else "next"

        for name in ["Submit", "Apply Now", "Continue"]:
            link = page.get_by_role("link", name=name, exact=False)
            if await link.count() and await link.first.is_visible():
                await link.first.click()
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=5000,
                    )
                except Exception:
                    await asyncio.sleep(2)
                return "next"

        return ""

    # ── Public Interface ──

    async def scan_current_values(self) -> dict[str, str]:
        fields = await self._scan_fields()
        values: dict[str, str] = {}
        for f in fields:
            label = f["label"]
            if not label or f["type"] == "file":
                continue
            if f["type"] == "checkbox":
                values[label] = "checked" if f.get("checked") else "unchecked"
            elif f["type"] == "radio":
                for r in await f["locator"].get_by_role("radio").all():
                    if await r.is_checked():
                        values[label] = await self._get_accessible_name(r)
                        break
            else:
                val = f.get("value") or ""
                if val:
                    values[label] = val
        return values

    async def fill(
        self,
        platform: str,
        cv_path: str | None,
        cl_path: str | None,
        profile: dict,
        custom_answers: dict,
        dry_run: bool,
    ) -> dict:
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
        _page_domain = getattr(self._page, 'url', '') or ''

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

            await self._dismiss_stale_dialogs()

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
            hydration_ms = int((time.monotonic() - t_hydration) * 1000)

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
                    if db_answer:
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
            for label, value in mapping.items():
                value_text = str(value).strip()
                if not value_text:
                    continue
                if fields_by_label.get(label, {}).get("type") in ("radio", "custom_dropdown"):
                    continue
                # Apply agent rule overrides from correction history
                override = _field_overrides.get(label.lower().strip())
                if override and override["action"] == "override_answer":
                    value_text = override["value"]
                    logger.info("Agent rule override: '%s' -> '%s'", label, value_text)
                total_fields_attempted += 1
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

            # 6. File uploads
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
                new_mapping: dict[str, str] = {}
                for nf in new_fields:
                    lbl = nf["label"]
                    # Check custom_answers first
                    ca_val = custom_answers.get(lbl)
                    if not ca_val:
                        ca_norm = _normalize_match_text(lbl)
                        for k, v in custom_answers.items():
                            if k.startswith("_") or not isinstance(v, str):
                                continue
                            if _normalize_match_text(k) == ca_norm:
                                ca_val = v
                                break
                    if ca_val and isinstance(ca_val, str):
                        new_mapping[lbl] = ca_val
                        continue
                    # Profile store sensitive fields (e.g. date_of_birth)
                    if nf.get("type") == "text":
                        el_type = nf.get("input_type", "")
                        lbl_lower = lbl.lower()
                        store = getattr(self, "_profile_store", None)
                        if store and (el_type == "date" or "date of birth" in lbl_lower or "dob" in lbl_lower):
                            dob = store.sensitive("date_of_birth")
                            if dob and re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
                                new_mapping[lbl] = dob
                                continue
                    # Screening pipeline fallback
                    from jobpulse.screening_answers import try_screening_v2
                    v2_answer = try_screening_v2(
                        lbl, _job_ctx,
                        field={"type": nf.get("type"), "options": nf.get("options")},
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

            # 8. Timing measurement + anti-detection delay
            page_fill_ms = int((time.monotonic() - t_hydration) * 1000) - hydration_ms
            page_timings_list.append((hydration_ms, page_fill_ms, 0))

            page_delay = _get_adaptive_page_delay(platform, self._timing_data)
            if page_delay > 0:
                await asyncio.sleep(page_delay * random.uniform(0.8, 1.2))

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

            # 10. Click next/submit
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
