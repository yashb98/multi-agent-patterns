#!/usr/bin/env python3
"""Live job application with human-in-the-loop approval.

1. Picks REAL jobs from DB (prioritising career pages over aggregators)
   OR accepts a direct --url for testing specific jobs
2. Launches HEADFUL Playwright browser so you can watch
3. Navigates to job page → detects page type → finds apply button
4. Handles auth walls via SSO handler (Google "Continue as Yash" etc.)
5. Runs UnifiedFieldScanner + FormFillEngine on the REAL form
6. PAUSES before submit — asks for your approval in the terminal
7. If approved: submits + runs post-apply hook
8. If rejected: saves screenshot for review, skips to next job

Usage:
    # Test first 3 jobs from DB (dry-run by default)
    python scripts/apply_live_with_review.py

    # Test a specific URL
    python scripts/apply_live_with_review.py --url "https://boards.greenhouse.io/twilio/jobs/7652892"

    # Actually submit (after human approval)
    python scripts/apply_live_with_review.py --url <URL> --submit
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["UNIFIED_FORM_ENGINE"] = "true"

# Load real applicant profile from centralized config
from jobpulse.config import APPLICANT_PROFILE as _REAL_PROFILE, WORK_AUTH

_TEST_PROFILE: dict[str, str] = dict(_REAL_PROFILE)

_CUSTOM_ANSWERS: dict[str, str] = {
    # Diversity — always prefer not to say
    "gender": "Prefer not to say",
    "sexual_orientation": "Prefer not to say",
    "ethnicity": "Prefer not to say",
    "disability": "Prefer not to say",
    "veteran": "Prefer not to say",
    "transgender": "Prefer not to say",
    # Work auth from real config
    "work_auth": "Yes" if WORK_AUTH.get("right_to_work_uk") else "No",
    "visa_sponsor": "No" if not WORK_AUTH.get("requires_sponsorship") else "Yes",
    "Are you authorized to work in the United Kingdom?": "Yes" if WORK_AUTH.get("right_to_work_uk") else "No",
    "Do you hold the right to work in the UK?": "Yes" if WORK_AUTH.get("right_to_work_uk") else "No",
    "Will you now or in the future require sponsorship?": "No" if not WORK_AUTH.get("requires_sponsorship") else "Yes",
    "How did you hear about this job?": "LinkedIn",
    "What is your gender?": "Prefer not to say",
    "Disability Status": "Prefer not to say",
    "Veteran Status": "Prefer not to say",
    "notice_period": WORK_AUTH.get("notice_period", "Immediately"),
    "salary_expectation": WORK_AUTH.get("salary_expectation", ""),
}


_AUTO_APPROVE = False  # Set by --auto-approve flag


async def _ask_human_approval(page, job_title: str, company: str, fields_filled: int, fields_total: int, dry_run: bool) -> bool:
    """Pause execution and ask human for approval before submitting.

    In interactive terminals: prompts for y/n/s.
    In non-interactive contexts (CI, agents): catches EOFError and
    auto-skips with a message so the browser stays open for review.
    """
    if _AUTO_APPROVE:
        print(f"   🤖 Auto-approved ({fields_filled}/{fields_total} filled)")
        return True

    # Save a screenshot of the filled form so the user can review it
    # even if the browser window isn't visible or is behind other windows
    screenshot_path = Path("/tmp") / f"review_{company.replace(' ', '_')[:20]}_{asyncio.get_event_loop().time():.0f}.png"
    screenshot_saved = False
    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
        screenshot_saved = True
        # On macOS, auto-open the screenshot in Preview so the user can see it
        import platform
        if platform.system() == "Darwin":
            import subprocess
            try:
                subprocess.run(["open", str(screenshot_path)], check=False, timeout=5)
            except Exception:
                pass
    except Exception:
        pass

    print("\n" + "=" * 70)
    if dry_run:
        print("🛑  HUMAN REVIEW REQUIRED (DRY RUN — no submission)")
    else:
        print("🛑  HUMAN REVIEW REQUIRED")
    print("=" * 70)
    print(f"   Job:    {job_title}")
    print(f"   Company: {company}")
    print(f"   Fields:  {fields_filled}/{fields_total} filled")
    print()
    if screenshot_saved:
        print(f"   📸 Screenshot saved: {screenshot_path}")
        print("   Open it to review the filled form.")
    print("   The browser window should also be open — check your taskbar/dock.")
    if dry_run:
        print("   This is a DRY RUN. Type 'y' to simulate submit, 'n' to skip.")
    else:
        print("   Type 'y' to submit, 'n' to skip, 's' to save another screenshot and skip.")
    print("=" * 70)

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(None, input, "   Approve? [y/n/s]: ")
    except EOFError:
        print("   ⚠️  Non-interactive terminal detected — auto-skipping.")
        print("   Add --auto-approve to submit without prompting, or run in an interactive shell.")
        return False

    response = response.strip().lower()

    if response == "y":
        print("   ✅ Human approved")
        return True
    elif response == "s":
        ss = Path("/tmp") / f"review_{company.replace(' ', '_')[:20]}_{asyncio.get_event_loop().time():.0f}.png"
        await page.screenshot(path=str(ss), full_page=True)
        print(f"   📸 Screenshot saved: {ss}")
        print("   ❌ Skipped")
        return False
    else:
        print("   ❌ Skipped")
        return False


async def _post_apply_hook(job_id: str, title: str, company: str, url: str, success: bool, fields_filled: int, dry_run: bool) -> None:
    """Run after application attempt — log to AB tracking, memory, etc."""
    from jobpulse.job_db import JobDB
    try:
        db = JobDB()
        db._conn.execute(
            """INSERT OR REPLACE INTO applications
               (job_id, title, company, url, platform, status, applied_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
            (job_id, title, company, url, "live_test",
             "dry_run" if dry_run else ("submitted" if success else "skipped"),
             f"fields_filled={fields_filled},dry_run={dry_run}")
        )
        db._conn.commit()
    except Exception as exc:
        print(f"   ⚠️  DB log failed: {exc}")
    print(f"   📝 Post-apply hook complete")


