"""Platform strategy ABC and registry.

Each ATS platform provides a strategy that customizes the shared
NativeFormFiller pipeline: timing, label mappings, navigation selectors,
pre/post hooks, and field scan overrides.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

_STRATEGY_REGISTRY: dict[str, type["BasePlatformStrategy"]] = {}


def register_strategy(cls: type["BasePlatformStrategy"]) -> type["BasePlatformStrategy"]:
    """Class decorator — registers a strategy by its name."""
    _STRATEGY_REGISTRY[cls.name] = cls
    return cls


def get_strategy(platform: str | None) -> "BasePlatformStrategy":
    """Return the strategy for a platform, or GenericStrategy as fallback."""
    key = (platform or "generic").lower()
    cls = _STRATEGY_REGISTRY.get(key)
    if cls is None:
        from jobpulse.ats_adapters.generic import GenericStrategy
        return GenericStrategy()
    return cls()


class BasePlatformStrategy(ABC):
    name: str = "base"
    min_page_time: float = 5.0
    max_form_pages: int = 20

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Return True if this strategy handles this URL."""

    def extra_label_mappings(self) -> dict[str, str]:
        return {}

    async def pre_fill(
        self, page: "Page", cv_path: str | None,
        profile: dict, custom_answers: dict,
    ) -> dict[str, Any]:
        return {}

    async def post_page(
        self, page: "Page", page_num: int, result: dict,
    ) -> None:
        pass

    def next_button_selectors(self) -> list[str]:
        return []

    def screening_defaults(self) -> dict[str, str]:
        return {}

    async def custom_field_scan(self, page: "Page") -> list[dict] | None:
        return None

    def field_fill_overrides(self) -> dict[str, Any]:
        return {}

    async def fill_combobox(
        self, page: "Page", locator: Any, value: str, label: str,
    ) -> str | None:
        """Override combobox fill behavior. Return selected text, or None to use default."""
        return None
