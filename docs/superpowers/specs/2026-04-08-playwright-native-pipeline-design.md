# Playwright Native Pipeline — Design Spec

## Goal

Refactor the PlaywrightDriver to use Playwright's native APIs (locators, accessibility tree, auto-waiting) instead of the extension's snapshot-based approach. The orchestrator stays as the shared entry point — only the form-filling loop diverges when `engine="playwright"`.

## Architecture

```
Orchestrator (shared for both engines):
  cookie dismiss → SSO → login → navigate → _fill_application()

_fill_application() branches:
  engine="extension" → state machine + snapshots (unchanged)
  engine="playwright" → _fill_native() (new)
```

The orchestrator handles lifecycle (cookies, SSO, login, navigation learning, stuck detection, anti-detection timing). Only `_fill_application` diverges — extension uses state machine + snapshots, Playwright uses native locators + LLM calls.

## _fill_native() — Per-Page Loop

```python
async def _fill_native(self, platform, cv_path, cl_path, profile, custom_answers, dry_run):
    for page_num in range(1, MAX_FORM_PAGES + 1):
        # 1. Scan fields natively (no snapshot)
        fields = await self._scan_fields_native()

        # 2. Detect if confirmation page → done
        if await self._is_confirmation_page():
            return {"success": True, "pages_filled": page_num}

        # 3. LLM call: map fields → values (Call 1)
        mapping = await self._llm_map_fields(fields, profile, custom_answers, platform)

        # 4. Screening questions if unresolved (Call 2, optional)
        unresolved = [f for f in fields if f["label"] not in mapping and f["type"] != "file"]
        if unresolved:
            screening = await self._llm_screening(unresolved, custom_answers.get("_job_context"))
            mapping.update(screening)

        # 5. Fill each field by label (top-to-bottom DOM order)
        for label, value in mapping.items():
            await self._fill_by_label(label, value)

        # 6. Handle file uploads (deterministic, no LLM)
        await self._upload_files_native(cv_path, cl_path)

        # 7. Auto-check consent boxes
        await self._check_consent_native()

        # 8. Anti-detection timing
        await self._enforce_page_timing(platform)

        # 9. Pre-submit review on final page (Call 3)
        if await self._is_submit_page():
            if dry_run:
                return {"success": True, "dry_run": True, "pages_filled": page_num}
            review = await self._llm_review()
            if not review.get("pass"):
                logger.warning("Pre-submit review failed: %s", review.get("issues"))

        # 10. Click next/continue/submit
        clicked = await self._click_navigation_button(dry_run)
        if clicked == "submitted":
            return {"success": True, "pages_filled": page_num}
        if clicked == "dry_run_stop":
            return {"success": True, "dry_run": True, "pages_filled": page_num}
        if not clicked:
            return {"success": False, "error": f"No navigation button on page {page_num}"}
```

## Native Field Scanning

Uses Playwright's role-based locators instead of JS eval snapshot:

```python
async def _scan_fields_native(self) -> list[dict]:
    fields = []

    # Text inputs (textbox role covers input[type=text/email/tel/number/etc])
    for locator in await self._page.get_by_role("textbox").all():
        label = await self._get_accessible_name(locator)
        fields.append({"label": label, "type": "text", "locator": locator,
                       "value": await locator.input_value(),
                       "required": await locator.get_attribute("required") is not None})

    # Dropdowns (combobox role = native <select>)
    for locator in await self._page.get_by_role("combobox").all():
        label = await self._get_accessible_name(locator)
        options = await locator.locator("option").all_text_contents()
        fields.append({"label": label, "type": "select", "locator": locator,
                       "options": options, "value": await locator.input_value()})

    # Radio groups
    for locator in await self._page.get_by_role("radiogroup").all():
        label = await self._get_accessible_name(locator)
        options = await locator.get_by_role("radio").all()
        option_labels = [await self._get_accessible_name(o) for o in options]
        fields.append({"label": label, "type": "radio", "options": option_labels,
                       "locator": locator})

    # Checkboxes
    for locator in await self._page.get_by_role("checkbox").all():
        label = await self._get_accessible_name(locator)
        fields.append({"label": label, "type": "checkbox", "locator": locator,
                       "checked": await locator.is_checked()})

    # Textareas
    for locator in await self._page.locator("textarea:visible").all():
        label = await self._get_accessible_name(locator)
        fields.append({"label": label, "type": "textarea", "locator": locator,
                       "value": await locator.input_value()})

    # File inputs
    for locator in await self._page.locator("input[type='file']").all():
        label = await self._get_accessible_name(locator)
        fields.append({"label": label, "type": "file", "locator": locator})

    return fields
```

