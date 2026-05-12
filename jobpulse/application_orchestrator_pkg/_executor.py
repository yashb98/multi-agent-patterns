"""Action execution — dispatches fill/click/upload/select to the driver.

Handles retry logic and post-fill validation. Streams field progress
to Telegram in real-time when a tg_stream is provided.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

_ACTION_TIMEOUT = 30


class ActionExecutor:
    """Dispatches form actions to the driver with retry and validation."""

    def __init__(self, orch):
        self._orch = orch

    @property
    def driver(self):
        return self._orch.driver

    async def execute_action(self, action: Any, tg_stream: Any = None):
        if hasattr(action, "model_dump"):
            # Pydantic Action model
            atype = getattr(action, "type", "")
            selector = getattr(action, "selector", "")
            value = getattr(action, "value", "")
            file_path = getattr(action, "file_path", None)
            label = getattr(action, "label", selector)
            tier = getattr(action, "tier", 1)
            confidence = getattr(action, "confidence", 1.0)
        else:
            atype = action.get("type", "")
            selector = action.get("selector", "")
            value = action.get("value", "")
            file_path = action.get("file_path")
            label = action.get("label", selector)
            tier = action.get("tier", 1)
            confidence = action.get("confidence", 1.0)

        try:
            await asyncio.wait_for(
                self._dispatch(atype, selector, value, file_path),
                timeout=_ACTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Action %s on %s timed out after %ds",
                atype,
                selector[:60] if selector else "?",
                _ACTION_TIMEOUT,
            )
            raise TimeoutError(f"Action {atype} timed out")

        # Stream field progress to Telegram in real-time
        if tg_stream is not None and atype in ("fill", "select", "fill_radio_group", "fill_custom_select", "fill_autocomplete", "fill_date"):
            try:
                await tg_stream.stream_field(
                    label=str(label),
                    value=str(value),
                    tier=int(tier),
                    confident=float(confidence) >= 0.7,
                )
            except Exception as _se:
                logger.debug("stream_field failed: %s", _se)

    async def _dispatch(self, atype: str, selector: str, value: str, file_path: str | None):
        """Dispatch action to driver. Each entry maps an action type to a
        callable that resolves the driver coroutine — keeps the action
        catalog readable instead of as a 30-line if/elif ladder."""
        check_truthy = value.lower() in ("true", "yes", "1", "checked") if value else True
        tag_values = [v.strip() for v in value.split(",") if v.strip()] if value else []

        dispatch = {
            "fill": lambda: self.driver.fill(selector, value),
            "upload": lambda: self.driver.upload(selector, Path(file_path) if file_path else file_path),
            "click": lambda: self.driver.click(selector),
            "select": lambda: self.driver.select_option(selector, value),
            "check": lambda: self.driver.check(selector, check_truthy),
            # v2 action types
            "fill_radio_group": lambda: self.driver.fill_radio_group(selector, value),
            "fill_custom_select": lambda: self.driver.fill_custom_select(selector, value),
            "fill_autocomplete": lambda: self.driver.fill_autocomplete(selector, value),
            "fill_tag_input": lambda: self.driver.fill_tag_input(selector, tag_values),
            "fill_date": lambda: self.driver.fill_date(selector, value),
            "fill_combobox": lambda: self.driver.fill_combobox(selector, value),
            "fill_contenteditable": lambda: self.driver.fill_contenteditable(selector, value),
            "scroll_to": lambda: self.driver.scroll_to(selector),
            "force_click": lambda: self.driver.force_click(selector),
            "check_consent_boxes": lambda: self.driver.check_consent_boxes(selector or None),
        }
        handler = dispatch.get(atype)
        if handler is None:
            return
        await handler()

    async def execute_action_with_retry(
        self, action: Any, tg_stream: Any = None, max_retries: int = 2
    ):
        """Execute action with retry for critical fields and post-fill validation."""
        selector = getattr(action, "selector", "") or (
            action.get("selector", "") if isinstance(action, dict) else ""
        )
        atype = getattr(action, "type", "") or (
            action.get("type", "") if isinstance(action, dict) else ""
        )

        for attempt in range(max_retries + 1):
            try:
                await self.execute_action(action, tg_stream=tg_stream)

                # Post-fill validation for fill actions
                if atype in ("fill", "fill_radio_group", "fill_custom_select", "fill_autocomplete", "fill_date", "fill_combobox") and selector:
                    try:
                        rescan = await self.driver.rescan_after_fill(selector)
                        errors = rescan.get("validation_errors", [])
                        if errors:
                            logger.warning("Validation error after %s: %s", atype, errors)
                            if attempt < max_retries:
                                await asyncio.sleep(1.5 * (attempt + 1))
                                continue
                    except (TimeoutError, ConnectionError):
                        pass  # Rescan failed — don't block the fill
                return  # Success
            except (TimeoutError, ConnectionError) as exc:
                logger.warning("Action %s attempt %d/%d failed: %r", atype, attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    raise  # Let caller handle
