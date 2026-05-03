"""Signal Interpreter — classifies browser signals into corrective actions.

Three-gate verification pipeline:
  Gate 1: Temporal — signal within 2s of fill
  Gate 2: DOM cross-check — aria-invalid or visible error element
  Gate 3: Field association — temporal, DOM proximity, or text matching

Classification via keyword tiers (no LLM for 95%+ of cases).
Deterministic correction transforms for format/range/type errors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

    from jobpulse.browser_intelligence import BrowserIntelligence, CapturedSignal

logger = get_logger(__name__)

_TEMPORAL_WINDOW_MS = 2000


class SignalType(str, Enum):
    FORMAT_ERROR = "format_error"
    REQUIRED_FIELD = "required_field"
    DUPLICATE = "duplicate"
    RANGE_ERROR = "range_error"
    TYPE_MISMATCH = "type_mismatch"
    OPTION_INVALID = "option_invalid"
    SUBMISSION_BLOCKED = "submission_blocked"
    UNKNOWN = "unknown"


@dataclass
class CorrectionAction:
    """A corrective action derived from a browser signal."""

    signal_type: str
    field_label: str
    error_message: str
    suggested_value: str | None
    transform: str
    confidence: float


@dataclass
class SubmissionError:
    """An error detected after form submission attempt."""

    field_label: str
    error_message: str
    signal_type: str


# ── Tier 1: Exact phrase matching ────────────────────────────────────────

_EXACT_RULES: list[tuple[str, SignalType]] = [
    ("is required", SignalType.REQUIRED_FIELD),
    ("cannot be blank", SignalType.REQUIRED_FIELD),
    ("cannot be empty", SignalType.REQUIRED_FIELD),
    ("must not be empty", SignalType.REQUIRED_FIELD),
    ("field is required", SignalType.REQUIRED_FIELD),
    ("please fill", SignalType.REQUIRED_FIELD),
    ("this field is required", SignalType.REQUIRED_FIELD),
    ("already registered", SignalType.DUPLICATE),
    ("already exists", SignalType.DUPLICATE),
    ("already in use", SignalType.DUPLICATE),
    ("account already", SignalType.DUPLICATE),
    ("please select", SignalType.OPTION_INVALID),
    ("select a valid", SignalType.OPTION_INVALID),
    ("not a valid option", SignalType.OPTION_INVALID),
    ("choose an option", SignalType.OPTION_INVALID),
    ("fix errors before", SignalType.SUBMISSION_BLOCKED),
    ("complete required fields", SignalType.SUBMISSION_BLOCKED),
    ("review your answers", SignalType.SUBMISSION_BLOCKED),
    ("please correct", SignalType.SUBMISSION_BLOCKED),
]

# ── Tier 2: Keyword cluster matching ─────────────────────────────────────

_KEYWORD_RULES: list[tuple[set[str], set[str], SignalType]] = [
    ({"format", "must be", "invalid format", "not valid", "incorrect format"},
     {"phone", "email", "date", "url", "postal", "zip", "postcode"},
     SignalType.FORMAT_ERROR),
    ({"minimum", "maximum", "between", "at least", "no more", "at most", "too short", "too long", "characters"},
     set(),
     SignalType.RANGE_ERROR),
    ({"must be a number", "numeric", "not a number", "integer", "decimal only", "digits only"},
     set(),
     SignalType.TYPE_MISMATCH),
]

# ── Correction transforms ───────────────────────────────────────────────


def _parse_date_to_iso(v: str) -> str:
    """Best-effort date normalization to YYYY-MM-DD."""
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(v.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return v


TRANSFORMS: dict[str, Any] = {
    "prepend_country_code": lambda v: "+44" + v.lstrip("0") if v.startswith("0") and not v.startswith("+") else v,
    "strip_non_numeric": lambda v: re.sub(r"[^\d]", "", v),
    "strip_currency": lambda v: re.sub(r"[£$€,\s]", "", v),
    "to_iso_date": _parse_date_to_iso,
    "lowercase_email": lambda v: v.lower().strip(),
    "strip_whitespace": lambda v: v.strip(),
    "none": lambda v: v,
}


def _classify_signal(text: str) -> SignalType:
    """Classify a signal via keyword tiers. No LLM."""
    text_lower = text.lower()

    for phrase, signal_type in _EXACT_RULES:
        if phrase in text_lower:
            return signal_type

    for primary_kws, secondary_kws, signal_type in _KEYWORD_RULES:
        if any(kw in text_lower for kw in primary_kws):
            if not secondary_kws or any(kw in text_lower for kw in secondary_kws):
                return signal_type

    return SignalType.UNKNOWN


def _infer_transform(signal_type: SignalType, error_text: str, field_label: str) -> str:
    """Infer the best correction transform from signal type and context."""
    label_lower = field_label.lower()
    text_lower = error_text.lower()

    if signal_type == SignalType.FORMAT_ERROR:
        if "phone" in label_lower or "phone" in text_lower:
            if "country" in text_lower or "+44" in text_lower or "international" in text_lower:
                return "prepend_country_code"
        if "email" in label_lower or "email" in text_lower:
            return "lowercase_email"
        if "date" in label_lower or "date" in text_lower:
            return "to_iso_date"
        if "postal" in label_lower or "zip" in label_lower or "postcode" in label_lower:
            return "strip_whitespace"

    if signal_type == SignalType.TYPE_MISMATCH:
        return "strip_non_numeric"

    if signal_type == SignalType.RANGE_ERROR:
        if "£" in error_text or "$" in error_text or "salary" in label_lower:
            return "strip_currency"

    return "none"


def _extract_range_bounds(text: str) -> tuple[int | None, int | None]:
    """Extract numeric bounds from error messages like 'between 1 and 100'."""
    m = re.search(r"between\s+(\d+)\s+and\s+(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"at least\s+(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), None
    m = re.search(r"(?:no more than|at most|maximum)\s+(\d+)", text, re.IGNORECASE)
    if m:
        return None, int(m.group(1))
    return None, None


def _is_form_relevant(text: str) -> bool:
    """Check if a signal text is likely a form validation error."""
    form_keywords = {
        "required", "invalid", "must", "format", "error", "cannot",
        "please", "field", "select", "enter", "provide", "valid",
        "match", "already", "minimum", "maximum", "between", "number",
        "numeric", "blank", "empty",
    }
    text_lower = text.lower()
    return any(kw in text_lower for kw in form_keywords)


# ── Field association ────────────────────────────────────────────────────

_FIELD_ASSOCIATION_JS = """(errorEl) => {
    // Strategy 1: aria-describedby / aria-errormessage link
    const id = errorEl.getAttribute('id');
    if (id) {
        const linked = document.querySelector(
            '[aria-describedby="' + id + '"], [aria-errormessage="' + id + '"]'
        );
        if (linked) return {
            label: linked.getAttribute('aria-label')
                || linked.getAttribute('name')
                || linked.getAttribute('placeholder') || '',
            strategy: 'aria_link'
        };
    }

    // Strategy 2: same parent container
    const container = errorEl.closest(
        '.form-group, .field-wrapper, .form-field, [class*=field], [class*=form-row]'
    );
    if (container) {
        const input = container.querySelector('input, select, textarea');
        if (input) return {
            label: input.getAttribute('aria-label')
                || input.getAttribute('name')
                || input.getAttribute('placeholder') || '',
            strategy: 'container'
        };
    }

    // Strategy 3: previous sibling input
    let prev = errorEl.previousElementSibling;
    let depth = 0;
    while (prev && depth < 5) {
        if (prev.matches('input, select, textarea')) return {
            label: prev.getAttribute('aria-label')
                || prev.getAttribute('name')
                || prev.getAttribute('placeholder') || '',
            strategy: 'sibling'
        };
        const inner = prev.querySelector('input, select, textarea');
        if (inner) return {
            label: inner.getAttribute('aria-label')
                || inner.getAttribute('name')
                || inner.getAttribute('placeholder') || '',
            strategy: 'sibling_inner'
        };
        prev = prev.previousElementSibling;
        depth++;
    }

    return null;
}"""

_DOM_CROSS_CHECK_JS = """(fieldEl) => {
    const invalid = fieldEl.getAttribute('aria-invalid') === 'true';

    const parent = fieldEl.closest(
        '.form-group, .field-wrapper, [class*=field], [class*=form]'
    ) || fieldEl.parentElement;
    let hasErrorEl = false;
    if (parent) {
        const errEl = parent.querySelector(
            '[role="alert"], .error, .field-error, .validation-error, '
            + '.invalid-feedback, [class*=error], [aria-live="polite"][class*=error]'
        );
        hasErrorEl = !!(errEl && errEl.offsetHeight > 0 && errEl.textContent.trim().length > 0);
    }

    return {invalid: invalid, hasErrorEl: hasErrorEl};
}"""


class SignalInterpreter:
    """Interprets captured browser signals into corrective actions."""

    async def check_after_fill(
        self,
        intelligence: BrowserIntelligence,
        field_label: str,
        field_locator: Locator,
        fill_timestamp_ms: float,
        page: Page,
    ) -> CorrectionAction | None:
        """Check for signals after a field fill attempt."""
        await intelligence.poll_mutations()

        signals = intelligence.get_signals(since_ms=fill_timestamp_ms)
        if not signals:
            return None

        relevant = [
            s for s in signals
            if s.timestamp_ms - fill_timestamp_ms <= _TEMPORAL_WINDOW_MS
            and _is_form_relevant(s.text)
        ]
        if not relevant:
            return None

        dom_state = await self._dom_cross_check(field_locator, page)
        if not dom_state.get("invalid") and not dom_state.get("hasErrorEl"):
            return None

        for signal in relevant:
            associated_label = self._associate_signal_to_field(
                signal, field_label,
            )
            if not associated_label:
                continue

            signal_type = _classify_signal(signal.text)
            if signal_type == SignalType.UNKNOWN:
                continue

            transform_name = _infer_transform(signal_type, signal.text, field_label)
            transform_fn = TRANSFORMS.get(transform_name, TRANSFORMS["none"])

            suggested = None
            confidence = 0.8

            if signal_type == SignalType.RANGE_ERROR:
                lo, hi = _extract_range_bounds(signal.text)
                if lo is not None or hi is not None:
                    suggested = str(lo) if lo is not None else str(hi)
                    confidence = 0.7

            if signal_type in (SignalType.REQUIRED_FIELD, SignalType.DUPLICATE, SignalType.SUBMISSION_BLOCKED):
                confidence = 0.9
                suggested = None

            logger.info(
                "Signal interpreted: type=%s field='%s' error='%s' transform=%s",
                signal_type.value, field_label, signal.text[:80], transform_name,
            )

            return CorrectionAction(
                signal_type=signal_type.value,
                field_label=field_label,
                error_message=signal.text,
                suggested_value=suggested,
                transform=transform_name,
                confidence=confidence,
            )

        return None

    async def check_after_submit(
        self,
        intelligence: BrowserIntelligence,
        page: Page,
    ) -> list[SubmissionError]:
        """Check for errors after a form submission attempt."""
        await intelligence.poll_mutations()

        signals = intelligence.get_signals()
        errors: list[SubmissionError] = []

        for signal in signals:
            if not _is_form_relevant(signal.text):
                continue

            signal_type = _classify_signal(signal.text)

            if signal.source == "network" and signal.metadata.get("status_code", 0) >= 400:
                field_errors = self._extract_network_field_errors(signal.text)
                for field_name, msg in field_errors.items():
                    errors.append(SubmissionError(
                        field_label=field_name,
                        error_message=msg,
                        signal_type=_classify_signal(msg).value,
                    ))
                if not field_errors and signal_type != SignalType.UNKNOWN:
                    errors.append(SubmissionError(
                        field_label="",
                        error_message=signal.text[:200],
                        signal_type=signal_type.value,
                    ))

            elif signal_type == SignalType.SUBMISSION_BLOCKED:
                errors.append(SubmissionError(
                    field_label="",
                    error_message=signal.text[:200],
                    signal_type=signal_type.value,
                ))

        return errors

    async def verify_correction(
        self,
        field_locator: Locator,
        page: Page,
    ) -> bool:
        """Verify that a correction resolved the error."""
        try:
            dom_state = await self._dom_cross_check(field_locator, page)
            return not dom_state.get("invalid") and not dom_state.get("hasErrorEl")
        except Exception:
            return False

    async def _dom_cross_check(self, field_locator: Locator, page: Page) -> dict:
        """Check DOM state around a field for validation errors."""
        try:
            handle = await field_locator.element_handle(timeout=2000)
            if not handle:
                return {}
            result = await page.evaluate(_DOM_CROSS_CHECK_JS, handle)
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}

    def _associate_signal_to_field(
        self,
        signal: CapturedSignal,
        filled_field_label: str,
    ) -> str | None:
        """Associate a signal to a field label."""
        if signal.source == "mutation":
            mutation_label = signal.metadata.get("field_label", "")
            if mutation_label:
                if self._labels_match(mutation_label, filled_field_label):
                    return filled_field_label
                return None
            return filled_field_label

        if signal.source == "network":
            field_errors = self._extract_network_field_errors(signal.text)
            for api_name in field_errors:
                if self._labels_match(api_name, filled_field_label):
                    return filled_field_label
            if not field_errors:
                return filled_field_label
            return None

        return filled_field_label

    def _extract_network_field_errors(self, body: str) -> dict[str, str]:
        """Extract field-level errors from an API response body."""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return {}

        errors = data.get("errors", data.get("error", data.get("fieldErrors", {})))

        if isinstance(errors, dict):
            result = {}
            for key, val in errors.items():
                if isinstance(val, list):
                    result[key] = "; ".join(str(v) for v in val)
                elif isinstance(val, str):
                    result[key] = val
                else:
                    result[key] = str(val)
            return result

        return {}

    @staticmethod
    def _labels_match(a: str, b: str) -> bool:
        """Fuzzy match two field labels."""
        def _norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())
        na, nb = _norm(a), _norm(b)
        if not na or not nb:
            return False
        return na == nb or na in nb or nb in na
