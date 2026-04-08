# Task 6: Scroll-Aware Timing + Smart Field-to-Field Delays

**Files:**
- Modify: `extension/content.js` — add `smartScroll` + `getFieldGap` utilities, update all fill functions

**Why:** `behaviorProfile.field_to_field_gap` is hardcoded at 500ms everywhere. Real humans read labels before filling — short labels (Name) take 300ms, long screening questions take 1500ms. Also, `scrollIntoView` waits a flat 300ms regardless of whether a scroll actually happened.

**Dependencies:** None (standalone utilities)

---

- [ ] **Step 1: Add `smartScroll` and `getFieldGap` utilities**

Add after `behaviorProfile` object (~line 32):

```javascript
/**
 * Calculate field-to-field gap based on label complexity.
 * Short labels (Name, Email) = fast. Long screening questions = slow.
 */
function getFieldGap(labelText) {
  const len = (labelText || "").length;
  if (len < 10) return 300 + Math.random() * 200;   // Simple: 300-500ms
  if (len < 40) return 500 + Math.random() * 300;   // Medium: 500-800ms
  if (len < 100) return 800 + Math.random() * 500;  // Long: 800-1300ms
  return 1200 + Math.random() * 500;                 // Screening Q: 1200-1700ms
}

/**
 * Scroll element into view and wait proportional to distance scrolled.
 * Short scrolls get short waits. No scroll needed = near-zero wait.
 */
async function smartScroll(el) {
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await delay(scrollWait);
  return scrollWait;
}
```

- [ ] **Step 2: Update `fillRadioGroup` to use `smartScroll` + `getFieldGap`**

In `fillRadioGroup`, find (~line 1060-1061):

```javascript
target.scrollIntoView({ behavior: "smooth", block: "center" });
await delay(behaviorProfile.reading_pause * 300 * (0.5 + Math.random()));
```

Replace with:

```javascript
await smartScroll(target);
await delay(getFieldGap(match));
```

- [ ] **Step 3: Update `fillCustomSelect` to use `smartScroll`**

Find (~line 1074-1075):

```javascript
trigger.scrollIntoView({ behavior: "smooth", block: "center" });
await delay(behaviorProfile.field_to_field_gap);
```

Replace with:

```javascript
await smartScroll(trigger);
```

- [ ] **Step 4: Update `fillAutocomplete` to use `smartScroll`**

Find (~line 1147-1148):

```javascript
el.scrollIntoView({ behavior: "smooth", block: "center" });
await delay(behaviorProfile.field_to_field_gap);
```

Replace with:

```javascript
await smartScroll(el);
```

- [ ] **Step 5: Update `fillTagInput` to use `smartScroll`**

Find (~line 1351-1352):

```javascript
el.scrollIntoView({ behavior: "smooth", block: "center" });
await delay(behaviorProfile.field_to_field_gap);
```

Replace with:

```javascript
await smartScroll(el);
```

- [ ] **Step 6: Update `fillDate` to use `smartScroll`**

Find (~line 1379-1380):

```javascript
el.scrollIntoView({ behavior: "smooth", block: "center" });
await delay(behaviorProfile.field_to_field_gap);
```

Replace with:

```javascript
await smartScroll(el);
```

- [ ] **Step 7: Update `fillCombobox` to use `smartScroll`**

Find in `fillCombobox` (~line 1224):

```javascript
el.scrollIntoView({ behavior: "smooth", block: "center" });
await delay(300);
```

Replace with:

```javascript
await smartScroll(el);
```

- [ ] **Step 8: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): scroll-aware timing + dynamic field-to-field delay

smartScroll() waits proportional to actual scroll distance (0-800ms).
getFieldGap() varies delay by label length (300ms for Name to 1700ms
for screening questions). Replaces flat 500ms behaviorProfile gap."
```
