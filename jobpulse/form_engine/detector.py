"""Detect the semantic input type of a DOM element."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.form_engine.models import InputType

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle

logger = get_logger(__name__)

_TEXT_LIKE_TYPES = {"text", "email", "tel", "url", "number", "password", "search", ""}


async def detect_input_type(element: ElementHandle) -> InputType:
    """Examine a DOM element and return its semantic InputType."""
    tag = await element.evaluate("el => el.tagName.toLowerCase()")

    if tag == "select":
        multi = await element.get_attribute("multiple")
        return InputType.MULTI_SELECT if multi is not None else InputType.SELECT_NATIVE

    if tag == "textarea":
        return InputType.TEXTAREA

    if tag == "input":
        input_type = (await element.get_attribute("type") or "text").lower()

        readonly = await element.get_attribute("readonly")
        disabled = await element.get_attribute("disabled")
        if readonly is not None or disabled is not None:
            return InputType.READONLY

        if input_type == "radio":
            return InputType.RADIO
        if input_type == "checkbox":
            return InputType.CHECKBOX
        if input_type == "file":
            return InputType.FILE_UPLOAD
        if input_type == "date":
            return InputType.DATE_NATIVE
        if input_type in _TEXT_LIKE_TYPES:
            return InputType.TEXT
        return InputType.TEXT

    role = await element.get_attribute("role")
    if role in ("listbox", "combobox"):
        return InputType.SELECT_CUSTOM
    if role == "switch":
        return InputType.TOGGLE_SWITCH
    if role == "radiogroup":
        return InputType.RADIO

    contenteditable = await element.get_attribute("contenteditable")
    if contenteditable == "true":
        return InputType.RICH_TEXT_EDITOR

    aria_multi = await element.get_attribute("aria-multiselectable")
    if aria_multi == "true":
        return InputType.MULTI_SELECT

    logger.debug("detector: unknown element tag=%s role=%s", tag, role)
    return InputType.UNKNOWN
