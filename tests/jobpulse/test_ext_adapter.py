"""Comprehensive tests for ExtensionAdapter — platform detection, delegation, integration.

Covers:
- Platform detection from URL (all 6 platforms + edge cases)
- Adapter routing to ApplicationOrchestrator
- Dry run mode passthrough
- Default parameter handling (None profile, None custom_answers)
- Cover letter path passthrough
- Integration: Greenhouse happy path, verification wall, stuck detection
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.ext_adapter import ExtensionAdapter, _detect_ats_platform
from jobpulse.ext_bridge import ExtensionBridge
from jobpulse.ext_models import (
    PageSnapshot, FieldInfo, ButtonInfo, VerificationWall, FillResult,
)


# =========================================================================
# Platform detection
# =========================================================================


class TestDetectPlatform:
    def test_greenhouse(self):
        assert _detect_ats_platform("https://boards.greenhouse.io/company/jobs/1") == "greenhouse"

    def test_lever(self):
        assert _detect_ats_platform("https://jobs.lever.co/company/abc") == "lever"

    def test_linkedin(self):
        assert _detect_ats_platform("https://www.linkedin.com/jobs/view/123") == "linkedin"

    def test_indeed(self):
        assert _detect_ats_platform("https://uk.indeed.com/viewjob?jk=abc") == "indeed"

    def test_workday(self):
        assert _detect_ats_platform("https://company.wd5.myworkdayjobs.com/en-US/jobs") == "workday"

    def test_workday_alt(self):
        assert _detect_ats_platform("https://company.workday.com/jobs") == "workday"

    def test_reed(self):
        assert _detect_ats_platform("https://www.reed.co.uk/jobs/data-analyst/123") == "reed"

    def test_totaljobs(self):
        assert _detect_ats_platform("https://www.totaljobs.com/job/data-engineer/456") == "totaljobs"

    def test_glassdoor(self):
        assert _detect_ats_platform("https://www.glassdoor.co.uk/job-listing/ml-engineer") == "glassdoor"

    def test_generic_unknown(self):
        assert _detect_ats_platform("https://careers.randomcompany.com/apply") == "generic"

    def test_case_insensitive(self):
        assert _detect_ats_platform("https://boards.GREENHOUSE.IO/Company") == "greenhouse"

    def test_empty_url(self):
        assert _detect_ats_platform("") == "generic"

    def test_url_with_query_params(self):
        assert _detect_ats_platform("https://linkedin.com/jobs/view/123?trk=abc") == "linkedin"


# =========================================================================
# Adapter detect
# =========================================================================


class TestAdapterDetect:
    def test_detect_always_false(self):
        adapter = ExtensionAdapter(bridge=MagicMock())
        assert adapter.detect("https://greenhouse.io") is False
        assert adapter.detect("https://linkedin.com") is False
        assert adapter.detect("") is False


# =========================================================================
# fill_and_submit delegation
# =========================================================================


class TestFillAndSubmit:
    @pytest.mark.asyncio
    async def test_delegates_to_orchestrator(self, tmp_path):
        """fill_and_submit creates orchestrator and calls apply."""
        mock_bridge = AsyncMock()
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"test")

        with patch("jobpulse.application_orchestrator.ApplicationOrchestrator") as MockOrch:
            mock_orch = AsyncMock()
            mock_orch.apply.return_value = {"success": True}
            MockOrch.return_value = mock_orch

            result = await adapter.fill_and_submit(
                url="https://boards.greenhouse.io/job/1",
                cv_path=cv,
                profile={"first_name": "Test"},
                custom_answers={"key": "val"},
            )
            assert result["success"] is True
            mock_orch.apply.assert_called_once()
            call_kwargs = mock_orch.apply.call_args.kwargs
            assert call_kwargs["platform"] == "greenhouse"
            assert call_kwargs["url"] == "https://boards.greenhouse.io/job/1"

    @pytest.mark.asyncio
    async def test_none_params_defaulted(self, tmp_path):
        """None profile and custom_answers default to empty dicts."""
        mock_bridge = AsyncMock()
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"test")

        with patch("jobpulse.application_orchestrator.ApplicationOrchestrator") as MockOrch:
            mock_orch = AsyncMock()
            mock_orch.apply.return_value = {"success": True}
            MockOrch.return_value = mock_orch

            await adapter.fill_and_submit(
                url="https://example.com",
                cv_path=cv,
                profile=None,
                custom_answers=None,
            )
            call_kwargs = mock_orch.apply.call_args.kwargs
            assert call_kwargs["profile"] == {}
            assert call_kwargs["custom_answers"] == {}

    @pytest.mark.asyncio
    async def test_dry_run_passed_through(self, tmp_path):
        mock_bridge = AsyncMock()
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"test")

        with patch("jobpulse.application_orchestrator.ApplicationOrchestrator") as MockOrch:
            mock_orch = AsyncMock()
            mock_orch.apply.return_value = {"success": True, "dry_run": True}
            MockOrch.return_value = mock_orch

            result = await adapter.fill_and_submit(
                url="https://example.com",
                cv_path=cv,
                dry_run=True,
            )
            call_kwargs = mock_orch.apply.call_args.kwargs
            assert call_kwargs["dry_run"] is True

    @pytest.mark.asyncio
    async def test_cover_letter_passed_through(self, tmp_path):
        mock_bridge = AsyncMock()
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"test")
        cl = tmp_path / "cl.pdf"
        cl.write_bytes(b"cover letter")

        with patch("jobpulse.application_orchestrator.ApplicationOrchestrator") as MockOrch:
            mock_orch = AsyncMock()
            mock_orch.apply.return_value = {"success": True}
            MockOrch.return_value = mock_orch

            await adapter.fill_and_submit(
                url="https://lever.co/job",
                cv_path=cv,
                cover_letter_path=cl,
            )
            call_kwargs = mock_orch.apply.call_args.kwargs
            assert call_kwargs["cover_letter_path"] == cl

    @pytest.mark.asyncio
    async def test_overrides_passed_through(self, tmp_path):
        mock_bridge = AsyncMock()
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"test")

        with patch("jobpulse.application_orchestrator.ApplicationOrchestrator") as MockOrch:
            mock_orch = AsyncMock()
            mock_orch.apply.return_value = {"success": True}
            MockOrch.return_value = mock_orch

            await adapter.fill_and_submit(
                url="https://example.com",
                cv_path=cv,
                overrides={"timeout": 60},
            )
            call_kwargs = mock_orch.apply.call_args.kwargs
            assert call_kwargs["overrides"] == {"timeout": 60}


# =========================================================================
# FormIntelligence creation
# =========================================================================


class TestFormIntelligenceCreation:
    @pytest.mark.asyncio
    async def test_creates_form_intelligence_with_bridge(self, tmp_path):
        """FormIntelligence is created with the bridge for Tier 3 Nano support."""
        mock_bridge = AsyncMock()
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"test")

        with patch("jobpulse.application_orchestrator.ApplicationOrchestrator") as MockOrch:
            mock_orch = AsyncMock()
            mock_orch.apply.return_value = {"success": True}
            MockOrch.return_value = mock_orch

            with patch("jobpulse.ext_adapter.FormIntelligence") as MockFI:
                mock_fi = MagicMock()
                MockFI.return_value = mock_fi

                await adapter.fill_and_submit(url="https://ex.com", cv_path=cv)
                MockFI.assert_called_once_with(bridge=mock_bridge)
                call_kwargs = mock_orch.apply.call_args.kwargs
                assert call_kwargs["form_intelligence"] is mock_fi


# =========================================================================
# Integration tests (full orchestrator flow, not mocked)
# =========================================================================


def _snap(url="", fields=None, buttons=None, wall=None, text="", has_files=False):
    return PageSnapshot(
        url=url, title="Test", fields=fields or [], buttons=buttons or [],
        verification_wall=wall, page_text_preview=text,
        has_file_inputs=has_files, iframe_count=0, timestamp=1000,
    )


class TestIntegrationFlows:
    @pytest.mark.asyncio
    async def test_fill_and_submit_greenhouse_happy_path(self, tmp_path):
        """Greenhouse single-page: orchestrator navigates -> detects form -> fills -> confirms."""
        mock_bridge = AsyncMock(spec=ExtensionBridge)
        mock_bridge.connected = True
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF-1.4 test")

        snap_form = _snap(
            url="https://boards.greenhouse.io/company/jobs/1",
            fields=[
                FieldInfo(selector="#first_name", input_type="text", label="First Name"),
                FieldInfo(selector="#last_name", input_type="text", label="Last Name"),
                FieldInfo(selector="#email", input_type="email", label="Email"),
            ],
            has_files=True,
        )
        snap_confirm = _snap(
            url="https://boards.greenhouse.io/company/jobs/1",
            text="Thank you for applying! Your application has been received.",
        )

        mock_bridge.get_snapshot.side_effect = [
            snap_form, snap_form, snap_form,
            snap_confirm, snap_confirm, snap_confirm, snap_confirm,
        ]
        mock_bridge.fill.return_value = FillResult(success=True, value_set="filled")
        mock_bridge.upload.return_value = True
        mock_bridge.screenshot.return_value = b"screenshot"

        profile = {"first_name": "Yash", "last_name": "B", "email": "yash@test.com"}

        result = await adapter.fill_and_submit(
            url="https://boards.greenhouse.io/company/jobs/1",
            cv_path=cv,
            cover_letter_path=None,
            profile=profile,
            custom_answers={},
            dry_run=True,
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_fill_and_submit_verification_wall(self, tmp_path):
        """Verification wall stops the application."""
        mock_bridge = AsyncMock(spec=ExtensionBridge)
        mock_bridge.connected = True
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF-1.4 test")

        snap_wall = _snap(
            url="https://boards.greenhouse.io/company/jobs/1",
            wall=VerificationWall(wall_type="cloudflare", confidence=0.95),
        )
        mock_bridge.get_snapshot.return_value = snap_wall

        result = await adapter.fill_and_submit(
            url="https://boards.greenhouse.io/company/jobs/1",
            cv_path=cv,
            cover_letter_path=None,
            profile={},
            custom_answers={},
        )
        assert result["success"] is False
        assert "CAPTCHA" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fill_and_submit_stuck_detection(self, tmp_path):
        """Safety cap prevents infinite loops — orchestrator detects stuck pages."""
        mock_bridge = AsyncMock(spec=ExtensionBridge)
        mock_bridge.connected = True
        adapter = ExtensionAdapter(bridge=mock_bridge)
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"%PDF-1.4 test")

        snap_stuck = _snap(
            url="https://boards.greenhouse.io/company/jobs/1",
            fields=[FieldInfo(selector="#q1", input_type="text", label="First Name")],
            has_files=True,
            text="x" * 800,
        )
        mock_bridge.get_snapshot.return_value = snap_stuck
        mock_bridge.fill.return_value = FillResult(success=True, value_set="answer")
        mock_bridge.screenshot.return_value = b"screenshot"

        result = await adapter.fill_and_submit(
            url="https://boards.greenhouse.io/company/jobs/1",
            cv_path=cv,
            cover_letter_path=None,
            profile={},
            custom_answers={},
        )
        assert result["success"] is False
