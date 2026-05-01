"""Feature-based page type classifier with calibrated confidence scores.

Replaces the magic confidence numbers in page_analyzer.py with weighted
feature combinations and softmax-normalised confidence.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.form_models import PageSnapshot, PageType

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Embedding anchors — short descriptions per page type for semantic matching
# ---------------------------------------------------------------------------

_PAGE_TYPE_ANCHORS: dict[str, str] = {
    "verification_wall": "security challenge captcha or verification blocking page access",
    "confirmation": "application submitted successfully thank you for applying",
    "email_verification": "check your email to verify your account click the link",
    "session_expired": "session timed out expired please sign in log in again",
    "consent_gate": "agree to terms conditions privacy policy consent data processing",
    "signup_form": "create new account sign up register with email and password",
    "login_form": "sign in log in to your account with email and password",
    "job_description": "job listing role description requirements responsibilities apply button",
    "application_form": "job application form personal details resume upload work experience",
    "unknown": "unrecognized page content",
}

# ---------------------------------------------------------------------------
# Compiled regexes (derived from page_analyzer.py heuristic rules)
# ---------------------------------------------------------------------------

_APPLY_BUTTONS = re.compile(
    r"^(easy\s*apply|apply\s*(now|for\s*this)?|submit\s*application|start\s*application"
    r"|apply\s*for\s*(this\s*)?job|apply\s*on\s*company\s*website"
    r"|i.?m\s*interested|submit\s*interest)$",
    re.IGNORECASE,
)
_LOGIN_BUTTONS = re.compile(r"^(sign\s*in|log\s*in|login)$", re.IGNORECASE)
_SIGNUP_BUTTONS = re.compile(
    r"^(create\s*account|sign\s*up|register|join\s*now|get\s*started)$", re.IGNORECASE
)
_ACCEPT_BUTTONS = re.compile(r"(accept|agree|continue|proceed)", re.IGNORECASE)

_CONFIRMATION_PATTERNS = re.compile(
    r"(thank\s*you\s*(for\s*)?(applying|your\s*application|submitting)"
    r"|application\s*(received|submitted|sent)"
    r"|we\s*(have\s*)?received\s*your\s*application"
    r"|successfully\s*submitted)",
    re.IGNORECASE,
)
_EMAIL_VERIFY_PATTERNS = re.compile(
    r"(check\s*your\s*email|verify\s*your\s*(email|account)"
    r"|sent\s*(a\s*)?(verification|confirmation)\s*(email|link)"
    r"|click\s*the\s*link\s*(in\s*your\s*email|to\s*verify)"
    r"|confirm\s*your\s*email\s*address)",
    re.IGNORECASE,
)
_SESSION_EXPIRED_PATTERNS = re.compile(
    r"(session\s*(has\s*)?(expired|timed?\s*out)"
    r"|please\s*(sign|log)\s*in\s*again"
    r"|you\s*(have\s*)?been\s*(signed|logged)\s*out"
    r"|login\s*session\s*(has\s*)?(expired|ended))",
    re.IGNORECASE,
)
_CONSENT_GATE_PATTERNS = re.compile(
    r"(agree\s*to\s*(our\s*)?(privacy|data)\s*(policy|processing)"
    r"|consent\s*to\s*(the\s*)?(processing|collection|use)\s*of\s*(your\s*)?(personal\s*)?data"
    r"|accept\s*(our\s*)?terms\s*(and|&)\s*(conditions|privacy)"
    r"|by\s*continuing.*consent\s*to)",
    re.IGNORECASE,
)
_APPLICATION_LABELS = re.compile(
    r"(first\s*name|last\s*name|phone|resume|cv|cover\s*letter|linkedin|portfolio"
    r"|work\s*experience|education|sponsorship|right\s*to\s*work|salary|notice\s*period"
    r"|why\s*(are\s*you|do\s*you)\s*(interested|applying))",
    re.IGNORECASE,
)
_JOB_VIEW_URLS = re.compile(
    r"linkedin\.com/jobs/view/"
    r"|boards\.greenhouse\.io/.+/jobs/"
    r"|jobs\.lever\.co/.+/"
    r"|indeed\.com/viewjob"
    r"|\.myworkdayjobs\.com/"
    r"|\.zohorecruit\.\w+/jobs/",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Default weights — derived from existing heuristic rules, structured
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "verification_wall": {
        "bias": 0.0,
        "verification_wall_present": 6.0,
        "embedding_similarity": 1.5,
    },
    "confirmation": {
        "bias": 0.0,
        "confirmation_signal_count": 5.0,
        "embedding_similarity": 2.0,
    },
    "email_verification": {
        "bias": 0.0,
        "email_verify_signal_count": 5.0,
        "embedding_similarity": 2.0,
    },
    "session_expired": {
        "bias": 0.0,
        "session_expired_signal_count": 5.0,
        "embedding_similarity": 1.5,
    },
    "consent_gate": {
        "bias": -1.0,
        "consent_signal_count": 3.0,
        "consent_and_accept": 2.0,
        "no_application_fields": 0.5,
        "embedding_similarity": 2.0,
    },
    "signup_form": {
        "bias": 0.0,
        "password_count_ge_2": 4.0,
        "has_signup_button": 1.5,
        "password_count": 0.5,
        "embedding_similarity": 2.0,
    },
    "login_form": {
        "bias": -1.0,
        "login_all_required": 5.0,
        "has_login_button": 0.5,
        "has_password": 0.5,
        "has_email_field": 0.5,
        "no_application_fields": 0.5,
        "embedding_similarity": 2.0,
    },
    "job_description": {
        "bias": -0.5,
        "has_apply_button": 3.5,
        "no_application_fields": 0.5,
        "no_file_inputs": 0.3,
        "url_job_view_pattern": 2.5,
        "few_fields": 0.3,
        "dialog_is_site_prompt": 2.0,
        "embedding_similarity": 2.0,
    },
    "application_form": {
        "bias": -0.5,
        "has_application_fields": 3.5,
        "has_file_inputs": 2.5,
        "dialog_present": 2.5,
        "dialog_with_form_content": 3.0,
        "dialog_is_site_prompt": -5.0,
        "field_count_ge_3": 2.0,
        "embedding_similarity": 2.0,
    },
    "unknown": {
        "bias": 1.0,
    },
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_SITE_PROMPT_PATTERNS = re.compile(
    r"(are you interested|save.{0,10}application|not interested|maybe later"
    r"|how did you hear|rate.{0,10}experience|take.{0,10}survey"
    r"|subscribe|newsletter|cookie|privacy.{0,5}settings"
    r"|sign up for alerts|job alert|similar jobs|recommended)",
    re.IGNORECASE,
)


@dataclass
class PageFeatures:
    """Extracted features from a page snapshot."""

    has_application_labels: bool
    has_file_inputs: bool
    has_login_button: bool
    has_signup_button: bool
    password_count: int
    confirmation_signals: list[str]
    email_verify_signals: list[str]
    session_expired_signals: list[str]
    consent_signals: list[str]
    dialog_present: bool
    dialog_is_site_prompt: bool
    field_count: int
    button_count: int
    url_path: str
    verification_wall_present: bool
    has_apply_button: bool
    has_email_field: bool
    has_accept_button: bool
    _page_text_preview: str = ""


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class PageTypeClassifier:
    """Feature-based page type classifier with calibrated confidence scores."""

    def __init__(self, weights_path: str | None = None) -> None:
        self.weights = self._load_weights(weights_path)

    def classify(self, snapshot: PageSnapshot | dict[str, Any]) -> tuple[PageType, float]:
        """Classify page type and return confidence (0.0-1.0)."""
        features = self._extract_features(snapshot)
        return self.classify_from_features(features)

    def classify_from_features(self, features: PageFeatures) -> tuple[PageType, float]:
        """Classify from pre-extracted features."""
        scores = self._score_all_types(features)
        best_type = max(scores, key=scores.get)
        confidence = self._normalize_confidence(scores, best_type)
        return best_type, confidence

    def _extract_features(self, snapshot: PageSnapshot | dict[str, Any]) -> PageFeatures:
        if hasattr(snapshot, "model_dump"):
            snapshot_dict = snapshot.model_dump()
        else:
            snapshot_dict = dict(snapshot) if not isinstance(snapshot, dict) else snapshot

        buttons = snapshot_dict.get("buttons", [])
        fields = snapshot_dict.get("fields", [])
        page_text = snapshot_dict.get("page_text_preview", "")
        url = snapshot_dict.get("url", "")
        verification_wall = snapshot_dict.get("verification_wall")

        button_texts = [b.get("text", "") for b in buttons if b.get("text")]
        field_types = [f.get("input_type", "") for f in fields]
        field_labels = [f.get("label", "") for f in fields if f.get("label")]

        has_application_labels = any(
            _APPLICATION_LABELS.search(lbl) for lbl in field_labels
        )
        password_count = sum(1 for t in field_types if t == "password")
        has_email_field = any(t == "email" for t in field_types) or any(
            "email" in lbl.lower() for lbl in field_labels
        )

        dialog_present = snapshot_dict.get("has_dialog", False)
        if not dialog_present:
            dialog_present = snapshot_dict.get("modal_detected", False)
        if not dialog_present:
            dialog_present = any(
                "dialog" in f.get("selector", "").lower() for f in fields
            )

        dialog_text = snapshot_dict.get("dialog_text", "")
        dialog_is_site_prompt = bool(
            dialog_present
            and dialog_text
            and _SITE_PROMPT_PATTERNS.search(dialog_text)
            and not has_application_labels
        )

        return PageFeatures(
            has_application_labels=has_application_labels,
            has_file_inputs=snapshot_dict.get("has_file_inputs", False),
            has_login_button=any(
                _LOGIN_BUTTONS.search(t) for t in button_texts if t
            ),
            has_signup_button=any(
                _SIGNUP_BUTTONS.search(t) for t in button_texts if t
            ),
            password_count=password_count,
            confirmation_signals=_find_matches(_CONFIRMATION_PATTERNS, page_text),
            email_verify_signals=_find_matches(_EMAIL_VERIFY_PATTERNS, page_text),
            session_expired_signals=_find_matches(
                _SESSION_EXPIRED_PATTERNS, page_text
            ),
            consent_signals=_find_matches(_CONSENT_GATE_PATTERNS, page_text),
            dialog_present=dialog_present,
            dialog_is_site_prompt=dialog_is_site_prompt,
            field_count=len(fields),
            button_count=len(buttons),
            url_path=url,
            verification_wall_present=verification_wall is not None,
            has_apply_button=any(
                _APPLY_BUTTONS.search(t) for t in button_texts if t
            ),
            has_email_field=has_email_field,
            has_accept_button=any(
                _ACCEPT_BUTTONS.search(t) for t in button_texts if t
            ),
            _page_text_preview=page_text[:200] if page_text else "",
        )

    def _compute_embedding_scores(self, features: PageFeatures) -> dict[str, float]:
        """Compute embedding similarity between page text and each page type anchor."""
        try:
            from shared.semantic_utils import semantic_similarity

            page_text = features._page_text_preview
            if not page_text or len(page_text.strip()) < 30:
                return {}
            scores: dict[str, float] = {}
            for page_type, anchor in _PAGE_TYPE_ANCHORS.items():
                scores[page_type] = semantic_similarity(page_text[:200], anchor)
            return scores
        except Exception:
            return {}

    def _score_all_types(self, features: PageFeatures) -> dict[PageType, float]:
        has_login_or_signup = (
            features.has_login_button
            or features.has_signup_button
            or features.has_email_field
            or features.password_count >= 1
        )
        wall_is_embedded = features.verification_wall_present and has_login_or_signup

        derived: dict[str, float] = {
            "bias": 1.0,
            "verification_wall_present": 0.0 if wall_is_embedded else (1.0 if features.verification_wall_present else 0.0),
            "confirmation_signal_count": float(len(features.confirmation_signals)),
            "email_verify_signal_count": float(len(features.email_verify_signals)),
            "session_expired_signal_count": float(len(features.session_expired_signals)),
            "consent_signal_count": float(len(features.consent_signals)),
            "consent_and_accept": (
                1.0
                if features.consent_signals and features.has_accept_button
                else 0.0
            ),
            "no_application_fields": (
                1.0 if not features.has_application_labels else 0.0
            ),
            "password_count_ge_2": (
                1.0 if features.password_count >= 2 else 0.0
            ),
            "has_signup_button": 1.0 if features.has_signup_button else 0.0,
            "password_count": float(features.password_count),
            "has_login_button": 1.0 if features.has_login_button else 0.0,
            "has_password": 1.0 if features.password_count >= 1 else 0.0,
            "has_email_field": 1.0 if features.has_email_field else 0.0,
            "login_all_required": (
                1.0
                if (
                    features.has_login_button
                    and features.password_count >= 1
                    and features.has_email_field
                    and not features.has_application_labels
                )
                else 0.0
            ),
            "has_apply_button": 1.0 if features.has_apply_button else 0.0,
            "no_file_inputs": 1.0 if not features.has_file_inputs else 0.0,
            "url_job_view_pattern": (
                1.0
                if features.url_path and _JOB_VIEW_URLS.search(features.url_path)
                else 0.0
            ),
            "few_fields": 1.0 if features.field_count <= 5 else 0.0,
            "has_application_fields": 1.0 if features.has_application_labels else 0.0,
            "has_file_inputs": 1.0 if features.has_file_inputs else 0.0,
            "dialog_present": 1.0 if features.dialog_present and not features.dialog_is_site_prompt else 0.0,
            "dialog_is_site_prompt": 1.0 if features.dialog_is_site_prompt else 0.0,
            "dialog_with_form_content": (
                1.0
                if (
                    features.dialog_present
                    and not features.dialog_is_site_prompt
                    and (features.has_application_labels or features.field_count >= 3)
                )
                else 0.0
            ),
            "field_count_ge_3": 1.0 if features.field_count >= 3 else 0.0,
        }

        embedding_scores = self._compute_embedding_scores(features)

        scores: dict[PageType, float] = {}
        for page_type in PageType:
            type_weights = self.weights.get(page_type.value, {})
            score = type_weights.get("bias", 0.0)
            for feature_name, weight in type_weights.items():
                if feature_name == "bias":
                    continue
                if feature_name == "embedding_similarity":
                    value = embedding_scores.get(page_type.value, 0.0)
                else:
                    value = derived.get(feature_name, 0.0)
                score += value * weight
            scores[page_type] = score

        return scores

    def _normalize_confidence(
        self, scores: dict[PageType, float], best: PageType
    ) -> float:
        max_score = max(scores.values())
        exp_scores: dict[PageType, float] = {}
        for pt, s in scores.items():
            try:
                exp_scores[pt] = math.exp(s - max_score)
            except OverflowError:
                exp_scores[pt] = 0.0

        total = sum(exp_scores.values())
        if total == 0:
            return 0.0
        return exp_scores[best] / total

    def _load_weights(self, path: str | None) -> dict[str, dict[str, float]]:
        if path and Path(path).exists():
            with open(path, encoding="utf-8") as f:
                loaded: dict[str, dict[str, float]] = json.load(f)
            logger.info("Loaded classifier weights from %s", path)
            return loaded
        return DEFAULT_WEIGHTS.copy()


def _find_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    """Return all non-overlapping matches of pattern in text."""
    return [m.group(0) for m in pattern.finditer(text)]
