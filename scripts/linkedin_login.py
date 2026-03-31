"""Open LinkedIn login in a persistent Chrome browser for session refresh.

Usage: python scripts/linkedin_login.py
- Opens Chrome with the saved profile
- Navigate to LinkedIn and log in
- Wait until you see the LinkedIn feed/homepage
- Press Enter in the terminal to save the session
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from jobpulse.config import DATA_DIR

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        str(DATA_DIR / "chrome_profile"),
        headless=False,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--disable-infobars",
        ],
        ignore_default_args=["--enable-automation"],
        viewport={"width": 1280, "height": 800},
    )

    # Use first page if exists, otherwise create new
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.linkedin.com/login")

    print("\n" + "=" * 50)
    print("  LinkedIn Login — Session Refresh")
    print("=" * 50)
    print("\n1. Log in to LinkedIn in the browser window")
    print("2. Wait until you see the LinkedIn FEED (homepage)")
    print("3. THEN press Enter here to save the session\n")

    storage_path = str(DATA_DIR / "linkedin_storage.json")

    while True:
        response = input("\nPress Enter to check login status (or 'q' to quit)... ")
        if response.strip().lower() == "q":
            break

        cookies = ctx.cookies()
        li_at = [c for c in cookies if c["name"] == "li_at"]
        li_cookies = [c for c in cookies if "linkedin" in c.get("domain", "")]

        print(f"\n   Total cookies: {len(cookies)}")
        print(f"   LinkedIn cookies: {len(li_cookies)}")

        if li_at:
            print(f"   ✅ li_at cookie FOUND (expires: {li_at[0].get('expires', 'session')})")

            # Save storage state (cookies + localStorage) to JSON
            ctx.storage_state(path=storage_path)
            print(f"   ✅ Storage state saved to {storage_path}")
            print("\n   Session is saved! You can close now.")
            break
        else:
            print("   ❌ li_at cookie NOT found yet.")
            print("   Make sure you:")
            print("   - Completed the full login (email + password + any 2FA)")
            print("   - Can see the LinkedIn FEED with posts")
            print("   - Are NOT on a 'verify' or 'checkpoint' page")
            print("   Try again after navigating to https://www.linkedin.com/feed/")

    ctx.close()
    print("\nBrowser closed.")
