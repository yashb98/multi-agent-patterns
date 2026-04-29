#!/usr/bin/env python3
"""Live pipeline test — local ATS form + ACTUAL pipeline code.

ALL 562 jobs in the DB require auth (LinkedIn, Indeed, Reed, Google Jobs).
This script creates a realistic local ATS form and exercises the full pipeline:
PageAnalyzer → FormNavigator → UnifiedFieldScanner → FormFillEngine
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

FORM_HTML = (Path(__file__).parent / "test_ats_form.html").read_text()


def _start_local_server(port: int = 8765) -> threading.Thread:
    """Start a background HTTP server serving the test form."""
    handler = http.server.SimpleHTTPRequestHandler
    os.chdir(Path(__file__).parent)
    srv = socketserver.TCPServer(("", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.5)
    return t, srv


async def main() -> int:
    from playwright.async_api import async_playwright

    from jobpulse.ats_adapters.discovery import detect_platform
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.navigation.wait_conditions import wait_for_page_stable
    from jobpulse.page_analysis.classifier import PageTypeClassifier
    from jobpulse.application_orchestrator_pkg._navigator import score_apply_button

    port = 8765
    print("🌐  Starting local test server...")
    _start_local_server(port)
    url = f"http://localhost:{port}/test_ats_form.html"
    print(f"   → {url}")
    print()

    async with async_playwright() as p:
        print("🖥️   Launching browser...")
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        # ── Step 1: Navigate ──
        print("📡  Step 1: Navigate to form...")
        await page.goto(url, wait_until="networkidle")
        await wait_for_page_stable(page, timeout_ms=3000)
        print(f"   ✅ Loaded: {page.url}")

        # ── Step 2: Page analysis ──
        print("\n🔍  Step 2: Page analysis...")
        body_text = await page.locator("body").text_content() or ""
        classifier = PageTypeClassifier()
        page_type, confidence = classifier.classify({
            "url": page.url,
            "page_text_preview": body_text[:2000],
            "fields": [],
            "buttons": [],
        })
        print(f"   → Type: {page_type.value} (conf={confidence:.2f})")

        # ── Step 3: Find apply/navigation buttons ──
        print("\n👆  Step 3: Find navigation buttons...")
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

        apply_buttons.sort(key=lambda x: x[0], reverse=True)
        print(f"   → {len(apply_buttons)} candidates")
        for score, _, text in apply_buttons[:5]:
            print(f"      '{text}' (score={score})")

        # ── Step 4: UnifiedFieldScanner ──
        print("\n🔎  Step 4: UnifiedFieldScanner...")
        scanner = UnifiedFieldScanner(page)
        fields = await scanner.scan()
        print(f"   ✅ {len(fields)} fields detected")
        print()
        print("-" * 70)
        for i, f in enumerate(fields, 1):
            w = f.attributes.get("widget_library", "")
            req = "*" if f.required else " "
            print(f"   {req} {i:2}. [{f.input_type:12}] [{w:12}] {f.label[:40]!r}")
        print("-" * 70)
        print()

        # ── Step 5: FormFillEngine (dry_run) ──
        print("⚙️   Step 5: FormFillEngine (dry_run)...")
        driver = type("FakeDriver", (), {"page": page, "_page": page})()
        engine = FormFillEngine(
            page=page, driver=driver, application_id="live_local_test"
        )
        result = await engine.fill(
            profile=_TEST_PROFILE,
            custom_answers={},
            platform="generic",
            dry_run=True,
        )

        print()
        print("=" * 70)
        print("📊  RESULT")
        print("=" * 70)
        print(f"   success:       {result.success}")
        print(f"   pages:         {result.pages_filled}")
        print(f"   fields filled: {result.total_fields_filled}")
        print(f"   fields failed: {result.total_fields_failed}")
        print(f"   llm_calls:     {result.llm_calls}")
        print(f"   time:          {result.time_seconds:.1f}s")
        if result.error:
            print(f"   error:         {result.error}")
        print("=" * 70)

        # ── Step 6: Check what the engine actually filled ──
        print("\n🔎  Step 6: Verifying filled values...")
        filled_count = 0
        empty_count = 0
        for f in fields:
            sel = f.attributes.get("selector", "") or f.selector or ""
            if f.input_type in ("radio", "checkbox"):
                try:
                    checked = await page.locator(f"{sel}:checked").count()
                    filled_count += 1 if checked > 0 else 0
                    empty_count += 0 if checked > 0 else 1
                except Exception:
                    empty_count += 1
            elif f.input_type in ("file", "button"):
                empty_count += 1
            elif sel:
                try:
                    val = await page.input_value(sel)
                    filled_count += 1 if val and val.strip() else 0
                    empty_count += 0 if val and val.strip() else 1
                except Exception:
                    empty_count += 1
            else:
                empty_count += 1

        print(f"   Fields with values: {filled_count}")
        print(f"   Fields still empty: {empty_count}")

        ss = Path("/tmp") / "pipeline_local_test.png"
        await page.screenshot(path=str(ss), full_page=True)
        print(f"\n📸  Screenshot: {ss}")

        await browser.close()
        print("\n✅  DONE")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
