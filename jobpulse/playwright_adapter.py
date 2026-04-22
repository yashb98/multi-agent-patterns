"""PlaywrightAdapter — ATS adapter using Playwright CDP for form filling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


def _detect_ats_platform(url: str) -> str:
    from jobpulse.jd_analyzer import detect_ats_platform
    return detect_ats_platform(url) or "generic"


class PlaywrightAdapter(BaseATSAdapter):
    name: str = "playwright"

    def detect(self, url: str) -> bool:
        return False

    async def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict[str, Any] | None = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> dict:
        from jobpulse.application_orchestrator import ApplicationOrchestrator
        from jobpulse.playwright_driver import PlaywrightDriver

        profile = profile or {}
        custom_answers = custom_answers or {}
        platform = _detect_ats_platform(url)
        logger.info("PlaywrightAdapter: applying to %s via %s", url, platform)

        driver = PlaywrightDriver()
        await driver.connect()

        orchestrator = ApplicationOrchestrator(driver=driver, engine="playwright")
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
            )
        finally:
            await driver.close()
        return result
