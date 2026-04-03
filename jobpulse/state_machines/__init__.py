"""Platform-specific application state machines."""

from __future__ import annotations

from enum import StrEnum

from shared.logging_config import get_logger

from jobpulse.ext_models import Action, PageSnapshot

logger = get_logger(__name__)


class ApplicationState(StrEnum):
    """States in the job application flow."""

    INITIAL = "initial"
    LOGIN_WALL = "login_wall"
    CONTACT_INFO = "contact_info"
    RESUME_UPLOAD = "resume_upload"
    EXPERIENCE = "experience"
    SCREENING_QUESTIONS = "screening_questions"
    REVIEW = "review"
    SUBMIT = "submit"
    CONFIRMATION = "confirmation"
    VERIFICATION_WALL = "verification_wall"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ApplicationState.CONFIRMATION,
            ApplicationState.VERIFICATION_WALL,
            ApplicationState.ERROR,
        )


class PlatformStateMachine:
    """Base state machine for job application flows."""

    platform: str = "base"

    def __init__(self) -> None:
        self.current_state = ApplicationState.INITIAL

    def reset(self) -> None:
        self.current_state = ApplicationState.INITIAL

    @property
    def is_terminal(self) -> bool:
        return self.current_state.is_terminal

    def detect_state(self, snapshot: PageSnapshot) -> ApplicationState:
        """Analyze snapshot to determine current application state."""
        # Verification wall takes priority
        if snapshot.verification_wall:
            self.current_state = ApplicationState.VERIFICATION_WALL
            return self.current_state

        # Confirmation detection (universal)
        text = snapshot.page_text_preview.lower()
        if any(
            phrase in text
            for phrase in (
                "thank you for applying",
                "application has been received",
                "application submitted",
                "successfully submitted",
            )
        ):
            self.current_state = ApplicationState.CONFIRMATION
            return self.current_state

        # Platform-specific detection — subclasses override _detect_platform_state
        detected = self._detect_platform_state(snapshot)
        self.current_state = detected
        return detected

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        """Override in subclasses for platform-specific state detection."""
        return self._detect_by_fields(snapshot)

    def _detect_by_fields(self, snapshot: PageSnapshot) -> ApplicationState:
        """Heuristic state detection based on visible fields."""
        labels_lower = [f.label.lower() for f in snapshot.fields]

        # File inputs = resume upload
        if snapshot.has_file_inputs or any(f.input_type == "file" for f in snapshot.fields):
            return ApplicationState.RESUME_UPLOAD

        # Contact fields
        contact_keywords = ("first name", "last name", "email", "phone", "name")
        if any(kw in label for label in labels_lower for kw in contact_keywords):
            return ApplicationState.CONTACT_INFO

        # Screening questions (select/radio/textarea with question-like labels)
        question_types = ("select", "radio", "textarea")
        if any(f.input_type in question_types for f in snapshot.fields):
            return ApplicationState.SCREENING_QUESTIONS

        # Submit button
        for btn in snapshot.buttons:
            btn_text = btn.text.lower()
            if "submit" in btn_text and "application" in btn_text:
                return ApplicationState.SUBMIT

        # Has fields but couldn't classify — treat as screening
        if snapshot.fields:
            return ApplicationState.SCREENING_QUESTIONS

        return ApplicationState.INITIAL

    def get_actions(
        self,
        state: ApplicationState,
        snapshot: PageSnapshot,
        profile: dict[str, str],
        custom_answers: dict[str, str],
        cv_path: str,
        cl_path: str | None,
        form_intelligence: object | None = None,
    ) -> list[Action]:
        """Return ordered list of actions for current state."""
        if state == ApplicationState.CONTACT_INFO:
            return self._actions_contact_info(snapshot, profile)
        if state == ApplicationState.RESUME_UPLOAD:
            return self._actions_resume_upload(snapshot, cv_path, cl_path)
        if state == ApplicationState.SCREENING_QUESTIONS:
            return self._actions_screening(snapshot, profile, custom_answers, form_intelligence)
        if state == ApplicationState.SUBMIT:
            return self._actions_submit(snapshot)
        return []

    def _actions_contact_info(
        self, snapshot: PageSnapshot, profile: dict[str, str]
    ) -> list[Action]:
        """Fill contact info fields from profile."""
        actions: list[Action] = []
        field_map = {
            "first name": "first_name",
            "last name": "last_name",
            "email": "email",
            "phone": "phone",
            "linkedin": "linkedin",
        }
        for field in snapshot.fields:
            label = field.label.lower()
            for keyword, profile_key in field_map.items():
                if keyword in label and profile_key in profile:
                    if not field.current_value:
                        actions.append(
                            Action(
                                type="fill",
                                selector=field.selector,
                                value=profile[profile_key],
                            )
                        )
                    break
        return actions

    def _actions_resume_upload(
        self, snapshot: PageSnapshot, cv_path: str, cl_path: str | None
    ) -> list[Action]:
        """Upload CV (and cover letter if field exists)."""
        actions: list[Action] = []
        for field in snapshot.fields:
            if field.input_type == "file":
                label = field.label.lower()
                if "cover" in label and cl_path:
                    actions.append(
                        Action(type="upload", selector=field.selector, file_path=cl_path)
                    )
                else:
                    actions.append(
                        Action(type="upload", selector=field.selector, file_path=cv_path)
                    )
        return actions

    def _actions_screening(
        self,
        snapshot: PageSnapshot,
        profile: dict[str, str],
        custom_answers: dict[str, str],
        form_intelligence: object | None = None,
    ) -> list[Action]:
        """Answer screening questions — uses FormIntelligence router when provided,
        otherwise falls back to screening_answers.get_answer()."""
        from jobpulse.screening_answers import get_answer

        actions: list[Action] = []
        job_context = custom_answers.get("_job_context")
        # job_context is stored as a string key; pass None to get_answer if not a dict
        context_dict = None
        if isinstance(job_context, dict):
            context_dict = job_context

        for field in snapshot.fields:
            if field.current_value:
                continue  # Already filled

            if form_intelligence is not None:
                field_answer = form_intelligence.resolve(  # type: ignore[union-attr]
                    question=field.label,
                    job_context=context_dict,
                    input_type=field.input_type,
                    platform=self.platform,
                )
                answer = field_answer.answer if field_answer.answer else None
            else:
                answer = get_answer(
                    field.label,
                    context_dict,
                    input_type=field.input_type,
                    platform=self.platform,
                )

            if not answer:
                continue

            if field.input_type == "select":
                actions.append(Action(type="select", selector=field.selector, value=answer))
            elif field.input_type in ("radio", "checkbox"):
                actions.append(Action(type="check", selector=field.selector, value=answer))
            else:
                actions.append(Action(type="fill", selector=field.selector, value=answer))

        return actions

    def _actions_submit(self, snapshot: PageSnapshot) -> list[Action]:
        """Click submit button."""
        for btn in snapshot.buttons:
            if "submit" in btn.text.lower() and btn.enabled:
                return [Action(type="click", selector=btn.selector)]
        return []

    def transition(
        self, from_state: ApplicationState, new_snapshot: PageSnapshot
    ) -> ApplicationState:
        """Transition to next state based on new snapshot."""
        return self.detect_state(new_snapshot)


