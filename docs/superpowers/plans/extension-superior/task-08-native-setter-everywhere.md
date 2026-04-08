# Task 8: `nativeInputValueSetter` in All Typing Functions

**Files:**
- Modify: `extension/content.js` — update `fillAutocomplete`, `fillCombobox`, `fillTagInput`, `fillCustomSelect`

**Why:** Task 1 fixed `fillField` to use `setNativeValue`, but 4 other functions still do `el.value += char` which silently fails on React controlled inputs. All typing paths must use the native setter.

**Dependencies:** Task 1 (`setNativeValue` function must exist)

---

- [ ] **Step 1: Update `fillAutocomplete`**

Find in `fillAutocomplete` (~line 1152-1163). Replace all `el.value` assignments:

Replace:
```javascript
el.value = "";
el.dispatchEvent(new Event("input", { bubbles: true }));
await delay(200);

const typeText = value.substring(0, Math.min(value.length, 5));
for (const char of typeText) {
  el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
  el.value += char;
  el.dispatchEvent(new Event("input", { bubbles: true }));
```

With:
```javascript
setNativeValue(el, "");
el.dispatchEvent(new Event("input", { bubbles: true }));
await delay(200);

const typeText = value.substring(0, Math.min(value.length, 5));
for (const char of typeText) {
  el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
  setNativeValue(el, el.value + char);
  el.dispatchEvent(new Event("input", { bubbles: true }));
```

Also find the fallback path (~line 1201-1203):
```javascript
el.value = value;
```
Replace with:
```javascript
setNativeValue(el, value);
```

- [ ] **Step 2: Update `fillCombobox` inner input typing**

Find in `fillCombobox` (~line 1309-1314):

Replace:
```javascript
innerInput.value = "";
for (const char of value) {
  innerInput.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
  innerInput.value += char;
```

With:
```javascript
setNativeValue(innerInput, "");
for (const char of value) {
  innerInput.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
  setNativeValue(innerInput, innerInput.value + char);
```

- [ ] **Step 3: Update `fillTagInput`**

Find in `fillTagInput` (~line 1357-1362):

Replace:
```javascript
el.value = "";
el.dispatchEvent(new Event("input", { bubbles: true }));

for (const char of val) {
  el.value += char;
```

With:
```javascript
setNativeValue(el, "");
el.dispatchEvent(new Event("input", { bubbles: true }));

for (const char of val) {
  setNativeValue(el, el.value + char);
```

- [ ] **Step 4: Update `fillCustomSelect` search input**

Find in `fillCustomSelect` (~line 1084-1091):

Replace:
```javascript
searchInput.value = "";
searchInput.dispatchEvent(new Event("input", { bubbles: true }));
const filterText = value.substring(0, Math.min(value.length, 5));
for (const char of filterText) {
  searchInput.value += char;
```

With:
```javascript
setNativeValue(searchInput, "");
searchInput.dispatchEvent(new Event("input", { bubbles: true }));
const filterText = value.substring(0, Math.min(value.length, 5));
for (const char of filterText) {
  setNativeValue(searchInput, searchInput.value + char);
```

- [ ] **Step 5: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): nativeInputValueSetter across all typing functions

fillAutocomplete, fillCombobox, fillTagInput, fillCustomSelect now use
setNativeValue() instead of direct el.value assignment. Fixes React
controlled inputs in autocomplete/typeahead/tag fields."
```
