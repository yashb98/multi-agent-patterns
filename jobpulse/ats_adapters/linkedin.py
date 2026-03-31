"""LinkedIn Easy Apply adapter — full multi-step wizard with Ralph Loop support.

Flow: Click Easy Apply → Page 1 (Contact) → Page 2 (Resume/CL) →
      Page 3 (Work experience) → Page 4+ (Questions) → Review → Submit

Each step takes a screenshot for Ralph Loop diagnosis on failure.
"""

import os
import random
import time
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)

# Anti-detection user agents
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Auto-submit control — only submit if explicitly enabled
_AUTO_SUBMIT = os.environ.get("JOB_AUTOPILOT_AUTO_SUBMIT", "false").lower() == "true"


def _human_delay(min_s: float = 0.5, max_s: float = 2.0) -> None:
    """Random delay to mimic human interaction speed."""
    time.sleep(random.uniform(min_s, max_s))


def _human_type(page, selector: str, text: str) -> None:
    """Type text character by character with random delays."""
    el = page.query_selector(selector)
    if not el:
        return
    el.click()
    _human_delay(0.3, 0.8)
    el.fill("")  # clear first
    for char in text:
        page.keyboard.type(char, delay=random.randint(50, 150))
    _human_delay(0.5, 1.0)


def _screenshot(page, cv_path: Path, step: str) -> Path:
    """Take a step-specific screenshot."""
    screenshot_path = cv_path.parent / f"linkedin_{step}.png"
    try:
        page.screenshot(path=str(screenshot_path))
    except Exception:
        pass
    return screenshot_path


def _find_modal(page):
    """Find the Easy Apply modal dialog."""
    for sel in ["[role='dialog']", ".artdeco-modal", ".jobs-easy-apply-modal"]:
        modal = page.query_selector(sel)
        if modal:
            return modal
    return None


def _click_next_or_submit(page) -> str:
    """Click the Next/Review/Submit button inside the modal. Returns which button was found."""
    modal = _find_modal(page)
    if not modal:
        return "no_modal"

    # Look for buttons in priority order: Submit > Review > Next
    for label, action in [
        ("Submit application", "submit"),
        ("Submit", "submit"),
        ("Review", "review"),
        ("Next", "next"),
    ]:
        buttons = modal.query_selector_all("button")
        for btn in buttons:
            text = btn.text_content() or ""
            if label.lower() in text.strip().lower():
                try:
                    btn.scroll_into_view_if_needed()
                    _human_delay(0.3, 0.8)
                    btn.click()
                    _human_delay(1.0, 2.0)
                    return action
                except Exception as exc:
                    logger.warning("LinkedIn: failed to click '%s': %s", label, exc)

    return "no_button"


def _fill_location_typeahead(page, location: str) -> None:
    """Fill the location typeahead field in the Easy Apply modal."""
    # LinkedIn location uses a typeahead with aria attributes
    for sel in [
        "input[aria-label*='typeahead'][aria-label*='ocation']",
        "input[aria-label*='City']",
        "input[id*='location']",
        "input[placeholder*='City']",
    ]:
        el = page.query_selector(sel)
        if el:
            el.fill("")
            _human_delay(0.2, 0.5)
            el.type(location[:5], delay=random.randint(80, 150))
            _human_delay(1.0, 2.0)  # wait for suggestions
            # Click first suggestion
            for suggestion_sel in [
                "[role='option']",
                ".basic-typeahead__selectable",
                "li.search-typeahead-v2__hit",
            ]:
                suggestion = page.query_selector(suggestion_sel)
                if suggestion:
                    suggestion.click()
                    _human_delay(0.5, 1.0)
                    return
            # No suggestion — press Enter on what we typed
            page.keyboard.press("Enter")
            _human_delay(0.3, 0.5)
            return


def _fill_contact_page(page, profile: dict) -> None:
    """Fill Page 1: Contact info (phone, email, location)."""
    # Phone
    for sel in ["[name='phoneNumber']", "input[id*='phone']"]:
        el = page.query_selector(sel)
        if el:
            current = el.input_value() or ""
            if not current.strip():
                _human_type(page, sel, profile.get("phone", ""))
            break

    # Email — usually pre-filled, skip if populated
    for sel in ["[name='email']", "input[id*='email']"]:
        el = page.query_selector(sel)
        if el:
            current = el.input_value() or ""
            if not current.strip():
                _human_type(page, sel, profile.get("email", ""))
            break

    # Location typeahead — required, often empty
    _fill_location_typeahead(page, profile.get("location", "Dundee, UK"))


