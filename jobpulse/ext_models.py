"""Pydantic models for the Chrome extension WebSocket protocol."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class PageType(StrEnum):
    """Classification of what type of page we're looking at."""

    JOB_DESCRIPTION = "job_description"
    LOGIN_FORM = "login_form"
    SIGNUP_FORM = "signup_form"
    EMAIL_VERIFICATION = "email_verification"
    APPLICATION_FORM = "application_form"
    CONFIRMATION = "confirmation"
    VERIFICATION_WALL = "verification_wall"
    UNKNOWN = "unknown"


class AccountInfo(BaseModel):
    """Stored credentials for an ATS platform."""

    domain: str
    email: str
    verified: bool = False
    created_at: str = ""
    last_login: str = ""


class NavigationStep(BaseModel):
    """One step in a learned navigation sequence."""

    page_type: str
    action: str  # click_apply, fill_login, fill_signup, verify_email, sso_google
    selector: str = ""
    url: str = ""


class FieldInfo(BaseModel):
    """A form field detected on the page."""
    model_config = {"extra": "ignore"}

    selector: str
    input_type: str  # Dynamic — content script detects type from DOM
    label: str
    required: bool = False
    current_value: str = ""
    options: list[str] = []
    attributes: dict[str, str] = {}
    in_shadow_dom: bool = False
    in_iframe: bool = False
    iframe_index: int | None = None
    # v2: parent context for form intelligence
    group_label: str = ""
    group_selector: str = ""
    parent_text: str = ""
    fieldset_legend: str = ""
    help_text: str = ""
    error_text: str = ""
    aria_describedby: str = ""
    # v3: exhaustive DOM context — all surrounding text for LLM analysis
    dom_context: str = ""
    label_sources: list[str] = []


class ButtonInfo(BaseModel):
    """A button or submit element on the page."""
    model_config = {"extra": "ignore"}

    selector: str
    text: str
    type: str = "button"
    enabled: bool = True
    href: str = ""
    target: str = ""


class FormGroup(BaseModel):
    """A form group: label + input(s) paired together."""

    group_selector: str
    question: str  # The label/legend text
    fields: list[FieldInfo] = []
    is_required: bool = False
    is_answered: bool = False
    fieldset_legend: str = ""
    help_text: str = ""


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
    page_stable: bool = True
    timestamp: int = 0
    # v2 additions
    form_groups: list[FormGroup] = []
    progress: tuple[int, int] | None = None  # (current_step, total_steps)
    modal_detected: bool = False


class ExtCommand(BaseModel):
    """Command sent from Python to the Chrome extension."""

    id: str
    action: Literal[
        # Core browser actions (handled by background.js)
        "navigate",
        "screenshot",
        "get_snapshot",
        "close_tab",
        "real_click",
        "real_type",
        # Core form actions (forwarded to content.js)
        "fill",
        "click",
        "upload",
        "select",
        "check",
        "analyze_field",
        "force_click",
        "scroll_to",
        "wait_for_selector",
        "wait_for_apply",
        # v2 form engine
        "fill_radio_group",
        "fill_custom_select",
        "fill_autocomplete",
        "fill_combobox",
        "fill_tag_input",
        "fill_date",
        "fill_contenteditable",
        "check_consent_boxes",
        "scan_form_groups",
        "rescan_after_fill",
        "scan_validation_errors",
        # Page analysis
        "scan_jd",
        "scan_cards",
        "get_field_context",
        # MV3 state persistence
        "save_form_progress",
        "get_form_progress",
        "clear_form_progress",
    ]
    payload: dict[str, Any] = {}


class ExtResponse(BaseModel):
    """Response or event sent from Chrome extension to Python."""

    id: str
    type: Literal["ack", "result", "snapshot", "navigation", "mutation", "error", "pong"]
    payload: dict[str, Any] = {}


class FillResult(BaseModel):
    """Result of a field fill operation."""
    model_config = {"extra": "ignore"}

    success: bool
    value_set: str = ""
    value_verified: bool = True
    error: str = ""


class Action(BaseModel):
    """An action for the state machine to execute."""

    type: Literal[
        "fill", "upload", "click", "select", "check", "wait",
        # v2 action types
        "fill_radio_group", "fill_custom_select", "fill_autocomplete",
        "fill_tag_input", "fill_date", "scroll_to", "force_click",
        "check_consent_boxes", "fill_combobox", "fill_contenteditable",
    ]
    selector: str = ""
    value: str | None = None
    file_path: str | None = None


class FieldAnswer(BaseModel):
    """Result of the form intelligence tier resolution."""

    answer: str
    tier: int  # 1=pattern, 2=semantic_cache, 3=nano, 4=llm, 5=vision
    confidence: float  # 0.0-1.0
    tier_name: str = "unknown"
