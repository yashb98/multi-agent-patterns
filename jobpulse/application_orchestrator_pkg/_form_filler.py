"""Form filling — delegates to NativeFormFiller (Playwright locators + LLM)."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from jobpulse.application_orchestrator_pkg._executor import ActionExecutor
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator

logger = get_logger(__name__)

MAX_FORM_PAGES = 20


class FormFiller:
    """Form filling via NativeFormFiller."""

    def __init__(self, orch, executor: "ActionExecutor", navigator: "FormNavigator"):
        self._orch = orch
        self.executor = executor
        self.navigator = navigator

    @property
    def driver(self):
        return self._orch.driver

    async def fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
    ) -> dict:
        from jobpulse.native_form_filler import NativeFormFiller
        filler = NativeFormFiller(page=self.driver.page, driver=self.driver)
        return await filler.fill(
            platform=platform,
            cv_path=str(cv_path) if cv_path else None,
            cl_path=str(cover_letter_path) if cover_letter_path else None,
            profile=profile or {},
            custom_answers=custom_answers or {},
            dry_run=dry_run,
        )
