# Task 3: Post-Fill Verification in All Playwright Fillers

**Files:**
- Modify: `jobpulse/form_engine/text_filler.py`
- Modify: `jobpulse/form_engine/select_filler.py`
- Modify: `jobpulse/form_engine/checkbox_filler.py`
- Modify: `jobpulse/form_engine/radio_filler.py`
- Modify: `jobpulse/form_engine/date_filler.py`
- Modify: `jobpulse/form_engine/multi_select_filler.py`

**Why:** Every filler must set `value_verified` on its `FillResult` by reading back the value after filling. This is what makes A/B comparison meaningful — we can measure if fills actually stick.

**Dependencies:** Task 2 (value_verified field must exist on FillResult)

---

- [ ] **Step 1: Update `text_filler.fill_text` — add verification after fill**

After `await el.fill(fill_value)` (line 50), add read-back:

```python
        await el.fill(fill_value)

        # Verify value stuck
        actual = await el.evaluate("el => el.value || ''")
        verified = actual == fill_value or fill_value[:10] in actual

        logger.debug("text_filler: filled %s (%d chars, verified=%s)", selector, len(fill_value), verified)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=fill_value, value_verified=verified,
        )
```

- [ ] **Step 2: Update `select_filler.fill_select` — verify selectedIndex**

After `await page.select_option(selector, label=match)` (line 104), add:

```python
        await page.select_option(selector, label=match)
        verified = await page.eval_on_selector(
            selector, "el => el.options[el.selectedIndex]?.text?.trim() || ''"
        )
        logger.debug("select_filler: filled %s with '%s'", selector, match)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=match,
            value_verified=_normalize(verified) == _normalize(match),
        )
```

- [ ] **Step 3: Update `checkbox_filler.fill_checkbox` — verify checked state**

After `await el.check()` / `await el.uncheck()` (line 66), add verification:

```python
        actual = await el.is_checked()
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(should_check), value_set=str(should_check),
            value_verified=actual == should_check,
        )
```

- [ ] **Step 4: Update `radio_filler.fill_radio_group` — verify checked**

After `await radio.click()` (line 94), verify:

```python
                await radio.click()
                actual_checked = await radio.is_checked()
                return FillResult(
                    success=True, selector=group_selector,
                    value_attempted=value, value_set=match,
                    value_verified=actual_checked,
                )
```

- [ ] **Step 5: Update `date_filler.fill_date` — verify value**

After native date fill (line 82) and text date fill (line 94), add verification:

Native: `value_verified=(await el.evaluate("el => el.value") == value)`
Text: `value_verified=(formatted[:4] in await el.evaluate("el => el.value || ''"))`

- [ ] **Step 6: Update `multi_select_filler.fill_tag_input` — verify count**

After the tag loop, change return to:
```python
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(values), value_set=str(added),
            value_verified=len(added) > 0,
        )
```

- [ ] **Step 7: Commit**

```bash
git add jobpulse/form_engine/text_filler.py jobpulse/form_engine/select_filler.py \
  jobpulse/form_engine/checkbox_filler.py jobpulse/form_engine/radio_filler.py \
  jobpulse/form_engine/date_filler.py jobpulse/form_engine/multi_select_filler.py
git commit -m "feat(form_engine): post-fill verification on all Playwright fillers

Every filler now reads back the value after filling and sets value_verified
on the FillResult. Covers text, select, checkbox, radio, date, tag input."
```
