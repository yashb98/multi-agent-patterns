"""Base ATS adapter abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

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
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
        engine: str = "extension",
    ) -> dict:
        """Fill form and submit.

        Args:
            overrides: Ralph Loop learned fixes — selector overrides, wait
                adjustments, strategy switches, field remaps, interaction mods.
                Adapters can use resolve_selector() to apply selector overrides.

        Returns:
            dict with keys:
                success (bool): whether submission succeeded
                screenshot (Path | None): path to screenshot if taken
                error (str | None): error message if failed
        """

    def resolve_selector(self, selector: str, overrides: dict[str, Any] | None = None) -> str:
        """Return the override selector if one exists, otherwise the original.

        Adapters should call this before every query_selector() to benefit
        from Ralph Loop learned selector fixes.
        """
        if overrides and selector in overrides.get("selector_overrides", {}):
            new = overrides["selector_overrides"][selector]
            logger.debug("Selector override: %s → %s", selector, new)
            return new
        return selector

    def get_wait_override(self, step: str, default_ms: int, overrides: dict[str, Any] | None = None) -> int:
        """Return learned wait time for a step, or the default."""
        if overrides and step in overrides.get("wait_overrides", {}):
            return overrides["wait_overrides"][step]
        return default_ms

