"""FormVerifier -- heuristic + vision checks for form fill correctness.

Runs after every form.fields_filled event to catch mistakes early.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.logging_config import get_logger

logger = get_logger(__name__)

_PHONE_PATTERN = re.compile(r"^[+\d\s\-()]{7,20}$")
_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_NAME_PATTERN = re.compile(r"^[A-Za-z\s\-'.]{2,60}$")

_PHONE_LABELS = {"phone", "telephone", "mobile", "cell", "contact number", "phone number"}
_EMAIL_LABELS = {"email", "e-mail", "email address"}
_NAME_LABELS = {"name", "full name", "first name", "last name", "surname"}
_FILE_LABELS = {"resume", "cv", "cover letter", "attachment", "upload"}


@dataclass
class VerifyResult:
    """Result of a single verification check."""

    field_mismatch: bool = False
    duplicate_upload: bool = False
    empty_required: bool = False
    unexpected_element: bool = False
    details: str = ""

    @property
    def all_ok(self) -> bool:
        return not (
            self.field_mismatch
            or self.duplicate_upload
            or self.empty_required
            or self.unexpected_element
        )


class FormVerifier:
    """Heuristic checks for form fill correctness."""

    def check_field_mismatches(self, results: list[dict]) -> VerifyResult:
        """Detect values that don't match their field type (e.g. name in phone)."""
        issues: list[str] = []
        for r in results:
            label = r.get("label", "").lower().strip()
            value = str(r.get("value", ""))
            if not value:
                continue
            if any(k in label for k in _PHONE_LABELS):
                if not _PHONE_PATTERN.match(value):
                    issues.append(
                        f"Phone field '{r['label']}' has non-phone value: '{value[:30]}'"
                    )
            if any(k in label for k in _EMAIL_LABELS):
                if not _EMAIL_PATTERN.match(value):
                    issues.append(
                        f"Email field '{r['label']}' has non-email value: '{value[:30]}'"
                    )
            if any(k in label for k in _NAME_LABELS):
                if _PHONE_PATTERN.match(value) or _EMAIL_PATTERN.match(value):
                    issues.append(
                        f"Name field '{r['label']}' has non-name value: '{value[:30]}'"
                    )
        if issues:
            logger.warning("Field mismatches detected: %s", "; ".join(issues))
        return VerifyResult(
            field_mismatch=len(issues) > 0,
            details="; ".join(issues),
        )

    def check_duplicate_uploads(self, events: list[dict]) -> VerifyResult:
        """Detect the same file uploaded more than once across pages."""
        uploads: list[tuple[str, str]] = []
        for e in events:
            if e.get("event_type") != "form.fields_filled":
                continue
            for r in e.get("payload", {}).get("results", []):
                label = r.get("label", "").lower()
                if any(k in label for k in _FILE_LABELS) and r.get("value"):
                    uploads.append((r["label"], r["value"]))
        seen_files: set[str] = set()
        for label, value in uploads:
            if value in seen_files:
                logger.warning("Duplicate upload detected: %s", value)
                return VerifyResult(
                    duplicate_upload=True,
                    details=f"File '{value}' uploaded multiple times",
                )
            seen_files.add(value)
        return VerifyResult()

    def check_empty_required(self, results: list[dict]) -> VerifyResult:
        """Detect required fields left empty."""
        empty: list[str] = []
        for r in results:
            if r.get("required") and not str(r.get("value", "")).strip():
                empty.append(r.get("label", "unknown"))
        if empty:
            details = f"Empty required fields: {', '.join(empty)}"
            logger.warning(details)
            return VerifyResult(empty_required=True, details=details)
        return VerifyResult()
