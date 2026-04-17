"""TSV-based batch state tracking for resumability."""

from __future__ import annotations

import csv
from datetime import datetime, UTC
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)


class BatchState:
    """Track batch job statuses in a TSV file for crash recovery."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._entries: dict[str, dict] = {}
        if self._path.exists():
            self._load()

    def _load(self) -> None:
        with open(self._path, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                self._entries[row["job_id"]] = row

    def _save(self) -> None:
        fields = ["job_id", "status", "started_at", "completed_at", "score", "error", "retries"]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            for entry in self._entries.values():
                writer.writerow({k: entry.get(k, "") for k in fields})

    def mark_started(self, job_id: str) -> None:
        self._entries[job_id] = {
            "job_id": job_id,
            "status": "started",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": "",
            "score": "",
            "error": "",
            "retries": "0",
        }
        self._save()

    def mark_completed(self, job_id: str, score: float = 0.0) -> None:
        entry = self._entries.get(job_id, {"job_id": job_id})
        entry["status"] = "completed"
        entry["completed_at"] = datetime.now(UTC).isoformat()
        entry["score"] = str(score)
        self._entries[job_id] = entry
        self._save()

    def mark_failed(self, job_id: str, error: str = "") -> None:
        entry = self._entries.get(job_id, {"job_id": job_id})
        entry["status"] = "failed"
        entry["error"] = error
        retries = int(entry.get("retries", "0")) + 1
        entry["retries"] = str(retries)
        self._entries[job_id] = entry
        self._save()

    def get_status(self, job_id: str) -> str | None:
        entry = self._entries.get(job_id)
        return entry["status"] if entry else None

    def get_pending(self, job_ids: list[str]) -> list[str]:
        """Return job IDs that need processing (not yet completed)."""
        return [
            jid for jid in job_ids
            if self.get_status(jid) not in ("completed", "started")
        ]
