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
from typing import TYPE_CHECKING, Any

from openai import OpenAI

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

# Per-platform minimum page times (seconds) — kept in sync with orchestrator
_PLATFORM_MIN_PAGE_TIME: dict[str, float] = {
    "workday": 45.0,
    "linkedin": 8.0,
    "greenhouse": 5.0,
    "lever": 5.0,
    "indeed": 10.0,
    "generic": 5.0,
}

MAX_FORM_PAGES = 20


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

    # ── Label Extraction ──

    async def _get_accessible_name(self, locator: Any) -> str:
        """Extract the label a screen reader would announce for this element."""
        return await locator.evaluate(
            "el => el.labels?.[0]?.textContent?.trim() || "
            "el.getAttribute('aria-label') || "
            "el.placeholder || ''"
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

        # Dropdowns (combobox role = native <select>)
        for loc in await page.get_by_role("combobox").all():
            label = await self._get_accessible_name(loc)
            options = await loc.locator("option").all_text_contents()
            fields.append({
                "label": label, "type": "select", "locator": loc,
                "options": options, "value": await loc.input_value(),
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

        el = locator.first
        await self._smart_scroll(el)
        await self._move_mouse_to(el)

        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        input_type = await el.get_attribute("type") or ""

        if tag == "select":
            await el.select_option(label=value)
        elif input_type == "checkbox":
            if value.lower() in ("true", "yes", "1"):
                await el.check()
            else:
                await el.uncheck()
        elif input_type == "radio":
            await page.get_by_label(value).check()
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

    # ── LLM Calls ──

    async def _map_fields(
        self, fields: list[dict], profile: dict,
        custom_answers: dict, platform: str,
    ) -> dict:
        """LLM Call 1: map profile data to form field labels.

        Returns {"label": "value"} for each field the LLM can fill.
        Skips file upload fields. Marks already-filled fields in the prompt.
        """
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
            f"Skip already-filled fields. Skip file upload fields.\n\n"
            f"Fields:\n{chr(10).join(field_descriptions)}\n\n"
            f"Profile: {json.dumps(profile)}\n"
            f"Platform: {platform}\n"
            f"Known answers: {json.dumps(custom_answers)}"
        )

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)

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

        prompt = (
            f"Answer these screening questions for a job application.\n"
            f"Context: {job_context or 'Not provided'}\n\n"
            f"{chr(10).join(questions)}\n\n"
            f'Return JSON {{"label": "answer"}}. Be truthful.'
        )

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)

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

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
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

    async def _upload_files(
        self, cv_path: str | None, cl_path: str | None,
    ) -> None:
        """Upload CV and cover letter to file inputs (deterministic, no LLM).

        Matches by label keyword. Skips autofill/drag-and-drop inputs.
        Uploads CV at most once (deduplication).
        """
        file_inputs = await self._page.locator("input[type='file']").all()
        cv_uploaded = False

        for fi in file_inputs:
            label = await self._get_accessible_name(fi)
            label_lower = label.lower()

            if "autofill" in label_lower or "drag and drop" in label_lower:
                continue

            if "cover" in label_lower and cl_path:
                await fi.set_input_files(str(cl_path))
            elif cv_path and not cv_uploaded:
                await fi.set_input_files(str(cv_path))
                cv_uploaded = True

    async def _check_consent(self) -> None:
        """Auto-check unchecked consent/terms/privacy checkboxes."""
        consent_keywords = [
            "agree", "consent", "terms", "privacy", "accept", "acknowledge",
        ]
        checkboxes = await self._page.get_by_role("checkbox").all()

        for cb in checkboxes:
            label = await self._get_accessible_name(cb)
            if any(kw in label.lower() for kw in consent_keywords):
                if not await cb.is_checked():
                    await cb.check()
