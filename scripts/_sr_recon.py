"""One-shot deep DOM recon on the ASOS SmartRecruiters form."""
import json
from playwright.sync_api import sync_playwright

RECON_JS = r"""() => {
    const fields = [];
    const definitions = [];
    let visited = 0;

    function extractLabel(el) {
        const aria = el.getAttribute && el.getAttribute('aria-label');
        if (aria && aria.length > 2) return aria;
        const prev = el.previousElementSibling;
        if (prev) {
            const pt = prev.textContent.trim();
            if (pt.length > 3 && pt.length < 300 && pt !== '*') return pt;
        }
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
                const lines = parent.textContent.trim().split('\n')
                    .map(l => l.trim())
                    .filter(l => l.length > 3 && l.length < 300 && l !== '*');
                if (lines.length > 0) return lines[0];
            }
            current = host;
        }
        let p = el.parentElement;
        for (let j = 0; j < 8 && p; j++) {
            const text = p.textContent.trim();
            if (text.length > 3 && text.length < 200 && text !== '*') return text;
            p = p.parentElement;
        }
        return (el.getAttribute && el.getAttribute('placeholder')) || '';
    }

    function walkNode(node, depth) {
        if (!node || visited > 3000 || depth > 20) return;
        if (!node.tagName) return;
        if (node.tagName === 'IFRAME') return;
        visited++;

        const defAttr = node.getAttribute && node.getAttribute('definition');
        if (defAttr && defAttr.startsWith('[')) {
            try { definitions.push(...JSON.parse(defAttr)); } catch(e) {}
        }

        const tag = node.tagName;
        const role = node.getAttribute && node.getAttribute('role');
        const type = node.getAttribute && node.getAttribute('type');
        const isFile = tag === 'INPUT' && type === 'file';
        const interactiveTags = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
        const interactiveRoles = new Set(['textbox','combobox','radio','checkbox','spinbutton']);
        const isHidden = tag === 'INPUT' && type === 'hidden';
        const isButton = tag === 'BUTTON' || role === 'button';

        if ((interactiveTags.has(tag) || interactiveRoles.has(role) || isFile) && !isHidden && !isButton) {
            let fieldType;
            if (isFile) fieldType = 'file';
            else if (tag === 'TEXTAREA') fieldType = 'textarea';
            else if (role === 'combobox' || tag === 'SELECT') fieldType = 'combobox';
            else if (role === 'radio') fieldType = 'radio';
            else if (role === 'checkbox') fieldType = 'checkbox';
            else if (role === 'spinbutton') fieldType = 'number';
            else fieldType = 'text';

            const visible = !!(node.offsetParent !== null || node.offsetWidth > 0 || isFile);

            let optionText = '';
            if (fieldType === 'radio') {
                const root = node.getRootNode();
                if (root !== document && root.host) {
                    const t = root.host.textContent.trim();
                    if (t && t.length < 50) optionText = t;
                }
                if (!optionText) optionText = node.getAttribute('value') || '';
            }

            fields.push({
                fieldType, visible, depth,
                label: (extractLabel(node) || '').substring(0, 200),
                value: (node.value || '').substring(0, 100),
                checked: !!node.checked,
                optionText,
            });
        }

        if (node.children) {
            for (const child of node.children) walkNode(child, depth);
        }
        if (node.shadowRoot) {
            for (const child of node.shadowRoot.children) walkNode(child, depth + 1);
        }
    }

    walkNode(document.body, 0);
    return { fields, definitions, visited };
}"""

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
result = page.evaluate(RECON_JS)

print(f"Visited: {result['visited']} nodes")
print(f"Fields: {len(result['fields'])}, Definitions: {len(result['definitions'])}")

print("\n--- Visible Fields ---")
for i, rf in enumerate(result["fields"]):
    if not rf.get("visible") and rf["fieldType"] != "file":
        continue
    label = (rf.get("label") or "")[:70]
    ftype = rf["fieldType"]
    val = (rf.get("value") or "")[:30]
    opt = rf.get("optionText", "")[:20]
    print(f"  [{i:2d}] {ftype:10s} d={rf['depth']} val=\"{val}\" opt=\"{opt}\" | \"{label}\"")

print("\n--- Definitions ---")
for d in result["definitions"]:
    label = (d.get("label") or "")[:70]
    dtype = d.get("type", "")
    req = d.get("required", False)
    print(f"  {dtype:10s} req={req} | \"{label}\"")

pw.stop()
