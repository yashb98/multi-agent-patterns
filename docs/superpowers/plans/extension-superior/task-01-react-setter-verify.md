# Task 1: React `nativeInputValueSetter` + Post-Fill Verification

**Files:**
- Modify: `extension/content.js` — `fillField` function (~line 821-856) + add `setNativeValue` utility

**Why:** `fillField()` does `el.value += char` which silently fails on React controlled inputs (60%+ of ATS forms). After typing, it never checks if the value actually stuck — React/Angular can reset on `change` events.

**Dependencies:** None — this is the foundation other tasks build on.

---

- [ ] **Step 1: Add `setNativeValue` utility function**

Add this after the `resolveSelector` function (after line ~320) in `content.js`:

```javascript
/**
 * Set input value using the native setter — bypasses React/Vue/Angular
 * controlled component wrappers that ignore direct .value assignment.
 */
function setNativeValue(el, value) {
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
  if (descriptor && descriptor.set) {
    descriptor.set.call(el, value);
  } else {
    el.value = value;
  }
}
```

- [ ] **Step 2: Rewrite `fillField()` to use native setter + post-fill verify**

Replace the `fillField` function (lines ~821-856) with:

```javascript
async function fillField(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Detect contenteditable (Lever, some Workday forms)
  if (el.getAttribute("contenteditable") === "true" || el.isContentEditable) {
    return fillContentEditable(el, value);
  }

  // Scroll-aware timing: measure if scroll actually happens
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await delay(scrollWait);

  // Visual cursor + highlight
  await moveCursorTo(el);
  highlightElement(el);
  await cursorClickFlash();

  // Smart read-time: longer labels = more reading time
  const labelLength = (el.getAttribute("aria-label") || el.placeholder || "").length;
  const readDelay = Math.min(1500, 200 + labelLength * 15);
  await delay(readDelay);

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing value using native setter (React-safe)
  setNativeValue(el, "");
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type each character with realistic timing variance
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    // Use native setter to build the value char-by-char
    setNativeValue(el, el.value + char);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    const speed = behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
    await delay(Math.max(30, speed));
  }

  // Finalize: blur triggers validation on most forms
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  // ── Post-fill verification ──
  await delay(100); // Let framework process events
  const actualValue = el.value || "";
  const verified = actualValue === value;

  if (!verified && actualValue !== value) {
    // Retry: force-set via native setter + InputEvent (React workaround)
    setNativeValue(el, value);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    await delay(100);

    const retryValue = el.value || "";
    return {
      success: retryValue === value || retryValue.length > 0,
      value_set: retryValue,
      value_verified: retryValue === value,
      retried: true,
    };
  }

  return { success: true, value_set: actualValue, value_verified: true };
}
```

**Note:** This references `fillContentEditable` which is built in Task 2. Until Task 2 is done, the contenteditable branch will throw — that's fine, the function didn't handle it before either.

- [ ] **Step 3: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): React nativeInputValueSetter + post-fill verification

fillField() now uses native prototype setter for React/Vue/Angular
controlled inputs. After typing, verifies value stuck; retries with
InputEvent if framework rejected char-by-char input."
```
