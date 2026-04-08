# Task 6: PlaywrightDriver — All Fill Methods

**Files:**
- Modify: `jobpulse/playwright_driver.py`

**Why:** Implement fill, click, select_option, check_box, fill_radio, fill_date, fill_autocomplete, fill_contenteditable, upload_file, and scan_validation_errors using Playwright's native API with post-fill verification and retry.

**Dependencies:** Task 5 (core driver must exist)

---

- [ ] **Step 1: Add retry helper at module level**

```python
async def _with_retry(fn, max_retries=2, delay_ms=500):
    """Retry an async function on transient errors."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                await asyncio.sleep(delay_ms / 1000)
    return {"success": False, "error": str(last_exc), "retry_count": max_retries}
```

- [ ] **Step 2: Add fill() and click()**

```python
    async def fill(self, selector: str, value: str) -> dict:
        async def _do():
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element {selector} not found"}
            await el.scroll_into_view_if_needed()
            await el.fill(value)
            actual = await el.evaluate("el => el.value || ''")
            verified = actual == value or value[:10] in actual
            return {"success": True, "value_set": value, "value_verified": verified}
        return await _with_retry(_do)

    async def click(self, selector: str) -> dict:
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await el.scroll_into_view_if_needed()
        await el.click()
        return {"success": True}
```

- [ ] **Step 3: Add select_option() and check_box()**

```python
    async def select_option(self, selector: str, value: str) -> dict:
        async def _do():
            options = await self._page.eval_on_selector_all(
                f"{selector} option", "els => els.map(e => e.textContent.trim())"
            )
            match = _fuzzy_match(value, options)
            if not match:
                return {"success": False, "error": f"No match for '{value}' in {options[:5]}"}
            await self._page.select_option(selector, label=match)
            actual = await self._page.eval_on_selector(
                selector, "el => el.options[el.selectedIndex]?.text?.trim() || ''"
            )
            return {"success": True, "value_set": match, "value_verified": match.lower() == actual.lower()}
        return await _with_retry(_do)

    async def check_box(self, selector: str, checked: bool) -> dict:
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        if checked:
            await el.check()
        else:
            await el.uncheck()
        actual = await el.is_checked()
        return {"success": True, "value_set": str(checked), "value_verified": actual == checked}
```

- [ ] **Step 4: Add fill_radio(), fill_date()**

```python
    async def fill_radio(self, selector: str, value: str) -> dict:
        radios = await self._page.query_selector_all(selector)
        if not radios:
            return {"success": False, "error": "No radio elements found"}
        for radio in radios:
            label = await radio.evaluate(
                "el => el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || el.parentElement?.textContent?.trim() || ''"
            )
            if value.lower() in label.lower():
                await radio.click()
                checked = await radio.is_checked()
                return {"success": True, "value_set": label, "value_verified": checked}
        return {"success": False, "error": f"No radio matching '{value}'"}

    async def fill_date(self, selector: str, value: str) -> dict:
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        input_type = await el.get_attribute("type")
        await el.scroll_into_view_if_needed()
        await el.fill(value)
        actual = await el.evaluate("el => el.value || ''")
        return {"success": True, "value_set": value, "value_verified": value[:4] in actual}
```

- [ ] **Step 5: Add fill_autocomplete(), fill_contenteditable(), upload_file()**

```python
    async def fill_autocomplete(self, selector: str, value: str) -> dict:
        async def _do():
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element {selector} not found"}
            await el.scroll_into_view_if_needed()
            await el.fill("")
            await el.type(value[:5] if len(value) >= 5 else value, delay=80)
            await self._page.wait_for_timeout(1500)
            suggestions = await self._page.query_selector_all("li, [role='option']")
            for sug in suggestions:
                text = await sug.text_content()
                if text and value.lower() in text.strip().lower():
                    await sug.click()
                    return {"success": True, "value_set": text.strip(), "value_verified": True}
            await el.fill(value)
            actual = await el.evaluate("el => el.value || ''")
            return {"success": True, "value_set": value, "value_verified": actual == value, "no_suggestions": True}
        return await _with_retry(_do)

    async def fill_contenteditable(self, selector: str, value: str) -> dict:
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await el.click()
        await self._page.evaluate("document.execCommand('selectAll', false, null)")
        await self._page.evaluate("document.execCommand('delete', false, null)")
        for char in value:
            await self._page.evaluate(f"document.execCommand('insertText', false, {repr(char)})")
            await asyncio.sleep(0.03 + 0.05 * __import__('random').random())
        actual = await el.text_content() or ""
        return {"success": True, "value_set": value, "value_verified": value[:10] in actual}

    async def upload_file(self, selector: str, path: str) -> dict:
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await el.set_input_files(path)
        return {"success": True, "value_set": path}
```

- [ ] **Step 6: Add scan_validation_errors()**

```python
    async def scan_validation_errors(self) -> dict:
        from jobpulse.form_engine.validation import scan_for_errors
        errors = await scan_for_errors(self._page)
        return {
            "errors": [{"field_selector": e.field_selector, "error_message": e.error_message} for e in errors],
            "has_errors": len(errors) > 0,
            "count": len(errors),
        }
```

- [ ] **Step 7: Add `_fuzzy_match` helper at module level**

```python
def _fuzzy_match(value: str, options: list[str]) -> str | None:
    v = value.lower().strip()
    for opt in options:
        if opt.lower().strip() == v:
            return opt
    for opt in options:
        if opt.lower().strip().startswith(v):
            return opt
    for opt in options:
        if v in opt.lower().strip():
            return opt
    return None
```

- [ ] **Step 8: Commit**

```bash
git add jobpulse/playwright_driver.py
git commit -m "feat: PlaywrightDriver fill methods — all 10 fill operations with verification and retry"
```
