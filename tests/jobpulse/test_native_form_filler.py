"""Tests for NativeFormFiller — Playwright native pipeline."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_filler(page_mock=None, driver_mock=None):
    """Create a NativeFormFiller with mocked dependencies."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = page_mock or MagicMock()
    driver = driver_mock or AsyncMock()
    driver.page = page
    return NativeFormFiller(page=page, driver=driver)


# ── _get_accessible_name ──


@pytest.mark.asyncio
async def test_get_accessible_name_returns_label():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="Email Address")

    result = await filler._get_accessible_name(locator)
    assert result == "Email Address"
    locator.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_get_accessible_name_empty_fallback():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="")

    result = await filler._get_accessible_name(locator)
    assert result == ""


# ── _scan_fields ──


@pytest.mark.asyncio
async def test_scan_fields_text_inputs():
    """Scans textbox role elements and returns field dicts."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    textbox = AsyncMock()
    textbox.input_value = AsyncMock(return_value="")
    textbox.get_attribute = AsyncMock(return_value=None)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[textbox])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role

    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="First Name"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["label"] == "First Name"
    assert fields[0]["type"] == "text"
    assert fields[0]["locator"] is textbox


@pytest.mark.asyncio
async def test_scan_fields_select_with_options():
    """Scans combobox (select) elements and captures options."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    select_el = AsyncMock()
    select_el.input_value = AsyncMock(return_value="")
    select_el.evaluate = AsyncMock(return_value="select")
    option_locator = MagicMock()
    option_locator.all_text_contents = AsyncMock(return_value=["USA", "UK", "Canada"])
    select_el.locator = lambda sel: option_locator

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[select_el])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="Country"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "select"
    assert fields[0]["options"] == ["USA", "UK", "Canada"]


@pytest.mark.asyncio
async def test_scan_fields_checkbox():
    """Scans checkbox elements with checked state."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="Agree to terms"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "checkbox"
    assert fields[0]["checked"] is False


# ── _fill_by_label ──


@pytest.mark.asyncio
async def test_fill_by_label_text_input():
    """Fills a text field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="john@example.com")

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Email", "john@example.com")

    assert result["success"] is True
    el.fill.assert_called_once_with("john@example.com")


@pytest.mark.asyncio
async def test_fill_by_label_select():
    """Fills a select field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(side_effect=["input", "select", "United States"])
    el.get_attribute = AsyncMock(return_value=None)
    el.select_option = AsyncMock()
    option_locator = AsyncMock()
    option_locator.all_text_contents = AsyncMock(return_value=["United States", "Canada", "UK"])
    el.locator = MagicMock(return_value=option_locator)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country", "United States")

    assert result["success"] is True
    el.select_option.assert_called_once_with(label="United States", timeout=5000)


@pytest.mark.asyncio
async def test_fill_by_label_not_found():
    """Returns error when no field matches label or placeholder."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=empty_locator)

    with patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Nonexistent", "value")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_fill_by_label_checkbox():
    """Checks a checkbox found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value="checkbox")
    el.check = AsyncMock()
    el.is_checked = AsyncMock(return_value=True)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("I agree", "yes")

    assert result["success"] is True
    el.check.assert_called_once()


@pytest.mark.asyncio
async def test_fill_by_label_placeholder_fallback():
    """Falls back to placeholder when label locator finds nothing."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="test")

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    placeholder_locator = MagicMock()
    placeholder_locator.count = AsyncMock(return_value=1)
    placeholder_locator.nth = MagicMock(return_value=el)
    placeholder_locator.first = el

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=placeholder_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Search", "test")

    assert result["success"] is True
    page.get_by_placeholder.assert_called_once()


# ── _map_fields (LLM Call 1) ──


