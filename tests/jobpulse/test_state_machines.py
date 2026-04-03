"""Tests for platform state machines."""

import pytest
from jobpulse.state_machines import (
    ApplicationState,
    PlatformStateMachine,
    get_state_machine,
)
from jobpulse.ext_models import PageSnapshot, FieldInfo, ButtonInfo, VerificationWall


def _snapshot(url="", title="", fields=None, buttons=None, wall=None, text=""):
    return PageSnapshot(
        url=url,
        title=title,
        fields=fields or [],
        buttons=buttons or [],
        verification_wall=wall,
        page_text_preview=text,
        has_file_inputs=False,
        iframe_count=0,
        timestamp=1000,
    )


# --- Base class tests ---

def test_application_state_terminal_states():
    """confirmation, verification_wall, error are terminal."""
    terminal = {ApplicationState.CONFIRMATION, ApplicationState.VERIFICATION_WALL, ApplicationState.ERROR}
    for state in ApplicationState:
        if state in terminal:
            assert state.is_terminal is True
        else:
            assert state.is_terminal is False


def test_get_state_machine_returns_correct_platform():
    for platform in ("greenhouse", "lever", "linkedin", "indeed", "workday", "generic"):
        sm = get_state_machine(platform)
        assert sm.platform == platform


def test_get_state_machine_unknown_returns_generic():
    sm = get_state_machine("unknown_ats")
    assert sm.platform == "generic"


def test_state_machine_initial_state():
    sm = get_state_machine("greenhouse")
    assert sm.current_state == ApplicationState.INITIAL


def test_state_machine_detects_verification_wall():
    sm = get_state_machine("greenhouse")
    snap = _snapshot(
        url="https://boards.greenhouse.io/apply",
        wall=VerificationWall(wall_type="cloudflare", confidence=0.95),
    )
    state = sm.detect_state(snap)
    assert state == ApplicationState.VERIFICATION_WALL


def test_state_machine_is_terminal_after_verification():
    sm = get_state_machine("greenhouse")
    sm.current_state = ApplicationState.VERIFICATION_WALL
    assert sm.is_terminal is True


def test_state_machine_is_terminal_after_confirmation():
    sm = get_state_machine("greenhouse")
    sm.current_state = ApplicationState.CONFIRMATION
    assert sm.is_terminal is True


def test_state_machine_reset():
    sm = get_state_machine("greenhouse")
    sm.current_state = ApplicationState.CONFIRMATION
    sm.reset()
    assert sm.current_state == ApplicationState.INITIAL


# --- Greenhouse tests ---

def test_greenhouse_detect_contact_info():
    sm = get_state_machine("greenhouse")
    snap = _snapshot(
        url="https://boards.greenhouse.io/company/jobs/123",
        fields=[
            FieldInfo(selector="#first_name", input_type="text", label="First Name", required=True),
            FieldInfo(selector="#last_name", input_type="text", label="Last Name", required=True),
            FieldInfo(selector="#email", input_type="email", label="Email", required=True),
        ],
    )
    state = sm.detect_state(snap)
    assert state == ApplicationState.CONTACT_INFO


def test_greenhouse_detect_resume_upload():
    sm = get_state_machine("greenhouse")
    snap = _snapshot(
        url="https://boards.greenhouse.io/company/jobs/123",
        fields=[
            FieldInfo(selector="input[type=file]", input_type="file", label="Resume/CV"),
        ],
    )
    snap.has_file_inputs = True
    state = sm.detect_state(snap)
    assert state == ApplicationState.RESUME_UPLOAD


def test_greenhouse_detect_confirmation():
    sm = get_state_machine("greenhouse")
    snap = _snapshot(
        url="https://boards.greenhouse.io/company/jobs/123",
        text="Thank you for applying! Your application has been received.",
    )
    state = sm.detect_state(snap)
    assert state == ApplicationState.CONFIRMATION


def test_greenhouse_get_actions_contact_info():
    sm = get_state_machine("greenhouse")
    snap = _snapshot(
        url="https://boards.greenhouse.io/company/jobs/123",
        fields=[
            FieldInfo(selector="#first_name", input_type="text", label="First Name", required=True),
        ],
    )
    profile = {"first_name": "Yash", "last_name": "B", "email": "yash@test.com"}
    actions = sm.get_actions(
        ApplicationState.CONTACT_INFO, snap, profile, {}, "/tmp/cv.pdf", None
    )
    assert len(actions) >= 1
    assert actions[0].type == "fill"


# --- LinkedIn tests ---

def test_linkedin_detect_login_wall():
    sm = get_state_machine("linkedin")
    snap = _snapshot(
        url="https://www.linkedin.com/jobs/view/123",
        text="Sign in to apply",
        buttons=[ButtonInfo(selector="a.sign-in", text="Sign in", type="link", enabled=True)],
    )
    state = sm.detect_state(snap)
    assert state == ApplicationState.LOGIN_WALL


def test_linkedin_detect_screening_questions():
    sm = get_state_machine("linkedin")
    snap = _snapshot(
        url="https://www.linkedin.com/jobs/view/123",
        fields=[
            FieldInfo(selector=".fb-dash-form-element select", input_type="select",
                      label="How many years of experience do you have?",
                      options=["1", "2", "3", "4", "5+"]),
        ],
        text="Additional Questions",
    )
    state = sm.detect_state(snap)
    assert state == ApplicationState.SCREENING_QUESTIONS


# --- Generic tests ---

def test_generic_detect_form():
    sm = get_state_machine("generic")
    snap = _snapshot(
        url="https://company.com/careers/apply",
        fields=[
            FieldInfo(selector="input[name=name]", input_type="text", label="Full Name"),
            FieldInfo(selector="input[name=email]", input_type="email", label="Email"),
        ],
    )
    state = sm.detect_state(snap)
    assert state in (ApplicationState.CONTACT_INFO, ApplicationState.SCREENING_QUESTIONS)