def _fill_resume_page(page, cv_path: Path, cover_letter_path: Path | None, cl_generator=None) -> None:
    """Fill Page 2: Resume upload + optional cover letter."""
    # Check if there's already an uploaded resume
    existing_resume = page.query_selector(
        ".jobs-document-upload-redesign-card__file-name, "
        "[data-test-document-name]"
    )
    if existing_resume:
        logger.info("LinkedIn: existing resume detected, skipping upload")
    else:
        # Upload resume
        file_inputs = page.query_selector_all("input[type='file']")
        for fi in file_inputs:
            # Find the one near "resume" text
            parent = fi.evaluate_handle("el => el.closest('.jobs-document-upload-redesign-card') || el.parentElement")
            parent_text = parent.text_content() if parent else ""
            if "resume" in (parent_text or "").lower() or not file_inputs.index(fi):
                if cv_path.exists():
                    fi.set_input_files(str(cv_path))
                    _human_delay(1.5, 3.0)  # wait for upload
                    logger.info("LinkedIn: uploaded resume %s", cv_path.name)
                break

    # Cover letter — check if upload field exists
    cl_section = page.query_selector(
        "label:has-text('cover letter'), "
        "[data-test-form-element]:has-text('cover letter')"
    )
    if cl_section:
        # Lazy CL generation
        if cover_letter_path is None and cl_generator is not None:
            try:
                cover_letter_path = cl_generator()
                logger.info("LinkedIn: lazy-generated cover letter")
            except Exception as exc:
                logger.warning("LinkedIn: CL generation failed: %s", exc)

        if cover_letter_path and cover_letter_path.exists():
            cl_inputs = page.query_selector_all("input[type='file']")
            for fi in cl_inputs:
                parent = fi.evaluate_handle("el => el.closest('.jobs-document-upload-redesign-card') || el.parentElement")
                parent_text = parent.text_content() if parent else ""
                if "cover" in (parent_text or "").lower():
                    fi.set_input_files(str(cover_letter_path))
                    _human_delay(1.5, 3.0)
                    logger.info("LinkedIn: uploaded cover letter")
                    break


def _fill_experience_page(page) -> None:
    """Fill Page 3: Work experience — usually pre-filled from LinkedIn profile."""
    # This page typically shows existing experience and asks to confirm.
    # Usually no action needed. Just verify it loaded.
    _human_delay(1.0, 2.0)


