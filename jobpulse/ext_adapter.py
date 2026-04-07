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
    """Detect ATS platform from URL."""
    url_lower = url.lower()
    if "greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower:
        return "lever"
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "indeed.com" in url_lower:
        return "indeed"
    if "workday" in url_lower or "myworkdayjobs" in url_lower:
        return "workday"
    if "reed.co.uk" in url_lower:
        return "reed"
    if "totaljobs.com" in url_lower:
        return "totaljobs"
    if "glassdoor" in url_lower:
        return "glassdoor"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    if "bamboohr.com" in url_lower:
        return "bamboohr"
    if "ashbyhq.com" in url_lower or "jobs.ashby.com" in url_lower:
        return "ashby"
    if "jobvite.com" in url_lower:
        return "jobvite"
    if "icims.com" in url_lower:
        return "icims"
    if "taleo" in url_lower or "oracle.com/careers" in url_lower:
        return "taleo"
    return "generic"


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
    ) -> dict:
        """Main entry point — delegates to ApplicationOrchestrator."""
        from jobpulse.application_orchestrator import ApplicationOrchestrator

        profile = profile or {}
        custom_answers = custom_answers or {}

        platform = _detect_ats_platform(url)
        logger.info("ExtensionAdapter: applying to %s via %s (orchestrator)", url, platform)

        fi = FormIntelligence(bridge=self.bridge)

        orchestrator = ApplicationOrchestrator(bridge=self.bridge)
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
        return result
