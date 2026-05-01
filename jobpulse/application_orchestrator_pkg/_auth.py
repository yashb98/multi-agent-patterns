"""Authentication handlers — login, signup, email verification.

Handles credential-based login, account creation, and Gmail verification
polling during the application flow.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class AuthHandler:
    """Handles login, signup, and email verification during application flow."""

    def __init__(self, orch):
        self._orch = orch

    @property
    def driver(self):
        return self._orch.driver

    @property
    def accounts(self):
        return self._orch.accounts

    @property
    def gmail(self):
        return self._orch.gmail

    @property
    def sso(self):
        return self._orch.sso

    @property
    def navigator(self):
        """Access the FormNavigator from the shared orchestrator.

        Goes through the orchestrator because AuthHandler is constructed
        before FormNavigator (see application_orchestrator_pkg/__init__.py).
        The navigator is needed at call time (handle_login/signup), by which
        point the orchestrator has both attributes wired.
        """
        return self._orch._navigator

    @staticmethod
    def _as_dict(snapshot: Any) -> dict:
        if hasattr(snapshot, "model_dump"):
            return snapshot.model_dump()
        return snapshot

    async def handle_login(self, snapshot: dict, platform: str) -> dict:
        """Login via reasoner — analyzes actual page content."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import (
            NavigationActionExecutor, emit_fill_failures,
        )
        from jobpulse.applicator import PROFILE

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        logger.info("Auth login via reasoner: %s — %s",
                    action.action, action.page_understanding[:60])

        page = getattr(self.driver, "page", None)
        if page is not None:
            executor = NavigationActionExecutor(page)
            result = await executor.execute(action, profile=PROFILE)
            domain = _extract_domain(snapshot.get("url", ""))
            emit_fill_failures(result, domain=domain, source="auth_login")

        import asyncio
        await asyncio.sleep(2.0)
        post_snap = self._as_dict(await self.driver.get_snapshot())

        nav = getattr(self._orch, "_navigator", None)
        if nav is not None:
            verification = await nav._verify_action(
                pre_snapshot=snapshot, post_snapshot=post_snap, action_kind=action.action,
            )
            verification = nav._check_expected_outcome(action, verification)
            if verification.ghost_click:
                logger.warning("Auth login: ghost click detected — page did not progress")
            if verification.expected_outcome_met is False:
                logger.warning(
                    "Auth login: expected_outcome '%s' not met",
                    action.expected_outcome,
                )
        return post_snap

    async def handle_signup(self, snapshot: dict, platform: str) -> dict:
        """Signup via reasoner — analyzes actual page content."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import (
            NavigationActionExecutor, emit_fill_failures,
        )
        from jobpulse.applicator import PROFILE

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        logger.info("Auth signup via reasoner: %s — %s",
                    action.action, action.page_understanding[:60])

        page = getattr(self.driver, "page", None)
        if page is not None:
            executor = NavigationActionExecutor(page)
            result = await executor.execute(action, profile=PROFILE)
            domain = _extract_domain(snapshot.get("url", ""))
            emit_fill_failures(result, domain=domain, source="auth_signup")

        import asyncio
        await asyncio.sleep(2.0)
        post_snap = self._as_dict(await self.driver.get_snapshot())

        nav = getattr(self._orch, "_navigator", None)
        if nav is not None:
            verification = await nav._verify_action(
                pre_snapshot=snapshot, post_snapshot=post_snap, action_kind=action.action,
            )
            verification = nav._check_expected_outcome(action, verification)
            if verification.ghost_click:
                logger.warning("Auth signup: ghost click detected — page did not progress")
            if verification.expected_outcome_met is False:
                logger.warning(
                    "Auth signup: expected_outcome '%s' not met",
                    action.expected_outcome,
                )
        return post_snap

    async def handle_email_verification(self, snapshot: dict, platform: str, return_url: str) -> dict:
        domain = _extract_domain(snapshot.get("url", ""))
        logger.info("Waiting for verification email from %s", domain)

        link = self.gmail.wait_for_verification(domain)
        if not link:
            logger.warning("Verification email not received for %s", domain)
            return snapshot

        await self.driver.navigate(link)
        self._as_dict(await self.driver.get_snapshot())
        self.accounts.mark_verified(domain)

        logger.info("Returning to application: %s", return_url[:80])
        await self.driver.navigate(return_url)
        return self._as_dict(await self.driver.get_snapshot())


# ── Module-level utilities ──

def _extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url


def find_signup_link(snapshot: dict) -> dict | None:
    pattern = re.compile(r"(create\s*account|sign\s*up|register|don.?t\s*have|new\s*user)", re.IGNORECASE)
    for btn in snapshot.get("buttons", []):
        if pattern.search(btn.get("text", "")):
            return btn
    return None
