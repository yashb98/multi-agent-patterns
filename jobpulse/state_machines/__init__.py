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
        """Answer screening questions — full-page LLM analysis.

        Sends the entire form page to Claude in one call. The LLM sees all fields,
        labels, options, and page context simultaneously and returns fill actions
        for every field. Falls back to per-field pattern matching if the LLM fails.
        """
        from jobpulse.form_analyzer import analyze_form_page

        job_context = custom_answers.get("_job_context")
        context_dict = job_context if isinstance(job_context, dict) else None

        # Full-page LLM analysis — single call, all fields at once
        try:
            actions = analyze_form_page(
                snapshot,
                job_context=context_dict,
                platform=self.platform,
            )
            # Trust the LLM's decision — if it returns [] it means no fields
            # should be filled (e.g. search/nav page, not an application form)
            return actions
        except Exception as exc:
            logger.warning("FormAnalyzer failed: %s — falling back to per-field", exc)

        # Fallback: per-field pattern matching (old behavior)
        from jobpulse.screening_answers import get_answer

        actions = []
        for field in snapshot.fields:
            if field.current_value:
                continue

            if form_intelligence is not None:
                field_answer = form_intelligence.resolve(
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

            if field.input_type in ("select", "custom_select"):
                actions.append(Action(type="select", selector=field.selector, value=answer))
            elif field.input_type == "search_autocomplete":
                actions.append(Action(type="fill_autocomplete", selector=field.selector, value=answer))
            elif field.input_type == "radio":
                actions.append(Action(type="fill_radio_group", selector=field.selector, value=answer))
            elif field.input_type == "checkbox":
                actions.append(Action(type="check", selector=field.selector, value=answer))
            elif field.input_type == "date":
                actions.append(Action(type="fill_date", selector=field.selector, value=answer))
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


class SmartRecruitersStateMachine(PlatformStateMachine):
    platform = "smartrecruiters"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        url = snapshot.url.lower()
        # SmartRecruiters uses multi-step modal with "Apply" tab
        if "/apply" in url or "apply" in snapshot.page_text_preview.lower()[:200]:
            return self._detect_by_fields(snapshot)
        # Login wall via SSO prompt
        for field in snapshot.fields:
            if "sso" in field.label.lower() or "sign in" in field.label.lower():
                return ApplicationState.LOGIN_WALL
        return self._detect_by_fields(snapshot)


class BambooHRStateMachine(PlatformStateMachine):
    platform = "bamboohr"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        # BambooHR uses data-testid attributes for form sections
        for field in snapshot.fields:
            attrs = field.attributes
            test_id = attrs.get("data-testid", "")
            if "resume" in test_id or "coverLetter" in test_id:
                return ApplicationState.RESUME_UPLOAD
            if "login" in test_id or "signIn" in test_id:
                return ApplicationState.LOGIN_WALL
        return self._detect_by_fields(snapshot)


class AshbyStateMachine(PlatformStateMachine):
    platform = "ashby"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        # Ashby uses single-page forms with sections identified by class names
        text = snapshot.page_text_preview.lower()
        if "personal information" in text:
            return ApplicationState.CONTACT_INFO
        if "resume" in text and snapshot.has_file_inputs:
            return ApplicationState.RESUME_UPLOAD
        if "additional" in text or "screening" in text:
            return ApplicationState.SCREENING_QUESTIONS
        return self._detect_by_fields(snapshot)


class JobviteStateMachine(PlatformStateMachine):
    platform = "jobvite"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        # Jobvite uses jv-* prefixed form elements
        for field in snapshot.fields:
            attrs = field.attributes
            jv_id = attrs.get("id", "")
            if jv_id.startswith("jv-"):
                if "login" in jv_id or "sign" in jv_id:
                    return ApplicationState.LOGIN_WALL
                if "resume" in jv_id or "cv" in jv_id:
                    return ApplicationState.RESUME_UPLOAD
        return self._detect_by_fields(snapshot)


class ICIMSStateMachine(PlatformStateMachine):
    platform = "icims"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        url = snapshot.url.lower()
        # iCIMS uses /portal/apply/ URL pattern with numbered steps
        if "/portal/" in url:
            text = snapshot.page_text_preview.lower()
            if "sign in" in text or "create account" in text:
                return ApplicationState.LOGIN_WALL
            if "upload" in text and snapshot.has_file_inputs:
                return ApplicationState.RESUME_UPLOAD
        # iCIMS uses iCIMS_* name attributes
        for field in snapshot.fields:
            name = field.attributes.get("name", "")
            if name.startswith("iCIMS"):
                break
        return self._detect_by_fields(snapshot)


class TaleoStateMachine(PlatformStateMachine):
    platform = "taleo"

    def _detect_platform_state(self, snapshot: PageSnapshot) -> ApplicationState:
        # Taleo (Oracle) uses multi-page wizard with numbered steps
        text = snapshot.page_text_preview.lower()
        if "sign in" in text or "create an account" in text:
            if not any(f.input_type not in ("hidden",) for f in snapshot.fields
                       if "password" not in f.label.lower()):
                return ApplicationState.LOGIN_WALL
        # Taleo step detection via URL fragments
        url = snapshot.url.lower()
        if "requisition" in url and "apply" not in url:
            return ApplicationState.INITIAL  # Job description page
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
    "smartrecruiters": SmartRecruitersStateMachine,
    "bamboohr": BambooHRStateMachine,
    "ashby": AshbyStateMachine,
    "jobvite": JobviteStateMachine,
    "icims": ICIMSStateMachine,
    "taleo": TaleoStateMachine,
    "generic": GenericStateMachine,
}


