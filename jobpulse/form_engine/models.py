"""Data models for the form engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InputType(str, Enum):
    """Semantic type of a form input element."""

    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT_NATIVE = "select_native"
    SELECT_CUSTOM = "select_custom"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    DATE_NATIVE = "date_native"
    DATE_CUSTOM = "date_custom"
    SEARCH_AUTOCOMPLETE = "search_autocomplete"
    FILE_UPLOAD = "file_upload"
    MULTI_SELECT = "multi_select"
    TAG_INPUT = "tag_input"
    TOGGLE_SWITCH = "toggle_switch"
    RICH_TEXT_EDITOR = "rich_text_editor"
    READONLY = "readonly"
    UNKNOWN = "unknown"


@dataclass
class FillResult:
    """Result of attempting to fill a single form field."""

    success: bool
    selector: str
    value_attempted: str
    value_set: str | None = None
    error: str | None = None
    skipped: bool = False


@dataclass
class FieldInfo:
    """Detected information about a form field."""

    selector: str
    input_type: InputType
    label: str = ""
    required: bool = False
    current_value: str = ""
    options: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
