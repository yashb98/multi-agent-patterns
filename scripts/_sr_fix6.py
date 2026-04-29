"""Fix Gender — clear input value directly, then type exact match."""
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

# Clear: triple click to select all, then Backspace
gender.click(click_count=3)
page.wait_for_timeout(300)
gender.press("Backspace")
page.wait_for_timeout(500)
# Fill with empty to clear React state
gender.fill("")
page.wait_for_timeout(500)
print(f"After clear: '{gender.input_value()}'")

# Now type — SmartRecruiters filters options by input text
# "Man" shows both "Man (including trans man)" and "Transgender - Male/Man"
# Try typing just the exact option text if available
# From the deep scan: options include various gender identities
# "Man (including trans man)" is likely the first option when typing "Man"

gender.type("Man", delay=100)
page.wait_for_timeout(2000)

# Check what options are now visible
options = page.get_by_role("option").all()
print(f"Options shown: {len(options)}")

# Navigate - first ArrowDown selects the first option
gender.press("ArrowDown")
page.wait_for_timeout(300)

# Check current highlighted option
print(f"After ArrowDown: '{gender.input_value()}'")

# Read highlighted option via aria-selected
highlighted = page.evaluate("""() => {
    const opts = [];
    function find(node, depth) {
        if (!node || depth > 12) return;
        const role = node.getAttribute && node.getAttribute('role');
        if (role === 'option') {
            const sel = node.getAttribute('aria-selected');
            const dis = node.getAttribute('aria-disabled');
            const cls = node.className || '';
            const active = cls.includes('active') || cls.includes('focused') || cls.includes('highlight');
            // Deep text
            let text = '';
            function gt(n) {
                if (n.nodeType === 3) text += n.textContent;
                if (n.tagName === 'SLOT' && n.assignedNodes) {
                    for (const a of n.assignedNodes()) gt(a);
                    return;
                }
                if (n.children) for (const c of n.children) gt(c);
                if (n.shadowRoot) for (const c of n.shadowRoot.children) gt(c);
            }
            gt(node);
            const clean = text.replace(/\\s+/g, ' ').trim();
            if (clean) opts.push({ text: clean.substring(0, 80), sel, dis, active });
        }
        if (node.children) for (const c of node.children) find(c, depth);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) find(c, depth + 1);
    }
    find(document.body, 0);
    return opts;
}""")

print(f"Options after ArrowDown:")
for i, h in enumerate(highlighted):
    print(f"  [{i}] '{h['text']}' sel={h['sel']} dis={h['dis']} active={h['active']}")

# Select with Enter
gender.press("Enter")
page.wait_for_timeout(500)
val = gender.input_value()
print(f"\nFinal gender: '{val}'")

# If it still shows Transgender, try Escape and re-approach
if 'transgender' in val.lower() or 'agender' in val.lower():
    print("Wrong selection, trying different approach...")
    gender.press("Escape")
    page.wait_for_timeout(300)
    gender.click(click_count=3)
    page.wait_for_timeout(200)
    gender.press("Backspace")
    page.wait_for_timeout(300)
    gender.fill("")
    page.wait_for_timeout(300)

    # Try "Man (" to specifically match "Man (including..."
    gender.type("Man (", delay=100)
    page.wait_for_timeout(2000)
    gender.press("ArrowDown")
    page.wait_for_timeout(200)
    gender.press("Enter")
    page.wait_for_timeout(500)
    print(f"Final gender v2: '{gender.input_value()}'")

# Screenshot
screenshot_path = "/tmp/asos_page2_fixed6.png"
page.wait_for_timeout(500)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot: {screenshot_path}")

pw.stop()