def _answer_questions(page, profile: dict, custom_answers: dict) -> None:
    """Fill Page 4+: Additional screening questions."""
    from jobpulse.screening_answers import get_answer

    modal = _find_modal(page)
    if not modal:
        return

    # Find all form groups in the modal
    form_groups = modal.query_selector_all(
        ".jobs-easy-apply-form-section__grouping, "
        "[data-test-form-element], "
        ".fb-dash-form-element, "
        "fieldset"
    )

    for group in form_groups:
        try:
            # Get the question label
            label_el = group.query_selector("label, legend, .fb-form-element-label, span.t-14")
            if not label_el:
                continue
            question = label_el.text_content().strip()
            if not question:
                continue

            # Check if already answered
            input_el = group.query_selector("input:not([type='hidden']), select, textarea")
            if not input_el:
                continue

            # Get current value
            tag = input_el.evaluate("el => el.tagName.toLowerCase()")

            if tag == "select":
                # Dropdown — try to pick the best answer
                answer = get_answer(question, {"title": "", "company": ""})
                if answer:
                    options = input_el.query_selector_all("option")
                    for opt in options:
                        opt_text = opt.text_content().strip()
                        if answer.lower() in opt_text.lower() or opt_text.lower() in answer.lower():
                            input_el.select_option(label=opt_text)
                            _human_delay(0.3, 0.8)
                            break
                    else:
                        # Default: select first non-empty, non-"Select an option" value
                        for opt in options:
                            val = opt.get_attribute("value") or ""
                            text = opt.text_content().strip()
                            if val and text and "select" not in text.lower():
                                input_el.select_option(value=val)
                                _human_delay(0.3, 0.8)
                                break

            elif tag == "input":
                input_type = (input_el.get_attribute("type") or "text").lower()
                current = input_el.input_value() or ""

                if input_type == "radio":
                    # Radio group — pick "Yes" if available, else first option
                    radios = group.query_selector_all("input[type='radio']")
                    answer = get_answer(question, {"title": "", "company": ""})
                    picked = False
                    for radio in radios:
                        radio_label = radio.evaluate("el => el.closest('label')?.textContent || el.nextSibling?.textContent || ''")
                        if answer and answer.lower() in (radio_label or "").lower():
                            radio.click()
                            picked = True
                            _human_delay(0.3, 0.5)
                            break
                    if not picked and radios:
                        # Default: click first "Yes" or first option
                        for radio in radios:
                            radio_label = radio.evaluate("el => el.closest('label')?.textContent || ''")
                            if "yes" in (radio_label or "").lower():
                                radio.click()
                                _human_delay(0.3, 0.5)
                                break
                        else:
                            radios[0].click()
                            _human_delay(0.3, 0.5)

                elif input_type in ("text", "tel", "email", "number", ""):
                    if not current.strip():
                        answer = get_answer(question, {"title": "", "company": ""})
                        if answer:
                            input_el.fill(answer)
                            _human_delay(0.3, 0.5)

            elif tag == "textarea":
                current = input_el.input_value() or ""
                if not current.strip():
                    answer = get_answer(question, {"title": "", "company": ""})
                    if answer:
                        input_el.fill(answer)
                        _human_delay(0.3, 0.5)

        except Exception as exc:
            logger.warning("LinkedIn: error answering question: %s", exc)
            continue


