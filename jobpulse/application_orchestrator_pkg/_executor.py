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

        if atype == "fill":
            await self.driver.fill(selector, value)
        elif atype == "upload":
            await self.driver.upload(selector, Path(file_path) if file_path else file_path)
        elif atype == "click":
            await self.driver.click(selector)
        elif atype == "select":
            await self.driver.select_option(selector, value)
        elif atype == "check":
            await self.driver.check(selector, value.lower() in ("true", "yes", "1", "checked") if value else True)
        # v2 action types
        elif atype == "fill_radio_group":
            await self.driver.fill_radio_group(selector, value)
        elif atype == "fill_custom_select":
            await self.driver.fill_custom_select(selector, value)
        elif atype == "fill_autocomplete":
            await self.driver.fill_autocomplete(selector, value)
        elif atype == "fill_tag_input":
            values = [v.strip() for v in value.split(",") if v.strip()] if value else []
            await self.driver.fill_tag_input(selector, values)
        elif atype == "fill_date":
            await self.driver.fill_date(selector, value)
        elif atype == "fill_combobox":
            await self.driver.fill_combobox(selector, value)
        elif atype == "fill_contenteditable":
            await self.driver.fill_contenteditable(selector, value)
        elif atype == "scroll_to":
            await self.driver.scroll_to(selector)
        elif atype == "force_click":
            await self.driver.force_click(selector)
        elif atype == "check_consent_boxes":
            await self.driver.check_consent_boxes(selector or None)

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
