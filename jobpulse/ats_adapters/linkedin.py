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


def _handle_login_wall(page) -> str:
    """Detect and handle LinkedIn auth prompts before Easy Apply.

    Checks for 'Continue as [Name]' (session refresh) and generic Sign-in.
    Returns: 'clicked_continue' | 'needs_login' | 'no_wall'
    """
    # 'Continue as [Name]' / 'Continue on [Name]' — session cookie valid but needs confirmation
    # Try specific selectors first, then broad text scan as fallback
    for sel in [
        "button:has-text('Continue as')",
        "button:has-text('Continue on')",
        "a:has-text('Continue as')",
        "a:has-text('Continue on')",
        "[data-tracking-control-name*='continue']",
        ".sign-in-modal__continue-btn",
        # Broad fallback — catches any clickable element with "Continue as" text
        ":has-text('Continue as'):visible",
    ]:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            logger.info("LinkedIn: 'Continue as' found via '%s' — clicking", sel)
            el.click()
            _human_delay(2.0, 3.0)
            return "clicked_continue"

    # Text-based fallback: scan all clickable elements for "continue as" or "continue on"
    try:
        for el in page.query_selector_all("a, button, [role='link'], [role='button']"):
            text = (el.text_content() or "").strip().lower()
            if text.startswith("continue as") or text.startswith("continue on"):
                logger.info("LinkedIn: 'Continue' element found via text scan — clicking: %s", text)
                el.click()
                _human_delay(2.0, 3.0)
                return "clicked_continue"
    except Exception as exc:
        logger.debug("LinkedIn: text scan for Continue failed: %s", exc)

    # Generic sign-in wall (session expired / guest layout)
    # First try to dismiss the overlay modal (X button) — sometimes the page
    # serves guest layout with a join/sign-in modal even when session is valid.
    for dismiss_sel in [
        "button[data-tracking-control-name='public_jobs_apply-link-offsite_sign-up-modal_modal_dismiss']",
        ".modal__dismiss",
        "button[aria-label='Dismiss']",
        "button[aria-label='Close']",
        ".artdeco-modal__dismiss",
        "button.contextual-sign-in-modal__modal-dismiss",
        "button.contextual-sign-in-modal__modal-dismiss-btn",
        # Generic X/dismiss buttons in LinkedIn modals
        "button[data-tracking-control-name*='dismiss']",
        "button.modal__dismiss",
    ]:
        try:
            dismiss_btn = page.query_selector(dismiss_sel)
            if dismiss_btn:
                logger.info("LinkedIn: dismissing sign-in overlay via '%s'", dismiss_sel)
                dismiss_btn.click()
                _human_delay(1.0, 2.0)
                # Check if Easy Apply button is now visible
                easy_btn = page.query_selector(
                    "button.jobs-apply-button, button:has-text('Easy Apply')"
                )
                if easy_btn:
                    logger.info("LinkedIn: overlay dismissed, Easy Apply visible")
                    return "no_wall"
        except Exception:
            pass

    for sel in [
        "button:has-text('Sign in')",
        "a:has-text('Sign in')",
        ".authwall-join-form__form-toggle--bottom a",
    ]:
        el = page.query_selector(sel)
        if el:
            logger.warning(
                "LinkedIn: Sign-in wall detected — session may be expired. "
                "Run: python scripts/linkedin_login.py"
            )
            return "needs_login"

    return "no_wall"


