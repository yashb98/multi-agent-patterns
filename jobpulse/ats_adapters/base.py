"""Base ATS adapter abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypedDict

from shared.logging_config import get_logger

logger = get_logger(__name__)


class FillSubmitResult(TypedDict, total=False):
    success: bool
    screenshot: Path | None
    error: str | None
    field_types: dict[str, str]
    screening_questions: list[dict]
    time_seconds: float
    pages: int


class BaseATSAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Return True if this adapter handles this URL."""

    @abstractmethod
    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
        engine: str = "extension",
    ) -> FillSubmitResult:
        """Fill form and submit.

        Args:
            overrides: learned fixes — selector overrides, wait adjustments,
                strategy switches, field remaps, interaction mods.
        """

