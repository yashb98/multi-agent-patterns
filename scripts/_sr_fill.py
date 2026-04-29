"""Fill ASOS SmartRecruiters page 2 using the dynamic adapter."""
import time
from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.connect_over_cdp("http://localhost:9222")
ctx = browser.contexts[0]

page = None
for p in ctx.pages:
    if "oneclick-ui" in p.url:
        page = p
        break

if not page:
    print("ERROR: form tab not found")
    pw.stop()
    exit(1)

print(f"Title: {page.title()[:60]}")
print(f"URL: {page.url[:100]}")

# Import the adapter
from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersAdapter

adapter = SmartRecruitersAdapter()

# Job context for screening answers
job_context = {
    "job_title": "Machine Learning Engineer",
    "company": "ASOS",
    "location": "London, UK",
}

custom_answers = {
    "_job_context": job_context,
}

profile = {
    "_company": "ASOS",
    "_role": "Machine Learning Engineer",
}

# Scan and fill the page
print("\n=== Running scan_and_fill_page ===")
result = adapter._scan_and_fill_page(page, profile, custom_answers)

print(f"\nField types filled: {len(result.get('field_types', []))}")
for ft in result.get("field_types", []):
    print(f"  {ft}")

print(f"\nScreening Qs: {len(result.get('screening_qs', []))}")
for sq in result.get("screening_qs", []):
    print(f"  {sq[:80]}")

# Take screenshot
screenshot_path = "/tmp/asos_page2_filled.png"
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot saved: {screenshot_path}")

# Check for Submit button
nav = adapter._detect_navigation(page)
if nav:
    action, btn_text = nav
    print(f"\nNavigation: {action} -> '{btn_text}'")
else:
    print("\nNo navigation button found")

pw.stop()
