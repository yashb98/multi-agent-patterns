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
            if self._domain_field_mappings:
                logger.info("Loaded %d domain-specific field mappings for %s",
                            len(self._domain_field_mappings),
                            FormExperienceDB.normalize_domain(url))
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
            await page.get_by_label(fill_value).check()
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
                from jobpulse.form_experience_db import FormExperienceDB
                page_url = getattr(self._page, "url", "") or ""
                if page_url:
                    FormExperienceDB().record_fill_technique(
                        domain_or_url=page_url,
                        field_label=label,
                        field_type=f"{tag}:{input_type or role}",
                        technique=fill_technique,
                        value_used=actual or fill_value,
                        success=True,
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
        """Fill radiogroup fields that have labels+options from CDP scan."""
        page = self._page
        filled = 0
        for field in radio_fields:
            question = field["label"]
            options = field.get("options", [])
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
                continue

            try:
                radio = page.get_by_role("radio", name=target, exact=True)
                if await radio.count():
                    await self._smart_scroll(radio.first)
                    await radio.first.check(force=True)
                    filled += 1
                    logger.info("Radio group: '%s' → '%s'", question[:80], target)
            except Exception as exc:
                logger.warning("Radio group fill failed for '%s': %s", question[:80], exc)
        return filled

    # ── Page Detection ──

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
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if url:
                self._stored_exp = FormExperienceDB().lookup(url)
        except Exception:
            pass

        self._load_domain_field_mappings()
        self._load_cached_screening_answers()

        if self._domain_field_mappings:
            direct_filled = await self._fill_by_element_ids(profile, custom_answers)
            if direct_filled:
                logger.info("DIRECT ID FILL: pre-filled %d fields before page loop", len(direct_filled))

        await handle_modal_cv_upload(self._page, cv_path)

        await self._dismiss_stale_dialogs()

        seen_field_types: list[str] = []
        seen_screening: list[str] = []
        all_agent_mappings: dict[str, str] = {}
        total_fields_attempted = 0
        total_fields_filled = 0
        total_fill_failures: list[str] = []
        t0 = time.monotonic()
        _prev_fingerprint = ""
        _stuck_count = 0

        def _result(base: dict) -> dict:
            base.setdefault("field_types", seen_field_types)
            base.setdefault("screening_questions", seen_screening)
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
            return base

        page_url = getattr(self._page, 'url', '') or ''

        for page_num in range(1, MAX_FORM_PAGES + 1):
            await self._dismiss_stale_dialogs()

            # 1. Scan fields
            fields = await self._scan_fields()

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
                validation = self._fe_db.validate_against_live(
                    page_url, seen_field_types, live_page_count=None,
                )
                if validation["trusted"]:
                    self._known_domain = True
                    logger.info(
                        "FAST PATH: domain %s validated (%.0f%% match, %d prior applies)",
                        FormExperienceDB.normalize_domain(page_url),
                        validation["match_ratio"] * 100,
                        self._stored_exp.get("apply_count", 0),
                    )
                else:
                    self._known_domain = False
                    logger.warning(
                        "DRIFT DETECTED on %s — match %.0f%%, diverged: %s. Using full LLM path.",
                        FormExperienceDB.normalize_domain(page_url),
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
            unresolved = [
                f for f in fields
                if f["label"] not in mapping and f["type"] != "file"
            ]
            if unresolved:
                from jobpulse.screening_answers import try_instant_answer, try_screening_v2
                _job_ctx_raw = custom_answers.get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                still_unresolved = []
                for f in unresolved:
                    db_answer = self._cached_screening.get(f["label"].lower().strip())
                    if db_answer:
                        mapping[f["label"]] = db_answer
                        seen_screening.append(f"{f['label']}:{db_answer}")
                        continue
                    cached = try_instant_answer(
                        f["label"], _job_ctx,
                        input_type=f.get("type"), platform=platform,
                    )
                    if cached:
                        cached_text = str(cached).strip()
                        if cached_text:
                            mapping[f["label"]] = cached_text
                            seen_screening.append(f"{f['label']}:{cached_text}")
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
                            seen_screening.append(f"{f['label']}:{v2_text}")
                        else:
                            still_unresolved.append(f)
                    else:
                        still_unresolved.append(f)

                if still_unresolved:
                    screening, s_calls = await screen_questions(
                        still_unresolved, custom_answers.get("_job_context"),
                        self._profile_store, self._correction_warning,
                    )
                    self._llm_fallback_count += s_calls
                    screening = clean_mapping(screening)
                    mapping.update(screening)
                    for q, a in screening.items():
                        seen_screening.append(f"{q}:{a}")

            all_agent_mappings.update({k: str(v) for k, v in mapping.items()})

            # 5. Fill each field by label
            fill_failures = []
            pending_retries: list[dict[str, Any]] = []
            for label, value in mapping.items():
                value_text = str(value).strip()
                if not value_text:
                    continue
                total_fields_attempted += 1
                try:
                    result = await self._fill_by_label(label, value_text)
                    if result.get("success") and result.get("value_verified", True):
                        total_fields_filled += 1
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
                        retry_result = await self._fill_by_label(label, retry_value)
                        if retry_result.get("success") and retry_result.get("value_verified", True):
                            total_fields_filled += 1
                            mapping[label] = retry_value
                            all_agent_mappings[label] = retry_value
                            if is_screening_like_field(item["field"]):
                                seen_screening.append(f"{label}:{retry_value}")
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

            # 8. Anti-detection timing + timing measurement
            page_fill_ms = int((time.monotonic() - t0) * 1000) if page_num == 1 else None
            if page_fill_ms is not None:
                try:
                    from jobpulse.form_experience_db import FormExperienceDB
                    FormExperienceDB().store_timing(
                        page_url,
                        hydration_ms=0,
                        fill_ms=page_fill_ms,
                        transition_ms=0,
                    )
                except Exception:
                    pass

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