def get_state_machine(platform: str) -> PlatformStateMachine:
    """Return a fresh state machine for the given platform."""
    cls = _MACHINES.get(platform, GenericStateMachine)
    return cls()


import re as _re

_BUTTON_PRIORITY = [
    (_re.compile(r"submit\s*(application|my\s*application)?", _re.IGNORECASE), 100),
    (_re.compile(r"review(\s+(&|and)\s+submit)?", _re.IGNORECASE), 90),
    (_re.compile(r"save\s*(and|&)\s*(continue|next|proceed)", _re.IGNORECASE), 70),
    (_re.compile(r"continue", _re.IGNORECASE), 60),
    (_re.compile(r"next(\s*step)?", _re.IGNORECASE), 50),
    (_re.compile(r"proceed", _re.IGNORECASE), 40),
]

_PROGRESS_PATTERNS = [
    _re.compile(r"step\s+(\d+)\s+(?:of|/)\s+(\d+)", _re.IGNORECASE),
    _re.compile(r"page\s+(\d+)\s+(?:of|/)\s+(\d+)", _re.IGNORECASE),
    _re.compile(r"(\d+)\s+(?:of|/)\s+(\d+)", _re.IGNORECASE),
]


def find_next_button(buttons: list[dict]) -> dict | None:
    """Find highest-priority navigation button (Submit > Review > Continue > Next)."""
    candidates: list[tuple[dict, int]] = []
    for btn in buttons:
        if not btn.get("enabled", True):
            continue
        text = btn.get("text", "")
        for pattern, priority in _BUTTON_PRIORITY:
            if pattern.search(text):
                candidates.append((btn, priority))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def detect_progress(page_text: str) -> tuple[int, int] | None:
    """Parse 'Step 2 of 5' indicators. Returns (current, total) or None."""
    for pattern in _PROGRESS_PATTERNS:
        match = pattern.search(page_text)
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            if 1 <= current <= total <= 20:
                return current, total
    return None


def is_page_stuck(prev_snapshot: dict, curr_snapshot: dict) -> bool:
    """Detect if page hasn't changed. Compares chars 200-700 to skip wrappers."""
    prev_text = prev_snapshot.get("page_text_preview", "")
    curr_text = curr_snapshot.get("page_text_preview", "")
    prev_slice = prev_text[200:700] if len(prev_text) > 700 else prev_text
    curr_slice = curr_text[200:700] if len(curr_text) > 700 else curr_text
    return prev_slice == curr_slice and len(prev_slice) > 10
