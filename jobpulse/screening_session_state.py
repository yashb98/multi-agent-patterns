"""Per-run session fill state for in-run dedup + introspection answers.

Holds a mapping of every fill the current dry-run has attempted so later
fields (introspection / consent / agreement questions) can resolve
their answers from the session itself rather than re-asking the user
or LLM.

Lifecycle: instantiated per ``apply_job()`` call, passed into
``screening_pipeline.resolve`` and held on ``NativeFormFiller``.
Discarded at the end of the run. NOT a persistent store — see
``jobpulse/form_engine/verified_fills_db.py`` for the cross-run cache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field


_REQUIRED_MARKER_RE = re.compile(
    r"\s*(?:\*|\(\s*required\s*\)|\brequired\b|\(\s*\*\s*\))\s*$",
    re.IGNORECASE,
)


def normalize_label(label: str) -> str:
    """Strip required-markers and lowercase — the in-run lookup key."""
    if not label:
        return ""
    return _REQUIRED_MARKER_RE.sub("", label).rstrip().lower()


@dataclass
class FillRecord:
    label: str
    value: str
    field_type: str
    verified: bool


@dataclass
class SessionFillState:
    """Records every fill attempted in the current run."""

    _fills: dict[str, FillRecord] = dataclass_field(default_factory=dict)

    def record_fill(
        self, label: str, value: str, *, field_type: str, verified: bool,
    ) -> None:
        self._fills[normalize_label(label)] = FillRecord(
            label=label, value=value, field_type=field_type, verified=verified,
        )

    def has_filled(self, label: str) -> bool:
        return normalize_label(label) in self._fills

    def was_verified(self, label: str) -> bool:
        rec = self._fills.get(normalize_label(label))
        return bool(rec and rec.verified)

    def get(self, label: str) -> FillRecord | None:
        return self._fills.get(normalize_label(label))

    def get_filled_labels_normalized(self) -> set[str]:
        return set(self._fills.keys())

    def references_present(self, candidate_labels: list[str]) -> bool:
        norm = {normalize_label(l) for l in candidate_labels if l}
        if not norm:
            return False
        return norm.issubset(self.get_filled_labels_normalized())

    def clear(self) -> None:
        self._fills.clear()