@pytest.mark.asyncio
async def test_map_fields_basic():
    """Maps profile data to form fields via LLM."""
    filler = _make_filler()
    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Phone", "type": "text", "value": "", "required": False},
        {"label": "Resume", "type": "file"},
    ]
    profile = {"email": "test@example.com", "phone": "+44123456789"}

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Email": "test@example.com", "Phone": "+44123456789"}'

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._map_fields(fields, profile, {}, "greenhouse")

    assert result == {"Email": "test@example.com", "Phone": "+44123456789"}


@pytest.mark.asyncio
async def test_map_fields_skips_file_fields():
    """File fields are excluded from the LLM prompt."""
    filler = _make_filler()
    fields = [
        {"label": "Resume", "type": "file"},
    ]

    result = await filler._map_fields(fields, {}, {}, "linkedin")
    assert result == {}


@pytest.mark.asyncio
async def test_map_fields_includes_options():
    """Dropdown options are passed in the prompt."""
    filler = _make_filler()
    fields = [
        {"label": "Country", "type": "select", "options": ["USA", "UK"], "value": ""},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Country": "UK"}'

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._map_fields(fields, {}, {}, "greenhouse")

    assert result == {"Country": "UK"}
    prompt = mock_openai.return_value.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "USA" in prompt


# ── _screen_questions (LLM Call 2) ──


@pytest.mark.asyncio
async def test_screen_questions_basic():
    filler = _make_filler()
    unresolved = [
        {"label": "Are you authorized to work in the UK?", "type": "radio",
         "options": ["Yes", "No"]},
        {"label": "Expected salary", "type": "text"},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"Are you authorized to work in the UK?": "Yes", "Expected salary": "50000"}'
    )

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._screen_questions(unresolved, "SWE at Acme")

    assert result["Are you authorized to work in the UK?"] == "Yes"
    assert result["Expected salary"] == "50000"


@pytest.mark.asyncio
async def test_screen_questions_includes_options():
    filler = _make_filler()
    unresolved = [
        {"label": "Years of experience", "type": "select",
         "options": ["0-1", "2-3", "4-5", "6+"]},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Years of experience": "2-3"}'

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._screen_questions(unresolved, "Data Analyst")

    prompt = mock_openai.return_value.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "0-1" in prompt


# ── _review_form (LLM Call 3) ──

import base64


@pytest.mark.asyncio
async def test_review_form_pass():
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")
    filler = _make_filler(page_mock=page)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"pass": true}'

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._review_form()

    assert result["pass"] is True


@pytest.mark.asyncio
async def test_review_form_fail_with_issues():
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")
    filler = _make_filler(page_mock=page)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"pass": false, "issues": ["Phone empty", "Wrong country"]}'
    )

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._review_form()

    assert result["pass"] is False
    assert len(result["issues"]) == 2


@pytest.mark.asyncio
async def test_review_form_sends_image():
    """Screenshot is sent as base64 image_url in the LLM message."""
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG test")
    filler = _make_filler(page_mock=page)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"pass": true}'

    with patch("jobpulse.native_form_filler.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        await filler._review_form()

    messages = mock_openai.return_value.chat.completions.create.call_args[1]["messages"]
    content = messages[0]["content"]
    assert isinstance(content, list)
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1


# ── _upload_files ──


@pytest.mark.asyncio
async def test_upload_files_cv_only():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "id": "resume", "name": "", "label": "upload resume"},
    ])
    fi = MagicMock()
    nth_mock = MagicMock(return_value=fi)
    page.locator = MagicMock(return_value=MagicMock(nth=nth_mock))

    with patch.object(filler, "_upload_pdf", new_callable=AsyncMock) as mock_upload:
        await filler._upload_files("/tmp/cv.pdf", None)

    mock_upload.assert_called_once_with(fi, "/tmp/cv.pdf")


