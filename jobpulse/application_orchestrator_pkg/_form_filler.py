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
        page = self.driver.page
        # Use iframe content if an ATS iframe exists (e.g. iCIMS)
        iframe = page.frame(name="icims_content_iframe") if page else None
        filler = NativeFormFiller(page=iframe or page, driver=self.driver)
        result = await filler.fill(
            platform=platform,
            cv_path=str(cv_path) if cv_path else None,
            cl_path=str(cover_letter_path) if cover_letter_path else None,
            profile=profile or {},
            custom_answers=custom_answers or {},
            dry_run=dry_run,
        )

        if not result.get("success"):
            try:
                from jobpulse.page_analyzer import _dom_detect
                from jobpulse.form_models import PageType
                post = await self.driver.get_snapshot(force_refresh=True)
                if hasattr(post, "model_dump"):
                    post = post.model_dump()
                post_type, _ = _dom_detect(post)
                if post_type in (PageType.SESSION_EXPIRED, PageType.LOGIN_FORM):
                    result["error"] = "session_expired"
            except Exception:
                pass

        return result
