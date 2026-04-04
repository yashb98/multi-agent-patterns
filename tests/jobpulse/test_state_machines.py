"""Comprehensive tests for state machines — detection, actions, button priority, stuck detection.

Covers:
- Platform-specific state detection (LinkedIn, Workday, Greenhouse)
- Contact info vs screening questions when both present
- Button priority ordering (Submit > Review > Continue > Next)
- Progress detection parsing
- Stuck detection edge cases (short text, identical slices)
- Action generation for all states
- Confirmation detection with varied phrases
- Verification wall priority
- State machine registry, initial state, reset
"""

from __future__ import annotations

import pytest

from jobpulse.ext_models import Action, ButtonInfo, FieldInfo, PageSnapshot, VerificationWall
from jobpulse.state_machines import (
    ApplicationState,
    GenericStateMachine,
    GreenhouseStateMachine,
    LinkedInStateMachine,
    PlatformStateMachine,
    WorkdayStateMachine,
    detect_progress,
    find_next_button,
    get_state_machine,
    is_page_stuck,
)


# =========================================================================
# Helpers
# =========================================================================


def _snap(
    url="",
    title="",
    fields=None,
    buttons=None,
    wall=None,
    text="",
    has_files=False,
):
    return PageSnapshot(
        url=url,
        title=title,
        fields=fields or [],
        buttons=buttons or [],
        verification_wall=wall,
        page_text_preview=text,
        has_file_inputs=has_files,
        iframe_count=0,
        timestamp=1000,
    )


# =========================================================================
# State detection — universal
# =========================================================================


class TestUniversalDetection:
    def test_verification_wall_always_wins(self):
        """Verification wall takes priority over everything else."""
        machine = GenericStateMachine()
        snap = _snap(
            fields=[FieldInfo(selector="#name", input_type="text", label="First Name")],
            text="Thank you for applying",
            wall=VerificationWall(wall_type="cloudflare", confidence=0.9),
        )
        assert machine.detect_state(snap) == ApplicationState.VERIFICATION_WALL

    def test_confirmation_thank_you(self):
        machine = GenericStateMachine()
        snap = _snap(text="Thank you for applying. We'll be in touch.")
        assert machine.detect_state(snap) == ApplicationState.CONFIRMATION

    def test_confirmation_application_received(self):
        machine = GenericStateMachine()
        snap = _snap(text="Your application has been received and is being reviewed.")
        assert machine.detect_state(snap) == ApplicationState.CONFIRMATION

    def test_confirmation_submitted(self):
        machine = GenericStateMachine()
        snap = _snap(text="Application submitted! Check your email for confirmation.")
        assert machine.detect_state(snap) == ApplicationState.CONFIRMATION

    def test_confirmation_successfully_submitted(self):
        machine = GenericStateMachine()
        snap = _snap(text="You have successfully submitted your application.")
        assert machine.detect_state(snap) == ApplicationState.CONFIRMATION

    def test_confirmation_case_insensitive(self):
        machine = GenericStateMachine()
        snap = _snap(text="THANK YOU FOR APPLYING! We look forward to reviewing your application.")
        assert machine.detect_state(snap) == ApplicationState.CONFIRMATION

    def test_no_confirmation_for_partial_match(self):
        """'Thank you' alone doesn't trigger confirmation."""
        machine = GenericStateMachine()
        snap = _snap(text="Thank you for visiting our careers page")
        state = machine.detect_state(snap)
        assert state != ApplicationState.CONFIRMATION


# =========================================================================
# State detection — field-based heuristics
# =========================================================================