class LinkedInAdapter(BaseATSAdapter):
    name: str = "linkedin"

    def detect(self, url: str) -> bool:
        return "linkedin.com" in url

    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict | None = None,
    ) -> dict:
        try:
            from jobpulse.utils.safe_io import managed_persistent_browser
        except ImportError:
            logger.warning("Playwright not installed — LinkedIn adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("LinkedIn Easy Apply: %s", url)
        try:
            from jobpulse.config import DATA_DIR

            chrome_profile = str(DATA_DIR / "chrome_profile")
            chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

            with managed_persistent_browser(
                user_data_dir=chrome_profile,
                headless=False,
                executable_path=chrome_path,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
                user_agent=random.choice(_USER_AGENTS),
                viewport={"width": 1280, "height": 800},
            ) as (_browser, page):
                # --- Step 1: Navigate to job page ---
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                _human_delay(2, 4)
                _screenshot(page, cv_path, "01_job_page")

                # --- Step 2: Click Easy Apply button ---
                easy_apply_btn = None
                for sel in [
                    self.resolve_selector("button.jobs-apply-button", overrides),
                    self.resolve_selector("button[aria-label*='Easy Apply']", overrides),
                    "a:has-text('Easy Apply')",
                    "button:has-text('Easy Apply')",
                ]:
                    easy_apply_btn = page.query_selector(sel)
                    if easy_apply_btn:
                        break

                if not easy_apply_btn:
                    # Check for external apply
                    external = page.query_selector("a:has-text('Apply'), button:has-text('Apply')")
                    if external:
                        _screenshot(page, cv_path, "02_external_apply")
                        return {
                            "success": False,
                            "screenshot": cv_path.parent / "linkedin_02_external_apply.png",
                            "error": "External Apply — not Easy Apply. Cannot auto-submit.",
                        }
                    _screenshot(page, cv_path, "02_no_apply_button")
                    return {
                        "success": False,
                        "screenshot": cv_path.parent / "linkedin_02_no_apply_button.png",
                        "error": "Easy Apply button not found on page",
                    }

                easy_apply_btn.scroll_into_view_if_needed()
                _human_delay(0.5, 1.5)
                easy_apply_btn.click()
                _human_delay(1.5, 3.0)
                _screenshot(page, cv_path, "03_modal_open")
                logger.info("LinkedIn: Easy Apply modal opened")

                # --- Step 3: Navigate through modal pages ---
                max_pages = 8  # safety cap
                page_num = 0
                last_action = ""

                for page_num in range(1, max_pages + 1):
                    modal = _find_modal(page)
                    if not modal:
                        logger.warning("LinkedIn: modal disappeared at page %d", page_num)
                        break

                    modal_text = modal.text_content() or ""

                    # Detect which page we're on
                    if page_num == 1 or "phone" in modal_text.lower() or "contact" in modal_text.lower():
                        _fill_contact_page(page, profile)
                        logger.info("LinkedIn: filled contact page (%d)", page_num)

                    elif "resume" in modal_text.lower() or "upload" in modal_text.lower():
                        _fill_resume_page(page, cv_path, cover_letter_path,
                                          cl_generator=custom_answers.get("_cl_generator") if custom_answers else None)
                        logger.info("LinkedIn: filled resume page (%d)", page_num)

                    elif "experience" in modal_text.lower() or "work history" in modal_text.lower():
                        _fill_experience_page(page)
                        logger.info("LinkedIn: confirmed experience page (%d)", page_num)

                    else:
                        # Generic question page — use screening answers
                        _answer_questions(page, profile, custom_answers or {})
                        logger.info("LinkedIn: answered questions page (%d)", page_num)

                    _screenshot(page, cv_path, f"page_{page_num:02d}")

                    # Click Next / Review / Submit
                    last_action = _click_next_or_submit(page)
                    logger.info("LinkedIn: page %d → action=%s", page_num, last_action)

                    if last_action == "submit":
                        if _AUTO_SUBMIT:
                            _human_delay(1.0, 2.0)
                            _screenshot(page, cv_path, "submitted")
                            logger.info("LinkedIn: APPLICATION SUBMITTED")
                            return {
                                "success": True,
                                "screenshot": cv_path.parent / "linkedin_submitted.png",
                                "error": None,
                            }
                        else:
                            _screenshot(page, cv_path, "review_before_submit")
                            logger.info("LinkedIn: reached Submit — AUTO_SUBMIT=false, stopping")
                            return {
                                "success": True,
                                "screenshot": cv_path.parent / "linkedin_review_before_submit.png",
                                "error": None,
                                "needs_manual_submit": True,
                            }

                    if last_action == "review":
                        # Review page — take screenshot and try to submit
                        _human_delay(1.0, 2.0)
                        _screenshot(page, cv_path, "review_page")
                        logger.info("LinkedIn: reached Review page")

                        if _AUTO_SUBMIT:
                            last_action = _click_next_or_submit(page)
                            if last_action == "submit":
                                _human_delay(1.0, 2.0)
                                _screenshot(page, cv_path, "submitted")
                                logger.info("LinkedIn: APPLICATION SUBMITTED")
                                return {
                                    "success": True,
                                    "screenshot": cv_path.parent / "linkedin_submitted.png",
                                    "error": None,
                                }
                        else:
                            return {
                                "success": True,
                                "screenshot": cv_path.parent / "linkedin_review_page.png",
                                "error": None,
                                "needs_manual_submit": True,
                            }

                    if last_action in ("no_modal", "no_button"):
                        # Check if we accidentally submitted or if something went wrong
                        _screenshot(page, cv_path, f"stuck_page_{page_num}")
                        # Check for success confirmation
                        success_indicators = page.query_selector(
                            "[data-test-modal-header]:has-text('submitted'), "
                            "h2:has-text('submitted'), "
                            "h2:has-text('Application sent'), "
                            ".artdeco-modal h2:has-text('application')"
                        )
                        if success_indicators:
                            _screenshot(page, cv_path, "confirmed_submitted")
                            logger.info("LinkedIn: detected submission confirmation")
                            return {
                                "success": True,
                                "screenshot": cv_path.parent / "linkedin_confirmed_submitted.png",
                                "error": None,
                            }

                        return {
                            "success": False,
                            "screenshot": cv_path.parent / f"linkedin_stuck_page_{page_num}.png",
                            "error": f"Stuck at page {page_num}: {last_action}. Modal may have closed.",
                        }

                # Exceeded max pages
                _screenshot(page, cv_path, "max_pages_exceeded")
                return {
                    "success": False,
                    "screenshot": cv_path.parent / "linkedin_max_pages_exceeded.png",
                    "error": f"Exceeded max {max_pages} pages without reaching Submit",
                }

        except Exception as exc:
            logger.error("LinkedIn adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
