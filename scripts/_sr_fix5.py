"""Fix Gender — remove tag chip via JS, then select correct option."""
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

# Step 1: Find and analyze ALL spl-tag elements on the page
print("\n=== Finding tag chips ===")
tag_info = page.evaluate("""() => {
    const tags = [];
    function walk(node, path) {
        if (!node || !node.tagName) return;
        if (node.tagName === 'SPL-TAG') {
            let text = '';
            // Get text from light DOM slots
            if (node.assignedSlot) text = node.assignedSlot.textContent;
            if (!text) text = node.textContent;
            if (!text && node.shadowRoot) {
                // Try slots inside shadow root
                const slots = node.shadowRoot.querySelectorAll('slot');
                for (const s of slots) {
                    const assigned = s.assignedNodes();
                    for (const a of assigned) {
                        text += (a.textContent || '');
                    }
                }
            }
            // Find close/remove button
            let hasClose = false;
            if (node.shadowRoot) {
                const btns = node.shadowRoot.querySelectorAll('button, [role="button"]');
                hasClose = btns.length > 0;
                tags.push({
                    text: (text || '').trim().substring(0, 80),
                    path: path,
                    hasClose,
                    closeBtnCount: btns.length,
                });
            } else {
                tags.push({
                    text: (text || '').trim().substring(0, 80),
                    path: path,
                    hasClose: false,
                    closeBtnCount: 0,
                });
            }
        }
        if (node.children) {
            for (const c of node.children) walk(c, path + '>' + c.tagName);
        }
        if (node.shadowRoot) {
            for (const c of node.shadowRoot.children) walk(c, path + '#shadow>' + c.tagName);
        }
    }
    walk(document.body, 'body');
    return tags;
}""")

print(f"Found {len(tag_info)} spl-tag elements:")
for t in tag_info:
    print(f"  text='{t['text']}' close={t['hasClose']} btns={t['closeBtnCount']}")
    print(f"    path: ...{t['path'][-80:]}")

# Step 2: Click close button on gender-related tags
print("\n=== Removing gender tags ===")
removed = page.evaluate("""() => {
    const removed = [];
    function walk(node) {
        if (!node || !node.tagName) return;
        if (node.tagName === 'SPL-TAG') {
            let text = node.textContent.trim();
            // Check if this is a gender-related tag
            const genderTerms = ['female', 'male', 'man', 'woman', 'transgender', 'non-binary'];
            const isGender = genderTerms.some(t => text.toLowerCase().includes(t));
            if (isGender && node.shadowRoot) {
                const btn = node.shadowRoot.querySelector('button');
                if (btn) {
                    btn.click();
                    removed.push(text.substring(0, 50));
                    return;
                }
            }
        }
        if (node.children) for (const c of node.children) walk(c);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) walk(c);
    }
    walk(document.body);
    return removed;
}""")
print(f"Removed: {removed}")
page.wait_for_timeout(1000)

# Step 3: Now fill gender combobox
combos = page.get_by_role("combobox").all()
gender = combos[10]
print(f"\nGender after clear: '{gender.input_value()}'")

# Click and type
gender.click()
page.wait_for_timeout(500)
gender.fill("Man")
page.wait_for_timeout(2000)

# Count options
options = page.get_by_role("option").all()
print(f"Options available: {len(options)}")

# Read option text via deep shadow walk
option_details = page.evaluate("""() => {
    const results = [];
    function walk(node, depth) {
        if (!node || depth > 12) return;
        const role = node.getAttribute && node.getAttribute('role');
        if (role === 'option') {
            const disabled = node.getAttribute('aria-disabled') === 'true';
            const selected = node.getAttribute('aria-selected') === 'true';
            // Get ALL text content recursively through shadow roots
            let allText = '';
            function deepText(n) {
                if (n.nodeType === 3) { allText += n.textContent; return; }
                // Check for slot content
                if (n.tagName === 'SLOT') {
                    const assigned = n.assignedNodes();
                    for (const a of assigned) deepText(a);
                    return;
                }
                if (n.children) for (const c of n.children) deepText(c);
                if (n.shadowRoot) for (const c of n.shadowRoot.children) deepText(c);
            }
            deepText(node);
            results.push({
                text: allText.trim().substring(0, 80),
                disabled, selected,
                classes: node.className.substring(0, 80),
            });
        }
        if (node.children) for (const c of node.children) walk(c, depth);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) walk(c, depth + 1);
    }
    walk(document.body, 0);
    return results;
}""")

print("Option details:")
for i, od in enumerate(option_details):
    print(f"  [{i}] '{od['text']}' disabled={od['disabled']} selected={od['selected']}")

# Find the right option — "Man" not "Transgender - Male/Man"
target_idx = None
for i, od in enumerate(option_details):
    text = od['text'].lower()
    if not od['disabled'] and ('man' in text) and 'transgender' not in text:
        target_idx = i
        break

if target_idx is not None:
    print(f"\nSelecting option [{target_idx}]: '{option_details[target_idx]['text']}'")
    # Navigate with arrow keys
    for _ in range(target_idx + 1):
        gender.press("ArrowDown")
        page.wait_for_timeout(200)
    gender.press("Enter")
else:
    print("\nNo matching non-transgender 'Man' option found, trying first option")
    gender.press("ArrowDown")
    page.wait_for_timeout(200)
    gender.press("Enter")

page.wait_for_timeout(500)
print(f"Final gender: {gender.input_value()}")

# Screenshot
screenshot_path = "/tmp/asos_page2_fixed5.png"
page.wait_for_timeout(500)
page.screenshot(path=screenshot_path, full_page=True)
print(f"\nScreenshot: {screenshot_path}")

pw.stop()
