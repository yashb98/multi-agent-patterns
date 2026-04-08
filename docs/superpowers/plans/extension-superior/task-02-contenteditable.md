# Task 2: Contenteditable / Rich Text Editor Support

**Files:**
- Modify: `extension/content.js` — add `fillContentEditable` function after `fillField`

**Why:** `fillField()` targets `el.value` but `contenteditable` elements (used by Lever cover letter fields, some Workday forms) don't have `.value`. They use `innerText`/`innerHTML`. The previous `fillField` would silently fail on these.

**Dependencies:** Task 1 (fillField now delegates to this function for contenteditable elements)

---

- [ ] **Step 1: Add `fillContentEditable` function**

Add immediately after the `fillField` function in `content.js`:

```javascript
/**
 * Fill a contenteditable element (Lever cover letter, Workday rich text).
 * Uses document.execCommand('insertText') which preserves undo stack
 * and triggers framework event listeners correctly.
 */
async function fillContentEditable(el, value) {
  if (!el) return { success: false, error: "Contenteditable element is null" };

  // Scroll-aware timing
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await delay(scrollWait);

  await moveCursorTo(el);
  highlightElement(el);
  await cursorClickFlash();

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing content
  el.innerText = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type character by character using execCommand
  // execCommand('insertText') preserves undo stack + triggers framework events
  for (const char of value) {
    document.execCommand("insertText", false, char);
    const speed = behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
    await delay(Math.max(30, speed));
  }

  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  // Verify — check innerText contains at least the start of value
  const actualText = (el.innerText || el.textContent || "").trim();
  const verified = actualText.includes(value.substring(0, 20));

  return {
    success: true,
    value_set: actualText,
    value_verified: verified,
    contenteditable: true,
  };
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): contenteditable support for Lever/Workday rich text

Uses document.execCommand('insertText') per character to preserve undo
stack and trigger framework events. Handles cover letter and screening
question fields that use contenteditable instead of textarea."
```
