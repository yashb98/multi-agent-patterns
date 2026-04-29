"""Fix Gender Identity — need to clear "Transgender - Male/Man" and select just "Man"."""
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
print(f"Current gender: {gender.input_value()}")

# The SR combobox puts selected value in the input. We need to:
# 1. Clear current value (type Backspace to remove tag or triple-click + delete)
# 2. Type search text that uniquely matches "Man" but not "Transgender - Male/Man"

# Step 1: Clear by selecting all text and deleting
gender.click()
page.wait_for_timeout(300)
gender.press("Control+a")
page.wait_for_timeout(200)
gender.press("Backspace")
page.wait_for_timeout(500)
# Press Backspace a few more times to remove any tag chips
for _ in range(5):
    gender.press("Backspace")
    page.wait_for_timeout(100)

print(f"After clear: '{gender.input_value()}'")

# Step 2: Type "Man" — this shows filtered options
# The issue: "Man" matches both "Man" and "Transgender - Male/Man"
# The options are role="option" but their text is empty (in shadow DOM)
# Let's try to read the actual rendered text via evaluate on the listbox
gender.fill("Man")
page.wait_for_timeout(2000)

# Get the option text from the deeper shadow DOM
option_texts = page.evaluate("""() => {
    const results = [];
    function findOptions(node, depth) {
        if (!node || depth > 10) return;
        const role = node.getAttribute && node.getAttribute('role');
        if (role === 'option') {
            // The option text might be inside a deeper element
            let text = '';
            function getText(n) {
                if (n.nodeType === 3) text += n.textContent;
                if (n.children) for (const c of n.children) getText(c);
                if (n.shadowRoot) for (const c of n.shadowRoot.children) getText(c);
            }
            getText(node);
            results.push(text.trim());
        }
        if (node.children) for (const c of node.children) findOptions(c, depth);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) findOptions(c, depth + 1);
    }
    findOptions(document.body, 0);
    return results;
}""")
print(f"\nOption texts from deep walk: {option_texts}")

# Step 3: Select by keyboard — "Man" should be first if fewer letters typed
# Or we can use the option index. Let's try "Man (including" to narrow it down
gender.fill("")
page.wait_for_timeout(300)
gender.fill("Man (incl")
page.wait_for_timeout(2000)

option_texts2 = page.evaluate("""() => {
    const results = [];
    function findOptions(node, depth) {
        if (!node || depth > 10) return;
        const role = node.getAttribute && node.getAttribute('role');
        if (role === 'option') {
            let text = '';
            function getText(n) {
                if (n.nodeType === 3) text += n.textContent;
                if (n.children) for (const c of n.children) getText(c);
                if (n.shadowRoot) for (const c of n.shadowRoot.children) getText(c);
            }
            getText(node);
            results.push(text.trim());
        }
        if (node.children) for (const c of node.children) findOptions(c, depth);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) findOptions(c, depth + 1);
    }
    findOptions(document.body, 0);
    return results;
}""")
print(f"Options for 'Man (incl': {option_texts2}")

# If we found "Man (including trans man)" as the only option, select it
options = page.get_by_role("option").all()
print(f"Number of options: {len(options)}")
if len(options) == 1:
    options[0].click()
    print("Selected the only option")
elif len(options) > 0:
    # Select the first one
    options[0].click()
    print("Selected first option")

page.wait_for_timeout(500)
print(f"Final gender: {gender.input_value()}")

# Also check — if there's also an "Intersex" or other unwanted tag, clear it
# Final screenshot
screenshot_path = "/tmp/asos_page2_fixed4.png"
page.wait_for_timeout(500)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot: {screenshot_path}")

pw.stop()
