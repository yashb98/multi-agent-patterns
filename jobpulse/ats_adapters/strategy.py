"""Platform strategy ABC and registry.

Each ATS platform provides a strategy that customizes the shared
form-filling pipeline: timing, label mappings, navigation selectors,
pre/post hooks, field scan overrides, widget libraries, and wait times.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

_STRATEGY_REGISTRY: dict[str, type["BasePlatformStrategy"]] = {}


def register_strategy(cls: type["BasePlatformStrategy"]) -> type["BasePlatformStrategy"]:
    """Class decorator — registers a strategy by its name."""
    _STRATEGY_REGISTRY[cls.name] = cls
    return cls


def get_strategy(
    platform: str | None,
    url: str | None = None,
) -> "BasePlatformStrategy":
    """Return the strategy for a platform.

    Resolution order:
    1. Hand-coded strategy registered by name (greenhouse, workday, etc.)
    2. LearnedStrategy synthesized from FormExperienceDB if URL's domain
       has ≥3 successful applications (only when url is provided)
    3. GenericStrategy as final fallback
    """
    key = (platform or "generic").lower()
    cls = _STRATEGY_REGISTRY.get(key)
    if cls is not None:
        return cls()

    # Synthesis path — runtime-generated strategy from accumulated FE data
    if url:
        try:
            from jobpulse.ats_adapters._strategy_synthesis import (
                synthesize_strategy_for_domain,
            )
            learned = synthesize_strategy_for_domain(url)
            if learned is not None:
                return learned
        except Exception as exc:
            # OPRAL: synthesis is the runtime alternative to a hand-coded
            # strategy. A failure here means the apply pipeline silently
            # falls back to GenericStrategy without using accumulated FE data.
            logger.warning("get_strategy: synthesis failed for %r: %s", url, exc)

    from jobpulse.ats_adapters.generic import GenericStrategy
    return GenericStrategy()


class BasePlatformStrategy(ABC):
    """Base class for per-platform customization of the form-filling pipeline.

    Subclasses override only what differs from the generic pipeline.
    All methods have safe defaults; the unified engine calls them
    opportunistically and falls back to generic behaviour.
    """

    name: str = "base"
    min_page_time: float = 5.0
    max_form_pages: int = 20

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Return True if this strategy handles this URL."""

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def apply_button_selectors(self) -> list[str]:
        """CSS selectors for known apply buttons on this platform.

        These are used *in addition to* the unified text scoring in
        ``score_apply_button``.  They help when the button text is
        non-English or heavily styled.
        """
        return []

    def next_page_selectors(self) -> list[str]:
        """Selectors for 'Next', 'Continue', 'Review' pagination buttons."""
        return []

    def submit_selectors(self) -> list[str]:
        """Selectors for final 'Submit Application' buttons."""
        return []

    # ------------------------------------------------------------------
    # Timing / waits
    # ------------------------------------------------------------------

    def wait_for_form_hydrated_ms(self) -> int:
        """How long to wait for form fields to appear after navigation.

        Workday, for example, can take 15 s+ to hydrate.
        """
        return 5000

    # ------------------------------------------------------------------
    # Field scanning
    # ------------------------------------------------------------------

    def form_container_hint(self) -> str | None:
        """CSS selector for the form container on this platform.

        Used as a Tier 3 fallback during container resolution.
        """
        return None

    def expected_field_range(self) -> tuple[int, int]:
        """Expected (min, max) number of fields on a single form page."""
        return (1, 30)

    def known_widget_libraries(self) -> list[str]:
        """Widget libraries commonly used by this platform.

        The engine prioritises these libraries during widget detection.
        """
        return []

    def iframe_names(self) -> list[str]:
        """Iframes that may contain the actual form."""
        return []

    async def custom_field_scan(self, page: "Page") -> list[dict] | None:
        """Platform-specific field scan override.

        Return a list of raw field dicts, or None to use the unified scanner.
        """
        return None

    # ------------------------------------------------------------------
    # Label / mapping
    # ------------------------------------------------------------------

    def normalize_label(self, label: str) -> str:
        """Platform-specific label cleaning.

        E.g. strip mandatory asterisks, normalise Unicode, collapse whitespace.
        """
        return label

    def extra_label_mappings(self) -> dict[str, str]:
        """Extra label → profile-key mappings for this platform."""
        return {}

    # NOTE: screening defaults are NOT defined on strategies. Screening answers
    # are PII (.claude/rules/pii-policy.md) and MUST come from `ScreeningPipeline`
    # at runtime (DB cache → intent classifier → option aligner → LLM fallback).
    # Per-platform hardcoded answer dicts were removed in the S12 audit (2026-05-08)
    # — they were both a PII-policy violation and dead code (zero production callers).

    # ------------------------------------------------------------------
    # Pre / post hooks
    # ------------------------------------------------------------------

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        """Run before any fields are filled.

        Return a dict of metadata (e.g. {"cv_uploaded": True}).
        """
        return {}

    async def post_page(
        self,
        page: "Page",
        page_num: int,
        result: dict,
    ) -> None:
        """Run after each form page is processed."""
        pass

    # ------------------------------------------------------------------
    # Fill overrides
    # ------------------------------------------------------------------

    def field_fill_overrides(self) -> dict[str, Any]:
        """Field-type-specific overrides (e.g. custom date format)."""
        return {}

    async def fill_combobox(
        self,
        page: "Page",
        locator: Any,
        value: str,
        label: str,
    ) -> str | None:
        """Override combobox fill behaviour.

        Return the selected text, or None to use the default strategy.
        """
        return None