@pytest.mark.asyncio
async def test_upload_files_cv_and_cl():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "id": "resume", "name": "", "label": "upload resume"},
        {"idx": 1, "id": "cover_letter", "name": "", "label": "upload cover letter"},
    ])
    fi_cv = MagicMock()
    fi_cl = MagicMock()
    nth_mock = MagicMock(side_effect=lambda i: fi_cv if i == 0 else fi_cl)
    page.locator = MagicMock(return_value=MagicMock(nth=nth_mock))

    with patch.object(filler, "_upload_pdf", new_callable=AsyncMock) as mock_upload:
        await filler._upload_files("/tmp/cv.pdf", "/tmp/cl.pdf")

    assert mock_upload.call_count == 2
    mock_upload.assert_any_call(fi_cv, "/tmp/cv.pdf")
    mock_upload.assert_any_call(fi_cl, "/tmp/cl.pdf")


@pytest.mark.asyncio
async def test_upload_files_skips_autofill():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "id": "resume", "name": "", "label": "autofill from resume"},
    ])
    page.locator = MagicMock(return_value=MagicMock(nth=MagicMock()))

    with patch.object(filler, "_upload_pdf", new_callable=AsyncMock) as mock_upload:
        await filler._upload_files("/tmp/cv.pdf", None)

    mock_upload.assert_not_called()


# ── _check_consent ──


@pytest.mark.asyncio
async def test_check_consent_checks_unchecked():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    with patch.object(filler, "_get_accessible_name", return_value="I agree to the terms"):
        await filler._check_consent()

    cb.check.assert_called_once()


@pytest.mark.asyncio
async def test_check_consent_skips_non_consent():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    with patch.object(filler, "_get_accessible_name", return_value="Subscribe to newsletter"):
        await filler._check_consent()

    cb.check.assert_not_called()


@pytest.mark.asyncio
async def test_check_consent_skips_already_checked():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=True)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    with patch.object(filler, "_get_accessible_name", return_value="I accept privacy policy"):
        await filler._check_consent()

    cb.check.assert_not_called()


# ── _is_confirmation_page ──


@pytest.mark.asyncio
async def test_is_confirmation_page_true():
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.text_content = AsyncMock(
        return_value="Thank you for applying! We will review your application."
    )
    page.locator = MagicMock(return_value=body_locator)
    filler = _make_filler(page_mock=page)

    assert await filler._is_confirmation_page() is True


@pytest.mark.asyncio
async def test_is_confirmation_page_false():
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.text_content = AsyncMock(
        return_value="Please fill in your details below."
    )
    page.locator = MagicMock(return_value=body_locator)
    filler = _make_filler(page_mock=page)

    assert await filler._is_confirmation_page() is False


# ── _is_submit_page ──


@pytest.mark.asyncio
async def test_is_submit_page_true():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)

    def _get_by_role(role, name=None, exact=False):
        if "Submit" in (name or ""):
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role
    assert await filler._is_submit_page() is True


@pytest.mark.asyncio
async def test_is_submit_page_false():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.get_by_role = MagicMock(return_value=empty)

    assert await filler._is_submit_page() is False


# ── _click_navigation ──


@pytest.mark.asyncio
async def test_click_navigation_submit():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)
    btn.first.click = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Submit" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    with patch.object(filler, "_move_mouse_to", new_callable=AsyncMock):
        result = await filler._click_navigation(dry_run=False)

    assert result == "submitted"


@pytest.mark.asyncio
async def test_click_navigation_dry_run_stop():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Submit" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    result = await filler._click_navigation(dry_run=True)
    assert result == "dry_run_stop"


@pytest.mark.asyncio
async def test_click_navigation_next():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)
    btn.first.click = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Continue" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    with patch.object(filler, "_move_mouse_to", new_callable=AsyncMock):
        result = await filler._click_navigation(dry_run=False)

    assert result == "next"


@pytest.mark.asyncio
async def test_click_navigation_none_found():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.get_by_role = MagicMock(return_value=empty)

    result = await filler._click_navigation(dry_run=False)
    assert result == ""


# ── fill() — main loop ──


