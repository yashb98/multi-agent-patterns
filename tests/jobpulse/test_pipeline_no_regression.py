"""Verify pipeline produces identical output with all feature flags OFF."""
from unittest.mock import MagicMock, patch
import pytest


class TestPipelineNoRegression:
    def test_generate_materials_unchanged_when_flags_off(self):
        """With all flags off, enhanced_generate_materials just delegates."""
        from jobpulse.pipeline_hooks import enhanced_generate_materials

        mock_bundle = MagicMock()
        mock_bundle.cv_path = "/tmp/test.pdf"
        mock_bundle.cv_text = "Some CV text with \u2014 dashes"
        mock_original = MagicMock(return_value=mock_bundle)

        result = enhanced_generate_materials(
            original_fn=mock_original,
            listing=MagicMock(),
            screen=None,
            db=MagicMock(),
            repos=[],
            notion_failures=[],
        )
        assert result.cv_text == "Some CV text with \u2014 dashes"
        mock_original.assert_called_once()

    def test_ghost_detection_passthrough_when_off(self):
        from jobpulse.pipeline_hooks import with_ghost_detection

        listings = [MagicMock(), MagicMock(), MagicMock()]
        result = with_ghost_detection(listings, {})
        assert len(result) == 3

    def test_archetype_noop_when_off(self):
        from jobpulse.pipeline_hooks import with_archetype_detection

        listing = MagicMock(spec=[])
        with_archetype_detection(listing)
        assert not hasattr(listing, "archetype") or listing.archetype is None