# --- Platform implementations ---


class GreenhouseStateMachine(PlatformStateMachine):
    platform = "greenhouse"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        return self._detect_by_fields(snapshot)


class LeverStateMachine(PlatformStateMachine):
    platform = "lever"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        return self._detect_by_fields(snapshot)


class LinkedInStateMachine(PlatformStateMachine):
    platform = "linkedin"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        text = snapshot.page_text_preview.lower()

        # Login wall: "sign in" in text and no meaningful fillable fields
        if "sign in" in text:
            fillable = [
                f
                for f in snapshot.fields
                if f.input_type not in ("hidden",) and "password" not in f.label.lower()
            ]
            if not fillable:
                return ApplicationState.LOGIN_WALL

        # Screening questions detection
        labels = [f.label.lower() for f in snapshot.fields]
        if "additional questions" in text or any(
            f.input_type in ("select", "radio") for f in snapshot.fields
        ):
            if any("experience" in lbl or "years" in lbl for lbl in labels):
                return ApplicationState.SCREENING_QUESTIONS
            # Any select/radio = screening
            if any(f.input_type in ("select", "radio") for f in snapshot.fields):
                return ApplicationState.SCREENING_QUESTIONS

        # Review page
        for btn in snapshot.buttons:
            if "review" in btn.text.lower():
                return ApplicationState.REVIEW

        return self._detect_by_fields(snapshot)


class IndeedStateMachine(PlatformStateMachine):
    platform = "indeed"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        return self._detect_by_fields(snapshot)


class WorkdayStateMachine(PlatformStateMachine):
    platform = "workday"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        for field in snapshot.fields:
            attrs = field.attributes
            auto_id = attrs.get("data-automation-id", "")
            if "signIn" in auto_id:
                return ApplicationState.LOGIN_WALL
        return self._detect_by_fields(snapshot)


class GenericStateMachine(PlatformStateMachine):
    platform = "generic"


# --- Registry ---

_MACHINES: dict[str, type[PlatformStateMachine]] = {
    "greenhouse": GreenhouseStateMachine,
    "lever": LeverStateMachine,
    "linkedin": LinkedInStateMachine,
    "indeed": IndeedStateMachine,
    "workday": WorkdayStateMachine,
    "generic": GenericStateMachine,
}


def get_state_machine(platform: str) -> PlatformStateMachine:
    """Return a fresh state machine for the given platform."""
    cls = _MACHINES.get(platform, GenericStateMachine)
    return cls()
