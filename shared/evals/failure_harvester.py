"""Auto-generate eval cases from production failures.

Reads from:
- FormExperienceDB.form_failure_reasons table
- .claude/mistakes.md entries
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class FailureHarvester:
    def __init__(
        self,
        form_experience_db=None,
        mistakes_path: str | None = None,
    ):
        self._form_db = form_experience_db
        self._mistakes_path = mistakes_path

    def harvest_form_failures(self) -> list[dict[str, Any]]:
        if self._form_db is None:
            return []
        try:
            import sqlite3
            with sqlite3.connect(self._form_db._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM form_failure_reasons ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        except Exception:
            return []

        cases = []
        for i, row in enumerate(rows):
            r = dict(row)
            cases.append({
                "case_id": f"harvest-fail-{i+1:03d}",
                "flow": "fill_failure_class",
                "input": {
                    "error_message": r.get("details", "unknown error"),
                    "field_label": r.get("field_label", ""),
                    "field_type": "text",
                },
                "expected": {
                    "failure_class": r.get("failure_type", "unknown"),
                },
            })
        return cases

    def harvest_mistakes(self) -> list[dict[str, Any]]:
        if not self._mistakes_path:
            return []
        path = Path(self._mistakes_path)
        if not path.exists():
            return []

        text = path.read_text(encoding="utf-8")
        entries = re.findall(
            r"-\s+\*\*(\w+)\*\*:\s+(.+)",
            text,
        )

        cases = []
        for i, (category, description) in enumerate(entries):
            flow = "fill_failure_class"
            if "screening" in category.lower():
                flow = "screening_answer"
            elif "field_mapping" in category.lower() or "mapping" in category.lower():
                flow = "field_mapping"
            elif "page" in category.lower() or "classification" in category.lower():
                flow = "page_classification"

            cases.append({
                "case_id": f"harvest-mistake-{i+1:03d}",
                "flow": flow,
                "input": {
                    "description": description.strip(),
                    "category": category,
                },
                "expected": {},
            })
        return cases

    def harvest_all(self) -> list[dict[str, Any]]:
        return self.harvest_form_failures() + self.harvest_mistakes()
