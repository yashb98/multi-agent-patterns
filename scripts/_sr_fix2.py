"""Fix Gender Identity and Pronouns on ASOS page 2.

SmartRecruiters spl-autocomplete uses tag chips (spl-tag) for selected values.
Must remove existing chip before selecting new value. Also "Male" partial-matches
"Female" — need to handle option selection more carefully.
"""
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

# --- FIX Gender Identity (combo[10]) ---
print("\n--- Fix: Gender Identity ---")
gender = combos[10]

# First, check all available options
gender.click()
page.wait_for_timeout(500)

# Clear current selection — look for spl-tag close button
# SmartRecruiters puts selected values as spl-tag chips with a close button
close_buttons = page.evaluate("""() => {
    // Find all spl-tag elements and their close buttons
    const tags = [];
    function findTags(node) {
        if (!node) return;
        if (node.tagName === 'SPL-TAG') {
            const text = node.textContent.trim();
            tags.push(text);
        }
        if (node.children) for (const c of node.children) findTags(c);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) findTags(c);
    }
    // Look near the gender combobox
    const combos = document.querySelectorAll('spl-autocomplete');
    for (const combo of combos) {
        findTags(combo);
    }
    return tags;
}""")
print(f"  Found tags: {close_buttons}")

# Try removing the "Female" tag by pressing Backspace in the combobox
gender.fill("")
page.wait_for_timeout(300)
gender.press("Backspace")
page.wait_for_timeout(500)
gender.press("Backspace")
page.wait_for_timeout(500)

# Now type "Man" instead of "Male" to avoid matching "Fe-male"
gender.fill("Man")
page.wait_for_timeout(1500)

# List available options
options = page.get_by_role("option").all()
print(f"  Options visible: {len(options)}")
for i, opt in enumerate(options[:5]):
    try:
        txt = opt.text_content(timeout=500)
        print(f"    [{i}] {txt[:50]}")
    except:
        pass

# Select the right one
for opt in options:
    try:
        txt = opt.text_content(timeout=500).strip().lower()
        if txt in ("man", "male", "man (including trans man)"):
            opt.click()
            print(f"  Selected: {opt.text_content()[:40]}")
            break
    except:
        continue
else:
    # Fallback: just select first option containing "man"
    if options:
        options[0].click()
        print(f"  Selected first option")

page.wait_for_timeout(500)
print(f"  Gender val: {gender.input_value()}")

# --- FIX Pronouns (combo[0]) ---
print("\n--- Fix: Pronouns ---")
pronouns = combos[0]
val = pronouns.input_value()
print(f"  Current: {val}")

# Clear existing
pronouns.fill("")
page.wait_for_timeout(300)
pronouns.press("Backspace")
page.wait_for_timeout(500)
pronouns.press("Backspace")
page.wait_for_timeout(500)

# Type "He" — should match He/Him
pronouns.fill("He/")
page.wait_for_timeout(1500)

options = page.get_by_role("option").all()
print(f"  Options visible: {len(options)}")
for i, opt in enumerate(options[:5]):
    try:
        txt = opt.text_content(timeout=500)
        print(f"    [{i}] {txt[:50]}")
    except:
        pass

for opt in options:
    try:
        txt = opt.text_content(timeout=500).strip().lower()
        if "he/" in txt or txt.startswith("he"):
            opt.click()
            print(f"  Selected: {opt.text_content()[:40]}")
            break
    except:
        continue

page.wait_for_timeout(500)
print(f"  Pronouns val: {pronouns.input_value()}")

# Screenshot
screenshot_path = "/tmp/asos_page2_fixed2.png"
page.wait_for_timeout(500)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot: {screenshot_path}")

# Final status
print("\n=== Combo Final Status ===")
combos = page.get_by_role("combobox").all()
for i, cb in enumerate(combos):
    try:
        val = cb.input_value(timeout=500)
        if val:
            print(f"  combo[{i}] = \"{val[:50]}\"")
    except:
        pass

pw.stop()
