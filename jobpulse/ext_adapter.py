"""ExtensionAdapter — ATS adapter that uses the Chrome extension via WebSocket."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.form_intelligence import FormIntelligence

if TYPE_CHECKING:
    from jobpulse.ext_bridge import ExtensionBridge

logger = get_logger(__name__)


def _detect_ats_platform(url: str) -> str:
    """Detect ATS platform from URL. Delegates to jd_analyzer's canonical implementation."""
    from jobpulse.jd_analyzer import detect_ats_platform
    return detect_ats_platform(url) or "generic"


class ExtensionAdapter(BaseATSAdapter):
    """ATS adapter that uses the Chrome extension instead of Playwright."""

    name: str = "extension"

    def __init__(self, bridge: ExtensionBridge) -> None:
        self.bridge = bridge

    def detect(self, url: str) -> bool:
        """Always returns False — routing is by APPLICATION_ENGINE config, not URL detection."""
        return False

    async def fill_and_submit(  # type: ignore[override]
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
        engine: str = "extension",
    ) -> dict:
        """Main entry point — delegates to ApplicationOrchestrator."""
        from jobpulse.application_orchestrator import ApplicationOrchestrator

        profile = profile or {}
        custom_answers = custom_answers or {}

        platform = _detect_ats_platform(url)
        logger.info("ExtensionAdapter: applying to %s via %s (engine=%s)", url, platform, engine)

        # Select driver based on engine
        if engine == "playwright":
            from jobpulse.playwright_driver import PlaywrightDriver
            pw_driver = PlaywrightDriver()
            await pw_driver.connect()
            driver = pw_driver
        else:
            driver = self.bridge

        fi = FormIntelligence(bridge=driver)

        orchestrator = ApplicationOrchestrator(driver=driver, engine=engine)
        try:
            result = await orchestrator.apply(
                url=url,
                platform=platform,
                cv_path=cv_path,
                cover_letter_path=cover_letter_path,
                profile=profile,
                custom_answers=custom_answers,
                overrides=overrides,
                dry_run=dry_run,
                form_intelligence=fi,
            )
        finally:
            if engine == "playwright" and not dry_run:
                await driver.close()
        return result