@pytest.mark.asyncio
async def test_fill_single_page_success():
    filler = _make_filler()

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Resume", "type": "file", "locator": AsyncMock()},
    ]

    with patch.object(filler, "_handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Email": "test@test.com"}), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={"email": "test@test.com"}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is True
    assert "field_types" in result
    assert "agent_mapping" in result


@pytest.mark.asyncio
async def test_fill_dry_run_stops():
    filler = _make_filler()

    fields = [{"label": "Name", "type": "text", "value": "", "required": True}]

    with patch.object(filler, "_handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Name": "John"}), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=True), \
         patch.object(filler, "_review_form", return_value={"pass": True}), \
         patch.object(filler, "_click_navigation", return_value="dry_run_stop"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=True,
        )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert "agent_mapping" in result


@pytest.mark.asyncio
async def test_fill_confirmation_page():
    filler = _make_filler()

    with patch.object(filler, "_handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=[]), \
         patch.object(filler, "_is_confirmation_page", return_value=True), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_no_nav_button():
    filler = _make_filler()

    fields = [{"label": "Name", "type": "text", "value": "", "required": True}]

    with patch.object(filler, "_handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Name": "John"}), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value=""), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is False
    assert "No navigation button" in result["error"]


@pytest.mark.asyncio
async def test_fill_calls_screening_for_unresolved():
    """fill() calls _screen_questions for unresolved non-file fields."""
    filler = _make_filler()

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Work auth?", "type": "radio", "options": ["Yes", "No"]},
    ]

    with patch.object(filler, "_handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Email": "a@b.com"}), \
         patch("jobpulse.screening_answers.try_instant_answer", return_value=None), \
         patch.object(filler, "_screen_questions", return_value={"Work auth?": "Yes"}) as mock_screen, \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={"email": "a@b.com"}, custom_answers={}, dry_run=False,
        )

    mock_screen.assert_called_once()
    assert result["success"] is True


# ── Orchestrator integration ──

from jobpulse.application_orchestrator import ApplicationOrchestrator


@pytest.mark.asyncio
async def test_fill_application_routes_to_native_filler():
    """fill_application creates NativeFormFiller when engine='playwright'."""
    driver = AsyncMock()
    driver.page = MagicMock()
    orch = ApplicationOrchestrator(driver=driver, engine="playwright")

    with patch("jobpulse.native_form_filler.NativeFormFiller") as MockFiller:
        mock_instance = AsyncMock()
        mock_instance.fill = AsyncMock(return_value={"success": True, "pages_filled": 1})
        MockFiller.return_value = mock_instance

        result = await orch._filler.fill_application(
            platform="greenhouse",
            snapshot={"url": "https://example.com", "fields": [], "buttons": []},
            cv_path="/tmp/cv.pdf",
            cover_letter_path=None,
            profile={"email": "test@test.com"},
            custom_answers={},
            overrides=None,
            dry_run=False,
            form_intelligence=None,
        )

    MockFiller.assert_called_once_with(page=driver.page, driver=driver)
    mock_instance.fill.assert_called_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_application_extension_still_uses_state_machine():
    """_fill_application uses state machine when engine='extension'."""
    driver = AsyncMock()
    driver.page = None
    driver.get_form_progress = AsyncMock(return_value=None)
    orch = ApplicationOrchestrator(driver=driver, engine="extension")

    # Snapshot that triggers CONFIRMATION in state machine
    snapshot = {
        "url": "https://example.com",
        "title": "Apply",
        "fields": [],
        "buttons": [{"text": "Submit", "selector": "#submit", "type": "submit", "enabled": True}],
        "page_text_preview": "Thank you for applying! Your application has been received.",
    }

    result = await orch._fill_application(
        platform="greenhouse",
        snapshot=snapshot,
        cv_path="/tmp/cv.pdf",
        cover_letter_path=None,
        profile={},
        custom_answers={},
        overrides=None,
        dry_run=False,
        form_intelligence=None,
    )

    assert result.get("success") is True
