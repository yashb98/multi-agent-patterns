"""Form validation error detection and required field scanning."""

from __future__ import annotations

from dataclasses import dataclass

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationError:
    """A detected form validation error."""

    field_selector: str
    error_message: str
    field_label: str = ""


async def scan_for_errors(page) -> list[ValidationError]:
    """Scan the current page for visible validation error messages.

    Detection strategies:
    - [aria-invalid="true"] elements
    - Elements with class containing "error", "invalid"
    - role="alert" elements
    """
    errors: list[ValidationError] = []

    # Strategy 1: aria-invalid elements
    invalid_els = await page.query_selector_all("[aria-invalid='true']")
    for el in invalid_els:
        el_id = await el.get_attribute("id") or ""
        # Try to find associated error message
        error_msg = await el.evaluate(
            """el => {
                // Check aria-errormessage
                const errId = el.getAttribute('aria-errormessage');
                if (errId) {
                    const errEl = document.getElementById(errId);
                    if (errEl) return errEl.textContent.trim();
                }
                // Check sibling/parent for error text
                const parent = el.closest('.form-group, .field-wrapper, .form-field');
                if (parent) {
                    const errEl = parent.querySelector('.error, .invalid-feedback, [role="alert"]');
                    if (errEl) return errEl.textContent.trim();
                }
                return '';
            }"""
        )
        selector = f"#{el_id}" if el_id else "[aria-invalid='true']"
        errors.append(ValidationError(
            field_selector=selector, error_message=error_msg or "Invalid field",
        ))

    # Strategy 2: role="alert" elements (often used for form errors)
    alerts = await page.query_selector_all("[role='alert']")
    for alert in alerts:
        text = await alert.text_content()
        if text and text.strip():
            errors.append(ValidationError(
                field_selector="[role='alert']",
                error_message=text.strip(),
            ))

    logger.debug("validation: found %d errors on page", len(errors))
    return errors


async def find_required_unfilled(page) -> list[str]:
    """Find all required form fields that are currently empty.

    Returns list of selectors for unfilled required fields.
    """
    unfilled: list[str] = []

    # Check input/select/textarea with required attribute
    required_els = await page.query_selector_all(
        "input[required], select[required], textarea[required], "
        "[aria-required='true']"
    )

    for el in required_els:
        value = await el.evaluate("el => el.value || ''")
        if not value.strip():
            el_id = await el.get_attribute("id") or ""
            el_name = await el.get_attribute("name") or ""
            if el_id:
                selector = f"#{el_id}"
            elif el_name:
                selector = f"[name='{el_name}']"
            else:
                selector = "input[required]"
            unfilled.append(selector)

    logger.debug("validation: %d required fields unfilled", len(unfilled))
    return unfilled


async def has_errors(page) -> bool:
    """Quick check: are there any validation errors on the page?"""
    errors = await scan_for_errors(page)
    return len(errors) > 0
