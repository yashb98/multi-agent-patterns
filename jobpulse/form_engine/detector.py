"""SemanticTypeResolver — examine a DOM element and return its semantic InputType.

Replaces the simplistic tag-name checking with widget-aware, context-aware
resolution. Uses WidgetLibraryDetector for custom widget libraries.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.form_engine.models import InputType
from jobpulse.form_engine.widget_detector import WidgetLibraryDetector

if TYPE_CHECKING:
    from playwright.async_api import ElementHandle

logger = get_logger(__name__)

_TEXT_LIKE_TYPES = {"text", "email", "tel", "url", "number", "password", "search", ""}


class SemanticTypeResolver:
    """Resolve the semantic input type of a DOM element with widget awareness."""

    def __init__(self, widget_detector: WidgetLibraryDetector | None = None) -> None:
        self._widget_detector = widget_detector

    async def resolve(
        self, element: "ElementHandle", ancestor_locator=None
    ) -> tuple[InputType, float]:
        """Examine a DOM element and return (InputType, confidence 0.0-1.0).

        Args:
            element: Playwright ElementHandle to examine.
            ancestor_locator: Optional ancestor locator for widget detection.
        """
        tag = await self._safe_eval(element, "el => el.tagName.toLowerCase()")

        # Widget library detection (highest confidence for known widgets)
        if self._widget_detector and ancestor_locator:
            lib = await self._widget_detector.detect_for_field(ancestor_locator)
            if lib:
                widget_type = self._widget_type_to_input_type(lib)
                if widget_type:
                    return widget_type, 0.95

        # Native HTML elements
        if tag == "select":
            multi = await element.get_attribute("multiple")
            return (
                InputType.MULTI_SELECT if multi is not None else InputType.SELECT_NATIVE
            ), 0.98

        if tag == "textarea":
            return InputType.TEXTAREA, 0.98

        if tag == "input":
            input_type = (await element.get_attribute("type") or "text").lower()

            readonly = await element.get_attribute("readonly")
            disabled = await element.get_attribute("disabled")
            if readonly is not None or disabled is not None:
                return InputType.READONLY, 0.98

            if input_type == "radio":
                return InputType.RADIO, 0.98
            if input_type == "checkbox":
                return InputType.CHECKBOX, 0.98
            if input_type == "file":
                return InputType.FILE_UPLOAD, 0.98
            if input_type == "date":
                return InputType.DATE_NATIVE, 0.98
            if input_type in _TEXT_LIKE_TYPES:
                return InputType.TEXT, 0.95
            if input_type == "tel":
                return InputType.TEXT, 0.95  # Phone is still text semantically
            if input_type == "number":
                return InputType.TEXT, 0.90  # Number inputs are text-like
            return InputType.TEXT, 0.80

        # ARIA roles
        role = await element.get_attribute("role")
        if role == "listbox":
            return InputType.SELECT_CUSTOM, 0.90
        if role == "combobox":
            # Could be autocomplete, custom select, or tag input
            multi = await element.get_attribute("aria-multiselectable")
            if multi == "true":
                return InputType.MULTI_SELECT, 0.90
            return InputType.SELECT_CUSTOM, 0.90
        if role == "switch":
            return InputType.TOGGLE_SWITCH, 0.95
        if role == "radiogroup":
            return InputType.RADIO, 0.95
        if role == "searchbox":
            return InputType.SEARCH_AUTOCOMPLETE, 0.90
        if role == "slider":
            return InputType.TEXT, 0.70  # Sliders are range inputs — map to text

        # Content editable
        contenteditable = await element.get_attribute("contenteditable")
        if contenteditable == "true":
            return InputType.RICH_TEXT_EDITOR, 0.90

        # Unknown but interactive
        tabindex = await element.get_attribute("tabindex")
        if tabindex is not None:
            return InputType.TEXT, 0.50

        logger.debug("resolver: unknown element tag=%s role=%s", tag, role)
        return InputType.UNKNOWN, 0.20

    @staticmethod
    def _widget_type_to_input_type(library: str) -> InputType | None:
        """Map detected widget library to InputType."""
        mapping = {
            "react_select": InputType.SELECT_CUSTOM,
            "mui_autocomplete": InputType.SEARCH_AUTOCOMPLETE,
            "ant_select": InputType.SELECT_CUSTOM,
            "smartrecruiters_spl": InputType.SELECT_CUSTOM,
            "workday_wd": InputType.SELECT_CUSTOM,
            "greenhouse_custom": InputType.SELECT_CUSTOM,
            "intl_tel_input": InputType.TEXT,  # Phone field, still text
        }
        return mapping.get(library)

    @staticmethod
    async def _safe_eval(element: "ElementHandle", expr: str):
        """Safely evaluate JS on an element, returning empty string on failure."""
        try:
            return await element.evaluate(expr)
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

async def detect_input_type(element: "ElementHandle") -> InputType:
    """Legacy API — delegate to SemanticTypeResolver.

    Kept for compatibility with existing tests and callers that expect
    a standalone async function.
    """
    resolver = SemanticTypeResolver()
    input_type, _confidence = await resolver.resolve(element)
    return input_type
