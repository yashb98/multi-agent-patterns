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

from shared.agents import get_openai_client
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

        client = get_openai_client()
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

        client = get_openai_client()
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

        client = get_openai_client()
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
            ("next", ["Save & Continue", "Continue", "Next", "Proceed"]),
        ]

        for action, names in button_names:
            for name in names:
                btn = page.get_by_role("button", name=name, exact=False)
                if await btn.count() and await btn.first.is_visible():
                    if action == "submit" and dry_run:
                        return "dry_run_stop"
                    await self._move_mouse_to(btn.first)
                    await btn.first.click()
                    await page.wait_for_load_state(
                        "networkidle", timeout=10000,
                    )
                    return "submitted" if action == "submit" else "next"

        # Fallback: links with submit-like text
        for name in ["Submit", "Apply Now", "Continue"]:
            link = page.get_by_role("link", name=name, exact=False)
            if await link.count() and await link.first.is_visible():
                await link.first.click()
                await page.wait_for_load_state(
                    "networkidle", timeout=10000,
                )
                return "next"

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
        for page_num in range(1, MAX_FORM_PAGES + 1):
            # 1. Scan fields
            fields = await self._scan_fields()

            # 2. Confirmation page?
            if await self._is_confirmation_page():
                return {"success": True, "pages_filled": page_num}

            # 3. LLM Call 1: map fields
            mapping = await self._map_fields(
                fields, profile, custom_answers, platform,
            )

            # 4. LLM Call 2: screening for unresolved non-file fields
            unresolved = [
                f for f in fields
                if f["label"] not in mapping and f["type"] != "file"
            ]
            if unresolved:
                screening = await self._screen_questions(
                    unresolved, custom_answers.get("_job_context"),
                )
                mapping.update(screening)

            # 5. Fill each field by label
            for label, value in mapping.items():
                await self._fill_by_label(label, value)

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
                    return {
                        "success": True, "dry_run": True,
                        "pages_filled": page_num,
                    }
                review = await self._review_form()
                if not review.get("pass"):
                    logger.warning(
                        "Pre-submit review failed: %s", review.get("issues"),
                    )

            # 10. Click next/submit
            clicked = await self._click_navigation(dry_run)
            if clicked == "submitted":
                return {"success": True, "pages_filled": page_num}
            if clicked == "dry_run_stop":
                return {
                    "success": True, "dry_run": True,
                    "pages_filled": page_num,
                }
            if not clicked:
                return {
                    "success": False,
                    "error": f"No navigation button on page {page_num}",
                }

        return {
            "success": False,
            "error": f"Exhausted {MAX_FORM_PAGES} form pages",
        }
