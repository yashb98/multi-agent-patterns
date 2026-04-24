"""NativeFormFiller — Playwright native form-filling pipeline.

Uses Playwright's locator API (get_by_label, get_by_role, accessibility tree)
and LLM calls instead of extension-style snapshots and state machines.

Single Responsibility: this class owns field scanning, LLM mapping, label-based
filling, file uploads, consent, and navigation for the native engine. The
ApplicationOrchestrator delegates to this class when engine="playwright".
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import time
from typing import TYPE_CHECKING, Any

from shared.agents import get_openai_client, get_model_name
from shared.logging_config import get_logger
from shared.pii import assert_prompt_has_wrapped_pii, pii_json, wrap_pii_value

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

# Per-platform minimum page times (seconds) — kept in sync with orchestrator
_PLATFORM_MIN_PAGE_TIME: dict[str, float] = {
    "workday": 45.0,
    "linkedin": 3.0,
    "greenhouse": 5.0,
    "lever": 5.0,
    "indeed": 10.0,
    "generic": 5.0,
}

MAX_FORM_PAGES = 20

# Deterministic label→profile_key mapping. Grows as the LLM discovers new labels.
# Seed dict (always present), augmented by learned mappings from SQLite on first use.
_SEED_LABEL_TO_PROFILE_KEY: dict[str, str] = {
    "first name": "first_name", "last name": "last_name",
    "email": "email", "email address": "email",
    "confirm your email": "email", "confirm email": "email",
    "phone": "phone", "phone number": "phone", "mobile number": "phone",
    "linkedin": "linkedin", "linkedin url": "linkedin", "linkedin profile": "linkedin",
    "website": "portfolio", "portfolio": "portfolio", "personal website": "portfolio",
    "github": "github", "github url": "github",
    "city": "location", "location": "location",
    "headline": "headline", "current title": "headline",
    "address": "address", "street address": "address",
    "postcode": "postcode", "zip code": "postcode", "postal code": "postcode",
    "country": "country",
    "name": "full_name",
}

_FIELD_LABEL_TO_PROFILE_KEY: dict[str, str] = dict(_SEED_LABEL_TO_PROFILE_KEY)
_label_db_loaded = False

# ── Generic lookup tables (NOT personal data) ──

_GENDER_ALIASES: dict[str, tuple[str, ...]] = {
    "male": ("man",),
    "man": ("male",),
    "female": ("woman",),
    "woman": ("female",),
    "non-binary": ("nonbinary", "non binary", "prefer to self-describe"),
}

_ETHNICITY_ALIASES: dict[str, tuple[str, ...]] = {
    "asian indian": (
        "asian (indian, pakistani, bangladeshi, chinese, any other asian background)",
        "asian or asian british - indian",
    ),
    "asian or asian british - indian": (
        "asian (indian, pakistani, bangladeshi, chinese, any other asian background)",
        "asian indian",
    ),
    "white": ("white or caucasian", "white british", "white european"),
    "black african": ("black or black british - african",),
    "black caribbean": ("black or black british - caribbean",),
    "mixed": ("mixed or multiple ethnic groups",),
}

# ISO-style country data: canonical_name → (abbreviations + dial codes)
_COUNTRY_DATA: dict[str, tuple[str, ...]] = {
    "United Kingdom": ("uk", "gb", "u k", "great britain", "+44", "44", "united kingdom (+44)"),
    "United States": ("us", "usa", "+1", "1", "united states (+1)"),
    "Germany": ("de", "deutschland", "+49", "49"),
    "France": ("fr", "+33", "33"),
    "India": ("in", "+91", "91"),
    "Canada": ("ca", "+1"),
    "Australia": ("au", "+61", "61"),
    "Ireland": ("ie", "+353", "353"),
    "Netherlands": ("nl", "+31", "31"),
    "Spain": ("es", "+34", "34"),
    "Italy": ("it", "+39", "39"),
    "Japan": ("jp", "+81", "81"),
    "China": ("cn", "+86", "86"),
    "Brazil": ("br", "+55", "55"),
    "Singapore": ("sg", "+65", "65"),
    "Switzerland": ("ch", "+41", "41"),
    "Sweden": ("se", "+46", "46"),
    "Poland": ("pl", "+48", "48"),
    "Portugal": ("pt", "+351", "351"),
    "Belgium": ("be", "+32", "32"),
}


def _build_option_aliases(
    store: Any | None = None,
) -> dict[str, tuple[str, ...]]:
    """Build alias dict from generic data tables.

    Generic tables (_GENDER_ALIASES, _ETHNICITY_ALIASES, _COUNTRY_DATA)
    provide all needed infrastructure mappings.  The ``store`` parameter is
    reserved for future use (Task 2 integration) and is currently unused —
    the generic tables already cover all common ATS option variants.
    """
    aliases: dict[str, tuple[str, ...]] = {}
    # Gender
    aliases.update(_GENDER_ALIASES)
    # Ethnicity
    aliases.update(_ETHNICITY_ALIASES)
    # Country: build bidirectional mappings from _COUNTRY_DATA
    for canonical, abbrevs in _COUNTRY_DATA.items():
        canonical_lower = canonical.lower()
        # canonical → abbreviations
        existing = aliases.get(canonical_lower, ())
        aliases[canonical_lower] = existing + tuple(
            a for a in abbrevs if a not in existing
        )
        # each abbreviation → canonical
        for abbr in abbrevs:
            existing = aliases.get(abbr, ())
            if canonical_lower not in existing:
                aliases[abbr] = existing + (canonical_lower,)

    return aliases


def _profile_prompt_json(profile: dict[str, Any]) -> str:
    return pii_json(profile, "applicant.profile")


def _screening_prompt_profile(store: Any = None) -> dict[str, Any]:
    if store:
        ident = store.identity()
        work_auth = store.as_work_auth()
        return {
            "first_name": ident.first_name,
            "last_name": ident.last_name,
            "education": ident.education,
            "location": ident.location,
            "visa_status": work_auth.get("visa_status", ""),
            "notice_period": work_auth.get("notice_period", ""),
        }
    from jobpulse.applicator import PROFILE, WORK_AUTH

    return {
        "first_name": PROFILE["first_name"],
        "last_name": PROFILE["last_name"],
        "education": PROFILE["education"],
        "location": PROFILE["location"],
        "visa_status": WORK_AUTH["visa_status"],
        "notice_period": WORK_AUTH["notice_period"],
    }


def _screening_prompt_background(profile: dict[str, Any], store: Any = None) -> str:
    relocation = "Yes"
    commuting = "Yes"
    right_to_work = "Yes"
    country = "the UK"

    if store:
        relocation = store.screening_default("relocation") or "Yes"
        commuting = store.screening_default("commuting") or "Yes"
        right_to_work = store.screening_default("right_to_work") or "Yes"
        location = store.identity().location or ""
        parts = [p.strip() for p in location.split(",")]
        country = parts[-1] if len(parts) >= 2 else "the UK"

    return (
        f"Name: {wrap_pii_value('applicant.first_name', profile['first_name'])} "
        f"{wrap_pii_value('applicant.last_name', profile['last_name'])}. "
        f"Education: {wrap_pii_value('applicant.education', profile['education'])}. "
        f"Location: {wrap_pii_value('applicant.location', profile['location'])}. "
        f"Visa: {wrap_pii_value('applicant.visa_status', profile['visa_status'])}. "
        f"Notice: {wrap_pii_value('applicant.notice_period', profile['notice_period'])}. "
        f"Willing to relocate: {relocation}. "
        f"Commuting: {commuting}. "
        f"Right to work {country}: {right_to_work}."
    )


def _get_label_db_path() -> str:
    from jobpulse.config import DATA_DIR
    return str(DATA_DIR / "field_label_mappings.db")


def _ensure_label_db() -> None:
    """Load persisted label→profile_key mappings from SQLite on first use."""
    global _label_db_loaded
    if _label_db_loaded:
        return
    _label_db_loaded = True
    import sqlite3
    db_path = _get_label_db_path()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS label_mappings (
                    label TEXT PRIMARY KEY,
                    profile_key TEXT NOT NULL,
                    times_used INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            rows = conn.execute("SELECT label, profile_key FROM label_mappings").fetchall()
            for label, key in rows:
                if label not in _FIELD_LABEL_TO_PROFILE_KEY:
                    _FIELD_LABEL_TO_PROFILE_KEY[label] = key
        if rows:
            logger.info("Loaded %d persisted label mappings from SQLite", len(rows))
    except Exception as exc:
        logger.debug("Could not load label mappings: %s", exc)


def _persist_label_mapping(label: str, profile_key: str) -> None:
    """Save a new label→profile_key mapping to SQLite for future sessions."""
    import sqlite3
    from datetime import datetime, timezone
    try:
        with sqlite3.connect(_get_label_db_path()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS label_mappings (
                    label TEXT PRIMARY KEY,
                    profile_key TEXT NOT NULL,
                    times_used INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                """INSERT INTO label_mappings (label, profile_key, times_used, created_at)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(label) DO UPDATE SET
                       times_used = times_used + 1""",
                (label, profile_key, datetime.now(timezone.utc).isoformat()),
            )
    except Exception as exc:
        logger.debug("Could not persist label mapping: %s", exc)


def _get_field_gap(label_text: str = "") -> float:
    """Return delay in seconds based on label length (simulates reading)."""
    length = len(label_text)
    if length < 10:
        return 0.3 + random.uniform(0, 0.15)
    if length < 30:
        return 0.5 + random.uniform(0, 0.3)
    if length < 60:
        return 0.8 + random.uniform(0, 0.4)
    return 1.2 + random.uniform(0, 0.5)


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", str(text).lower()).strip()


def _canonicalize_country_value(label: str, value: str, *, store: Any | None = None) -> str:
    """Normalize country abbreviations to canonical names using _COUNTRY_DATA.

    When *store* is provided, extracts the user's country from
    ``store.identity().location`` to resolve their home country.
    Falls back to _COUNTRY_DATA lookup otherwise.
    """
    norm_label = _normalize_match_text(label)
    if "country" not in norm_label:
        return value

    norm_value = _normalize_match_text(value)

    # Try ProfileStore location first
    if store is not None:
        try:
            location = store.identity().location
            if location:
                # Extract country from "City, Country" format
                country_part = location.rsplit(",", 1)[-1].strip()
                for canonical, abbrevs in _COUNTRY_DATA.items():
                    if canonical.lower() == country_part.lower():
                        if norm_value in abbrevs or norm_value == canonical.lower():
                            return canonical
        except Exception:
            pass

    # Fall back to _COUNTRY_DATA lookup
    for canonical, abbrevs in _COUNTRY_DATA.items():
        if norm_value in abbrevs or norm_value == canonical.lower():
            return canonical

    return value


def _best_option_match(
    label: str, value: str, options: list[str], *, store: Any | None = None,
) -> str | None:
    """Return the best option match with country/gender/ethnicity alias support."""
    if not options:
        return None

    canonical_value = _canonicalize_country_value(label, value, store=store)
    norm_label = _normalize_match_text(label)
    norm_value = _normalize_match_text(canonical_value)
    normalized_options = [_normalize_match_text(opt) for opt in options]
    if not norm_value:
        return None

    if "country" in norm_label and norm_value == "united kingdom":
        for opt, norm_opt in zip(options, normalized_options):
            if "united kingdom" in norm_opt and "+44" in opt:
                return opt
        for opt, norm_opt in zip(options, normalized_options):
            if norm_opt == "united kingdom" or norm_opt.startswith("united kingdom"):
                return opt
        for opt in options:
            if "+44" in opt:
                return opt

    if "right to work status" in norm_label or ("visa" in norm_label and "status" in norm_label):
        if "student visa" in norm_value:
            for opt, norm_opt in zip(options, normalized_options):
                if "student visa" in norm_opt:
                    return opt
        if "graduate visa" in norm_value:
            for opt, norm_opt in zip(options, normalized_options):
                if "graduate visa" in norm_opt:
                    return opt
        if "skilled worker" in norm_value or "tier 2" in norm_value:
            for opt, norm_opt in zip(options, normalized_options):
                if "skilled worker" in norm_opt or "tier 2" in norm_opt:
                    return opt

    option_aliases = _build_option_aliases(store)
    for alias in option_aliases.get(norm_value, ()):
        norm_alias = _normalize_match_text(alias)
        for opt, norm_opt in zip(options, normalized_options):
            if norm_opt == norm_alias or norm_alias.startswith(norm_opt) or norm_opt.startswith(norm_alias):
                return opt

    for opt, norm_opt in zip(options, normalized_options):
        if norm_opt == norm_value:
            return opt
    for opt, norm_opt in zip(options, normalized_options):
        if norm_opt.startswith(norm_value):
            return opt
    if len(norm_value) >= 4:
        for opt, norm_opt in zip(options, normalized_options):
            if norm_value in norm_opt:
                return opt

    value_tokens = {
        token for token in norm_value.split()
        if len(token) > 2 and token not in {"and", "for", "the", "with", "from", "valid"}
    }
    best_option = None
    best_score = 0
    for opt, norm_opt in zip(options, normalized_options):
        option_tokens = {
            token for token in norm_opt.split()
            if len(token) > 2 and token not in {"and", "for", "the", "with"}
        }
        overlap = len(value_tokens & option_tokens)
        if overlap > best_score:
            best_score = overlap
            best_option = opt
    if best_option is not None and best_score >= 2:
        return best_option
    return None


class NativeFormFiller:
    """Playwright-native form filler using locators and LLM calls.

    Constructor receives:
        page — Playwright Page for locator-based field access
        driver — PlaywrightDriver for human-like mouse/scroll behavior
    """

    def __init__(self, page: "Page", driver: Any) -> None:
        self._page = page
        self._driver = driver
        self._correction_warning: str = ""
        self._llm_fallback_count: int = 0
        self._profile_store: Any = None

    # ── Label Extraction ──

    async def _get_accessible_name(self, locator: Any) -> str:
        """Extract the label a screen reader would announce for this element.

        Excludes aria-hidden children (e.g. required-field asterisks) so
        the returned text matches what Playwright's get_by_label() sees.
        """
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

    # ── Field Scanning ──

    async def _scan_fields(self) -> list[dict]:
        """Scan visible form fields — a11y tree first, Playwright fallback.

        Uses CDP Accessibility.getFullAXTree to pierce shadow DOM (discovers
        fields invisible to standard DOM queries like SmartRecruiters spl-*
        web components).  Falls back to role-based locators when CDP is
        unavailable.

        Returns a list of dicts with: label, type, locator, and
        type-specific keys (value, options, checked, required).
        """
        from jobpulse.form_scanner import scan_form

        page = self._page

        scan = await scan_form(page)
        if scan.fields:
            return self._ax_scan_to_field_dicts(scan)

        return await self._scan_fields_locator_fallback()

    def _ax_scan_to_field_dicts(self, scan) -> list[dict]:
        """Convert FormScanResult to the legacy field-dict format."""
        page = self._page
        _ROLE_TO_TYPE = {
            "textbox": "text", "combobox": "combobox", "spinbutton": "text",
            "radio": "radio", "checkbox": "checkbox", "button": "button",
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

    async def _scan_fields_locator_fallback(self) -> list[dict]:
        """Legacy scanner using Playwright role locators (no shadow DOM)."""
        page = self._page
        fields: list[dict] = []

        for loc in await page.get_by_role("textbox").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "text", "locator": loc,
                "value": await loc.input_value(),
                "required": await loc.get_attribute("required") is not None,
            })

        for loc in await page.get_by_role("combobox").all():
            label = await self._get_accessible_name(loc)
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
            label = await self._get_accessible_name(loc)
            radios = await loc.get_by_role("radio").all()
            option_labels = [await self._get_accessible_name(r) for r in radios]
            fields.append({
                "label": label, "type": "radio", "options": option_labels,
                "locator": loc,
            })

        for loc in await page.get_by_role("checkbox").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "checkbox", "locator": loc,
                "checked": await loc.is_checked(),
            })

        for loc in await page.locator("textarea:visible").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "textarea", "locator": loc,
                "value": await loc.input_value(),
            })

        for loc in await page.locator("input[type='file']").all():
            label = await self._get_accessible_name(loc)
            fields.append({"label": label, "type": "file", "locator": loc})

        return fields

    # ── Auto-Gotcha Learning ──

    def _save_gotcha(self, label: str, problem: str, solution: str) -> None:
        """Auto-save a form-filling gotcha for the current domain."""
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

    # ── Human-Like Behavior (delegates to driver) ──

    async def _smart_scroll(self, el: Any) -> None:
        """Scroll element into view with human-like delay."""
        if hasattr(self._driver, '_smart_scroll'):
            await self._driver._smart_scroll(el)
        else:
            await el.scroll_into_view_if_needed()

    async def _move_mouse_to(self, el: Any) -> None:
        """Move mouse to element with Bezier curve."""
        if hasattr(self._driver, '_move_mouse_to'):
            await self._driver._move_mouse_to(el)

    async def _normalize_phone_value(self, label: str, value: str) -> str:
        """Normalize phone numbers for split country-code widgets."""
        if "phone" not in _normalize_match_text(label):
            return value

        digits = re.sub(r"\D+", "", value)
        if not digits:
            return value

        # Get phone code from ProfileStore country
        phone_code = "44"  # default
        store = getattr(self, "_profile_store", None)
        if store:
            location = store.identity().location or ""
            parts = [p.strip() for p in location.split(",")]
            country = parts[-1] if len(parts) >= 2 else ""
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
        """Fill a single form field using Playwright's label-based locator.

        Tries get_by_label first, falls back to get_by_placeholder.
        Handles text, select, checkbox, and radio input types.
        Returns {"success": bool, "value_set": str, "value_verified": bool}.
        """
        page = self._page
        await asyncio.sleep(_get_field_gap(label))

        special_result = await self._fill_special_widget(label, value)
        if special_result is not None:
            return special_result

        # Try label-based locator first
        locator = page.get_by_label(label, exact=False)

        if not await locator.count():
            locator = page.get_by_placeholder(label, exact=False)

        if not await locator.count():
            logger.warning("No field found for label '%s'", label)
            return {"success": False, "error": f"No field for '{label}'"}

        # Find the first fillable element among matches (skip icons, images, etc.)
        _FILLABLE_TAGS = {"input", "textarea", "select"}
        el = None
        try:
            for i in range(await locator.count()):
                candidate = locator.nth(i)
                t = await candidate.evaluate("el => el.tagName.toLowerCase()")
                if t in _FILLABLE_TAGS or await candidate.get_attribute("contenteditable"):
                    el = candidate
                    break
        except Exception:
            pass
        if el is None:
            locator = page.get_by_placeholder(label, exact=False)
            if not await locator.count():
                logger.warning("No fillable field found for label '%s'", label)
                return {"success": False, "error": f"No fillable field for '{label}'"}
            el = locator.first

        await self._smart_scroll(el)
        await self._move_mouse_to(el)

        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        input_type = await el.get_attribute("type") or ""
        role = await el.get_attribute("role") or ""

        fill_value = _canonicalize_country_value(label, value, store=self._profile_store)
        options_seen: list[str] = []
        expected_value = fill_value

        if tag == "select":
            selected = False
            options = await el.locator("option").all_text_contents()
            options_stripped = [o.strip() for o in options]
            options_seen = options_stripped
            # Try exact label match
            try:
                await el.select_option(label=fill_value, timeout=5000)
                selected = True
            except Exception:
                pass
            # Try deterministic option matching with UK/+44 preference
            if not selected:
                matched_option = _best_option_match(label, fill_value, options_stripped, store=self._profile_store)
                if matched_option is not None:
                    try:
                        await el.select_option(label=matched_option, timeout=5000)
                        selected = True
                        expected_value = matched_option
                        # Auto-save gotcha: exact match failed, fuzzy worked
                        self._save_gotcha(
                            label, "select_exact_failed",
                            f"Use option '{matched_option}' for value '{fill_value}'",
                        )
                    except Exception:
                        pass
            # Try by value attribute
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
            fill_value = _canonicalize_country_value(label, fill_value, store=self._profile_store)
            from jobpulse.form_scanner import (
                best_option_match as ax_best_match,
                best_range_match,
                scan_combobox_options,
            )
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
            else:
                await el.fill("")
                await el.fill(fill_value)
                await asyncio.sleep(0.8)
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
        return {
            "success": True,
            "value_set": fill_value,
            "value_verified": verified,
            "actual_value": actual,
            "options_seen": options_seen,
            "expected_value": expected_value,
        }

    async def _fill_special_widget(self, label: str, value: str) -> dict[str, Any] | None:
        """Handle widgets that are not exposed by a usable label locator."""
        norm_label = _normalize_match_text(label)
        if "country options" not in norm_label:
            return None

        button = self._page.locator("button.iti__selected-country").first
        if not await button.count():
            return {"success": False, "error": "No phone country widget found"}

        # Resolve country from ProfileStore
        search_term = "United Kingdom"
        phone_code = "+44"
        store = getattr(self, "_profile_store", None)
        if store:
            location = store.identity().location or ""
            parts = [p.strip() for p in location.split(",")]
            country = parts[-1] if len(parts) >= 2 else ""
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

    # ── Field Mapping (deterministic first, LLM fallback) ──

    @staticmethod
    def _is_screening_like_field(field: dict[str, Any]) -> bool:
        return (
            field.get("type") in {"select", "combobox", "radio", "checkbox"}
            or "?" in field.get("label", "")
        )

    @staticmethod
    def _learn_field_mapping(mapping: dict[str, str], profile: dict) -> None:
        """Learn new label→profile_key associations from LLM results.

        Persists new mappings to SQLite so future sessions skip the LLM.
        """
        from jobpulse.applicator import PROFILE
        profile_flat = {**PROFILE, **profile}

        value_to_key: dict[str, str] = {}
        for k, v in profile_flat.items():
            if v and isinstance(v, str):
                value_to_key[v.strip().lower()] = k

        new_count = 0
        for label, value in mapping.items():
            label_lower = label.lower()
            if label_lower in _FIELD_LABEL_TO_PROFILE_KEY:
                continue
            val_lower = str(value).strip().lower()
            profile_key = value_to_key.get(val_lower)
            if profile_key:
                _FIELD_LABEL_TO_PROFILE_KEY[label_lower] = profile_key
                _persist_label_mapping(label_lower, profile_key)
                new_count += 1

        if new_count:
            logger.info("Learned %d new field label mappings (persisted to SQLite)", new_count)

    def _try_cached_mapping(
        self, fields: list[dict], profile: dict, custom_answers: dict,
    ) -> dict | None:
        """Try to resolve field mapping from cached label→profile_key templates."""
        _ensure_label_db()
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if not url:
                return None
            db = FormExperienceDB()
            exp = db.lookup(url)
            if not exp or not exp.get("field_types"):
                return None

            from jobpulse.applicator import PROFILE, WORK_AUTH
            profile_flat = {**PROFILE, **profile}
            label_key_map = _FIELD_LABEL_TO_PROFILE_KEY

            mapping: dict[str, str] = {}
            unmapped: list[str] = []
            for f in fields:
                if f["type"] == "file" or f.get("value"):
                    continue
                label = f["label"]
                key = label_key_map.get(label.lower())
                if key and key in profile_flat:
                    mapping[label] = profile_flat[key]
                elif label.lower() in custom_answers:
                    mapping[label] = custom_answers[label.lower()]
                else:
                    unmapped.append(label)

            if unmapped:
                return None
            if mapping:
                logger.info("Field mapping: %d fields resolved from cache (0 LLM calls)", len(mapping))
            return mapping if mapping else None
        except Exception:
            return None

    @staticmethod
    def _clean_mapping(mapping: dict[str, Any]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for label, value in mapping.items():
            if value is None:
                continue
            text = str(value).strip()
            if text:
                cleaned[label] = text
        return cleaned

    def _seed_mapping(
        self,
        fields: list[dict],
        profile: dict,
        custom_answers: dict,
    ) -> tuple[dict[str, str], list[dict]]:
        """Resolve any field that has a deterministic profile/custom answer."""
        _ensure_label_db()
        from jobpulse.applicator import PROFILE

        profile_flat = {**PROFILE, **profile}
        mapping: dict[str, str] = {}
        unresolved: list[dict] = []

        for field in fields:
            if field["type"] == "file" or field.get("value"):
                continue

            label = field["label"]
            label_lower = label.lower()
            custom_value = custom_answers.get(label_lower)
            if isinstance(custom_value, str) and custom_value.strip():
                mapping[label] = custom_value.strip()
                continue

            profile_key = _FIELD_LABEL_TO_PROFILE_KEY.get(label_lower)
            profile_value = profile_flat.get(profile_key, "") if profile_key else ""
            if isinstance(profile_value, str) and profile_value.strip():
                mapping[label] = profile_value.strip()
                continue

            unresolved.append(field)

        return mapping, unresolved

    async def _map_fields(
        self, fields: list[dict], profile: dict,
        custom_answers: dict, platform: str,
    ) -> dict:
        """Map profile data to form field labels.

        Tries deterministic cache first; falls back to LLM.
        Returns {"label": "value"} for each field the LLM can fill.
        """
        # Tier 1: full cached mapping if we already know every field
        cached = self._try_cached_mapping(fields, profile, custom_answers)
        if cached is not None:
            return self._clean_mapping(cached)

        # Tier 1b: merge deterministic profile/custom mappings first, then ask
        # the LLM only for the labels we still cannot resolve locally.
        mapping, unresolved = self._seed_mapping(fields, profile, custom_answers)
        if not unresolved:
            return mapping

        # Tier 2: LLM mapping (fallback) for profile-like fields only.
        # Screening-style prompts (question labels, selects, radios, checkboxes)
        # should flow through `try_instant_answer()` / `_screen_questions()`
        # so option-aware logic can choose a valid answer.
        llm_fields = [
            field for field in unresolved
            if field["type"] not in {"select", "combobox", "radio", "checkbox"}
            and "?" not in field["label"]
        ]
        if not llm_fields:
            return mapping

        self._llm_fallback_count += 1
        field_descriptions = []
        for f in llm_fields:
            desc = f"- {f['label']} ({f['type']})"
            if f.get("options"):
                desc += f" options: {f['options'][:10]}"
            if f.get("value"):
                desc += f" [already filled: {f['value']}]"
            if f.get("required"):
                desc += " *required"
            field_descriptions.append(desc)

        if not field_descriptions:
            return {}

        prompt = (
            f'Map profile data to form fields. Return JSON {{"label": "value"}}.\n'
            f"CRITICAL: JSON keys MUST be the EXACT label text from the Fields list below. "
            f"Do NOT rename, normalize, or invent keys. Only include fields that appear in the list.\n"
            f"Skip fields marked [already filled]. Skip file upload fields.\n\n"
            f"Fields:\n{chr(10).join(field_descriptions)}\n\n"
            f"Profile: {_profile_prompt_json(profile)}\n"
            f"Platform: {platform}\n"
            f"Known answers: {json.dumps({k: v for k, v in custom_answers.items() if not k.startswith('_')})}"
            f"{self._correction_warning}"
        )
        assert_prompt_has_wrapped_pii(prompt, profile, "applicant.profile")

        client = get_openai_client()
        try:
            response = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=2000,
                temperature=0.0,
                timeout=30,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            llm_mapping = self._clean_mapping(json.loads(raw))
        except json.JSONDecodeError as e:
            logger.error("LLM returned invalid JSON for field mapping: %s", e)
            return mapping
        except Exception as e:
            logger.error("LLM field mapping call failed: %s", e)
            return mapping

        # Learn: save label→profile_key for deterministic reuse
        self._learn_field_mapping(llm_mapping, profile)
        mapping.update(llm_mapping)

        return mapping

    async def _screen_questions(
        self, unresolved_fields: list[dict], job_context: str | None,
    ) -> dict:
        """LLM Call 2: answer screening questions not mapped from profile.

        Only called when _map_fields left non-file fields unresolved.
        Returns {"label": "answer"} dict.
        """
        questions = []
        for f in unresolved_fields:
            opts = f.get("options", "free text")
            questions.append(f"Q: {f['label']} Options: {opts}")

        prompt_profile = _screening_prompt_profile(self._profile_store)
        applicant_bg = _screening_prompt_background(prompt_profile, self._profile_store)
        prompt = (
            f"Answer these screening questions for a job application.\n"
            f"Context: {job_context or 'Not provided'}\n"
            f"Applicant: {applicant_bg}\n\n"
            f"{chr(10).join(questions)}\n\n"
            f"CRITICAL: JSON keys MUST be the EXACT question label text. "
            f"Choose ONLY from the given options when options are listed.\n"
            f'Return JSON {{"label": "answer"}}.'
            f"{self._correction_warning}"
        )
        assert_prompt_has_wrapped_pii(prompt, prompt_profile, "applicant")

        client = get_openai_client()
        try:
            response = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=2000,
                temperature=0.0,
                timeout=30,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            answers = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("LLM returned invalid JSON for screening answers: %s", e)
            return {}
        except Exception as e:
            logger.error("LLM screening answer call failed: %s", e)
            return {}

        self._llm_fallback_count += 1
        # Cache LLM answers so the same questions never hit the LLM again
        try:
            from jobpulse.job_db import JobDB
            db = JobDB()
            for q, a in answers.items():
                db.cache_answer(q, str(a))
            logger.info("Cached %d screening answers from LLM", len(answers))
        except Exception as exc:
            logger.debug("Could not cache screening answers: %s", exc)

        return answers

    async def _recover_failed_fields_with_llm(
        self,
        failed_fields: list[dict[str, Any]],
        profile: dict[str, Any],
        custom_answers: dict[str, Any],
        platform: str,
    ) -> dict[str, str]:
        """Ask the LLM for alternate values after a DOM fill did not verify."""
        if not failed_fields:
            return {}

        from jobpulse.applicator import PROFILE

        profile_full = {**PROFILE, **profile}
        field_lines: list[str] = []
        for item in failed_fields:
            field = item["field"]
            result = item["result"]
            attempted = item["attempted_value"]
            actual = result.get("actual_value") or "<empty>"
            desc = (
                f"- {field['label']} ({field['type']}) attempted: {attempted!r}; "
                f"actual on page after fill: {actual!r}"
            )
            options = result.get("options_seen") or field.get("options") or []
            if options:
                desc += f"; visible options: {options[:15]}"
            field_lines.append(desc)

        prompt = (
            "A job application field fill did not stick in the DOM. "
            "Suggest alternate values only for fields you can improve.\n"
            f"Platform: {platform}\n"
            f"Job context: {custom_answers.get('_job_context') or 'Not provided'}\n"
            f"Applicant profile: {_profile_prompt_json(profile_full)}\n\n"
            f"Failed fields:\n{chr(10).join(field_lines)}\n\n"
            "Rules:\n"
            "- Return JSON only.\n"
            "- JSON keys must be the exact field labels above.\n"
            "- If options are listed, choose only from those options.\n"
            "- Prefer a different value from the failed attempt when that will help the widget stick.\n"
            "- Omit fields where the failure is browser/widget behavior rather than the value itself.\n"
        )
        assert_prompt_has_wrapped_pii(prompt, profile_full, "applicant.profile")

        self._llm_fallback_count += 1
        client = get_openai_client()
        try:
            response = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=1200,
                temperature=0.0,
                timeout=30,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            recovered = self._clean_mapping(json.loads(raw))
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON for fill recovery: %s", exc)
            return {}
        except Exception as exc:
            logger.error("LLM recovery call failed: %s", exc)
            return {}

        if not recovered:
            return {}

        try:
            from jobpulse.job_db import JobDB

            db = JobDB()
            failed_by_label = {item["field"]["label"]: item for item in failed_fields}
            for label, value in recovered.items():
                item = failed_by_label.get(label)
                if item and self._is_screening_like_field(item["field"]):
                    db.cache_answer(label, value)
                if item:
                    attempted = item["attempted_value"]
                    actual = item["result"].get("actual_value") or "<empty>"
                    self._save_gotcha(
                        label,
                        "dom_fill_unverified",
                        f"LLM recovery suggested '{value}' after '{attempted}' verified as '{actual}'",
                    )
        except Exception as exc:
            logger.debug("Could not persist LLM recovery learning: %s", exc)

        return recovered

    async def _recover_failed_fields_with_vision(
        self,
        failed_fields: list[dict[str, Any]],
        profile: dict[str, Any],
        custom_answers: dict[str, Any],
        platform: str,
    ) -> dict[str, str]:
        """Vision fallback: screenshot the form and ask a vision model to suggest values."""
        if not failed_fields:
            return {}

        try:
            screenshot_png = await self._page.screenshot(type="png")
        except Exception as exc:
            logger.warning("Vision recovery: could not capture screenshot: %s", exc)
            return {}

        from jobpulse.applicator import PROFILE

        profile_full = {**PROFILE, **profile}
        field_lines = []
        for item in failed_fields:
            field = item["field"]
            attempted = item["attempted_value"]
            actual = item["result"].get("actual_value") or "<empty>"
            desc = f"- {field['label']} ({field['type']}) attempted: {attempted!r}, actual: {actual!r}"
            options = item["result"].get("options_seen") or field.get("options") or []
            if options:
                desc += f"; options: {options[:10]}"
            field_lines.append(desc)

        b64_image = base64.b64encode(screenshot_png).decode("ascii")
        job_ctx = custom_answers.get("_job_context") or "Not provided"
        prompt = (
            "Look at this job application form screenshot. "
            "Some fields were not filled correctly. "
            "For each failed field below, identify the field in the screenshot and suggest the correct value.\n\n"
            f"Failed fields:\n{chr(10).join(field_lines)}\n\n"
            f"Applicant: {_profile_prompt_json(profile_full)}\n"
            f"Job context: {job_ctx}\n"
            f"Platform: {platform}\n\n"
            "Rules:\n"
            '- Return JSON only: {{"label": "value"}}.\n'
            "- Keys must be the exact field labels above.\n"
            "- If you can see dropdown options in the screenshot, choose from visible options.\n"
            "- Omit fields you cannot identify in the screenshot.\n"
        )
        assert_prompt_has_wrapped_pii(prompt, profile_full, "applicant.profile")

        self._llm_fallback_count += 1
        client = get_openai_client()
        try:
            response = client.responses.create(
                model="gpt-4.1-mini",
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{b64_image}",
                        },
                    ],
                }],
            )
            raw = response.output_text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            recovered = self._clean_mapping(json.loads(raw))
            logger.info("Vision recovery mapped %d fields", len(recovered))
        except json.JSONDecodeError as exc:
            logger.error("Vision recovery returned invalid JSON: %s", exc)
            return {}
        except Exception as exc:
            logger.error("Vision recovery call failed: %s", exc)
            return {}

        return recovered

    async def _vision_map_unlabeled_fields(
        self,
        fields: list[dict],
        profile: dict[str, Any],
        custom_answers: dict[str, Any],
        platform: str,
    ) -> dict[str, str]:
        """Vision fallback for fields with empty/missing labels (shadow DOM).

        Takes a screenshot and asks the vision model to identify unlabeled
        fields by their visual position and context on the page.
        """
        unlabeled = [f for f in fields if not f.get("label", "").strip() and f["type"] != "file"]
        if not unlabeled:
            return {}

        try:
            screenshot_png = await self._page.screenshot(type="png")
        except Exception as exc:
            logger.warning("Vision unlabeled scan: could not capture screenshot: %s", exc)
            return {}

        from jobpulse.applicator import PROFILE

        profile_full = {**PROFILE, **profile}
        b64_image = base64.b64encode(screenshot_png).decode("ascii")

        field_descs = []
        for i, f in enumerate(unlabeled):
            desc = f"- Field #{i+1} (type: {f['type']})"
            if f.get("value"):
                desc += f" [current value: {f['value']}]"
            if f.get("options"):
                desc += f" options: {f['options'][:10]}"
            field_descs.append(desc)

        job_ctx = custom_answers.get("_job_context") or "Not provided"
        prompt = (
            "Look at this job application form screenshot. "
            f"There are {len(unlabeled)} form fields with no accessible label (shadow DOM). "
            "Identify each field by its visual position and surrounding text in the screenshot.\n\n"
            f"Unlabeled fields:\n{chr(10).join(field_descs)}\n\n"
            f"Applicant profile: {_profile_prompt_json(profile_full)}\n"
            f"Job context: {job_ctx}\n"
            f"Platform: {platform}\n\n"
            "For each field you can identify, return the answer.\n"
            'Return JSON: {{"Field #1": "value", "Field #2": "value"}}.\n'
            "Only include fields you can confidently identify."
        )
        assert_prompt_has_wrapped_pii(prompt, profile_full, "applicant.profile")

        self._llm_fallback_count += 1
        client = get_openai_client()
        try:
            response = client.responses.create(
                model="gpt-4.1-mini",
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{b64_image}",
                        },
                    ],
                }],
            )
            raw = response.output_text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            vision_map = json.loads(raw)
            logger.info("Vision identified %d unlabeled fields", len(vision_map))
        except json.JSONDecodeError as exc:
            logger.error("Vision unlabeled mapping returned invalid JSON: %s", exc)
            return {}
        except Exception as exc:
            logger.error("Vision unlabeled mapping failed: %s", exc)
            return {}

        mapping: dict[str, str] = {}
        for key, value in vision_map.items():
            m = re.match(r"Field #(\d+)", key)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(unlabeled):
                label = unlabeled[idx].get("label") or f"_unlabeled_{idx}"
                mapping[label] = str(value).strip()

        return mapping

    async def _review_form(self) -> dict:
        """LLM Call 3: screenshot-based pre-submit review of the filled form.

        Returns {"pass": true} or {"pass": false, "issues": [...]}.
        """
        screenshot_bytes = await self._page.screenshot(type="png")
        b64 = base64.b64encode(screenshot_bytes).decode()

        prompt = (
            "Review this filled application form. Any empty required fields, "
            'wrong values, or mismatches? Return {"pass": true} or '
            '{"pass": false, "issues": [...]}'
        )

        self._llm_fallback_count += 1
        client = get_openai_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=1000,
            temperature=0.0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                    }},
                ],
            }],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)

    # ── Deterministic Helpers ──

    @staticmethod
    async def _upload_pdf(locator, file_path: str) -> None:
        """Upload a PDF with explicit MIME type and filename for ATS compatibility."""
        from pathlib import Path
        p = Path(file_path)
        await locator.set_input_files({
            "name": p.name,
            "mimeType": "application/pdf",
            "buffer": p.read_bytes(),
        })

    async def _page_has_cover_letter_file_input(self) -> bool:
        """True if a visible file input is clearly for a cover letter."""
        return await self._page.evaluate("""() => {
            const ins = document.querySelectorAll("input[type='file']");
            for (const el of ins) {
                const parts = [
                    (el.labels && el.labels[0] && el.labels[0].textContent) || "",
                    el.getAttribute("aria-label") || "",
                    el.id || "",
                    el.name || "",
                ].join(" ").toLowerCase();
                if (parts.includes("cover") || (parts.includes("letter") && !parts.includes("newsletter")))
                    return true;
            }
            return false;
        }""")

    async def _enable_optional_cover_letter_checkbox(self) -> None:
        """Tick optional 'include cover letter' style checkboxes when present."""
        positive = (
            "include a cover letter",
            "attach a cover letter",
            "add a cover letter",
            "upload a cover letter",
            "include cover letter",
        )
        try:
            for cb in await self._page.get_by_role("checkbox").all():
                label = (await self._get_accessible_name(cb)).strip().lower()
                if not label:
                    continue
                if not any(p in label for p in positive):
                    continue
                if await cb.is_checked():
                    continue
                logger.info(
                    "native_form_filler: enabling optional cover-letter checkbox: %s",
                    label[:100],
                )
                await cb.check()
                return
        except Exception as exc:
            logger.debug("native_form_filler: optional CL checkbox pass failed: %s", exc)

    async def _resolve_lazy_cover_letter_path(
        self,
        cl_path: str | None,
        custom_answers: dict | None,
    ) -> str | None:
        """Generate cover letter PDF only when the page exposes a CL file slot."""
        if cl_path:
            return cl_path
        gen = (custom_answers or {}).get("_cl_generator")
        if not callable(gen):
            return None
        if not await self._page_has_cover_letter_file_input():
            return None
        try:
            p = gen()
            return str(p) if p else None
        except Exception as exc:
            logger.warning("native_form_filler: lazy cover letter generation failed: %s", exc)
            return None

    async def _upload_files(
        self,
        cv_path: str | None,
        cl_path: str | None,
        custom_answers: dict | None = None,
    ) -> None:
        """Upload CV and cover letter to file inputs (deterministic, no LLM).

        Matches by label keyword, falling back to input id/name attributes.
        Skips autofill/drag-and-drop inputs. Uploads CV at most once.
        Cover letter PDF may be created lazily via ``custom_answers['_cl_generator']``.
        """
        await self._enable_optional_cover_letter_checkbox()
        cl_path = await self._resolve_lazy_cover_letter_path(cl_path, custom_answers)

        file_meta = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll("input[type='file']")).map((el, idx) => ({
                idx,
                id: (el.id || '').toLowerCase(),
                name: (el.name || '').toLowerCase(),
                label: (el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || '').toLowerCase(),
            }));
        }""")
        cv_uploaded = False
        cl_uploaded = False

        for meta in file_meta:
            identifiers = f"{meta['label']} {meta['id']} {meta['name']}"

            if "autofill" in meta["label"] or "drag and drop" in meta["label"]:
                continue

            if meta["id"]:
                fi = self._page.locator(f'input[type="file"][id="{meta["id"]}"]').first
            elif meta["name"]:
                fi = self._page.locator(f'input[type="file"][name="{meta["name"]}"]').first
            else:
                fi = self._page.locator("input[type='file']").nth(meta["idx"])

            if any(kw in identifiers for kw in ("cover", "cl", "letter")) and cl_path and not cl_uploaded:
                await self._upload_pdf(fi, str(cl_path))
                cl_uploaded = True
            elif cv_path and not cv_uploaded:
                await self._upload_pdf(fi, str(cv_path))
                cv_uploaded = True

    async def _check_consent(self) -> None:
        """Auto-check unchecked *required* consent checkboxes.

        Policy lives in ``jobpulse.form_engine.consent_policy``:
        - Marketing / newsletter / third-party-sharing / future-role opt-ins
          are left unchecked (GDPR exposure we don't get to opt out of).
        - Only required application consents (terms of service, privacy
          policy, data processing for this application) are auto-ticked.
        - Ambiguous labels default to unchecked so the user can handle them
          manually and the correction-capture flow can learn from it.
        """
        from jobpulse.form_engine.consent_policy import is_required_consent

        checkboxes = await self._page.get_by_role("checkbox").all()

        for cb in checkboxes:
            label = await self._get_accessible_name(cb)
            if not is_required_consent(label):
                if label.strip():
                    logger.debug("consent: skipping non-required checkbox: %r", label)
                continue
            if not await cb.is_checked():
                logger.info("consent: auto-ticking required consent: %r", label)
                await cb.check()

    # ── Modal CV Upload (Reed, etc.) ──

    async def _handle_modal_cv_upload(self, cv_path: str | None) -> bool:
        """Detect and handle modal-based CV upload (Reed Easy Apply pattern).

        Reed shows a modal with a pre-filled CV from the user profile.
        If the expected tailored CV filename doesn't match, clicks Update
        and uploads via the file chooser dialog.

        Returns True if a modal was handled, False if no modal detected.
        """
        if not cv_path:
            return False

        modal = self._page.locator('[data-qa="apply-job-modal"]')
        if not await modal.count():
            return False

        modal_text = await modal.text_content() or ""
        expected_filename = os.path.basename(cv_path)

        if expected_filename in modal_text:
            logger.info("Modal CV already matches: %s", expected_filename)
            return True

        logger.info("Modal CV mismatch — uploading tailored CV: %s", expected_filename)

        update_btn = self._page.locator('[data-qa="UpdateCvBtn"]')
        if not await update_btn.count():
            return False

        await update_btn.click()
        await asyncio.sleep(2)

        choose_btn = self._page.locator('text=Choose your CV file')
        if await choose_btn.is_visible(timeout=5000):
            async with self._page.expect_file_chooser(timeout=10000) as fc_info:
                await choose_btn.click()
            file_chooser = await fc_info.value
            from pathlib import Path as _Path
            _p = _Path(cv_path)
            await file_chooser.set_files({
                "name": _p.name,
                "mimeType": "application/pdf",
                "buffer": _p.read_bytes(),
            })
            logger.info("Uploaded tailored CV via modal file chooser")
            await asyncio.sleep(3)
            return True

        file_inputs = await self._page.locator("input[type='file']").all()
        if file_inputs:
            await self._upload_pdf(file_inputs[0], str(cv_path))
            logger.info("Uploaded tailored CV via hidden file input")
            await asyncio.sleep(3)
            return True

        logger.warning("Could not find file upload mechanism in CV modal")
        return False

    # ── Page Detection ──

    async def _is_confirmation_page(self) -> bool:
        """Check if current page is a confirmation/thank-you page."""
        body = await self._page.locator("body").text_content()
        body_lower = (body or "").lower()[:2000]
        return any(phrase in body_lower for phrase in (
            "thank you for applying",
            "application has been received",
            "application submitted",
            "successfully submitted",
        ))

    async def _is_submit_page(self) -> bool:
        """Check if current page has a visible submit button (final page)."""
        for name in ["Submit Application", "Submit", "Apply"]:
            btn = self._page.get_by_role("button", name=name, exact=False)
            if await btn.count() and await btn.first.is_visible():
                return True
        return False

    # ── Navigation ──

    async def _click_navigation(self, dry_run: bool) -> str:
        """Find and click the next/submit button.

        Returns:
            'submitted' — clicked a submit button
            'next' — clicked a continue/next button
            'dry_run_stop' — submit found but dry_run=True
            '' — no navigation button found
        """
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
                    # Modal-based forms (LinkedIn Easy Apply) don't trigger
                    # full page navigation — use a short sleep instead of
                    # networkidle which would timeout on modal overlays.
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=5000,
                        )
                    except Exception:
                        await asyncio.sleep(2)
                    return "submitted" if action == "submit" else "next"

        # Fallback: links with submit-like text
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
        """Read current field values from the form — call after user corrections."""
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
        """Fill an application form using native Playwright locators + LLM.

        Per-page loop:
        1. Scan fields via role-based locators
        2. Detect confirmation page -> done
        3. LLM Call 1: map profile -> field values
        4. LLM Call 2: screening questions (optional, for unresolved fields)
        5. Fill each field by label (DOM order)
        6. Upload files (deterministic)
        7. Auto-check consent boxes
        8. Anti-detection timing
        9. Pre-submit review on final page (LLM Call 3)
        10. Click next/submit
        """
        # 0. Build correction warning from form hints (once per fill)
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

        # Load ProfileStore for dynamic data
        try:
            from shared.profile_store import get_profile_store
            self._profile_store = get_profile_store()
        except Exception:
            self._profile_store = None

        # 0b. Handle modal-based CV upload (Reed Easy Apply pattern)
        await self._handle_modal_cv_upload(cv_path)

        seen_field_types: list[str] = []
        seen_screening: list[str] = []
        all_agent_mappings: dict[str, str] = {}
        total_fields_attempted = 0
        total_fields_filled = 0
        total_fill_failures: list[str] = []
        t0 = time.monotonic()

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
            return base

        for page_num in range(1, MAX_FORM_PAGES + 1):
            # 1. Scan fields
            fields = await self._scan_fields()

            # Track field types for form experience learning
            for f in fields:
                ft = f"{f['type']}:{f['label'].lower().replace(' ', '_')[:40]}"
                seen_field_types.append(ft)
            fields_by_label = {f["label"]: f for f in fields}

            # 2. Confirmation page?
            if await self._is_confirmation_page():
                return _result({"success": True, "pages_filled": page_num})

            # 3. LLM Call 1: map fields
            mapping = await self._map_fields(
                fields, profile, custom_answers, platform,
            )

            # 3b. Vision fallback for fields with empty labels (shadow DOM)
            vision_unlabeled = await self._vision_map_unlabeled_fields(
                fields, profile, custom_answers, platform,
            )
            if vision_unlabeled:
                mapping.update(vision_unlabeled)
                for lbl in vision_unlabeled:
                    if lbl not in fields_by_label:
                        fields_by_label[lbl] = {"label": lbl, "type": "text"}

            # 4. Screening: cache/pattern first, LLM only for remainder
            unresolved = [
                f for f in fields
                if f["label"] not in mapping and f["type"] != "file"
            ]
            if unresolved:
                from jobpulse.screening_answers import try_instant_answer
                _job_ctx_raw = custom_answers.get("_job_context")
                _job_ctx = _job_ctx_raw if isinstance(_job_ctx_raw, dict) else None
                still_unresolved = []
                for f in unresolved:
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
                    else:
                        still_unresolved.append(f)
                if still_unresolved:
                    screening = self._clean_mapping(await self._screen_questions(
                        still_unresolved, custom_answers.get("_job_context"),
                    ))
                    mapping.update(screening)
                    for q, a in screening.items():
                        seen_screening.append(f"{q}:{a}")

            # Track agent's original mapping for correction capture
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
                    logger.warning("Field fill failed for '%s': %s", label, fill_err)
                    pending_retries.append({
                        "field": fields_by_label.get(label, {"label": label, "type": "text"}),
                        "attempted_value": value_text,
                        "result": {"success": False, "error": str(fill_err)},
                    })

            # 6. File uploads
            await self._upload_files(cv_path, cl_path, custom_answers)

            # 7. Consent boxes
            await self._check_consent()

            # 7b. Second-chance LLM recovery for fields whose values did not
            # verify in the DOM. This lets the agent learn alternate phrasing
            # or exact widget option text after a deterministic attempt fails.
            if pending_retries:
                retry_candidates = [
                    item for item in pending_retries
                    if item["field"].get("type") != "checkbox"
                ]
                recovered = await self._recover_failed_fields_with_llm(
                    retry_candidates, profile, custom_answers, platform,
                )
                if recovered:
                    self._learn_field_mapping(recovered, profile)

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
                            if self._is_screening_like_field(item["field"]):
                                seen_screening.append(f"{label}:{retry_value}")
                            continue
                    still_failing.append(item)

                # 7c. Vision recovery: screenshot-based fallback for fields
                # that text-only LLM recovery could not fix.
                if still_failing:
                    vision_recovered = await self._recover_failed_fields_with_vision(
                        still_failing, profile, custom_answers, platform,
                    )
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
                else:
                    final_failed_labels = []

                fill_failures.extend(final_failed_labels)
                total_fill_failures.extend(final_failed_labels)

            # 8. Anti-detection timing
            min_time = _PLATFORM_MIN_PAGE_TIME.get(platform, 5.0)
            await asyncio.sleep(min_time * random.uniform(0.8, 1.2))

            # 9. Pre-submit review on final page
            if await self._is_submit_page():
                if dry_run:
                    return _result({
                        "success": True, "dry_run": True,
                        "pages_filled": page_num,
                    })
                review = await self._review_form()
                if not review.get("pass"):
                    logger.warning(
                        "Pre-submit review failed: %s", review.get("issues"),
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