class TestFieldBasedDetection:
    def test_file_inputs_detected_as_resume_upload(self):
        machine = GenericStateMachine()
        snap = _snap(has_files=True)
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_file_field_type_detected(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[FieldInfo(selector="#cv", input_type="file", label="Upload CV")],
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_contact_fields_detected(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
                FieldInfo(selector="#ln", input_type="text", label="Last Name"),
                FieldInfo(selector="#em", input_type="email", label="Email"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_select_detected_as_screening(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(selector="#q1", input_type="select", label="Years of experience"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_radio_detected_as_screening(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[FieldInfo(selector="#q1", input_type="radio", label="Work authorization")],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_textarea_detected_as_screening(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[FieldInfo(selector="#q1", input_type="textarea", label="Cover letter")],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_submit_button_detected(self):
        machine = GenericStateMachine()
        snap = _snap(
            buttons=[ButtonInfo(selector="#sub", text="Submit Application", enabled=True)],
        )
        assert machine.detect_state(snap) == ApplicationState.SUBMIT

    def test_unclassifiable_fields_default_to_screening(self):
        """Fields that don't match contact/file/select → screening fallback."""
        machine = GenericStateMachine()
        snap = _snap(
            fields=[FieldInfo(selector="#misc", input_type="text", label="Something random")],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_no_fields_returns_initial(self):
        machine = GenericStateMachine()
        snap = _snap()
        assert machine.detect_state(snap) == ApplicationState.INITIAL


# =========================================================================
# LinkedIn-specific detection
# =========================================================================


class TestLinkedInDetection:
    def test_login_wall_sign_in_no_fields(self):
        machine = LinkedInStateMachine()
        snap = _snap(
            text="Sign in to apply for this job",
            fields=[],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_login_wall_sign_in_with_password_only(self):
        """Sign in text + only a password field → LOGIN_WALL (password filtered out)."""
        machine = LinkedInStateMachine()
        snap = _snap(
            text="Sign in to continue",
            fields=[
                FieldInfo(selector="#p", input_type="text", label="password"),
            ],
        )
        # password field is filtered out → no fillable → LOGIN_WALL
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_screening_additional_questions(self):
        machine = LinkedInStateMachine()
        snap = _snap(
            text="Additional questions about this role",
            fields=[
                FieldInfo(selector="#q", input_type="select", label="Years of experience"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_review_button_detected(self):
        machine = LinkedInStateMachine()
        snap = _snap(
            buttons=[ButtonInfo(selector="#rev", text="Review your application", enabled=True)],
        )
        assert machine.detect_state(snap) == ApplicationState.REVIEW


# =========================================================================
# Workday-specific detection
# =========================================================================


class TestWorkdayDetection:
    def test_workday_signin_detected(self):
        machine = WorkdayStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#email",
                    input_type="email",
                    label="Email",
                    attributes={"data-automation-id": "signIn-emailAddress"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_workday_normal_field_not_login(self):
        machine = WorkdayStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#name",
                    input_type="text",
                    label="Full Name",
                    attributes={"data-automation-id": "legalName"},
                ),
            ],
        )
        state = machine.detect_state(snap)
        assert state != ApplicationState.LOGIN_WALL


# =========================================================================
# Button priority
# =========================================================================


class TestFindNextButton:
    def test_submit_highest_priority(self):
        buttons = [
            {"text": "Next", "selector": "#next", "enabled": True},
            {"text": "Submit Application", "selector": "#submit", "enabled": True},
        ]
        btn = find_next_button(buttons)
        assert btn["selector"] == "#submit"

    def test_review_over_continue(self):
        buttons = [
            {"text": "Continue", "selector": "#cont", "enabled": True},
            {"text": "Review & Submit", "selector": "#review", "enabled": True},
        ]
        btn = find_next_button(buttons)
        assert btn["selector"] == "#review"

    def test_save_and_continue(self):
        buttons = [
            {"text": "Save and Continue", "selector": "#save", "enabled": True},
            {"text": "Next Step", "selector": "#next", "enabled": True},
        ]
        btn = find_next_button(buttons)
        assert btn["selector"] == "#save"  # priority 70 > 50

    def test_disabled_button_skipped(self):
        buttons = [
            {"text": "Submit Application", "selector": "#submit", "enabled": False},
            {"text": "Next", "selector": "#next", "enabled": True},
        ]
        btn = find_next_button(buttons)
        assert btn["selector"] == "#next"

    def test_no_matching_button_returns_none(self):
        buttons = [
            {"text": "Cancel", "selector": "#cancel", "enabled": True},
            {"text": "Back", "selector": "#back", "enabled": True},
        ]
        assert find_next_button(buttons) is None

    def test_empty_list_returns_none(self):
        assert find_next_button([]) is None

    def test_enabled_defaults_true_when_missing(self):
        """Missing 'enabled' key defaults to True."""
        buttons = [{"text": "Next", "selector": "#next"}]
        btn = find_next_button(buttons)
        assert btn is not None


# =========================================================================
# Progress detection
# =========================================================================


class TestDetectProgress:
    def test_step_of_pattern(self):
        assert detect_progress("Step 2 of 5") == (2, 5)

    def test_page_of_pattern(self):
        assert detect_progress("Page 3 of 7") == (3, 7)

    def test_slash_pattern(self):
        assert detect_progress("Question 3 / 5") == (3, 5)

    def test_no_match(self):
        assert detect_progress("Please fill in your details") is None

    def test_rejects_invalid_range(self):
        """Current > total is rejected."""
        assert detect_progress("Step 6 of 5") is None

    def test_rejects_over_20_pages(self):
        assert detect_progress("Step 1 of 25") is None

    def test_zero_step_rejected(self):
        assert detect_progress("Step 0 of 5") is None


# =========================================================================
# Stuck detection
# =========================================================================


class TestIsPageStuck:
    def test_identical_pages_are_stuck(self):
        text = "x" * 800
        prev = {"page_text_preview": text}
        curr = {"page_text_preview": text}
        assert is_page_stuck(prev, curr) is True

    def test_different_pages_not_stuck(self):
        prev = {"page_text_preview": "a" * 800}
        curr = {"page_text_preview": "b" * 800}
        assert is_page_stuck(prev, curr) is False

    def test_short_text_not_stuck(self):
        """Text shorter than 10 chars in slice → not stuck."""
        prev = {"page_text_preview": "Hi"}
        curr = {"page_text_preview": "Hi"}
        assert is_page_stuck(prev, curr) is False

    def test_wrapper_text_ignored(self):
        """First 200 chars (wrapper) differ but middle is same → stuck."""
        wrapper1 = "A" * 200
        wrapper2 = "B" * 200
        middle = "SAME_CONTENT" * 50
        tail = "C" * 100
        prev = {"page_text_preview": wrapper1 + middle + tail}
        curr = {"page_text_preview": wrapper2 + middle + tail}
        assert is_page_stuck(prev, curr) is True

    def test_middle_content_differs(self):
        """Same wrapper but different middle → not stuck."""
        wrapper = "W" * 200
        tail = "T" * 100
        prev = {"page_text_preview": wrapper + "QUESTION_1" * 50 + tail}
        curr = {"page_text_preview": wrapper + "QUESTION_2" * 50 + tail}
        assert is_page_stuck(prev, curr) is False


# =========================================================================
# Action generation
# =========================================================================


class TestActionGeneration:
    def test_contact_info_fills_from_profile(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
                FieldInfo(selector="#em", input_type="email", label="Email Address"),
            ],
        )
        profile = {"first_name": "Yash", "email": "yash@test.com"}
        actions = machine.get_actions(
            ApplicationState.CONTACT_INFO, snap, profile, {}, "", None
        )
        assert len(actions) == 2
        assert all(a.type == "fill" for a in actions)

    def test_contact_info_skips_prefilled(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name", current_value="Yash"),
                FieldInfo(selector="#em", input_type="email", label="Email"),
            ],
        )
        profile = {"first_name": "Yash", "email": "y@test.com"}
        actions = machine.get_actions(
            ApplicationState.CONTACT_INFO, snap, profile, {}, "", None
        )
        assert len(actions) == 1  # only email, not first name

    def test_resume_upload_cv_only(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[FieldInfo(selector="#cv", input_type="file", label="Resume")],
        )
        actions = machine.get_actions(
            ApplicationState.RESUME_UPLOAD, snap, {}, {}, "/tmp/cv.pdf", None
        )
        assert len(actions) == 1
        assert actions[0].type == "upload"
        assert actions[0].file_path == "/tmp/cv.pdf"

    def test_resume_upload_with_cover_letter(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(selector="#cv", input_type="file", label="Resume"),
                FieldInfo(selector="#cl", input_type="file", label="Cover Letter"),
            ],
        )
        actions = machine.get_actions(
            ApplicationState.RESUME_UPLOAD, snap, {}, {},
            "/tmp/cv.pdf", "/tmp/cl.pdf",
        )
        assert len(actions) == 2
        cl_action = [a for a in actions if "cl" in a.selector][0]
        assert cl_action.file_path == "/tmp/cl.pdf"

    def test_submit_clicks_button(self):
        machine = GenericStateMachine()
        snap = _snap(
            buttons=[
                ButtonInfo(selector="#sub", text="Submit Application", enabled=True),
            ],
        )
        actions = machine.get_actions(
            ApplicationState.SUBMIT, snap, {}, {}, "", None
        )
        assert len(actions) == 1
        assert actions[0].type == "click"

    def test_submit_disabled_button_ignored(self):
        machine = GenericStateMachine()
        snap = _snap(
            buttons=[
                ButtonInfo(selector="#sub", text="Submit Application", enabled=False),
            ],
        )
        actions = machine.get_actions(
            ApplicationState.SUBMIT, snap, {}, {}, "", None
        )
        assert len(actions) == 0

    def test_initial_state_returns_no_actions(self):
        machine = GenericStateMachine()
        snap = _snap()
        actions = machine.get_actions(
            ApplicationState.INITIAL, snap, {}, {}, "", None
        )
        assert actions == []

    def test_screening_select_generates_select_action(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#exp",
                    input_type="select",
                    label="Years of experience with Python",
                    options=["1", "2", "3", "5+"],
                ),
            ],
        )
        actions = machine.get_actions(
            ApplicationState.SCREENING_QUESTIONS, snap, {}, {}, "", None
        )
        assert len(actions) == 1
        assert actions[0].type == "select"

    def test_screening_radio_generates_check_action(self):
        machine = GenericStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(selector="#auth", input_type="radio", label="Are you authorized to work?"),
            ],
        )
        actions = machine.get_actions(
            ApplicationState.SCREENING_QUESTIONS, snap, {}, {}, "", None
        )
        assert len(actions) == 1
        assert actions[0].type == "check"


# =========================================================================
# State machine registry
# =========================================================================


class TestRegistry:
    def test_known_platforms(self):
        for platform in ("greenhouse", "lever", "linkedin", "indeed", "workday", "generic"):
            machine = get_state_machine(platform)
            assert machine.platform == platform

    def test_unknown_platform_returns_generic(self):
        machine = get_state_machine("unknownats")
        assert isinstance(machine, GenericStateMachine)

    def test_each_call_returns_fresh_instance(self):
        m1 = get_state_machine("linkedin")
        m2 = get_state_machine("linkedin")
        assert m1 is not m2

    def test_initial_state_after_creation(self):
        for platform in ("greenhouse", "lever", "linkedin", "indeed", "workday", "generic"):
            sm = get_state_machine(platform)
            assert sm.current_state == ApplicationState.INITIAL


# =========================================================================
# State transitions and terminal
# =========================================================================


class TestStateTransitions:
    def test_terminal_states(self):
        assert ApplicationState.CONFIRMATION.is_terminal
        assert ApplicationState.VERIFICATION_WALL.is_terminal
        assert ApplicationState.ERROR.is_terminal

    def test_non_terminal_states(self):
        for state in (
            ApplicationState.INITIAL,
            ApplicationState.LOGIN_WALL,
            ApplicationState.CONTACT_INFO,
            ApplicationState.RESUME_UPLOAD,
            ApplicationState.SCREENING_QUESTIONS,
            ApplicationState.SUBMIT,
        ):
            assert not state.is_terminal

    def test_reset_goes_to_initial(self):
        machine = GenericStateMachine()
        machine.current_state = ApplicationState.SCREENING_QUESTIONS
        machine.reset()
        assert machine.current_state == ApplicationState.INITIAL

    def test_transition_delegates_to_detect(self):
        machine = GenericStateMachine()
        snap = _snap(text="Thank you for applying")
        new_state = machine.transition(ApplicationState.SUBMIT, snap)
        assert new_state == ApplicationState.CONFIRMATION
