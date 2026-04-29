"""Fix Gender and Pronouns — diagnose shadow DOM option structure first."""
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

# Step 1: Diagnose — what's the current state, what tag chips exist?
print("\n=== Gender combobox diagnosis ===")
diag = page.evaluate("""() => {
    const results = { tags: [], options: [], structure: '' };

    // Walk all shadow roots looking for spl-tag elements
    function findInShadow(node, path) {
        if (!node) return;
        const tag = node.tagName || '';

        if (tag === 'SPL-TAG' || tag === 'spl-tag') {
            results.tags.push({
                text: node.textContent.trim().substring(0, 50),
                path: path,
            });
        }

        // Look for listbox/option roles
        const role = node.getAttribute && node.getAttribute('role');
        if (role === 'option') {
            results.options.push({
                text: node.textContent.trim().substring(0, 50),
                value: (node.getAttribute && node.getAttribute('value')) || '',
            });
        }

        if (node.children) {
            for (const c of node.children) {
                findInShadow(c, path + '>' + (c.tagName || ''));
            }
        }
        if (node.shadowRoot) {
            for (const c of node.shadowRoot.children) {
                findInShadow(c, path + '>>shadow>>' + (c.tagName || ''));
            }
        }
    }

    findInShadow(document.body, 'body');
    return results;
}""")

print(f"Tags found: {len(diag['tags'])}")
for t in diag['tags']:
    print(f"  '{t['text']}' at {t['path'][-60:]}")

print(f"\nOptions found: {len(diag['options'])}")
for o in diag['options']:
    print(f"  text='{o['text']}' value='{o['value']}'")

# Step 2: Clear existing gender selection by finding and clicking the close button on spl-tag
print("\n=== Clearing gender tags ===")
# SPL-TAG has a close button inside its shadow DOM
cleared = page.evaluate("""() => {
    const cleared = [];
    function findAndClose(node) {
        if (!node) return;
        if (node.tagName === 'SPL-TAG') {
            // Look for close button in shadow root
            if (node.shadowRoot) {
                const closeBtn = node.shadowRoot.querySelector('button, [role="button"], .close, [aria-label="close"], [aria-label="remove"]');
                if (closeBtn) {
                    closeBtn.click();
                    cleared.push(node.textContent.trim().substring(0, 50));
                    return;
                }
                // Try any button-like element
                for (const el of node.shadowRoot.querySelectorAll('*')) {
                    if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') {
                        el.click();
                        cleared.push('button:' + node.textContent.trim().substring(0, 50));
                        return;
                    }
                }
            }
        }
        if (node.children) {
            for (const c of node.children) findAndClose(c);
        }
        if (node.shadowRoot) {
            for (const c of node.shadowRoot.children) findAndClose(c);
        }
    }
    findAndClose(document.body);
    return cleared;
}""")
print(f"Cleared: {cleared}")
page.wait_for_timeout(500)

# Step 3: Now click the gender combobox and type "Man"
print("\n=== Selecting Male/Man ===")
gender = page.get_by_role("combobox").all()[10]
gender.click()
page.wait_for_timeout(500)
gender.fill("Man")
page.wait_for_timeout(2000)

# Get options via Playwright's shadow-piercing get_by_role
options = page.get_by_role("option").all()
print(f"Options from get_by_role: {len(options)}")
for i, opt in enumerate(options[:10]):
    try:
        # Use innerText which is more reliable for visible text
        txt = opt.evaluate("el => el.innerText || el.textContent").strip()
        print(f"  [{i}] '{txt[:50]}'")
    except Exception as e:
        print(f"  [{i}] error: {e}")

# Try to select "Man" option
for opt in options:
    try:
        txt = opt.evaluate("el => el.innerText || el.textContent").strip().lower()
        if txt == "man" or txt == "male" or "man (including" in txt:
            opt.click()
            print(f"  --> Selected: '{txt[:40]}'")
            break
    except:
        continue
else:
    # Try keyboard selection
    print("  No exact match, trying keyboard...")
    gender.press("ArrowDown")
    page.wait_for_timeout(500)
    # Check what's highlighted
    for opt in page.get_by_role("option").all():
        try:
            txt = opt.evaluate("el => el.innerText || el.textContent").strip()
            print(f"  Highlighted: '{txt[:50]}'")
            break
        except:
            pass
    gender.press("Enter")

page.wait_for_timeout(500)
print(f"Gender val: {gender.input_value()}")

# Step 4: Fix pronouns similarly
print("\n=== Fixing Pronouns ===")
pronouns = page.get_by_role("combobox").all()[0]
# Clear existing
page.evaluate("""() => {
    function findAndClose(node) {
        if (!node) return;
        if (node.tagName === 'SPL-TAG') {
            if (node.shadowRoot) {
                const closeBtn = node.shadowRoot.querySelector('button');
                if (closeBtn) { closeBtn.click(); return; }
            }
        }
        if (node.children) for (const c of node.children) findAndClose(c);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) findAndClose(c);
    }
    findAndClose(document.body);
}""")
page.wait_for_timeout(500)

pronouns.click()
page.wait_for_timeout(300)
pronouns.fill("He/Him")
page.wait_for_timeout(2000)

options = page.get_by_role("option").all()
print(f"Pronoun options: {len(options)}")
for i, opt in enumerate(options[:5]):
    try:
        txt = opt.evaluate("el => el.innerText || el.textContent").strip()
        print(f"  [{i}] '{txt[:50]}'")
    except:
        pass

for opt in options:
    try:
        txt = opt.evaluate("el => el.innerText || el.textContent").strip().lower()
        if "he/him" in txt or txt.startswith("he/"):
            opt.click()
            print(f"  --> Selected: '{txt[:40]}'")
            break
    except:
        continue

page.wait_for_timeout(500)
print(f"Pronouns val: {pronouns.input_value()}")

# Screenshot
screenshot_path = "/tmp/asos_page2_fixed3.png"
page.wait_for_timeout(500)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot: {screenshot_path}")

pw.stop()
