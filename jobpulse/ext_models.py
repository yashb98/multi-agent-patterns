"""Pydantic models for the Chrome extension WebSocket protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class FieldInfo(BaseModel):
    """A form field detected on the page."""

    selector: str
    input_type: Literal[
        "text",
        "textarea",
        "select",
        "radio",
        "checkbox",
        "file",
        "date",
        "email",
        "number",
        "tel",
        "custom_select",
        "search_autocomplete",
        "multi_select",
        "toggle",
        "rich_text",
    ]
    label: str
    required: bool = False
    current_value: str = ""
    options: list[str] = []
    attributes: dict[str, str] = {}
    in_shadow_dom: bool = False
    in_iframe: bool = False
    iframe_index: int | None = None


class ButtonInfo(BaseModel):
    """A button or submit element on the page."""

    selector: str
    text: str
    type: str = "button"
    enabled: bool = True


class VerificationWall(BaseModel):
    """Detected bot verification challenge."""

    wall_type: Literal["cloudflare", "recaptcha", "hcaptcha", "text_challenge", "http_block"]
    confidence: float
    details: str = ""


class PageSnapshot(BaseModel):
    """Complete snapshot of the current page state."""

    url: str
    title: str
    fields: list[FieldInfo] = []
    buttons: list[ButtonInfo] = []
    verification_wall: VerificationWall | None = None
    page_text_preview: str = ""
    has_file_inputs: bool = False
    iframe_count: int = 0
    timestamp: int = 0


class ExtCommand(BaseModel):
    """Command sent from Python to the Chrome extension."""

    id: str
    action: Literal[
        "navigate",
        "fill",
        "click",
        "upload",
        "screenshot",
        "select",
        "check",
        "scroll",
        "wait",
        "close_tab",
    ]
    payload: dict[str, Any] = {}


class ExtResponse(BaseModel):
    """Response or event sent from Chrome extension to Python."""

    id: str
    type: Literal["ack", "result", "snapshot", "navigation", "mutation", "error", "pong"]
    payload: dict[str, Any] = {}


class FillResult(BaseModel):
    """Result of a field fill operation."""

    success: bool
    value_set: str = ""
    error: str = ""


class Action(BaseModel):
    """An action for the state machine to execute."""

    type: Literal["fill", "upload", "click", "select", "check", "wait"]
    selector: str = ""
    value: str | None = None
    file_path: str | None = None
