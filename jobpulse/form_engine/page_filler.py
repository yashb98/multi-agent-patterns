"""Page-level form filler — orchestrates detection and filling of all fields on a page."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.form_engine import checkbox_filler, date_filler, file_filler, multi_select_filler
from jobpulse.form_engine import radio_filler, select_filler, text_filler
from jobpulse.form_engine.models import FillResult, FieldInfo, InputType

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


async def fill_field_by_type(
    page: Page,
    field: FieldInfo,
    value: str,
    file_path: Path | None = None,
) -> FillResult:
    """Fill a single form field based on its detected InputType.

    Routes to the appropriate filler function.
    """
    if field.input_type == InputType.READONLY:
        return FillResult(
            success=True, selector=field.selector,
            value_attempted=value, skipped=True,
        )

    if field.input_type == InputType.UNKNOWN:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error="Unsupported input type: unknown",
        )

    if field.input_type in (InputType.TEXT, InputType.TEXTAREA):
        if field.input_type == InputType.TEXTAREA:
            return await text_filler.fill_textarea(page, field.selector, value)
        return await text_filler.fill_text(page, field.selector, value)

    if field.input_type == InputType.SELECT_NATIVE:
        return await select_filler.fill_select(page, field.selector, value)

    if field.input_type == InputType.SELECT_CUSTOM:
        return await select_filler.fill_custom_select(page, field.selector, value)

    if field.input_type == InputType.RADIO:
        return await radio_filler.fill_radio_group(page, field.selector, value)

    if field.input_type == InputType.CHECKBOX:
        should_check = value.lower() in ("true", "yes", "1", "checked")
        return await checkbox_filler.fill_checkbox(page, field.selector, should_check)

    if field.input_type in (InputType.DATE_NATIVE, InputType.DATE_CUSTOM):
        return await date_filler.fill_date(page, field.selector, value)

    if field.input_type == InputType.FILE_UPLOAD:
        if file_path is None:
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="No file path provided for file upload",
            )
        return await file_filler.fill_file_upload(page, field.selector, file_path)

    if field.input_type == InputType.SEARCH_AUTOCOMPLETE:
        return await text_filler.fill_autocomplete(page, field.selector, value)

    if field.input_type == InputType.TAG_INPUT:
        values = [v.strip() for v in value.split(",") if v.strip()]
        return await multi_select_filler.fill_tag_input(page, field.selector, values)

    if field.input_type == InputType.MULTI_SELECT:
        values = [v.strip() for v in value.split(",") if v.strip()]
        return await multi_select_filler.fill_native_multi_select(page, field.selector, values)

    if field.input_type == InputType.TOGGLE_SWITCH:
        should_check = value.lower() in ("true", "yes", "1", "on")
        return await checkbox_filler.fill_checkbox(page, field.selector, should_check)

    if field.input_type == InputType.RICH_TEXT_EDITOR:
        return await text_filler.fill_text(page, field.selector, value)

    return FillResult(
        success=False, selector=field.selector,
        value_attempted=value, error=f"Unsupported input type: {field.input_type}",
    )
