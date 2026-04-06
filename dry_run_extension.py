"""Dry-run: scan the CURRENT tab, click Easy Apply, scan the form.

No navigation — uses whatever tab is active in Chrome.
"""

import asyncio

from jobpulse.config import EXT_BRIDGE_HOST, EXT_BRIDGE_PORT
from jobpulse.ext_bridge import ExtensionBridge


def p(msg: str) -> None:
    print(msg, flush=True)


async def main() -> None:
    bridge = ExtensionBridge(host=EXT_BRIDGE_HOST, port=EXT_BRIDGE_PORT)
    await bridge.start()
    p(f"Bridge on ws://{EXT_BRIDGE_HOST}:{bridge.port}")
    p("Waiting for extension... (reload extension in chrome://extensions)")

    if not await bridge.wait_for_connection(timeout=120):
        p("No connection. Aborting.")
        await bridge.stop()
        return

    p("CONNECTED\n")

    # ── STEP 1: Navigate to job page (loads content script naturally) ──
    JOB_URL = "https://uk.linkedin.com/jobs/view/software-engineer-applied-ai-at-euphoric-4393458955"
    p("=" * 60)
    p("STEP 1: NAVIGATE TO JOB PAGE")
    p(f"  {JOB_URL}")

    snapshot = None
    try:
        snapshot = await bridge.navigate(JOB_URL, timeout_ms=25000)
        p(f"  Title: {snapshot.title}")
        p(f"  Fields: {len(snapshot.fields)} | Buttons: {len(snapshot.buttons)}")
    except Exception as e:
        p(f"  Navigate: {type(e).__name__}: {e}")
        # Try getting snapshot anyway
        snapshot = await bridge.get_snapshot(force_refresh=True)

    if snapshot:
        p(f"  URL: {snapshot.url}")
        p(f"  Title: {snapshot.title}")
        p(f"  Fields: {len(snapshot.fields)}")
        p(f"  Buttons: {len(snapshot.buttons)}")
        if snapshot.buttons:
            p(f"\n  BUTTONS:")
            for b in snapshot.buttons:
                p(f"    - \"{b.text}\" | type={b.type} | selector={b.selector}")
        if snapshot.fields:
            p(f"\n  FIELDS:")
            for i, f in enumerate(snapshot.fields[:15]):
                label = f.label or f.selector[:60]
                p(f"    [{i}] \"{label}\" | type={f.input_type} | required={f.required}")
        if snapshot.verification_wall:
            p(f"\n  VERIFICATION WALL: {snapshot.verification_wall.wall_type}")
        preview = (snapshot.page_text_preview or "")[:300]
        p(f"\n  Preview: {preview}")
    else:
        p("  No snapshot — make sure a tab is active and focused.")
        await bridge.stop()
        return

    # ── STEP 2: Screenshot ──
    p("")
    p("=" * 60)
    p("STEP 2: SCREENSHOT")
    try:
        png = await bridge.screenshot(timeout_ms=10000)
        with open("/tmp/jobpulse_step2_page.png", "wb") as f:
            f.write(png)
        p(f"  Saved: /tmp/jobpulse_step2_page.png ({len(png):,} bytes)")
    except Exception as e:
        p(f"  Failed: {type(e).__name__}: {e}")

    # ── STEP 3: Click Easy Apply ──
    p("")
    p("=" * 60)
    p("STEP 3: CLICK EASY APPLY")

    # Try selectors in order
    selectors = [
        "button.jobs-apply-button",
        "button[aria-label*='Easy Apply']",
        ".jobs-apply-button--top-card button",
        "button.jobs-apply-button--top-card",
    ]
    # Also check snapshot buttons
    if snapshot and snapshot.buttons:
        for b in snapshot.buttons:
            if "easy apply" in b.text.lower() or "apply" in b.text.lower():
                selectors.insert(0, b.selector)

    clicked = False
    for sel in selectors:
        p(f"  Trying: {sel}")
        try:
            result = await bridge.click(sel, timeout_ms=5000)
            if result:
                p(f"  CLICKED: {sel}")
                clicked = True
                break
        except Exception as e:
            p(f"    failed: {e}")

    if not clicked:
        p("  Could not find Easy Apply button.")
        p("  Please click it manually, then press Enter here to continue...")

    # Wait for modal
    p("  Waiting 4s for application modal to open...")
    await asyncio.sleep(4)

    # ── STEP 4: Scan application form ──
    p("")
    p("=" * 60)
    p("STEP 4: APPLICATION FORM SCAN")
    form_snap = await bridge.get_snapshot(force_refresh=True)
    if form_snap:
        p(f"  URL: {form_snap.url}")
        if form_snap.fields:
            p(f"\n  FORM FIELDS ({len(form_snap.fields)}):")
            for i, f in enumerate(form_snap.fields):
                label = f.label or f.selector[:60]
                val = (f.current_value or "")[:40]
                p(f"    [{i}] \"{label}\" | type={f.input_type} | required={f.required} | value=\"{val}\"")
                if f.options:
                    p(f"         options: {f.options[:5]}")
        else:
            p("  No form fields detected in modal.")

        if form_snap.buttons:
            p(f"\n  MODAL BUTTONS ({len(form_snap.buttons)}):")
            for b in form_snap.buttons:
                p(f"    - \"{b.text}\" | type={b.type} | enabled={b.enabled}")

        if form_snap.has_file_inputs:
            p("\n  FILE UPLOAD field detected (CV/Resume)")
    else:
        p("  No snapshot from modal.")

    # ── STEP 5: Screenshot of form ──
    p("")
    p("=" * 60)
    p("STEP 5: SCREENSHOT (form)")
    try:
        png = await bridge.screenshot(timeout_ms=10000)
        with open("/tmp/jobpulse_step5_form.png", "wb") as f:
            f.write(png)
        p(f"  Saved: /tmp/jobpulse_step5_form.png ({len(png):,} bytes)")
    except Exception as e:
        p(f"  Failed: {type(e).__name__}: {e}")

    # ── DONE ──
    p("")
    p("=" * 60)
    p("DRY RUN COMPLETE")
    p("  No data was submitted. Observation only.")
    p("  Ctrl+C to stop bridge.")

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await bridge.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        p("\nStopped.")
