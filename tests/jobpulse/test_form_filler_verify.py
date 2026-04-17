"""Tests for post-fill verification in FormFiller."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from jobpulse.application_orchestrator_pkg._form_filler import FormFiller


class TestVerifyFilledFields:
    @pytest.mark.asyncio
    async def test_verify_detects_empty_field(self):
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        filled_selectors = {"#email"}
        actions = [MagicMock(selector="#email", type="fill", value="test@test.com")]

        empty_field = MagicMock()
        empty_field.selector = "#email"
        empty_field.current_value = ""
        empty_field.input_type = "text"
        snapshot = MagicMock()
        snapshot.fields = [empty_field]

        retries = await filler._verify_filled_fields(filled_selectors, actions, snapshot)
        assert retries == 1
        mock_executor.execute_action_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_skips_verified_fields(self):
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        filled_selectors = {"#email"}
        actions = [MagicMock(selector="#email", type="fill", value="test@test.com")]

        ok_field = MagicMock()
        ok_field.selector = "#email"
        ok_field.current_value = "test@test.com"
        ok_field.input_type = "text"
        snapshot = MagicMock()
        snapshot.fields = [ok_field]

        retries = await filler._verify_filled_fields(filled_selectors, actions, snapshot)
        assert retries == 0
        mock_executor.execute_action_with_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_skips_file_inputs(self):
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        filled_selectors = {"#cv"}
        actions = [MagicMock(selector="#cv", type="upload", value="")]

        file_field = MagicMock()
        file_field.selector = "#cv"
        file_field.current_value = ""
        file_field.input_type = "file"
        snapshot = MagicMock()
        snapshot.fields = [file_field]

        retries = await filler._verify_filled_fields(filled_selectors, actions, snapshot)
        assert retries == 0

    @pytest.mark.asyncio
    async def test_verify_empty_selectors(self):
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        retries = await filler._verify_filled_fields(set(), [], MagicMock())
        assert retries == 0

    @pytest.mark.asyncio
    async def test_verify_handles_retry_failure(self):
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_executor.execute_action_with_retry.side_effect = TimeoutError("timeout")
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        filled_selectors = {"#phone"}
        actions = [MagicMock(selector="#phone", type="fill", value="+447123456789")]

        empty_field = MagicMock()
        empty_field.selector = "#phone"
        empty_field.current_value = ""
        empty_field.input_type = "text"
        snapshot = MagicMock()
        snapshot.fields = [empty_field]

        retries = await filler._verify_filled_fields(filled_selectors, actions, snapshot)
        assert retries == 0  # Failed retry doesn't count