async def apply_to_job(page, context, job_id: str, title: str, company: str, url: str, dry_run: bool) -> bool:
    """Apply to a single job. Returns True if processed, False on fatal error."""
    from jobpulse.ats_adapters.discovery import detect_platform
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.navigation.wait_conditions import wait_for_page_stable
    from jobpulse.page_analysis.classifier import PageTypeClassifier
    from jobpulse.application_orchestrator_pkg._navigator import score_apply_button

    print("-" * 70)
    print(f"🔎  {title} @ {company}")
    print(f"    {url[:65]}...")
    print()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await wait_for_page_stable(page, timeout_ms=10000)
    except Exception as exc:
        print(f"   ❌ Navigate failed: {exc}")
        return True

    final_url = page.url
    print(f"   ✅ Loaded: {final_url[:70]}")

    # Page analysis
    body_text = await page.locator("body").text_content() or ""
    classifier = PageTypeClassifier()
    page_type, confidence = classifier.classify({
        "url": final_url,
        "page_text_preview": body_text[:2000],
        "fields": [],
        "buttons": [],
    })
    print(f"   🔍 Page type: {page_type.value} (conf={confidence:.2f})")

    # Find apply button
    apply_buttons = []
    for sel in ["button", "a[role='button']", "a"]:
        for btn in await page.locator(sel).all():
            try:
                text = (await btn.text_content() or "").strip()
                if not text or len(text) > 60:
                    continue
                score = score_apply_button(text)
                if score > 0:
                    apply_buttons.append((score, btn, text))
            except Exception:
                continue

    if not apply_buttons:
        print("   ⚠️  No apply button — skipping")
        await page.screenshot(path=f"/tmp/apply_{job_id[:8]}_no_button.png")
        return True

    apply_buttons.sort(key=lambda x: x[0], reverse=True)
    print(f"   👆 Best apply button: '{apply_buttons[0][2]}' (score={apply_buttons[0][0]})")

    # Click apply
    clicked = False
    for score, btn, text in apply_buttons[:3]:
        try:
            await btn.click(force=True, timeout=15000)
            clicked = True
            print(f"   ✅ Clicked: '{text}'")
            break
        except Exception:
            try:
                el = await btn.element_handle()
                if el:
                    await page.evaluate("el => el.click()", el)
                    clicked = True
                    print(f"   ✅ JS-clicked: '{text}'")
                    break
            except Exception:
                pass

    if not clicked:
        print("   ❌ Click failed — skipping")
        return True

    await asyncio.sleep(5)
    if len(context.pages) > 1:
        page = context.pages[-1]
        print(f"   🔄 New tab: {page.url[:70]}")

    await wait_for_page_stable(page, timeout_ms=12000)
    final_ats = detect_platform(page.url)
    print(f"   🏢 ATS: {final_ats}")

    # If ATS page has its own "Apply" button (e.g. Greenhouse, Lever),
    # click it to reveal the form modal
    apply_clicked = False
    for apply_text in ["Apply for this job", "Apply", "Apply Now", "Start application"]:
        try:
            apply_btn = page.get_by_role("button", name=apply_text, exact=False).first
            if await apply_btn.count() and await apply_btn.is_visible():
                await apply_btn.click()
                print(f"   👆 Clicked ATS apply: '{apply_text}'")
                await asyncio.sleep(3)
                apply_clicked = True
                break
        except Exception:
            continue
    if not apply_clicked:
        for apply_text in ["Apply for this job", "Apply", "Apply Now"]:
            try:
                apply_link = page.get_by_role("link", name=apply_text, exact=False).first
                if await apply_link.count() and await apply_link.is_visible():
                    await apply_link.click()
                    print(f"   👆 Clicked ATS apply link: '{apply_text}'")
                    await asyncio.sleep(3)
                    break
            except Exception:
                continue

    # Check for login wall
    body_text2 = await page.locator("body").text_content() or ""
    body_lower = body_text2.lower()
    login_indicators = ["sign in", "log in", "login", "create account", "join now"]
    is_login = (
        any(ind in body_lower for ind in login_indicators)
        and any(ind in body_lower for ind in ["password", "email", "phone"])
    )

    if is_login:
        print(f"   ⚠️  Login wall detected — trying SSO...")
        from jobpulse.sso_handler import SSOHandler
        snapshot = {"url": page.url, "page_text_preview": body_text2[:2000], "buttons": [], "fields": []}
        for sel in ["button", "a"]:
            for btn in await page.locator(sel).all():
                try:
                    t = (await btn.text_content() or "").strip()
                    if t:
                        snapshot["buttons"].append({"text": t, "selector": sel, "enabled": True})
                except Exception:
                    pass

        class Bridge:
            def __init__(self, page):
                self.page = page
            async def click(self, selector):
                await self.page.locator(selector).first.click()
            async def get_snapshot(self):
                body = await self.page.locator("body").text_content() or ""
                buttons = []
                for sel in ["button", "a"]:
                    for btn in await self.page.locator(sel).all():
                        try:
                            t = (await btn.text_content() or "").strip()
                            if t:
                                buttons.append({"text": t, "selector": sel, "enabled": True})
                        except Exception:
                            pass
                return {"url": self.page.url, "page_text_preview": body[:2000], "buttons": buttons, "fields": []}

        sso = SSOHandler(Bridge(page))
        sso_info = sso.detect_sso(snapshot)
        if sso_info:
            print(f"   🔑 SSO available: {sso_info['provider']}")
            await sso.click_sso(sso_info)
            await wait_for_page_stable(page, timeout_ms=10000)
            body_text3 = await page.locator("body").text_content() or ""
            if not any(ind in body_text3.lower() for ind in login_indicators[:3]):
                print("   ✅ Auth succeeded")
            else:
                print("   ❌ Still on login page — skipping")
                return True
        else:
            print("   ❌ No SSO found — skipping (manual login needed)")
            return True

    # Scan & fill
    print(f"\n   🔎 Scanning form fields...")
    scanner = UnifiedFieldScanner(page)
    fields = await scanner.scan()
    print(f"   ✅ {len(fields)} fields detected")

    if len(fields) == 0:
        print("   ⚠️  No form fields — skipping")
        return True

    print()
    print("   " + "-" * 50)
    fillable = [f for f in fields if str(f.input_type) != "button"]
    for i, f in enumerate(fillable[:20], 1):
        req = "*" if f.required else " "
        opts = f" ({', '.join(f.options[:3])}{'...' if len(f.options) > 3 else ''})" if f.options else ""
        print(f"   {req} {i:2}. [{str(f.input_type):12}] {f.label[:35]!r}{opts}")
    if len(fillable) > 20:
        print(f"   ... {len(fillable) - 20} more")
    print("   " + "-" * 50)
    print()

    print(f"   ⚙️  FormFillEngine filling fields...")
    driver = type("FakeDriver", (), {"page": page, "_page": page})()
    engine = FormFillEngine(page=page, driver=driver, application_id=f"live_{job_id[:8]}")
    result = await engine.fill(
        profile=_TEST_PROFILE,
        custom_answers=_CUSTOM_ANSWERS,
        platform=final_ats,
        dry_run=dry_run,
    )
    print(f"   ✅ Filled {result.total_fields_filled} fields ({result.llm_calls} LLM calls)")
    if result.failed_labels:
        print(f"   ⚠️  Failed during fill: {result.failed_labels}")

    # ── POST-FILL DOM AUDIT ──
    # Re-read actual values from the DOM to detect fields that reported
    # success but didn't actually stick (wrong, empty, or reverted).
    print(f"   🔍 Auditing {len(fillable)} fields in DOM...")
    audit_ok = 0
    audit_bad = 0
    audit_issues: list[str] = []
    for f in fillable:
        try:
            actual = await page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return {status: 'missing_element'};
                    const tag = el.tagName.toLowerCase();
                    const type = el.type || '';
                    if (tag === 'input' && (type === 'checkbox' || type === 'radio')) {
                        return {status: 'ok', value: el.checked ? 'checked' : 'unchecked', tag};
                    }
                    if (tag === 'select') {
                        const opt = el.options[el.selectedIndex];
                        return {status: 'ok', value: opt ? opt.text.trim() : '', tag};
                    }
                    // For React Select and custom widgets, try to read visible text
                    let val = el.value || '';
                    if (!val && el.getAttribute('data-value')) {
                        val = el.getAttribute('data-value');
                    }
                    // Walk up to find select__single-value
                    if (!val) {
                        const control = el.closest('.select__control') || el.closest('[class*=\"select__\"]');
                        if (control) {
                            const sv = control.querySelector('.select__single-value');
                            if (sv) val = sv.textContent.trim();
                        }
                    }
                    return {status: 'ok', value: val.trim(), tag};
                }""",
                f.selector,
            )
            if actual.get("status") == "missing_element":
                audit_bad += 1
                audit_issues.append(f"  ❌ {f.label!r}: element not found ({f.selector})")
            elif actual.get("value") == "" and f.required:
                audit_bad += 1
                audit_issues.append(f"  ❌ {f.label!r}: EMPTY but required [{f.input_type}]")
            elif actual.get("value") == "" and not f.required:
                # Optional empty fields are fine
                audit_ok += 1
            else:
                audit_ok += 1
        except Exception as exc:
            audit_bad += 1
            audit_issues.append(f"  ❌ {f.label!r}: audit error ({exc})")

    if audit_issues:
        print(f"   ⚠️  DOM AUDIT: {audit_bad} issues found ({audit_ok} OK)")
        for issue in audit_issues[:15]:
            print(issue)
        if len(audit_issues) > 15:
            print(f"   ... and {len(audit_issues) - 15} more")
    else:
        print(f"   ✅ DOM AUDIT: all {audit_ok} fields verified")

    # Human approval gate
    approved = await _ask_human_approval(page, title, company, result.total_fields_filled, len(fillable), dry_run)

    if approved and not dry_run:
        print("   🚀 Submitting application...")
        try:
            for text in ["Submit Application", "Submit", "Apply", "Send Application"]:
                btn = page.get_by_role("button", name=text, exact=False).first
                if await btn.count():
                    await btn.click()
                    print("   ✅ Submitted!")
                    break
            else:
                print("   ⚠️  No submit button found")
        except Exception as exc:
            print(f"   ⚠️  Submit failed: {exc}")
        await asyncio.sleep(3)
        await _post_apply_hook(job_id, title, company, url, True, result.total_fields_filled, dry_run)
    else:
        await _post_apply_hook(job_id, title, company, url, False, result.total_fields_filled, dry_run)

    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="Live job application with human review")
    parser.add_argument("--url", type=str, default=None, help="Specific job URL to test")
    parser.add_argument("--max-jobs", type=int, default=3, help="Max jobs from DB to try")
    parser.add_argument("--submit", action="store_true", help="Actually submit (default: dry-run)")
    parser.add_argument("--auto-approve", action="store_true", help="Skip human approval prompt (use with --submit for full automation)")
    args = parser.parse_args()

    global _AUTO_APPROVE
    _AUTO_APPROVE = args.auto_approve
    dry_run = not args.submit

    from playwright.async_api import async_playwright
    from jobpulse.job_db import JobDB

    jobs: list[tuple[str, str, str, str]] = []

    if args.url:
        jobs.append(("manual", "Manual Test", "Test Company", args.url))
    else:
        db = JobDB()
        rows = db._conn.execute(
            """
            SELECT job_id, title, company, url, platform, ats_platform
            FROM job_listings
            WHERE url IS NOT NULL AND url != ''
            ORDER BY 
                CASE 
                    WHEN url LIKE '%careers%' OR url LIKE '%jobs%' THEN 1
                    WHEN platform = 'google_jobs' AND url NOT LIKE '%linkedin%' THEN 2
                    WHEN platform = 'reed' THEN 3
                    WHEN platform = 'indeed' THEN 4
                    WHEN platform = 'linkedin' THEN 5
                    ELSE 6
                END,
                found_at DESC
            LIMIT ?
            """,
            (args.max_jobs,)
        ).fetchall()
        for r in rows:
            jobs.append((r[0], r[1], r[2], r[3]))

    if not jobs:
        print("❌ No jobs to process")
        return 1

    print("=" * 70)
    print("🚀  LIVE APPLY — Real Jobs + Human Approval + Post-Apply Hook")
    print("=" * 70)
    if dry_run:
        print("   Mode: DRY RUN (no actual submission)")
        print("   Add --submit to enable real submission after approval")
    else:
        print("   Mode: LIVE SUBMIT ⚠️")
    print(f"   Jobs queued: {len(jobs)}")
    print("   Browser will open — you can watch the agent work")
    print("=" * 70)
    print()

    async with async_playwright() as p:
        print("🖥️   Launching HEADFUL browser...")

        # On macOS, prefer the user's installed Chrome/Edge over Playwright's
        # bundled Chromium — the bundled version sometimes doesn't show a window.
        import platform
        _is_mac = platform.system() == "Darwin"
        browser = None
        if _is_mac:
            for channel in ("chrome", "msedge"):
                try:
                    browser = await p.chromium.launch(
                        headless=False,
                        channel=channel,
                        slow_mo=50,
                    )
                    print(f"   ✅ Using installed {channel}")
                    break
                except Exception:
                    continue
        if browser is None:
            browser = await p.chromium.launch(
                headless=False,
                slow_mo=50,
                args=["--window-size=1440,900"],
            )
            print("   ⚠️  Using bundled Chromium (if window is invisible, install Google Chrome)")

        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # On macOS, aggressively bring the browser to the foreground
        if _is_mac:
            import subprocess
            import time
            time.sleep(1)  # Give the app time to launch
            for app in ("Google Chrome", "Microsoft Edge", "Chromium"):
                try:
                    subprocess.run(
                        ["osascript", "-e", f'tell application "{app}" to activate'],
                        capture_output=True, timeout=3
                    )
                except Exception:
                    pass

        for attempt, (job_id, title, company, url) in enumerate(jobs, 1):
            try:
                await apply_to_job(page, context, job_id, title, company, url, dry_run)
            except Exception as exc:
                print(f"   💥 Fatal error: {exc}")
                import traceback
                traceback.print_exc()

            # Keep the form page open for review between jobs,
            # only navigate away if there are more jobs to process
            is_last = attempt == len(jobs)
            if not is_last:
                try:
                    await page.goto("about:blank")
                    await asyncio.sleep(2)
                except Exception:
                    pass

        # ── Do NOT close the browser until the user explicitly presses Enter ──
        print()
        print("=" * 70)
        print("🔒  BROWSER LOCKED — Review the filled form at your own pace")
        print("=" * 70)
        print("   The browser window is staying open.")
        print("   Review the form, check that fields are filled correctly,")
        print("   then press Enter here to close the browser and exit.")
        print("=" * 70)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, input, "   Press Enter to close browser... ")
        except EOFError:
            # Non-interactive: just wait a few seconds then close
            print("   Non-interactive mode — closing in 10 seconds...")
            await asyncio.sleep(10)
        await browser.close()

    print("\n" + "=" * 70)
    print("✅  DONE")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
