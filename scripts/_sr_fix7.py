"""Fix Gender — use get_by_text to click the exact option."""
from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.connect_over_cdp("http://localhost:9222")
ctx = browser.contexts[0]

page = None
for p in ctx.pages:
    if "oneclick-ui" in p.url:
        page = p
        break

print(f"Page: {page.title()[:60]}")

combos = page.get_by_role("combobox").all()
gender = combos[10]
print(f"Current gender: '{gender.input_value()}'")

# Clear
gender.click(click_count=3)
page.wait_for_timeout(200)
gender.press("Backspace")
page.wait_for_timeout(300)
gender.fill("")
page.wait_for_timeout(500)

# Type "Man" to filter
gender.type("Man", delay=100)
page.wait_for_timeout(2000)

# Try using get_by_text to find and click the right option
# SmartRecruiters options are in shadow DOM — try locator with text matching
# Use page.locator to find the option containing exactly "Man" but not "Transgender"
options = page.get_by_role("option").all()
print(f"Visible options: {len(options)}")

# Try clicking each option and check if it's the right one
# Get the option element handles and check their text via JS
for i, opt in enumerate(options):
    try:
        handle = opt.element_handle(timeout=1000)
        # Get deep text
        text = page.evaluate("""(el) => {
            let t = '';
            function gt(n) {
                if (n.nodeType === 3) t += n.textContent;
                if (n.tagName === 'SLOT' && n.assignedNodes) {
                    for (const a of n.assignedNodes()) gt(a);
                    return;
                }
                if (n.children) for (const c of n.children) gt(c);
                if (n.shadowRoot) for (const c of n.shadowRoot.children) gt(c);
            }
            gt(el);
            return t.replace(/\\s+/g, ' ').trim();
        }""", handle)
        is_disabled = opt.evaluate("el => el.getAttribute('aria-disabled') === 'true'")
        print(f"  option[{i}]: '{text[:60]}' disabled={is_disabled}")

        # Click the one that starts with "Man" and doesn't have "Transgender"
        if "Man" in text and "Transgender" not in text and not is_disabled:
            # Use JavaScript click since Playwright click might fail on shadow elements
            page.evaluate("(el) => el.click()", handle)
            page.wait_for_timeout(500)
            print(f"  --> CLICKED via JS!")
            break
    except Exception as e:
        print(f"  option[{i}]: error {e}")

page.wait_for_timeout(500)
val = gender.input_value()
print(f"\nFinal gender: '{val}'")

# If still wrong, try dispatching input event
if 'transgender' in val.lower() or not val or 'agender' in val.lower():
    print("Still wrong. Trying to set value directly via JS...")
    # Find the combobox input and set its value + dispatch events
    gender.fill("")
    page.wait_for_timeout(300)
    gender.type("Man (including trans man)", delay=50)
    page.wait_for_timeout(2000)
    gender.press("ArrowDown")
    page.wait_for_timeout(300)
    gender.press("Enter")
    page.wait_for_timeout(500)
    print(f"Final v2: '{gender.input_value()}'")

# Screenshot
screenshot_path = "/tmp/asos_page2_fixed7.png"
page.wait_for_timeout(500)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot: {screenshot_path}")

pw.stop()