def _dump_page_context(page) -> dict:
    """Capture current page state for verbose logging and Ralph Loop diagnosis.

    Returns a dict with url, modal_text, inputs (list), buttons (list), selects (list).
    Each input entry: {type, name, id, placeholder, aria_label, value}.
    """
    ctx: dict = {
        "url": page.url,
        "modal_text": "",
        "inputs": [],
        "buttons": [],
        "selects": [],
    }

    # Modal content
    modal = _find_modal(page)
    if modal:
        ctx["modal_text"] = (modal.text_content() or "")[:500]

    # Inputs — cap at 20 to avoid log spam
    try:
        for inp in page.query_selector_all("input:not([type='hidden'])")[:20]:
            inp_type = inp.get_attribute("type") or "text"
            value = ""
            if inp_type not in ("file", "checkbox", "radio", "submit"):
                try:
                    value = inp.input_value() or ""
                except Exception:
                    pass
            ctx["inputs"].append({
                "type": inp_type,
                "name": inp.get_attribute("name") or "",
                "id": inp.get_attribute("id") or "",
                "placeholder": inp.get_attribute("placeholder") or "",
                "aria_label": inp.get_attribute("aria-label") or "",
                "value": value[:80],
            })
    except Exception as exc:
        logger.debug("_dump_page_context: input scan failed: %s", exc)

    # Buttons
    try:
        for btn in page.query_selector_all("button")[:20]:
            text = (btn.text_content() or "").strip()
            if text:
                ctx["buttons"].append(text)
    except Exception:
        pass

    # Selects
    try:
        for sel_el in page.query_selector_all("select")[:10]:
            ctx["selects"].append({
                "name": sel_el.get_attribute("name") or "",
                "id": sel_el.get_attribute("id") or "",
                "aria_label": sel_el.get_attribute("aria-label") or "",
            })
    except Exception:
        pass

    return ctx


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
    # Always upload our tailored CV — even if an existing resume is detected,
    # the tailored version is better for this specific application.
    file_inputs = page.query_selector_all("input[type='file']")
    uploaded = False
    for fi in file_inputs:
        fi_id = fi.get_attribute("id") or ""
        parent = fi.evaluate_handle("el => el.closest('.jobs-document-upload-redesign-card') || el.parentElement")
        parent_text = parent.text_content() if parent else ""
        if "resume" in (parent_text or "").lower() or "resume" in fi_id.lower():
            if cv_path.exists():
                fi.set_input_files(str(cv_path))
                _human_delay(1.5, 3.0)  # wait for upload
                logger.info("LinkedIn: uploaded tailored resume %s", cv_path.name)
                uploaded = True
            break
    if not uploaded:
        # Fallback: upload to the first file input
        if file_inputs and cv_path.exists():
            file_inputs[0].set_input_files(str(cv_path))
            _human_delay(1.5, 3.0)
            logger.info("LinkedIn: uploaded resume (fallback) %s", cv_path.name)

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
                answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
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
                    # LinkedIn wraps radios in labels that intercept clicks,
                    # so we click the <label> (via for= attr) or use force=True
                    radios = group.query_selector_all("input[type='radio']")
                    answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
                    picked = False
                    for radio in radios:
                        radio_label = radio.evaluate("el => el.closest('label')?.textContent || el.nextSibling?.textContent || ''")
                        if answer and answer.lower() in (radio_label or "").lower():
                            # Click the label element instead of radio to avoid intercept
                            radio_id = radio.get_attribute("id") or ""
                            label_el = group.query_selector(f"label[for='{radio_id}']") if radio_id else None
                            if label_el:
                                label_el.click()
                            else:
                                radio.click(force=True)
                            picked = True
                            _human_delay(0.3, 0.5)
                            break
                    if not picked and radios:
                        # Default: click first "Yes" or first option
                        for radio in radios:
                            radio_label = radio.evaluate("el => el.closest('label')?.textContent || ''")
                            if "yes" in (radio_label or "").lower():
                                radio_id = radio.get_attribute("id") or ""
                                label_el = group.query_selector(f"label[for='{radio_id}']") if radio_id else None
                                if label_el:
                                    label_el.click()
                                else:
                                    radio.click(force=True)
                                _human_delay(0.3, 0.5)
                                break
                        else:
                            radios[0].click(force=True)
                            _human_delay(0.3, 0.5)

                elif input_type == "checkbox":
                    # Checkbox handling — two cases:
                    # 1. Single checkbox ("I agree", "I certify") → check it
                    # 2. Multi-checkbox group (skills, sources) → match answer or pick first
                    all_checkboxes = group.query_selector_all("input[type='checkbox']")
                    answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)

                    if len(all_checkboxes) <= 1:
                        # Single checkbox — check unless answer says "No"
                        if not input_el.is_checked():
                            should_check = not (answer and answer.lower() in ("no", "false"))
                            if should_check:
                                cb_id = input_el.get_attribute("id") or ""
                                label_el = group.query_selector(f"label[for='{cb_id}']") if cb_id else None
                                if label_el:
                                    label_el.click()
                                else:
                                    input_el.click(force=True)
                                _human_delay(0.3, 0.5)
                                logger.debug("LinkedIn: checked single checkbox for '%s'", question[:60])
                    else:
                        # Multi-checkbox group — try to match answer text, else pick first
                        any_checked = any(cb.is_checked() for cb in all_checkboxes)
                        if not any_checked:
                            picked = False
                            if answer:
                                for cb in all_checkboxes:
                                    cb_label = cb.evaluate(
                                        "el => el.closest('label')?.textContent || "
                                        "el.parentElement?.textContent || ''"
                                    ).strip()
                                    if answer.lower() in cb_label.lower():
                                        cb_id = cb.get_attribute("id") or ""
                                        label_el = group.query_selector(f"label[for='{cb_id}']") if cb_id else None
                                        if label_el:
                                            label_el.click()
                                        else:
                                            cb.click(force=True)
                                        picked = True
                                        _human_delay(0.3, 0.5)
                                        logger.debug("LinkedIn: matched checkbox '%s' for '%s'", cb_label[:40], question[:40])
                                        break
                            if not picked:
                                # Pick first checkbox as fallback
                                cb = all_checkboxes[0]
                                cb_id = cb.get_attribute("id") or ""
                                label_el = group.query_selector(f"label[for='{cb_id}']") if cb_id else None
                                if label_el:
                                    label_el.click()
                                else:
                                    cb.click(force=True)
                                _human_delay(0.3, 0.5)
                                logger.debug("LinkedIn: checked first checkbox (fallback) for '%s'", question[:60])

                elif input_type in ("text", "tel", "email", "number", ""):
                    if not current.strip():
                        answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
                        if answer:
                            input_el.fill(answer)
                            _human_delay(0.3, 0.5)

            elif tag == "textarea":
                current = input_el.input_value() or ""
                if not current.strip():
                    answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
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
                # --- Step 0: Establish LinkedIn session ---
                # Navigate to LinkedIn feed to check login state.
                # If not logged in, wait for manual login in the browser window.
                page.goto("https://www.linkedin.com/feed/", timeout=45000, wait_until="domcontentloaded")
                _human_delay(2, 3)

                current_url = page.url
                if "/login" in current_url or "/authwall" in current_url or "/uas/" in current_url:
                    logger.info("LinkedIn: not logged in (url=%s), attempting auto-login", current_url)

                    # Try "Welcome Back" one-click login first
                    # LinkedIn shows a card with the account name that can be clicked
                    welcome_card = None
                    for sel in [
                        "[data-tracking-control-name='login_welcome_back_profile']",
                        ".welcome-back__profile",
                        # Click the account card containing "Yash" or the user's email
                        "div:has-text('Yash Bishnoi'):not(:has(div:has-text('Yash Bishnoi')))",
                    ]:
                        try:
                            welcome_card = page.query_selector(sel)
                        except Exception:
                            pass
                        if welcome_card:
                            break

                    # Fallback: find any clickable element that looks like a profile card
                    if not welcome_card:
                        try:
                            for el in page.query_selector_all("a, button, [role='button'], div[tabindex]"):
                                text = (el.text_content() or "").strip()
                                if "yash" in text.lower() and ("gmail" in text.lower() or "bishnoi" in text.lower()):
                                    welcome_card = el
                                    break
                        except Exception:
                            pass

                    if welcome_card:
                        logger.info("LinkedIn: 'Welcome Back' card found — clicking to log in")
                        welcome_card.click()
                        _human_delay(3, 5)

                        # Wait for redirect to feed or password prompt
                        for _wait_s in range(30):
                            current_url = page.url
                            if "/feed" in current_url or "/jobs" in current_url:
                                logger.info("LinkedIn: auto-login successful — redirected to %s", current_url)
                                break
                            time.sleep(1)
                    else:
                        logger.warning("LinkedIn: no Welcome Back card — waiting for manual login")
                        print("\n" + "=" * 50)
                        print("  LinkedIn session expired — log in manually")
                        print("  in the browser window, then wait for redirect.")
                        print("=" * 50 + "\n")

                    # Wait up to 90s for feed/jobs to load
                    for _wait_s in range(90):
                        current_url = page.url
                        if "/feed" in current_url or "/jobs" in current_url or "/in/" in current_url:
                            break
                        time.sleep(1)
                    else:
                        _screenshot(page, cv_path, "00_not_logged_in")
                        return {
                            "success": False,
                            "screenshot": cv_path.parent / "linkedin_00_not_logged_in.png",
                            "error": "LinkedIn login timed out after 90s",
                        }

                    # Save fresh session state
                    storage_path = Path(chrome_profile).parent / "linkedin_storage.json"
                    try:
                        _browser.storage_state(path=str(storage_path))
                        logger.info("LinkedIn: saved fresh storage state after login")
                    except Exception:
                        pass
                    _human_delay(1, 2)

                logger.info("LinkedIn: logged in — feed at %s", current_url)
                _screenshot(page, cv_path, "00_logged_in")

                # --- Step 1: Navigate to job page ---
                # Go to /jobs/ first to establish the logged-in jobs context,
                # then navigate to the specific job. Direct URL navigation
                # sometimes serves the guest layout even when logged in.
                page.goto("https://www.linkedin.com/jobs/", timeout=60000, wait_until="commit")
                _human_delay(2, 3)
                page.goto(url, timeout=60000, wait_until="commit")
                _human_delay(2, 4)
                _screenshot(page, cv_path, "01_job_page")

                # --- Login wall check (belt and suspenders) ---
                wall_result = _handle_login_wall(page)
                logger.info("LinkedIn: login wall check → %s", wall_result)
                if wall_result == "needs_login":
                    # We confirmed login on /feed/ — the job page may just need a reload
                    # or the guest layout may coexist with a valid session.
                    # Try reloading the page once before giving up.
                    logger.info("LinkedIn: sign-in wall on job page — trying reload")
                    page.reload(timeout=60000, wait_until="commit")
                    _human_delay(2, 4)
                    wall_result2 = _handle_login_wall(page)
                    logger.info("LinkedIn: login wall check after reload → %s", wall_result2)
                    if wall_result2 == "needs_login":
                        _screenshot(page, cv_path, "01b_needs_login")
                        return {
                            "success": False,
                            "screenshot": cv_path.parent / "linkedin_01b_needs_login.png",
                            "error": "LinkedIn session expired. Run: python scripts/linkedin_login.py",
                        }
                if wall_result == "clicked_continue":
                    _screenshot(page, cv_path, "01b_continued_as_yash")
                    _human_delay(1.0, 2.0)

                # --- Verbose page context (pre-modal) ---
                ctx = _dump_page_context(page)
                logger.info(
                    "LinkedIn [PAGE CONTEXT]: url=%s buttons=%s",
                    ctx["url"], ctx["buttons"][:5],
                )

                # --- Step 2: Click Easy Apply button ---
                # Dismiss any Premium prompts / overlays that block the button
                for dismiss_sel in [
                    "button[aria-label='Dismiss']",
                    "button[aria-label='Close']",
                    ".artdeco-modal__dismiss",
                    "button.artdeco-toast-item__dismiss",
                ]:
                    try:
                        dismiss = page.query_selector(dismiss_sel)
                        if dismiss and dismiss.is_visible():
                            dismiss.click()
                            logger.info("LinkedIn: dismissed overlay via '%s'", dismiss_sel)
                            _human_delay(0.5, 1.0)
                    except Exception:
                        pass

                # Scroll to top of the job card to find the apply button
                page.evaluate("window.scrollTo(0, 0)")
                _human_delay(0.5, 1.0)

                # Wait for job details to fully render
                for _wait in range(10):
                    job_title_el = page.query_selector(
                        ".jobs-unified-top-card h1, "
                        ".job-details-jobs-unified-top-card__job-title, "
                        "h1.t-24, h2.t-24"
                    )
                    if job_title_el:
                        logger.info("LinkedIn: job title loaded: %s", (job_title_el.text_content() or "")[:60])
                        break
                    time.sleep(1)
                else:
                    logger.warning("LinkedIn: job title element not found after 10s — continuing anyway")
                _human_delay(1, 2)

                # The green Easy Apply button is a <button> with class
                # 'jobs-apply-button'. The green pill badge is an <a> tag
                # that does NOT open the modal — we must skip it.
                easy_apply_btn = None
                for sel in [
                    self.resolve_selector("button.jobs-apply-button", overrides),
                    self.resolve_selector("button[aria-label*='Easy Apply']", overrides),
                    "button:has-text('Easy Apply')",
                    # Other logged-in layout variations
                    ".jobs-apply-button:not(a)",
                    ".jobs-s-apply button",
                    "[data-control-name='jobdetails_topcard_inapply']",
                    # Top card apply button
                    ".jobs-unified-top-card button:has-text('Apply')",
                    ".job-details-jobs-unified-top-card__container--two-pane button:has-text('Apply')",
                ]:
                    easy_apply_btn = page.query_selector(sel)
                    if easy_apply_btn:
                        logger.info("LinkedIn: Easy Apply button found via '%s'", sel)
                        break

                if not easy_apply_btn:
                    # Fallback: the green "Easy Apply" pill/badge IS the clickable
                    # element in newer LinkedIn layouts — it's an <a> tag.
                    for a_sel in [
                        "a:has-text('Easy Apply')",
                        "a.jobs-apply-button",
                    ]:
                        easy_apply_btn = page.query_selector(a_sel)
                        if easy_apply_btn:
                            logger.info("LinkedIn: Easy Apply <a> tag found via '%s' — using as button", a_sel)
                            break

                if not easy_apply_btn:
                    # Dump all visible buttons for diagnosis
                    all_btns = page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('button, a, [role="button"]'))
                            .filter(el => el.offsetParent !== null)
                            .map(el => ({
                                tag: el.tagName,
                                text: el.textContent.trim().substring(0, 60),
                                cls: el.className.substring(0, 80),
                                h: el.getBoundingClientRect().height
                            }))
                            .filter(x => x.text.toLowerCase().includes('apply') || x.cls.includes('apply'));
                    }""")
                    logger.info("LinkedIn: apply-related elements on page: %s", all_btns)
                    _screenshot(page, cv_path, "02_apply_debug")

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
                _human_delay(2.0, 4.0)

                # Wait for modal to appear (poll up to 5 seconds)
                modal = None
                for _wait in range(10):
                    modal = _find_modal(page)
                    if modal:
                        break
                    time.sleep(0.5)

                _screenshot(page, cv_path, "03_modal_open")
                if not modal:
                    logger.warning("LinkedIn: Easy Apply modal did NOT open after clicking")
                    _screenshot(page, cv_path, "03_no_modal")
                    return {
                        "success": False,
                        "screenshot": cv_path.parent / "linkedin_03_no_modal.png",
                        "error": "Easy Apply button clicked but modal did not open",
                    }
                logger.info("LinkedIn: Easy Apply modal opened")

                # --- Step 3: Navigate through modal pages ---
                max_pages = 15  # safety cap — Gousto has 10+ pages
                page_num = 0
                last_action = ""
                prev_modal_snippet = ""
                stuck_count = 0

                for page_num in range(1, max_pages + 1):
                    modal = _find_modal(page)
                    if not modal:
                        logger.warning("LinkedIn: modal disappeared at page %d", page_num)
                        break

                    modal_text = modal.text_content() or ""

                    # Stuck-page detection: if same content appears 2× in a row, we're looping
                    # Skip the generic modal wrapper (first ~300 chars are always
                    # "Dialog content start...") — compare chars 300-700 instead.
                    current_snippet = modal_text[300:700].strip()
                    if current_snippet and current_snippet == prev_modal_snippet:
                        stuck_count += 1
                        if stuck_count >= 2:
                            _screenshot(page, cv_path, f"stuck_same_page_{page_num}")
                            logger.error(
                                "LinkedIn: stuck on same page for %d iterations (page %d). "
                                "Likely unanswered required fields. Snippet: %s",
                                stuck_count + 1, page_num, current_snippet[:80],
                            )
                            return {
                                "success": False,
                                "screenshot": cv_path.parent / f"linkedin_stuck_same_page_{page_num}.png",
                                "error": (
                                    f"Stuck on same page for {stuck_count + 1} iterations at page {page_num}. "
                                    f"Required fields likely unanswered. Content: {current_snippet[:100]}"
                                ),
                            }
                    else:
                        stuck_count = 0
                    prev_modal_snippet = current_snippet

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

                    # Verbose context dump — shows all inputs/buttons for this page
                    ctx = _dump_page_context(page)
                    logger.info(
                        "LinkedIn [PAGE %d CONTEXT] modal_text=%s... inputs=%s buttons=%s",
                        page_num,
                        ctx["modal_text"][:100],
                        [{k: v for k, v in i.items() if v} for i in ctx["inputs"]],
                        ctx["buttons"],
                    )

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
