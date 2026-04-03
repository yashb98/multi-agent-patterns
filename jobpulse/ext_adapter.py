"""ExtensionAdapter — ATS adapter that uses the Chrome extension via WebSocket."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.form_intelligence import FormIntelligence
from jobpulse.state_machines import ApplicationState, get_state_machine

if TYPE_CHECKING:
    from jobpulse.ext_bridge import ExtensionBridge

logger = get_logger(__name__)

# Safety cap to prevent infinite state machine loops
MAX_ITERATIONS = 50


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
        """Main entry point — uses state machine to drive the application."""
        profile = profile or {}
        custom_answers = custom_answers or {}

        platform = _detect_ats_platform(url)
        machine = get_state_machine(platform)
        logger.info("ExtensionAdapter: applying to %s via %s state machine", url, platform)

        form_intelligence = FormIntelligence(bridge=self.bridge)

        snapshot = await self.bridge.navigate(url)
        iterations = 0

        while not machine.is_terminal and iterations < MAX_ITERATIONS:
            iterations += 1
            state = machine.detect_state(snapshot)
            logger.debug("State machine: %s (iteration %d)", state, iterations)

            if state == ApplicationState.VERIFICATION_WALL:
                return {
                    "success": False,
                    "error": "Verification wall detected",
                    "wall": snapshot.verification_wall.model_dump()
                    if snapshot.verification_wall
                    else {},
                }

            if state == ApplicationState.LOGIN_WALL:
                return {"success": False, "error": "Login required — user must log in manually"}

            actions = machine.get_actions(
                state,
                snapshot,
                profile,
                custom_answers,
                str(cv_path),
                str(cover_letter_path) if cover_letter_path else None,
                form_intelligence=form_intelligence,
            )

            if not actions and state not in (
                ApplicationState.CONFIRMATION,
                ApplicationState.SUBMIT,
            ):
                logger.warning("No actions for state %s — may be stuck", state)

            for action in actions:
                if action.type == "fill" and action.value:
                    await self.bridge.fill(action.selector, action.value)
                elif action.type == "upload" and action.file_path:
                    await self.bridge.upload(action.selector, Path(action.file_path))
                elif action.type == "click":
                    await self.bridge.click(action.selector)
                elif action.type == "select" and action.value:
                    await self.bridge.select_option(action.selector, action.value)
                elif action.type == "check" and action.value is not None:
                    await self.bridge.check(
                        action.selector,
                        action.value.lower() not in ("false", "no", "0"),
                    )

            # Wait for page update
            new_snapshot = await self.bridge.get_snapshot()
            if new_snapshot:
                snapshot = new_snapshot

            machine.transition(state, snapshot)

        if machine.current_state == ApplicationState.CONFIRMATION:
            return {"success": True}

        if iterations >= MAX_ITERATIONS:
            return {"success": False, "error": f"Stuck after {MAX_ITERATIONS} iterations"}

        return {"success": False, "error": f"Terminal state: {machine.current_state}"}
