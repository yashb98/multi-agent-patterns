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
    AshbyStateMachine,
    BambooHRStateMachine,
    GenericStateMachine,
    GreenhouseStateMachine,
    ICIMSStateMachine,
    JobviteStateMachine,
    LinkedInStateMachine,
    PlatformStateMachine,
    SmartRecruitersStateMachine,
    TaleoStateMachine,
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
        assert actions[0].type == "fill_radio_group"


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


# =========================================================================
# SmartRecruiters-specific detection
# =========================================================================


class TestSmartRecruitersDetection:
    def test_sso_sign_in_field_returns_login_wall(self):
        """SSO/sign-in labelled field triggers LOGIN_WALL."""
        machine = SmartRecruitersStateMachine()
        snap = _snap(
            url="https://jobs.smartrecruiters.com/company/job-123",
            fields=[
                FieldInfo(selector="#sso", input_type="text", label="SSO Login"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_sign_in_label_returns_login_wall(self):
        """Label containing 'sign in' triggers LOGIN_WALL when not on /apply URL."""
        machine = SmartRecruitersStateMachine()
        snap = _snap(
            url="https://jobs.smartrecruiters.com/company/job-123/sign-in",
            fields=[
                FieldInfo(selector="#signin", input_type="text", label="Sign In with Email"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_apply_url_delegates_to_field_detection(self):
        """/apply in URL routes to field-based detection (contact info)."""
        machine = SmartRecruitersStateMachine()
        snap = _snap(
            url="https://jobs.smartrecruiters.com/company/job-123/apply",
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
                FieldInfo(selector="#em", input_type="email", label="Email"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_apply_in_page_text_delegates_to_field_detection(self):
        """'apply' in first 200 chars of page text routes to field-based detection."""
        machine = SmartRecruitersStateMachine()
        snap = _snap(
            url="https://jobs.smartrecruiters.com/company/job-123",
            text="Apply for this job at Acme Corp",
            fields=[
                FieldInfo(selector="#cv", input_type="file", label="Resume"),
            ],
            has_files=True,
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_no_sso_no_apply_url_falls_back_to_generic(self):
        """No SSO fields and no /apply URL → field-based fallback."""
        machine = SmartRecruitersStateMachine()
        snap = _snap(
            url="https://jobs.smartrecruiters.com/company/job-123",
            fields=[
                FieldInfo(selector="#q1", input_type="select", label="Years of experience"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_platform_name(self):
        assert SmartRecruitersStateMachine.platform == "smartrecruiters"


# =========================================================================
# BambooHR-specific detection
# =========================================================================


class TestBambooHRDetection:
    def test_resume_data_testid_returns_resume_upload(self):
        """data-testid containing 'resume' triggers RESUME_UPLOAD."""
        machine = BambooHRStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#resume",
                    input_type="file",
                    label="Upload Resume",
                    attributes={"data-testid": "resume-upload"},
                ),
            ],
            has_files=True,
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_cover_letter_data_testid_returns_resume_upload(self):
        """data-testid containing 'coverLetter' triggers RESUME_UPLOAD."""
        machine = BambooHRStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#cl",
                    input_type="file",
                    label="Cover Letter",
                    attributes={"data-testid": "coverLetter-upload"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_login_data_testid_returns_login_wall(self):
        """data-testid containing 'login' triggers LOGIN_WALL."""
        machine = BambooHRStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#email",
                    input_type="email",
                    label="Email",
                    attributes={"data-testid": "login-email"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_sign_in_data_testid_returns_login_wall(self):
        """data-testid containing 'signIn' triggers LOGIN_WALL."""
        machine = BambooHRStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#pw",
                    input_type="text",
                    label="Password",
                    attributes={"data-testid": "signIn-password"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_no_special_testid_falls_back_to_field_detection(self):
        """Fields without relevant data-testid fall back to generic detection."""
        machine = BambooHRStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#fn",
                    input_type="text",
                    label="First Name",
                    attributes={"data-testid": "applicant-firstName"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_no_fields_returns_initial(self):
        machine = BambooHRStateMachine()
        snap = _snap()
        assert machine.detect_state(snap) == ApplicationState.INITIAL

    def test_platform_name(self):
        assert BambooHRStateMachine.platform == "bamboohr"


# =========================================================================
# Ashby-specific detection
# =========================================================================


class TestAshbyDetection:
    def test_personal_information_text_returns_contact_info(self):
        """'personal information' in page text triggers CONTACT_INFO."""
        machine = AshbyStateMachine()
        snap = _snap(
            text="Personal Information\nPlease fill in your details below.",
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_resume_text_with_file_input_returns_resume_upload(self):
        """'resume' in text + has_file_inputs triggers RESUME_UPLOAD."""
        machine = AshbyStateMachine()
        snap = _snap(
            text="Upload your resume or CV to continue.",
            has_files=True,
            fields=[
                FieldInfo(selector="#cv", input_type="file", label="Resume"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_resume_text_without_file_input_falls_through(self):
        """'resume' in text but no file inputs → falls through to field detection."""
        machine = AshbyStateMachine()
        snap = _snap(
            text="Attach your resume below.",
            has_files=False,
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
            ],
        )
        # No file inputs, so 'resume' check fails; falls through to _detect_by_fields
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_additional_text_returns_screening_questions(self):
        """'additional' in page text triggers SCREENING_QUESTIONS."""
        machine = AshbyStateMachine()
        snap = _snap(
            text="Additional questions to help us learn more about you.",
            fields=[
                FieldInfo(selector="#q1", input_type="textarea", label="Tell us about yourself"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_screening_text_returns_screening_questions(self):
        """'screening' in page text triggers SCREENING_QUESTIONS."""
        machine = AshbyStateMachine()
        snap = _snap(
            text="Screening questions — please answer all questions honestly.",
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_no_matching_text_falls_back_to_field_detection(self):
        """No matching text sections → generic field-based detection."""
        machine = AshbyStateMachine()
        snap = _snap(
            text="Welcome to our careers page.",
            fields=[
                FieldInfo(selector="#q1", input_type="select", label="Years of experience"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_platform_name(self):
        assert AshbyStateMachine.platform == "ashby"


# =========================================================================
# Jobvite-specific detection
# =========================================================================


class TestJobviteDetection:
    def test_jv_login_id_returns_login_wall(self):
        """Field with jv- prefix and 'login' in id triggers LOGIN_WALL."""
        machine = JobviteStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#jv-login-email",
                    input_type="email",
                    label="Email",
                    attributes={"id": "jv-login-email"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_jv_sign_in_id_returns_login_wall(self):
        """Field with jv- prefix and 'sign' in id triggers LOGIN_WALL."""
        machine = JobviteStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#jv-sign-in-pw",
                    input_type="text",
                    label="Password",
                    attributes={"id": "jv-sign-in-pw"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_jv_resume_id_returns_resume_upload(self):
        """Field with jv- prefix and 'resume' in id triggers RESUME_UPLOAD."""
        machine = JobviteStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#jv-resume-upload",
                    input_type="file",
                    label="Upload Resume",
                    attributes={"id": "jv-resume-upload"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_jv_cv_id_returns_resume_upload(self):
        """Field with jv- prefix and 'cv' in id triggers RESUME_UPLOAD."""
        machine = JobviteStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#jv-cv-file",
                    input_type="file",
                    label="CV",
                    attributes={"id": "jv-cv-file"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_jv_prefix_other_id_falls_back_to_field_detection(self):
        """jv-prefixed field with no special keyword → field-based fallback."""
        machine = JobviteStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#jv-first-name",
                    input_type="text",
                    label="First Name",
                    attributes={"id": "jv-first-name"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_non_jv_prefix_falls_back_to_field_detection(self):
        """Fields without jv- prefix use generic detection."""
        machine = JobviteStateMachine()
        snap = _snap(
            fields=[
                FieldInfo(
                    selector="#email",
                    input_type="email",
                    label="Email",
                    attributes={"id": "email"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_platform_name(self):
        assert JobviteStateMachine.platform == "jobvite"


# =========================================================================
# iCIMS-specific detection
# =========================================================================


class TestICIMSDetection:
    def test_portal_url_sign_in_returns_login_wall(self):
        """/portal/ URL + 'sign in' text triggers LOGIN_WALL."""
        machine = ICIMSStateMachine()
        snap = _snap(
            url="https://careers.company.icims.com/portal/apply/step1",
            text="Sign in to your iCIMS account to continue.",
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_portal_url_create_account_returns_login_wall(self):
        """/portal/ URL + 'create account' text triggers LOGIN_WALL."""
        machine = ICIMSStateMachine()
        snap = _snap(
            url="https://careers.company.icims.com/portal/apply/step1",
            text="Create account to apply for this position.",
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_portal_url_upload_with_file_returns_resume_upload(self):
        """/portal/ URL + 'upload' text + file inputs triggers RESUME_UPLOAD."""
        machine = ICIMSStateMachine()
        snap = _snap(
            url="https://careers.company.icims.com/portal/apply/step2",
            text="Upload your resume to continue.",
            has_files=True,
            fields=[
                FieldInfo(selector="#resume", input_type="file", label="Resume"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.RESUME_UPLOAD

    def test_portal_url_upload_without_file_inputs_falls_through(self):
        """/portal/ URL + 'upload' text but no file inputs → field-based detection."""
        machine = ICIMSStateMachine()
        snap = _snap(
            url="https://careers.company.icims.com/portal/apply/step2",
            text="Upload your resume to continue.",
            has_files=False,
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_non_portal_url_falls_back_to_field_detection(self):
        """URL without /portal/ → field-based detection (iCIMS_* name check + fallback)."""
        machine = ICIMSStateMachine()
        snap = _snap(
            url="https://careers.company.icims.com/jobs/1234/apply",
            fields=[
                FieldInfo(
                    selector="#icims-field",
                    input_type="text",
                    label="First Name",
                    attributes={"name": "iCIMS_FirstName"},
                ),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_portal_url_normal_form_falls_to_field_detection(self):
        """/portal/ URL with no sign-in/upload triggers → field-based detection."""
        machine = ICIMSStateMachine()
        snap = _snap(
            url="https://careers.company.icims.com/portal/apply/step3",
            text="Please answer all screening questions.",
            fields=[
                FieldInfo(selector="#q1", input_type="select", label="Years of experience"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.SCREENING_QUESTIONS

    def test_platform_name(self):
        assert ICIMSStateMachine.platform == "icims"


# =========================================================================
# Taleo-specific detection
# =========================================================================


class TestTaleoDetection:
    def test_sign_in_text_with_no_fillable_fields_returns_login_wall(self):
        """'sign in' + no non-password non-hidden fields → LOGIN_WALL."""
        machine = TaleoStateMachine()
        snap = _snap(
            url="https://company.taleo.net/careersection/apply",
            text="Sign in to your Oracle Taleo account to proceed.",
            fields=[],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_create_account_text_with_no_fillable_fields_returns_login_wall(self):
        """'create an account' + no non-hidden non-password fields → LOGIN_WALL."""
        machine = TaleoStateMachine()
        snap = _snap(
            url="https://company.taleo.net/careersection/apply",
            text="Create an account to apply for this job.",
            fields=[],
        )
        assert machine.detect_state(snap) == ApplicationState.LOGIN_WALL

    def test_sign_in_text_with_fillable_fields_falls_through(self):
        """'sign in' text but there ARE non-password fillable fields → no LOGIN_WALL."""
        machine = TaleoStateMachine()
        snap = _snap(
            url="https://company.taleo.net/careersection/apply",
            text="Sign in or fill in your details below.",
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
            ],
        )
        # The condition requires NO fillable non-password fields to return LOGIN_WALL
        # With a fillable text field, it falls through to _detect_by_fields
        state = machine.detect_state(snap)
        assert state != ApplicationState.LOGIN_WALL

    def test_requisition_url_without_apply_returns_initial(self):
        """URL with 'requisition' but not 'apply' → INITIAL (job description page)."""
        machine = TaleoStateMachine()
        snap = _snap(
            url="https://company.taleo.net/careersection/requisition/12345",
            text="Software Engineer at Acme Corp\nRequirements: Python, SQL",
        )
        assert machine.detect_state(snap) == ApplicationState.INITIAL

    def test_requisition_url_with_apply_falls_through(self):
        """URL with both 'requisition' and 'apply' → not treated as initial."""
        machine = TaleoStateMachine()
        snap = _snap(
            url="https://company.taleo.net/careersection/requisition/12345/apply",
            text="Apply for Software Engineer",
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
            ],
        )
        # 'apply' in URL prevents INITIAL return, falls to _detect_by_fields
        assert machine.detect_state(snap) != ApplicationState.INITIAL

    def test_normal_apply_page_uses_field_detection(self):
        """Standard Taleo apply page with contact fields → CONTACT_INFO."""
        machine = TaleoStateMachine()
        snap = _snap(
            url="https://company.taleo.net/careersection/apply/step1",
            text="Enter your personal information.",
            fields=[
                FieldInfo(selector="#fn", input_type="text", label="First Name"),
                FieldInfo(selector="#ln", input_type="text", label="Last Name"),
                FieldInfo(selector="#em", input_type="email", label="Email"),
            ],
        )
        assert machine.detect_state(snap) == ApplicationState.CONTACT_INFO

    def test_platform_name(self):
        assert TaleoStateMachine.platform == "taleo"


# =========================================================================
# get_state_machine() — all 12 platforms
# =========================================================================


class TestGetStateMachine:
    @pytest.mark.parametrize("platform,expected_platform_attr", [
        ("greenhouse", "greenhouse"),
        ("lever", "lever"),
        ("linkedin", "linkedin"),
        ("indeed", "indeed"),
        ("workday", "workday"),
        ("smartrecruiters", "smartrecruiters"),
        ("bamboohr", "bamboohr"),
        ("ashby", "ashby"),
        ("jobvite", "jobvite"),
        ("icims", "icims"),
        ("taleo", "taleo"),
        ("generic", "generic"),
    ])
    def test_returns_correct_machine(self, platform, expected_platform_attr):
        machine = get_state_machine(platform)
        assert machine.platform == expected_platform_attr

    def test_smartrecruiters_returns_correct_type(self):
        machine = get_state_machine("smartrecruiters")
        assert isinstance(machine, SmartRecruitersStateMachine)

    def test_bamboohr_returns_correct_type(self):
        machine = get_state_machine("bamboohr")
        assert isinstance(machine, BambooHRStateMachine)

    def test_ashby_returns_correct_type(self):
        machine = get_state_machine("ashby")
        assert isinstance(machine, AshbyStateMachine)

    def test_jobvite_returns_correct_type(self):
        machine = get_state_machine("jobvite")
        assert isinstance(machine, JobviteStateMachine)

    def test_icims_returns_correct_type(self):
        machine = get_state_machine("icims")
        assert isinstance(machine, ICIMSStateMachine)

    def test_taleo_returns_correct_type(self):
        machine = get_state_machine("taleo")
        assert isinstance(machine, TaleoStateMachine)

    def test_all_new_platforms_return_fresh_instances(self):
        """Each call returns a new instance — no shared state."""
        for platform in ("smartrecruiters", "bamboohr", "ashby", "jobvite", "icims", "taleo"):
            m1 = get_state_machine(platform)
            m2 = get_state_machine(platform)
            assert m1 is not m2

    def test_all_new_platforms_start_at_initial(self):
        for platform in ("smartrecruiters", "bamboohr", "ashby", "jobvite", "icims", "taleo"):
            machine = get_state_machine(platform)
            assert machine.current_state == ApplicationState.INITIAL

    def test_unknown_platform_still_returns_generic(self):
        machine = get_state_machine("workable")
        assert isinstance(machine, GenericStateMachine)


# =========================================================================
# URL-based platform detection (_detect_ats_platform from ext_adapter.py)
# =========================================================================


class TestNewPlatformURLDetection:
    """Tests for URL pattern matching of the 6 new platforms in _detect_ats_platform."""

    @pytest.fixture(autouse=True)
    def import_detector(self):
        from jobpulse.ext_adapter import _detect_ats_platform
        self._detect = _detect_ats_platform

    def test_smartrecruiters_url(self):
        assert self._detect("https://jobs.smartrecruiters.com/AcmeCorp/apply") == "smartrecruiters"

    def test_smartrecruiters_url_case_insensitive(self):
        assert self._detect("https://jobs.SmartRecruiters.COM/company/job") == "smartrecruiters"

    def test_bamboohr_url(self):
        assert self._detect("https://acme.bamboohr.com/jobs/123/apply") == "bamboohr"

    def test_bamboohr_url_case_insensitive(self):
        assert self._detect("https://ACME.BambooHR.COM/jobs/123") == "bamboohr"

    def test_ashby_hq_url(self):
        assert self._detect("https://jobs.ashbyhq.com/acme/software-engineer") == "ashby"

    def test_ashby_jobs_url(self):
        assert self._detect("https://jobs.ashby.com/acme/software-engineer/apply") == "ashby"

    def test_ashby_hq_subdomain_url(self):
        assert self._detect("https://acme.ashbyhq.com/apply") == "ashby"

    def test_jobvite_url(self):
        assert self._detect("https://jobs.jobvite.com/acmecorp/job/abc123/apply") == "jobvite"

    def test_jobvite_url_case_insensitive(self):
        assert self._detect("https://jobs.JOBVITE.COM/company/apply") == "jobvite"

    def test_icims_url(self):
        assert self._detect("https://careers.acme.icims.com/portal/apply/step1") == "icims"

    def test_icims_url_case_insensitive(self):
        assert self._detect("https://careers.acme.ICIMS.COM/jobs/apply") == "icims"

    def test_taleo_url(self):
        assert self._detect("https://acme.taleo.net/careersection/apply") == "taleo"

    def test_taleo_oracle_careers_url(self):
        assert self._detect("https://www.oracle.com/careers/engineering/job/123") == "taleo"

    def test_taleo_url_case_insensitive(self):
        assert self._detect("https://acme.TALEO.NET/apply") == "taleo"

    def test_greenhouse_still_detected(self):
        """Ensure existing platforms unaffected."""
        assert self._detect("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"

    def test_unknown_url_returns_generic(self):
        assert self._detect("https://careers.example.com/apply") == "generic"

    def test_empty_url_returns_generic(self):
        assert self._detect("") == "generic"
