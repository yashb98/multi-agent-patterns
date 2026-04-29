"""Fill remaining fields on ASOS page 2 that the adapter missed."""
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

print(f"Page: {page.title()[:60]}")

# 1. Check what's already filled and what's missing
combos = page.get_by_role("combobox").all()
print(f"\n=== Combobox status ({len(combos)} total) ===")
for i, cb in enumerate(combos):
    try:
        val = cb.input_value(timeout=1000)
        # Get label via the adapter's extract_label logic
        aria = cb.evaluate("el => el.getAttribute('aria-label')")
        print(f"  [{i}] val=\"{val[:40]}\" aria=\"{(aria or '')[:50]}\"")
    except Exception as e:
        print(f"  [{i}] error: {e}")

# 2. Check radios
radios = page.get_by_role("radio").all()
print(f"\n=== Radio status ({len(radios)} total) ===")
for i, r in enumerate(radios):
    try:
        checked = r.is_checked()
        val = r.evaluate("el => el.getAttribute('value')")
        # Try to get the question text
        label = r.evaluate("""(el) => {
            let current = el;
            for (let level = 0; level < 6; level++) {
                const root = current.getRootNode();
                if (root === document || !root.host) break;
                const host = root.host;
                if (host.tagName.toLowerCase().startsWith('sr-')) {
                    const innerParent = current.parentElement;
                    if (innerParent) {
                        for (const sib of innerParent.children) {
                            if (sib.tagName === 'SPAN' && sib !== current) {
                                const st = sib.textContent.trim();
                                if (st.length > 3 && st.length < 300) return st;
                            }
                        }
                    }
                    current = host;
                    continue;
                }
                const parent = host.parentElement;
                if (parent) {
                    const lines = parent.textContent.trim().split('\\n')
                        .map(l => l.trim())
                        .filter(l => l.length > 3 && l.length < 300 && l !== '*');
                    if (lines.length > 0) return lines[0];
                }
                current = host;
            }
            return '';
        }""")
        print(f"  [{i}] checked={checked} val=\"{val}\" label=\"{(label or '')[:60]}\"")
    except Exception as e:
        print(f"  [{i}] error: {e}")

# 3. Check text/number fields
texts = page.get_by_role("textbox").all()
print(f"\n=== Text fields ({len(texts)} total) ===")
for i, t in enumerate(texts):
    try:
        val = t.input_value(timeout=1000)
        aria = t.evaluate("el => el.getAttribute('aria-label')")
        print(f"  [{i}] val=\"{val[:40]}\" aria=\"{(aria or '')[:50]}\"")
    except Exception as e:
        print(f"  [{i}] error: {e}")

# 4. Check spinbuttons
spins = page.get_by_role("spinbutton").all()
print(f"\n=== Spinbuttons ({len(spins)} total) ===")
for i, s in enumerate(spins):
    try:
        val = s.input_value(timeout=1000)
        vis = s.is_visible()
        print(f"  [{i}] val=\"{val}\" visible={vis}")
    except Exception as e:
        print(f"  [{i}] error: {e}")

pw.stop()
