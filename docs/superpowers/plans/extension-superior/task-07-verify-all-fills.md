# Task 7: `value_verified` Flag on ALL Fill Operations

**Files:**
- Modify: `extension/content.js` — add `verifyFieldValue` utility + update return values

**Why:** Task 1 added `value_verified` to `fillField`. But `selectOption`, `checkBox`, `fillRadioGroup`, `fillDate`, `fillAutocomplete`, `fillCombobox` all return `{ success: true }` with no verification. Python needs to know if values actually stuck to retry intelligently.

**Dependencies:** Task 1 (established the pattern)

---

- [ ] **Step 1: Add `verifyFieldValue` utility**

Add after `setNativeValue` in content.js:

```javascript
/**
 * Verify a field's value matches what was intended.
 * Handles: input (el.value), select (selectedOptions),
 * radio (checked state), checkbox (checked state).
 */
function verifyFieldValue(el, intended) {
  if (!el) return false;
  const tag = el.tagName.toLowerCase();

  if (tag === "select") {
    const selected = el.options[el.selectedIndex];
    return selected && (
      normalizeText(selected.text) === normalizeText(intended) ||
      normalizeText(selected.value) === normalizeText(intended)
    );
  }

  if (el.type === "radio") return el.checked;
  if (el.type === "checkbox") {
    const want = intended === "true" || intended === true || intended === "yes";
    return el.checked === want;
  }

  // Text inputs, textareas
  return (el.value || "") === intended ||
    (el.value || "").includes(intended.substring(0, 10));
}
```

- [ ] **Step 2: Update `selectOption` returns**

In `selectOption`, find the two success return paths.

First success (~line 953, after fuzzy match):
```javascript
return { success: true, value_set: match };
```
Replace with:
```javascript
return { success: true, value_set: match, value_verified: verifyFieldValue(el, match) };
```

Second success (~line 963, after value match):
```javascript
return { success: true, value_set: opt.text };
```
Replace with:
```javascript
return { success: true, value_set: opt.text, value_verified: verifyFieldValue(el, opt.text) };
```

- [ ] **Step 3: Update `checkBox` return**

Find the return in `checkBox` (~line 982):
```javascript
return { success: true, value_set: String(el.checked) };
```
Replace with:
```javascript
return { success: true, value_set: String(el.checked), value_verified: el.checked === want };
```

- [ ] **Step 4: Update `fillRadioGroup` return**

Find the success return (~line 1064):
```javascript
return { success: true, value_set: match };
```
Replace with:
```javascript
return { success: true, value_set: match, value_verified: matched.radio.checked };
```

- [ ] **Step 5: Update `fillDate` returns**

Native date return (~line 1391):
```javascript
return { success: true, value_set: isoDate };
```
Replace with:
```javascript
return { success: true, value_set: isoDate, value_verified: el.value === isoDate };
```

Text date return (~line 1417):
```javascript
return { success: true, value_set: formatted };
```
Replace with:
```javascript
const dateVerified = el.value.includes(formatted.substring(0, 4));
return { success: true, value_set: formatted, value_verified: dateVerified };
```

- [ ] **Step 6: Update autocomplete/combobox/custom select returns**

For ALL success returns in `fillCustomSelect`, `fillAutocomplete`, `fillCombobox` — these click an option which is inherently verified. Add `value_verified: true` to each success return object. There are approximately:
- `fillCustomSelect`: 1 success return (~line 1137)
- `fillAutocomplete`: 3 success returns (~lines 1188, 1197, 1208)
- `fillCombobox`: 5 success returns (~lines 1280, 1293, 1329, 1339, 1345 area)

For `fillAutocomplete` no-suggestions fallback:
```javascript
return { success: true, value_set: value, no_suggestions: true, value_verified: el.value === value };
```

For all click-to-select returns, just add `value_verified: true`.

- [ ] **Step 7: Update `fillTagInput` return**

Find return (~line 1372):
```javascript
return { success: true, value_set: added.join(", "), count: added.length };
```
Replace with:
```javascript
return { success: true, value_set: added.join(", "), count: added.length, value_verified: added.length > 0 };
```

- [ ] **Step 8: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): value_verified flag on ALL fill operations

Every fill function now returns value_verified boolean so Python can
detect failed fills and retry. Covers select, radio, checkbox, text,
date, autocomplete, combobox, tag input, and custom select."
```