### _get_accessible_name

Extracts the label that a screen reader would announce:

```python
async def _get_accessible_name(self, locator) -> str:
    return await locator.evaluate(
        "el => el.labels?.[0]?.textContent?.trim() || "
        "el.getAttribute('aria-label') || "
        "el.placeholder || ''"
    )
```

## LLM Calls (3-5 per application)

### Call 1: Field Mapping (every page, ~$0.001)

```python
async def _llm_map_fields(self, fields, profile, custom_answers, platform) -> dict:
    field_descriptions = []
    for f in fields:
        if f["type"] == "file":
            continue
        desc = f"- {f['label']} ({f['type']})"
        if f.get("options"):
            desc += f" options: {f['options'][:10]}"
        if f.get("value"):
            desc += f" [already filled: {f['value']}]"
        if f.get("required"):
            desc += " *required"
        field_descriptions.append(desc)

    prompt = f"""Map profile data to form fields. Return JSON {{"label": "value"}}.
Skip already-filled fields. Skip file upload fields.

Fields:
{chr(10).join(field_descriptions)}

Profile: {json.dumps(profile)}
Platform: {platform}
Known answers: {json.dumps(custom_answers)}"""

    response = await smart_llm_call(prompt, model="gpt-4.1-mini")
    return json.loads(response)
```

### Call 2: Screening Questions (optional, only when Call 1 has unresolved fields)

```python
async def _llm_screening(self, unresolved_fields, job_context) -> dict:
    prompt = f"""Answer these screening questions for a job application.
Context: {job_context}

{chr(10).join(f"Q: {f['label']} Options: {f.get('options', 'free text')}" for f in unresolved_fields)}

Return JSON {{"label": "answer"}}. Be truthful."""

    response = await smart_llm_call(prompt, model="gpt-4.1-mini")
    return json.loads(response)
```

### Call 3: Pre-Submit Review (once, final page, screenshot-based, ~$0.003)

```python
async def _llm_review(self) -> dict:
    screenshot = await self._page.screenshot(type="png")
    prompt = ("Review this filled application form. Any empty required fields, "
              "wrong values, or mismatches? Return {\"pass\": true} or "
              "{\"pass\": false, \"issues\": [...]}")

    response = await smart_llm_call(prompt, model="gpt-4.1-mini", images=[screenshot])
    return json.loads(response)
```

### Calls 4-5 (rare, only when needed)

- **Complex navigation**: when multiple ambiguous buttons exist, ask LLM which to click
- **Unknown field types**: custom widgets that don't match any role, ask LLM how to interact

## Fill by Label

```python
async def _fill_by_label(self, label: str, value: str):
    await asyncio.sleep(_get_field_gap(label))

    # Try label-based locator first
    locator = self._page.get_by_label(label, exact=False)

    if not await locator.count():
        # Fallback: placeholder text
        locator = self._page.get_by_placeholder(label, exact=False)

    if not await locator.count():
        logger.warning("No field found for label '%s'", label)
        return {"success": False, "error": f"No field for '{label}'"}

    el = locator.first
    await self._smart_scroll(el)
    await self._move_mouse_to(el)

    tag = await el.evaluate("el => el.tagName.toLowerCase()")
    input_type = await el.get_attribute("type") or ""

    if tag == "select":
        await el.select_option(label=value)
    elif input_type == "checkbox":
        if value.lower() in ("true", "yes"):
            await el.check()
        else:
            await el.uncheck()
    elif input_type == "radio":
        await self._page.get_by_label(value).check()
    else:
        await el.fill(value)

    # Post-fill verification
    if tag == "select":
        actual = await el.evaluate("el => el.options[el.selectedIndex]?.text?.trim() || ''")
    elif input_type in ("checkbox", "radio"):
        actual = str(await el.is_checked())
    else:
        actual = await el.input_value()

    verified = value[:10].lower() in actual.lower() if actual else False
    return {"success": True, "value_set": value, "value_verified": verified}
```

## Navigation — Native Button Detection

