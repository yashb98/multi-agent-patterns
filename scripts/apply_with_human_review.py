#!/usr/bin/env python3
"""Human-in-the-loop job application pipeline.

1. Loads a job from the DB (or uses local test form)
2. Opens a VISIBLE browser window
3. Navigates and fills the form using AI
4. PAUSES before submit — asks YOU for approval
5. If approved: submits the application
6. Runs post-apply hook (logs to DB, sends notification, etc.)
"""
from __future__ import annotations

import asyncio
import http.server
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["UNIFIED_FORM_ENGINE"] = "true"

# ── Profile (edit this with your real details) ──
PROFILE: dict[str, str] = {
    "first_name": "Test",
    "last_name": "User",
    "email": "test.user.example@mailinator.com",
    "phone": "+1-555-0199",
    "linkedin": "https://linkedin.com/in/testuser",
    "portfolio": "https://example.com",
    "github": "https://github.com/testuser",
    "location": "San Francisco, CA",
    "country": "United States",
    "headline": "Software Engineer",
    # Screening answers — short, exact matches for common dropdowns
    "work_auth": "Yes, I am authorized",
    "visa_sponsor": "No",
    "gender": "Male",
    "ethnicity": "Asian",
    "disability": "No, I don't have a disability",
    "veteran": "Prefer not to say",
}


def _start_local_server(port: int = 8765) -> None:
    os.chdir(Path(__file__).parent)
    srv = socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)


def _human_approve(page_info: dict) -> bool:
    """Block and wait for human approval before submitting."""
    print("\n" + "=" * 70)
    print("🛑  HUMAN REVIEW REQUIRED")
    print("=" * 70)
    print(f"📋  Job: {page_info.get('title', 'Unknown')}")
    print(f"🏢  Company: {page_info.get('company', 'Unknown')}")
    print(f"🔗  URL: {page_info.get('url', '')}")
    print(f"📄  Pages filled: {page_info.get('pages', 0)}")
    print(f"✅  Fields filled: {page_info.get('filled', 0)}")
    print(f"❌  Fields failed: {page_info.get('failed', 0)}")
    if page_info.get('errors'):
        print("\n⚠️  Errors:")
        for err in page_info['errors'][:5]:
            print(f"   • {err}")
    print("\n🔍  Please review the browser window.")
    print("   The form has been filled. Check that everything looks correct.")
    print("=" * 70)
    while True:
        try:
            resp = input("\n👉  Submit application? [y/n/retry]: ").strip().lower()
        except EOFError:
            # Non-interactive — default to no
            print("Non-interactive mode — defaulting to NO submit")
            return False
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        if resp in ("r", "retry"):
            return None  # Signal to retry filling
        print("   Please enter 'y' (yes), 'n' (no), or 'r' (retry)")


async def main() -> int:
    from playwright.async_api import async_playwright

    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.navigation.wait_conditions import wait_for_page_stable

    # ── Start local test server ──
    port = 8765
    print("🌐  Starting local test server...")
    _start_local_server(port)
    url = f"http://localhost:{port}/test_ats_form.html"
    print(f"   → {url}")
    print()

    print("🖥️   Launching VISIBLE browser (close window to abort)...")
    print("   If no window appears, check that you're on macOS with a GUI session.")
    print()

    async with async_playwright() as p:
        # Launch visible browser with args that help on macOS
        browser = await p.chromium.launch(
            headless=False,
            args=["--window-size=1400,900", "--start-maximized"],
        )
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        # Navigate
        print("📡  Navigating to form...")
        await page.goto(url, wait_until="networkidle")
        await wait_for_page_stable(page, timeout_ms=3000)
        print(f"   ✅ Loaded: {page.url}")
        await asyncio.sleep(1)

        # Scan fields
        print("\n🔎  Scanning form fields...")
        scanner = UnifiedFieldScanner(page)
        fields = await scanner.scan()
        print(f"   ✅ {len(fields)} fields detected")
        for f in fields[:15]:
            req = "*" if f.required else " "
            print(f"   {req} [{f.input_type:12}] {f.label[:40]}")

        # Fill form
        print("\n⚙️   Filling form with AI...")
        driver = type("FakeDriver", (), {"page": page, "_page": page})()
        engine = FormFillEngine(page=page, driver=driver, application_id="human_review_test")

        result = await engine.fill(
            profile=PROFILE,
            custom_answers={},
            platform="generic",
            dry_run=True,  # dry_run=True means it navigates pages but doesn't click final submit
        )

        print(f"\n   ✅ Filled {result.total_fields_filled} fields across {result.pages_filled} pages")

        # Collect errors
        errors = []
        # We need to track errors from the engine — for now, we'll just do a visual check

        # Pause for human review
        page_info = {
            "title": "Software Engineer",
            "company": "Test Company",
            "url": page.url,
            "pages": result.pages_filled,
            "filled": result.total_fields_filled,
            "failed": result.total_fields_failed,
            "errors": errors,
        }

        decision = _human_approve(page_info)

        if decision is True:
            print("\n📤  Submitting application...")
            # Click the submit button
            try:
                submit_btn = page.get_by_role("button", name="Submit Application")
                if await submit_btn.count():
                    await submit_btn.click()
                    print("   ✅ Submit clicked!")
                    await asyncio.sleep(3)

                    # Check for confirmation
                    body = await page.locator("body").text_content() or ""
                    if "submitted" in body.lower() or "thank you" in body.lower():
                        print("   🎉 Application confirmed submitted!")
                    else:
                        print("   ⚠️  Submit clicked but no confirmation detected")
                else:
                    print("   ❌ Submit button not found")
            except Exception as exc:
                print(f"   ❌ Submit failed: {exc}")

            # Post-apply hook
            print("\n🔔  Running post-apply hook...")
            _post_apply_hook(page_info)

        elif decision is False:
            print("\n❌  Application NOT submitted (human rejected).")
        else:
            print("\n🔄  Retry requested — not yet implemented.")

        # Keep browser open for a moment so user can see final state
        print("\n⏳  Browser will close in 10 seconds (or close it manually)...")
        await asyncio.sleep(10)
        await browser.close()

    print("\n✅  DONE")
    return 0


def _post_apply_hook(info: dict) -> None:
    """Log application outcome and notify."""
    from datetime import datetime, timezone

    # Log to a simple JSONL file
    log_path = Path("data") / "applications_submitted.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        import json
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **info,
        }
        f.write(json.dumps(record) + "\n")

    print(f"   📝 Logged to {log_path}")
    print(f"   📧 Notification: Application to {info.get('company')} {'SUBMITTED' if info.get('submitted') else 'REJECTED by human'}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
