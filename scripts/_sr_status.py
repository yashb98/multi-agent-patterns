"""Final status check of all fields on ASOS page 2."""
from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.connect_over_cdp("http://localhost:9222")
ctx = browser.contexts[0]

page = None
for p in ctx.pages:
    if "oneclick-ui" in p.url:
        page = p
        break

print(f"Page: {page.title()[:60]}\n")

# Run the deep recon to get current state
from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersAdapter
adapter = SmartRecruitersAdapter()

# Use the simpler recon from _sr_recon.py
import scripts._sr_recon as recon_mod
result = page.evaluate(recon_mod.RECON_JS)

print("=== All Visible Fields ===")
for i, rf in enumerate(result["fields"]):
    if not rf.get("visible") and rf["fieldType"] != "file":
        continue
    label = (rf.get("label") or "")[:65]
    ftype = rf["fieldType"]
    val = (rf.get("value") or "")[:40]
    chk = rf.get("checked", False)
    opt = rf.get("optionText", "")[:20]
    status = "OK" if val or chk else "EMPTY"
    if ftype == "radio":
        status = "CHECKED" if chk else "unchecked"
    print(f"  [{i:2d}] {status:9s} {ftype:10s} | val=\"{val}\" | \"{label}\"")

# Check for validation errors
errors = page.evaluate("""() => {
    const errors = [];
    function walk(node, depth) {
        if (!node || depth > 12) return;
        if (node.tagName) {
            const text = node.textContent || '';
            const cls = node.className || '';
            if (text.includes('required') && (cls.includes('error') || cls.includes('invalid') || cls.includes('alert'))) {
                errors.push(text.trim().substring(0, 100));
            }
        }
        if (node.children) for (const c of node.children) walk(c, depth);
        if (node.shadowRoot) for (const c of node.shadowRoot.children) walk(c, depth + 1);
    }
    walk(document.body, 0);
    return errors;
}""")

if errors:
    print("\n=== Validation Errors ===")
    for e in errors:
        print(f"  {e}")

pw.stop()
