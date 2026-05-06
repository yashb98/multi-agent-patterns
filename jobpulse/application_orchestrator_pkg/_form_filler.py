"""Form filling — delegates to NativeFormFiller (Playwright locators + LLM)."""
from __future__ import annotations

import os
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

    async def _ensure_correct_tab(self, expected_url: str) -> None:
        """Switch driver to the tab whose URL matches the expected form URL.

        After navigation, the browser may have multiple tabs.  The driver's
        ``_page`` could point to a stale tab (e.g. a job-description page)
        instead of the application form tab.
        """
        from urllib.parse import urlparse

        current_url = getattr(self.driver.page, "url", "") or ""
        if not isinstance(current_url, str) or not isinstance(expected_url, str):
            return
        expected_parsed = urlparse(expected_url)
        current_parsed = urlparse(current_url)

        if (expected_parsed.netloc == current_parsed.netloc
                and expected_parsed.path == current_parsed.path):
            return

        context = getattr(self.driver.page, "context", None)
        if context is None:
            return

        for pg in context.pages:
            pg_parsed = urlparse(pg.url or "")
            if (pg_parsed.netloc == expected_parsed.netloc
                    and pg_parsed.path == expected_parsed.path):
                logger.warning(
                    "Tab mismatch: driver on %s, form on %s — switching",
                    current_url[:80], pg.url[:80],
                )
                self.driver._page = pg
                return

        logger.debug("No tab matches expected URL %s — staying on current", expected_url[:80])

    async def fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
        planned_action: dict | None = None,
    ) -> dict:
        page = self.driver.page

        # Ensure the active tab matches the form URL from navigation snapshot.
        # Navigation may have opened extra tabs; driver._page could point to
        # a stale tab if the context switched.
        expected_url = (snapshot or {}).get("url", "")
        if page and expected_url:
            await self._ensure_correct_tab(expected_url)
            page = self.driver.page

        # Use iframe content if an ATS iframe exists (e.g. iCIMS)
        iframe = page.frame(name="icims_content_iframe") if page else None
        effective_page = iframe or page

        # Feature flag: use unified FormFillEngine when enabled
        if os.environ.get("UNIFIED_FORM_ENGINE") == "true":
            from jobpulse.form_engine.engine import FormFillEngine
            engine = FormFillEngine(page=effective_page, driver=self.driver)
            result = await engine.fill(
                profile=profile or {},
                custom_answers=custom_answers or {},
                platform=platform,
                dry_run=dry_run,
                planned_action=planned_action,
            )
            # Convert FormFillResult to legacy dict format
            return {
                "success": result.success,
                "pages_filled": result.pages_filled,
                "time_seconds": result.time_seconds,
                "error": result.error,
                "agent_mapping": result.agent_mapping,
                "field_types": [],
                "screening_questions": [],
                "llm_fallback_count": result.llm_calls,
            }

        # Legacy path
        from jobpulse.native_form_filler import NativeFormFiller
        filler = NativeFormFiller(page=effective_page, driver=self.driver)
        result = await filler.fill(
            platform=platform,
            cv_path=str(cv_path) if cv_path else None,
            cl_path=str(cover_letter_path) if cover_letter_path else None,
            profile=profile or {},
            custom_answers=custom_answers or {},
            dry_run=dry_run,
            planned_action=planned_action,
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
