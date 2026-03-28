"""Base ATS adapter abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)


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
    ) -> dict:
        """Fill form and submit.

        Returns:
            dict with keys:
                success (bool): whether submission succeeded
                screenshot (Path | None): path to screenshot if taken
                error (str | None): error message if failed
        """
