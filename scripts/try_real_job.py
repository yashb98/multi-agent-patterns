#!/usr/bin/env python3
"""Real job application — Workday ATS flow with browser staying open."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["UNIFIED_FORM_ENGINE"] = "true"

_TEST_PROFILE: dict[str, str] = {
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
}

JOB_URL = "https://www.accenture.com/gb-en/careers/jobdetails?id=R00310405_en&utm_campaign=google_jobs_apply&utm_source=google_jobs_apply&utm_medium=organic"

async def _find_and_click_by_text(page, text: str):
    """Find any clickable element containing text and click it."""
    for sel in ["button", "a", "div[role='button']", "span[role='button']", "input[type='button']"]:
        for el in await page.locator(sel).all():
            try:
                t = (await el.text_content() or "").strip()
                if text.lower() in t.lower():
                    await el.click()
                    return True
            except Exception:
                continue
    return False

async def main():
    from playwright.async_api import async_playwright
    from jobpulse.ats_adapters.discovery import detect_platform
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.navigation.wait_conditions import wait_for_page_stable

    print("=" * 70)
    print("🚀  REAL WORKDAY ATS — Browser stays open for observation")
    print("=" * 70)
    print()

    ss_dir = Path("/tmp/real_job_test")
    ss_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        # Step 1: Navigate
        print("📡  Step 1: Navigate to Accenture job page...")
        await page.goto(JOB_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await page.screenshot(path=str(ss_dir / "01_accenture.png"))
        print(f"   ✅ Loaded")

        # Dismiss cookies
        for pattern in ["button:has-text('Accept All Cookies')", "#onetrust-accept-btn-handler"]:
            try:
                btn = page.locator(pattern).first
                if await btn.count() and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass

        # Step 2: Click Apply
        print("\n👆  Step 2: Click 'Apply for this job'...")
        apply_btn = page.locator("a:has-text('Apply for this job')").first
        if await apply_btn.count():
            await apply_btn.click()
            print("   ✅ Clicked")
        else:
            print("   ❌ Not found")
            return 1

        await asyncio.sleep(5)

        # Switch to Workday tab
        ctx = page.context
        if len(ctx.pages) > 1:
            page = ctx.pages[-1]
            print(f"   🆕 Workday tab: {page.url[:60]}...")
        await page.screenshot(path=str(ss_dir / "02_workday_landing.png"))

        # Step 3: Find and click "Apply Manually" or similar
        print("\n🖱️   Step 3: Looking for apply option on Workday...")

        # Print all buttons first
        print("   Buttons on Workday page:")
        for sel in ["button", "a", "div[role='button']"]:
            for el in await page.locator(sel).all():
                try:
                    t = (await el.text_content() or "").strip()
                    if t and len(t) < 60:
                        print(f"      - '{t}'")
                except Exception:
                    pass

        clicked = False
        for label in ["Apply Manually", "Autofill with Resume", "Apply", "Sign In"]:
            if await _find_and_click_by_text(page, label):
                print(f"   ✅ Clicked: '{label}'")
                clicked = True
                break

        if not clicked:
            print("   ❌ Could not find apply option")
            return 1

        await asyncio.sleep(5)
        await wait_for_page_stable(page, timeout_ms=8000)
        await page.screenshot(path=str(ss_dir / "03_workday_form.png"))

        # Step 4: Scan form
        print("\n🔎  Step 4: Scanning Workday form...")
        scanner = UnifiedFieldScanner(page)
        fields = await scanner.scan()
        print(f"   ✅ {len(fields)} fields")

        if fields:
            print()
            print("-" * 50)
            fillable = [f for f in fields if str(f.input_type) != "button"]
            for i, f in enumerate(fillable[:20], 1):
                req = "*" if f.required else " "
                print(f"   {req} {i:2}. [{str(f.input_type):12}] {f.label[:40]!r}")
            print("-" * 50)

            print("\n⚙️   Step 5: Filling...")
            driver = type("FakeDriver", (), {"page": page, "_page": page})()
            engine = FormFillEngine(page=page, driver=driver, application_id="accenture_workday")
            result = await engine.fill(
                profile=_TEST_PROFILE,
                custom_answers={},
                platform="workday",
                dry_run=False,
            )
            await page.screenshot(path=str(ss_dir / "04_filled.png"))
            print(f"   ✅ Filled {result.total_fields_filled} fields")

        # Keep open
        print("\n" + "=" * 70)
        print("🛑  Browser open for 60s. Review the Workday form.")
        print("=" * 70)
        await asyncio.sleep(60)
        await browser.close()
        print("✅  Done")
        return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