```python
async def _click_navigation_button(self, dry_run: bool) -> str:
    button_names = [
        ("submit", ["Submit Application", "Submit", "Apply"]),
        ("next", ["Save & Continue", "Continue", "Next", "Proceed"]),
    ]

    for action, names in button_names:
        for name in names:
            btn = self._page.get_by_role("button", name=name, exact=False)
            if await btn.count() and await btn.first.is_visible():
                if action == "submit" and dry_run:
                    return "dry_run_stop"
                await self._move_mouse_to(btn.first)
                await btn.first.click()
                await self._page.wait_for_load_state("networkidle", timeout=10000)
                return "submitted" if action == "submit" else "next"

    # Fallback: links with submit-like text
    for name in ["Submit", "Apply Now", "Continue"]:
        link = self._page.get_by_role("link", name=name, exact=False)
        if await link.count() and await link.first.is_visible():
            await link.first.click()
            await self._page.wait_for_load_state("networkidle", timeout=10000)
            return "next"

    return ""
```

## Page Detection (No State Machine)

```python
async def _is_confirmation_page(self) -> bool:
    """Check if current page is a confirmation/thank-you page."""
    body = await self._page.locator("body").text_content()
    body_lower = (body or "").lower()[:2000]
    return any(phrase in body_lower for phrase in (
        "thank you for applying", "application has been received",
        "application submitted", "successfully submitted",
    ))

async def _is_submit_page(self) -> bool:
    """Check if current page has a submit button (final page)."""
    for name in ["Submit Application", "Submit", "Apply"]:
        btn = self._page.get_by_role("button", name=name, exact=False)
        if await btn.count() and await btn.first.is_visible():
            return True
    return False
```

## File Uploads (Deterministic)

```python
async def _upload_files_native(self, cv_path, cl_path):
    file_inputs = await self._page.locator("input[type='file']").all()
    cv_uploaded = False

    for fi in file_inputs:
        label = await self._get_accessible_name(fi)
        label_lower = label.lower()

        if "autofill" in label_lower or "drag and drop" in label_lower:
            continue

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)

        if "cover" in label_lower and cl_path:
            await fi.set_input_files(str(cl_path))
        elif cv_path and not cv_uploaded:
            await fi.set_input_files(str(cv_path))
            cv_uploaded = True
```

## Consent Boxes

```python
async def _check_consent_native(self):
    consent_keywords = ["agree", "consent", "terms", "privacy", "accept", "acknowledge"]
    checkboxes = await self._page.get_by_role("checkbox").all()

    for cb in checkboxes:
        label = await self._get_accessible_name(cb)
        if any(kw in label.lower() for kw in consent_keywords):
            if not await cb.is_checked():
                await cb.check()
```

## What Stays Shared

| Component | Shared? | Notes |
|---|---|---|
| Orchestrator lifecycle | Yes | Cookie dismiss, SSO, login, navigation learning |
| TrackedDriver wrapper | Yes | Logs all fill calls to ABTracker |
| Engine-tagged learning | Yes | PatternStore + GotchasDB filter by engine |
| Human-like timing | Yes | Bezier mouse, scroll delays, field gaps |
| Validation scanner | Yes | 5-strategy scanner used in pre-submit |
| Telegram dashboard | Yes | engine stats/compare/learning commands |

## What's Different per Engine

| Aspect | Extension | Playwright Native |
|---|---|---|
| Field discovery | JS snapshot via WebSocket | `get_by_role` locators |
| Field data model | `PageSnapshot` + `FieldInfo` | `list[dict]` with labels + locators |
| Page classification | State machine | Native button/heading detection |
| Action format | `Action(selector="#input-37")` | `{label: "Email", value: "..."}` |
| Fill method | `bridge.fill("#input-37")` | `page.get_by_label("Email").fill()` |
| LLM input | Snapshot JSON | Field labels + types (no selectors) |
| Navigation | `find_next_button(snapshot)` | `get_by_role("button", name="Submit")` |
| Wait strategy | Manual `wait_for_timeout` | Playwright auto-wait |

## File Map

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | Add `_fill_native()` branch in `_fill_application()`. Add all native helper methods. |
| `jobpulse/playwright_driver.py` | Keep as-is for TrackedDriver compatibility. Native methods live in orchestrator. |
| `tests/jobpulse/test_native_pipeline.py` | Tests for field scanning, LLM mapping, fill-by-label, navigation |

## Cost

- LLM calls: 3-5 per application at ~$0.001-0.003 each = $0.003-0.015 total
- Same order of magnitude as the current extension pipeline
- Fewer calls than extension (which does snapshot → LLM per page for screening)
