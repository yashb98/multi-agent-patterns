"""SmartRecruiters ATS adapter.

SmartRecruiters uses Web Components with Shadow DOM (spl-* elements).
Standard querySelectorAll('input') returns nothing — must use Playwright's
get_by_label() / get_by_role() which pierce shadow DOM automatically.

Key quirks:
- spl-autocomplete for city, disability, gender dropdowns (ArrowDown + Enter)
- spl-button for Add/Save/Next/Submit (click via JS on the web component)
- Experience + Education auto-parsed when CV uploaded via the top file input
- Mandatory Resume* file input is separate from the auto-parse upload
- Screening questions on page 2 (radio buttons + autocomplete + checkbox)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)

# Known screening answers for esure-style SmartRecruiters forms
_SCREENING_DEFAULTS: dict[str, str] = {
    "financial_background": "No",
    "criminal_convictions": "No",
    "convicted_guilty": "No",
    "drivers_license": "No",
    "right_to_work": "Yes",
    "authorized_work": "Yes",
    "disability": "No",
    "gender": "Male",
}


class SmartRecruitersAdapter(BaseATSAdapter):
    name: str = "smartrecruiters"

    def detect(self, url: str) -> bool:
        return "smartrecruiters.com" in url

    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict | None = None,
        dry_run: bool = False,
        engine: str = "playwright_cdp",
    ) -> dict:
        from playwright.sync_api import sync_playwright

        field_types: list[str] = []
        screening_qs: list[str] = []
        start_time = time.monotonic()

        pw = None
        try:
            from jobpulse.config import PLAYWRIGHT_CDP_URL
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(PLAYWRIGHT_CDP_URL)
            ctx = browser.contexts[0]

            page = None
            for p in ctx.pages:
                if "smartrecruiters" in p.url:
                    page = p
                    break

            if not page:
                page = ctx.new_page()
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

            # --- PAGE 1: Personal Info + Experience + Education ---
            page1_result = self._fill_page1(page, profile, cv_path, cover_letter_path, overrides)
            field_types.extend(page1_result.get("field_types", []))

            # Click Next to go to screening questions
            self._click_spl_button(page, "Next")
            page.wait_for_timeout(3000)

            # --- PAGE 2: Screening Questions ---
            page2_result = self._fill_screening(page, custom_answers, overrides)
            field_types.extend(page2_result.get("field_types", []))
            screening_qs.extend(page2_result.get("screening_questions", []))

            # Screenshot before submit
            screenshot_path = cv_path.parent / "smartrecruiters_screenshot.png"
            page.screenshot(path=str(screenshot_path), full_page=True)

            if not dry_run:
                self._click_spl_button(page, "Submit")
                page.wait_for_timeout(3000)
                logger.info("SmartRecruiters: submitted application")

            elapsed = time.monotonic() - start_time

            return {
                "success": True,
                "screenshot": screenshot_path,
                "error": None,
                "pages_filled": 2,
                "field_types": field_types,
                "screening_questions": screening_qs,
                "time_seconds": elapsed,
            }

        except Exception as exc:
            logger.error("SmartRecruiters adapter error: %s", exc)
            elapsed = time.monotonic() - start_time
            return {
                "success": False,
                "screenshot": None,
                "error": str(exc),
                "pages_filled": 0,
                "field_types": field_types,
                "screening_questions": screening_qs,
                "time_seconds": elapsed,
            }
        finally:
            if pw:
                pw.stop()

    def _fill_page1(
        self,
        page: Any,
        profile: dict,
        cv_path: Path,
        cover_letter_path: Path | None,
        overrides: dict | None,
    ) -> dict:
        """Fill personal info, upload CV, verify experience/education on page 1."""
        field_types: list[str] = []

        # Upload CV via the top "Choose a file" input for auto-parsing
        file_inputs = page.locator("input[type='file']")
        file_count = file_inputs.count()
        if file_count > 0 and cv_path.exists():
            file_inputs.first.set_input_files({
                "name": cv_path.name,
                "mimeType": "application/pdf",
                "buffer": cv_path.read_bytes(),
            })
            logger.info("SmartRecruiters: uploaded CV for auto-parse")
            page.wait_for_timeout(3000)
            field_types.append("file:cv_autoparse")

        # Fill personal info fields using get_by_role which pierces shadow DOM
        textboxes = page.get_by_role("textbox")
        tb_count = textboxes.count()
        logger.info("SmartRecruiters: found %d textboxes", tb_count)

        # Map fields by label (shadow DOM piercing)
        label_map = {
            "First name": profile.get("first_name", ""),
            "Last name": profile.get("last_name", ""),
            "Email": profile.get("email", ""),
            "Confirm your email": profile.get("email", ""),
            "Phone number": profile.get("phone", ""),
            "LinkedIn": profile.get("linkedin", ""),
            "Website": profile.get("portfolio", ""),
        }

        for label, value in label_map.items():
            if not value:
                continue
            try:
                field = page.get_by_label(label, exact=False).first
                current = field.input_value(timeout=2000)
                if current and current.strip():
                    logger.debug("SmartRecruiters: %s already filled: %s", label, current[:30])
                    continue
                field.fill(value)
                field_types.append(f"text:{label.lower().replace(' ', '_')}")
                logger.info("SmartRecruiters: filled %s", label)
            except Exception as exc:
                logger.debug("SmartRecruiters: could not fill %s: %s", label, exc)

        # City autocomplete
        try:
            city_field = page.get_by_label("City", exact=False).first
            current_city = city_field.input_value(timeout=2000)
            if not current_city or not current_city.strip():
                city_field.fill("Dundee")
                page.wait_for_timeout(1000)
                city_field.press("ArrowDown")
                page.wait_for_timeout(300)
                city_field.press("Enter")
                field_types.append("autocomplete:city")
                logger.info("SmartRecruiters: filled city")
        except Exception as exc:
            logger.debug("SmartRecruiters: city fill failed: %s", exc)

        # Upload to mandatory Resume* field (separate from auto-parse)
        try:
            file_inputs_after = page.locator("input[type='file']")
            count_after = file_inputs_after.count()
            pdf_payload = {
                "name": cv_path.name,
                "mimeType": "application/pdf",
                "buffer": cv_path.read_bytes(),
            }
            if count_after > 1 and cv_path.exists():
                file_inputs_after.nth(count_after - 1).set_input_files(pdf_payload)
                field_types.append("file:resume_mandatory")
                logger.info("SmartRecruiters: uploaded to mandatory Resume field")
            elif count_after == 1:
                current_resume = page.get_by_text("Yash_Bishnoi")
                if current_resume.count() == 0:
                    file_inputs_after.first.set_input_files(pdf_payload)
                    field_types.append("file:resume_mandatory")
        except Exception as exc:
            logger.debug("SmartRecruiters: resume upload failed: %s", exc)

        return {"field_types": field_types}

    def _fill_screening(
        self,
        page: Any,
        custom_answers: dict,
        overrides: dict | None,
    ) -> dict:
        """Fill screening questions on page 2 (radios + autocomplete + checkbox)."""
        field_types: list[str] = []
        screening_qs: list[str] = []

        # Radio buttons come in Yes/No pairs
        radios = page.get_by_role("radio")
        radio_count = radios.count()
        logger.info("SmartRecruiters: found %d radio buttons", radio_count)

        # Read question texts via deep shadow DOM traversal
        questions = self._extract_question_texts(page)
        logger.info("SmartRecruiters: extracted %d questions", len(questions))

        # Answer radios in pairs (Yes=even index, No=odd index)
        for i in range(0, radio_count, 2):
            q_idx = i // 2
            q_text = questions[q_idx] if q_idx < len(questions) else f"question_{q_idx}"

            answer = self._resolve_screening_answer(q_text, custom_answers)
            if answer.lower() == "yes":
                radios.nth(i).click()
            else:
                radios.nth(i + 1).click()

            screening_qs.append(f"{q_text}:{answer}")
            field_types.append(f"radio:{q_text[:30]}")
            page.wait_for_timeout(200)
            logger.info("SmartRecruiters: Q%d (%s): %s", q_idx + 1, q_text[:40], answer)

        # Autocomplete fields (disability, gender)
        combos = page.get_by_role("combobox")
        combo_count = combos.count()

        autocomplete_answers = [
            ("disability", custom_answers.get("disability", _SCREENING_DEFAULTS["disability"])),
            ("gender", custom_answers.get("gender", _SCREENING_DEFAULTS["gender"])),
        ]

        for idx, (name, value) in enumerate(autocomplete_answers):
            if idx >= combo_count:
                break
            try:
                combos.nth(idx).click()
                page.wait_for_timeout(300)
                combos.nth(idx).fill(value)
                page.wait_for_timeout(500)

                option = page.get_by_role("option", name=value)
                if option.count() > 0:
                    option.first.click()
                else:
                    combos.nth(idx).press("ArrowDown")
                    page.wait_for_timeout(100)
                    combos.nth(idx).press("Enter")

                screening_qs.append(f"{name}:{value}")
                field_types.append(f"spl-autocomplete:{name}")
                logger.info("SmartRecruiters: set %s = %s", name, value)
                page.wait_for_timeout(300)
            except Exception as exc:
                logger.debug("SmartRecruiters: %s autocomplete failed: %s", name, exc)

        # Privacy checkbox
        try:
            checkbox = page.get_by_role("checkbox")
            if checkbox.count() > 0 and not checkbox.first.is_checked():
                checkbox.first.click()
                field_types.append("checkbox:privacy_notice")
                logger.info("SmartRecruiters: checked privacy notice")
        except Exception as exc:
            logger.debug("SmartRecruiters: checkbox failed: %s", exc)

        return {"field_types": field_types, "screening_questions": screening_qs}

    def _extract_question_texts(self, page: Any) -> list[str]:
        """Extract screening question texts via deep shadow DOM traversal."""
        try:
            result = page.evaluate("""() => {
                function getDeepText(node) {
                    let text = '';
                    if (node.shadowRoot) text += getDeepText(node.shadowRoot);
                    for (const child of node.childNodes) {
                        if (child.nodeType === 3) text += child.textContent;
                        else if (child.nodeType === 1) text += getDeepText(child);
                    }
                    return text;
                }
                const fullText = getDeepText(document.body);
                const lines = fullText.split('\\n')
                    .map(l => l.trim())
                    .filter(l => l.length > 20 && l.includes('?'));
                return lines;
            }""")
            return result if result else []
        except Exception:
            return []

    def _resolve_screening_answer(self, question: str, custom_answers: dict) -> str:
        """Resolve screening answer from custom_answers or defaults."""
        q_lower = question.lower()

        if custom_answers:
            for key, val in custom_answers.items():
                if key.startswith("_"):
                    continue
                if key.lower() in q_lower or q_lower in key.lower():
                    return str(val)

        if "right to work" in q_lower or "authorized" in q_lower:
            return "Yes"
        if "financial" in q_lower or "credit" in q_lower or "disclose" in q_lower:
            return "No"
        if "criminal" in q_lower or "convicted" in q_lower or "conviction" in q_lower:
            return "No"
        if "driver" in q_lower or "license" in q_lower or "licence" in q_lower:
            return "No"

        return "No"

    @staticmethod
    def _click_spl_button(page: Any, text: str) -> None:
        """Click an spl-button web component by its text content."""
        page.evaluate("""(buttonText) => {
            const btns = document.querySelectorAll('spl-button');
            for (const btn of btns) {
                if (btn.textContent.trim() === buttonText) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""", text)
        logger.info("SmartRecruiters: clicked '%s' button", text)
