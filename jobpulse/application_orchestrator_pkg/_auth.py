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

    @staticmethod
    def _as_dict(snapshot: Any) -> dict:
        if hasattr(snapshot, "model_dump"):
            return snapshot.model_dump()
        return snapshot

    async def handle_login(self, snapshot: dict, platform: str) -> dict:
        domain = _extract_domain(snapshot.get("url", ""))

        if not self.accounts.has_account(domain):
            signup_btn = find_signup_link(snapshot)
            if signup_btn:
                await self.driver.click(signup_btn["selector"])
                return self._as_dict(await self.driver.get_snapshot())
            return snapshot

        email, password = self.accounts.get_credentials(domain)
        logger.info("Logging into %s", domain)

        filled_email = False
        filled_password = False
        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            try:
                if ftype == "email" or "email" in label:
                    await self.driver.fill(field["selector"], email)
                    filled_email = True
                elif ftype == "password" or "password" in label:
                    await self.driver.fill(field["selector"], password)
                    filled_password = True
            except (TimeoutError, ConnectionError) as exc:
                logger.warning("Login fill failed for %s: %s", field.get("selector"), exc)

        if not filled_email or not filled_password:
            logger.warning("Login: could not fill email=%s password=%s for %s", filled_email, filled_password, domain)
            return snapshot

        clicked = False
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(sign\s*in|log\s*in|login)", btn.get("text", ""), re.IGNORECASE):
                await self.driver.click(btn["selector"])
                clicked = True
                break

        if not clicked:
            logger.warning("Login: no sign-in button found for %s", domain)
            return snapshot

        # Wait for page transition after login click
        import asyncio
        await asyncio.sleep(2.0)
        post_login = self._as_dict(await self.driver.get_snapshot())

        # Verify login succeeded — if we're still on the login page, don't mark success
        post_url = post_login.get("url", "").lower()
        post_text = post_login.get("page_text_preview", "").lower()
        still_login = any(
            kw in post_text for kw in ("sign in", "log in", "invalid", "incorrect", "wrong password")
        ) and "login" in post_url
        if still_login:
            logger.warning("Login appears to have failed for %s — not marking success", domain)
            return post_login

        self.accounts.mark_login_success(domain)
        return post_login

    async def handle_signup(self, snapshot: dict, platform: str) -> dict:
        from jobpulse.applicator import PROFILE

        domain = _extract_domain(snapshot.get("url", ""))
        email, password = self.accounts.create_account(domain)
        logger.info("Creating account on %s", domain)

        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            sel = field.get("selector", "")

            if ftype == "email" or "email" in label:
                await self.driver.fill(sel, email)
            elif ftype == "password":
                await self.driver.fill(sel, password)
            elif "first" in label:
                await self.driver.fill(sel, PROFILE.get("first_name", ""))
            elif "last" in label:
                await self.driver.fill(sel, PROFILE.get("last_name", ""))
            elif "name" in label and "user" not in label:
                await self.driver.fill(sel, f"{PROFILE.get('first_name', '')} {PROFILE.get('last_name', '')}".strip())
            elif "phone" in label or ftype == "tel":
                await self.driver.fill(sel, PROFILE.get("phone", ""))

        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(create|sign\s*up|register|join|submit)", btn.get("text", ""), re.IGNORECASE):
                await self.driver.click(btn["selector"])
                break

        return self._as_dict(await self.driver.get_snapshot())

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
