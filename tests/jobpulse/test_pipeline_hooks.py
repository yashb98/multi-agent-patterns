"""Tests for pipeline_hooks — feature flag wrappers."""
import os
import pytest
from unittest.mock import MagicMock, patch


class TestFeatureEnabled:
    def test_returns_false_by_default(self):
        from jobpulse.pipeline_hooks import feature_enabled

        assert feature_enabled("JOBPULSE_GHOST_DETECTION") is False

    def test_returns_true_when_set(self, monkeypatch):
        from jobpulse.pipeline_hooks import feature_enabled

        monkeypatch.setenv("JOBPULSE_GHOST_DETECTION", "true")
        assert feature_enabled("JOBPULSE_GHOST_DETECTION") is True

    def test_case_insensitive(self, monkeypatch):
        from jobpulse.pipeline_hooks import feature_enabled

        monkeypatch.setenv("JOBPULSE_GHOST_DETECTION", "True")
        assert feature_enabled("JOBPULSE_GHOST_DETECTION") is True


class TestWithGhostDetection:
    def test_passthrough_when_disabled(self):
        from jobpulse.pipeline_hooks import with_ghost_detection

        listings = [MagicMock(), MagicMock()]
        result = with_ghost_detection(listings, {})
        assert result == listings

    def test_filters_when_enabled(self, monkeypatch):
        from jobpulse.pipeline_hooks import with_ghost_detection

        monkeypatch.setenv("JOBPULSE_GHOST_DETECTION", "true")
        listing1 = MagicMock()
        listing1.job_id = "a"
        listing1.description_raw = "A real job"
        listing2 = MagicMock()
        listing2.job_id = "b"
        listing2.description_raw = "A real job"

        with patch("jobpulse.pipeline_hooks.detect_ghost_job") as mock_detect:
            result1 = MagicMock()
            result1.tier = "high_confidence"
            result1.should_block = False
            result2 = MagicMock()
            result2.tier = "suspicious"
            result2.should_block = True
            mock_detect.side_effect = [result1, result2]

            result = with_ghost_detection([listing1, listing2], {"a": "JD1", "b": "JD2"})
            assert len(result) == 1
            assert listing1.ghost_tier == "high_confidence"


class TestEnhancedGenerateMaterials:
    def test_delegates_to_original_when_disabled(self):
        from jobpulse.pipeline_hooks import enhanced_generate_materials

        mock_original = MagicMock(return_value="original_result")
        listing = MagicMock()
        result = enhanced_generate_materials(
            original_fn=mock_original,
            listing=listing,
            screen=None,
            db=MagicMock(),
            repos=[],
            notion_failures=[],
        )
        assert result == "original_result"
        mock_original.assert_called_once()

    def test_applies_normalize_when_enabled(self, monkeypatch):
        from jobpulse.pipeline_hooks import enhanced_generate_materials

        monkeypatch.setenv("JOBPULSE_ATS_NORMALIZE", "true")
        mock_bundle = MagicMock()
        mock_bundle.cv_path = None
        mock_original = MagicMock(return_value=mock_bundle)
        listing = MagicMock()

        result = enhanced_generate_materials(
            original_fn=mock_original,
            listing=listing,
            screen=None,
            db=MagicMock(),
            repos=[],
            notion_failures=[],
        )
        mock_original.assert_called_once()
