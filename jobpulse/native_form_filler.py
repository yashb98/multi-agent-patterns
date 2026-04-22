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
import time
from typing import TYPE_CHECKING, Any

from shared.agents import get_openai_client, get_model_name
from shared.logging_config import get_logger

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
        """Scan visible form fields using Playwright role-based locators.

        Returns a list of dicts with: label, type, locator, and
        type-specific keys (value, options, checked, required).
        """
        page = self._page
        fields: list[dict] = []

        # Text inputs (textbox role covers input[type=text/email/tel/number/etc])
        for loc in await page.get_by_role("textbox").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "text", "locator": loc,
                "value": await loc.input_value(),
                "required": await loc.get_attribute("required") is not None,
            })

        # Dropdowns — native <select> (has <option> children) and React Select
        # comboboxes (role=combobox on <input>, options rendered dynamically)
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

        # Radio groups
        for loc in await page.get_by_role("radiogroup").all():
            label = await self._get_accessible_name(loc)
            radios = await loc.get_by_role("radio").all()
            option_labels = [await self._get_accessible_name(r) for r in radios]
            fields.append({
                "label": label, "type": "radio", "options": option_labels,
                "locator": loc,
            })

        # Checkboxes
        for loc in await page.get_by_role("checkbox").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "checkbox", "locator": loc,
                "checked": await loc.is_checked(),
            })

        # Textareas
        for loc in await page.locator("textarea:visible").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "textarea", "locator": loc,
                "value": await loc.input_value(),
            })

        # File inputs
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

    # ── Fill By Label ──

    async def _fill_by_label(self, label: str, value: str) -> dict:
        """Fill a single form field using Playwright's label-based locator.

        Tries get_by_label first, falls back to get_by_placeholder.
        Handles text, select, checkbox, and radio input types.
        Returns {"success": bool, "value_set": str, "value_verified": bool}.
        """
        page = self._page
        await asyncio.sleep(_get_field_gap(label))

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

        if tag == "select":
            selected = False
            options = await el.locator("option").all_text_contents()
            options_stripped = [o.strip() for o in options]
            # Try exact label match
            try:
                await el.select_option(label=value, timeout=5000)
                selected = True
            except Exception:
                pass
            # Try fuzzy substring match
            if not selected:
                matched_idx = next(
                    (i for i, o in enumerate(options_stripped) if value.lower() in o.lower()),
                    next((i for i, o in enumerate(options_stripped) if o.lower() in value.lower()), None),
                )
                if matched_idx is not None:
                    try:
                        await el.select_option(index=matched_idx, timeout=5000)
                        selected = True
                        # Auto-save gotcha: exact match failed, fuzzy worked
                        self._save_gotcha(
                            label, "select_exact_failed",
                            f"Use option index {matched_idx} ('{options_stripped[matched_idx]}') for value '{value}'",
                        )
                    except Exception:
                        pass
            # Try by value attribute
            if not selected:
                try:
                    await el.select_option(value=value, timeout=5000)
                    selected = True
                except Exception:
                    pass
            if not selected:
                logger.warning("Could not select '%s' for '%s' — options: %s", value, label, options_stripped)
        elif input_type == "checkbox":
            if value.lower() in ("true", "yes", "1"):
                await el.check()
            else:
                await el.uncheck()
        elif input_type == "radio":
            await page.get_by_label(value).check()
        elif await el.get_attribute("role") == "combobox":
            await el.fill("")
            await el.fill(value)
            await asyncio.sleep(0.8)
            option = page.get_by_role("option", name=value, exact=False)
            if await option.count():
                await option.first.click()
            else:
                await el.press("ArrowDown")
                await asyncio.sleep(0.3)
                await el.press("Enter")
        else:
            await el.fill(value)

        # Post-fill verification
        if tag == "select":
            actual = await el.evaluate(
                "el => el.options[el.selectedIndex]?.text?.trim() || ''"
            )
        elif input_type in ("checkbox", "radio"):
            actual = str(await el.is_checked())
        else:
            actual = await el.input_value()

        verified = value[:10].lower() in actual.lower() if actual else False
        return {"success": True, "value_set": value, "value_verified": verified}

    # ── Field Mapping (deterministic first, LLM fallback) ──

    @staticmethod
    def _learn_field_mapping(mapping: dict[str, str], profile: dict) -> None:
        """Learn new label→profile_key associations from LLM results.

        Persists new mappings to SQLite so future sessions skip the LLM.
        """
        from jobpulse.applicator import PROFILE
        profile_flat = {**profile, **PROFILE}

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
            profile_flat = {**profile, **PROFILE}
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

    async def _map_fields(
        self, fields: list[dict], profile: dict,
        custom_answers: dict, platform: str,
    ) -> dict:
        """Map profile data to form field labels.

        Tries deterministic cache first; falls back to LLM.
        Returns {"label": "value"} for each field the LLM can fill.
        """
        # Tier 1: deterministic mapping from known label→profile_key
        cached = self._try_cached_mapping(fields, profile, custom_answers)
        if cached is not None:
            return cached

        # Tier 2: LLM mapping (fallback)
        field_descriptions = []
        for f in fields:
            if f["type"] == "file":
                continue
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
            f"Profile: {json.dumps(profile)}\n"
            f"Platform: {platform}\n"
            f"Known answers: {json.dumps({k: v for k, v in custom_answers.items() if not k.startswith('_')})}"
            f"{self._correction_warning}"
        )

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
            mapping = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("LLM returned invalid JSON for field mapping: %s", e)
            return {}
        except Exception as e:
            logger.error("LLM field mapping call failed: %s", e)
            return {}

        # Learn: save label→profile_key for deterministic reuse
        self._learn_field_mapping(mapping, profile)

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

        from jobpulse.applicator import PROFILE, WORK_AUTH
        applicant_bg = (
            f"Name: {PROFILE['first_name']} {PROFILE['last_name']}. "
            f"Education: {PROFILE['education']}. Location: {PROFILE['location']}. "
            f"Visa: {WORK_AUTH['visa_status']}. Notice: {WORK_AUTH['notice_period']}. "
            f"Willing to relocate: Yes, anywhere in the UK. "
            f"Commuting: Yes, willing to commute to any UK office. "
            f"Right to work UK: Yes."
        )
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

    async def _upload_files(
        self, cv_path: str | None, cl_path: str | None,
    ) -> None:
        """Upload CV and cover letter to file inputs (deterministic, no LLM).

        Matches by label keyword, falling back to input id/name attributes.
        Skips autofill/drag-and-drop inputs. Uploads CV at most once.
        """
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
            }
            return base

        for page_num in range(1, MAX_FORM_PAGES + 1):
            # 1. Scan fields
            fields = await self._scan_fields()

            # Track field types for form experience learning
            for f in fields:
                ft = f"{f['type']}:{f['label'].lower().replace(' ', '_')[:40]}"
                seen_field_types.append(ft)

            # 2. Confirmation page?
            if await self._is_confirmation_page():
                return _result({"success": True, "pages_filled": page_num})

            # 3. LLM Call 1: map fields
            mapping = await self._map_fields(
                fields, profile, custom_answers, platform,
            )

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
                        mapping[f["label"]] = cached
                        seen_screening.append(f"{f['label']}:{cached}")
                    else:
                        still_unresolved.append(f)
                if still_unresolved:
                    screening = await self._screen_questions(
                        still_unresolved, custom_answers.get("_job_context"),
                    )
                    mapping.update(screening)
                    for q, a in screening.items():
                        seen_screening.append(f"{q}:{a}")

            # Track agent's original mapping for correction capture
            all_agent_mappings.update({k: str(v) for k, v in mapping.items()})

            # 5. Fill each field by label
            fill_failures = []
            for label, value in mapping.items():
                total_fields_attempted += 1
                try:
                    result = await self._fill_by_label(label, value)
                    if result.get("success"):
                        total_fields_filled += 1
                    else:
                        fill_failures.append(label)
                        total_fill_failures.append(label)
                except Exception as fill_err:
                    logger.warning("Field fill failed for '%s': %s", label, fill_err)
                    fill_failures.append(label)
                    total_fill_failures.append(label)

            # 6. File uploads
            await self._upload_files(cv_path, cl_path)

            # 7. Consent boxes
            await self._check_consent()

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
