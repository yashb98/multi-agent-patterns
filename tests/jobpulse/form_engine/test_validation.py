"""Tests for form validation detection."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_scan_for_errors_finds_aria_invalid():
    from jobpulse.form_engine.validation import scan_for_errors

    error_el = MagicMock()
    error_el.get_attribute = AsyncMock(side_effect=lambda name: {
        "id": "email",
        "aria-invalid": "true",
    }.get(name))
    error_el.text_content = AsyncMock(return_value="")
    error_el.evaluate = AsyncMock(return_value="Please enter a valid email")

    page = MagicMock()
    # Strategy 1 returns our element; all other strategies return []
    page.query_selector_all = AsyncMock(side_effect=[
        [error_el],  # Strategy 1: aria-invalid
        [],          # Strategy 2: role=alert
        [],          # Strategy 3: error CSS classes
        [],          # Strategy 4: aria-errormessage
        [],          # Strategy 5: ATS — selector 1
        [],          # Strategy 5: ATS — selector 2
        [],          # Strategy 5: ATS — selector 3
        [],          # Strategy 5: ATS — selector 4
    ])

    errors = await scan_for_errors(page)
    assert len(errors) >= 1


@pytest.mark.asyncio
async def test_scan_for_errors_empty_page():
    from jobpulse.form_engine.validation import scan_for_errors

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[])

    errors = await scan_for_errors(page)
    assert errors == []


@pytest.mark.asyncio
async def test_find_required_unfilled():
    from jobpulse.form_engine.validation import find_required_unfilled

    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: {
        "required": "",
        "id": "email",
        "value": "",
    }.get(name))
    el.evaluate = AsyncMock(return_value="")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[el])

    unfilled = await find_required_unfilled(page)
    assert len(unfilled) >= 1


@pytest.mark.asyncio
async def test_scan_error_css_classes():
    """Strategy 3: elements with error CSS classes should be found."""
    from jobpulse.form_engine.validation import scan_for_errors, ValidationError

    error_el = MagicMock()
    error_el.text_content = AsyncMock(return_value="Name is required")

    page = MagicMock()
    page.query_selector_all = AsyncMock(side_effect=[
        [],           # Strategy 1: aria-invalid
        [],           # Strategy 2: role=alert
        [error_el],   # Strategy 3: error CSS classes
        [],           # Strategy 4: aria-errormessage
        [],           # Strategy 5: ATS — selector 1
        [],           # Strategy 5: ATS — selector 2
        [],           # Strategy 5: ATS — selector 3
        [],           # Strategy 5: ATS — selector 4
    ])

    errors = await scan_for_errors(page)
    assert len(errors) == 1
    assert errors[0].error_message == "Name is required"
    assert errors[0].field_selector == ".error"


@pytest.mark.asyncio
async def test_scan_aria_errormessage():
    """Strategy 4: aria-errormessage references should be resolved."""
    from jobpulse.form_engine.validation import scan_for_errors

    input_el = MagicMock()
    input_el.get_attribute = AsyncMock(side_effect=lambda name: {
        "aria-errormessage": "err1",
        "id": "email-field",
    }.get(name))

    err_el = MagicMock()
    err_el.text_content = AsyncMock(return_value="Invalid email")

    page = MagicMock()
    page.query_selector_all = AsyncMock(side_effect=[
        [],           # Strategy 1: aria-invalid
        [],           # Strategy 2: role=alert
        [],           # Strategy 3: error CSS classes
        [input_el],   # Strategy 4: aria-errormessage
        [],           # Strategy 5: ATS — selector 1
        [],           # Strategy 5: ATS — selector 2
        [],           # Strategy 5: ATS — selector 3
        [],           # Strategy 5: ATS — selector 4
    ])
    page.query_selector = AsyncMock(return_value=err_el)

    errors = await scan_for_errors(page)
    assert len(errors) == 1
    assert errors[0].error_message == "Invalid email"
    assert errors[0].field_selector == "#email-field"


@pytest.mark.asyncio
async def test_scan_ats_specific_selectors():
    """Strategy 5: Workday-style data-automation-id error element should be found."""
    from jobpulse.form_engine.validation import scan_for_errors

    ats_el = MagicMock()
    ats_el.text_content = AsyncMock(return_value="This field is required")

    page = MagicMock()
    page.query_selector_all = AsyncMock(side_effect=[
        [],        # Strategy 1: aria-invalid
        [],        # Strategy 2: role=alert
        [],        # Strategy 3: error CSS classes
        [],        # Strategy 4: aria-errormessage
        [ats_el],  # Strategy 5: ATS — selector 1 (Workday)
        [],        # Strategy 5: ATS — selector 2
        [],        # Strategy 5: ATS — selector 3
        [],        # Strategy 5: ATS — selector 4
    ])

    errors = await scan_for_errors(page)
    assert len(errors) == 1
    assert errors[0].error_message == "This field is required"
    assert errors[0].field_selector == "[data-automation-id*='error']"


@pytest.mark.asyncio
async def test_scan_deduplication():
    """Duplicate error messages across strategies should be deduplicated."""
    from jobpulse.form_engine.validation import scan_for_errors

    # Strategy 2 (role=alert) and Strategy 3 (CSS class) both report the same message
    alert_el = MagicMock()
    alert_el.text_content = AsyncMock(return_value="Email is required")

    css_el = MagicMock()
    css_el.text_content = AsyncMock(return_value="Email is required")

    page = MagicMock()
    page.query_selector_all = AsyncMock(side_effect=[
        [],          # Strategy 1: aria-invalid
        [alert_el],  # Strategy 2: role=alert
        [css_el],    # Strategy 3: error CSS classes
        [],          # Strategy 4: aria-errormessage
        [],          # Strategy 5: ATS — selector 1
        [],          # Strategy 5: ATS — selector 2
        [],          # Strategy 5: ATS — selector 3
        [],          # Strategy 5: ATS — selector 4
    ])

    errors = await scan_for_errors(page)
    assert len(errors) == 1
    assert errors[0].error_message == "Email is required"


@pytest.mark.asyncio
async def test_scan_ignores_long_text():
    """Strategy 3 should ignore elements whose text is 200+ characters."""
    from jobpulse.form_engine.validation import scan_for_errors

    long_text = "x" * 250
    long_el = MagicMock()
    long_el.text_content = AsyncMock(return_value=long_text)

    page = MagicMock()
    page.query_selector_all = AsyncMock(side_effect=[
        [],          # Strategy 1: aria-invalid
        [],          # Strategy 2: role=alert
        [long_el],   # Strategy 3: error CSS classes — long text, should be ignored
        [],          # Strategy 4: aria-errormessage
        [],          # Strategy 5: ATS — selector 1
        [],          # Strategy 5: ATS — selector 2
        [],          # Strategy 5: ATS — selector 3
        [],          # Strategy 5: ATS — selector 4
    ])

    errors = await scan_for_errors(page)
    assert errors == []
