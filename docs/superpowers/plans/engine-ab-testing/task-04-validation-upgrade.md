# Task 4: Validation Scanner Upgrade — 3 Missing Strategies

**Files:**
- Modify: `jobpulse/form_engine/validation.py`
- Test: `tests/jobpulse/form_engine/test_validation.py` (extend existing)

**Why:** The Playwright validation scanner only has 2 strategies (aria-invalid, role=alert). The extension has 5. We need parity: add error CSS classes, aria-errormessage, and ATS-specific selectors.

---

- [ ] **Step 1: Add Strategy 3 — error CSS classes**

After the role=alert block (~line 67), add:

```python
    # Strategy 3: Elements with error-related CSS classes
    error_class_els = await page.query_selector_all(
        ".error:not([role='alert']), .field-error, .invalid-feedback, "
        ".form-error, .input-error, .validation-error"
    )
    for el in error_class_els:
        text = await el.text_content()
        if text and text.strip() and len(text.strip()) < 200:
            errors.append(ValidationError(
                field_selector=".error",
                error_message=text.strip(),
            ))
```

- [ ] **Step 2: Add Strategy 4 — aria-errormessage references**

```python
    # Strategy 4: aria-errormessage — element references an error message by ID
    errormsg_els = await page.query_selector_all("[aria-errormessage]")
    for el in errormsg_els:
        err_id = await el.get_attribute("aria-errormessage")
        if err_id:
            err_el = await page.query_selector(f"#{err_id}")
            if err_el:
                text = await err_el.text_content()
                if text and text.strip():
                    el_id = await el.get_attribute("id") or ""
                    errors.append(ValidationError(
                        field_selector=f"#{el_id}" if el_id else "[aria-errormessage]",
                        error_message=text.strip(),
                    ))
```

- [ ] **Step 3: Add Strategy 5 — ATS-specific selectors**

```python
    # Strategy 5: ATS-specific error patterns
    ats_selectors = [
        "[data-automation-id*='error']",           # Workday
        ".application-field--error",                # Greenhouse
        ".application-error",                       # Lever
        "[class*='ErrorMessage']",                  # iCIMS / generic React
    ]
    for sel in ats_selectors:
        ats_els = await page.query_selector_all(sel)
        for el in ats_els:
            text = await el.text_content()
            if text and text.strip():
                errors.append(ValidationError(
                    field_selector=sel,
                    error_message=text.strip(),
                ))
```

- [ ] **Step 4: Deduplicate errors before returning**

Before the return, add dedup:

```python
    # Deduplicate by error message
    seen = set()
    unique_errors = []
    for err in errors:
        if err.error_message not in seen:
            seen.add(err.error_message)
            unique_errors.append(err)
    errors = unique_errors

    logger.debug("validation: found %d errors on page", len(errors))
    return errors
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/validation.py
git commit -m "feat(validation): add 3 missing strategies — error classes, aria-errormessage, ATS-specific

Playwright validation scanner now has 5 strategies matching extension:
aria-invalid, role=alert, error CSS classes, aria-errormessage, ATS selectors."
```
