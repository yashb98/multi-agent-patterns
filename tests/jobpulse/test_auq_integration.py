"""Integration test: field_mapper returns confidence-scored mappings."""
from __future__ import annotations

import pytest
from unittest.mock import patch


class TestMapFieldsWithConfidence:
    @pytest.mark.asyncio
    async def test_seed_mapping_returns_high_confidence(self):
        from jobpulse.form_engine.field_mapper import map_fields_with_confidence

        fields = [
            {"label": "first name", "type": "text", "options": [], "value": ""},
            {"label": "email", "type": "text", "options": [], "value": ""},
        ]
        profile = {"first_name": "Test", "email": "test@example.com"}

        with patch("jobpulse.form_engine.field_mapper.try_cached_mapping", return_value=None), \
             patch("jobpulse.form_engine.field_mapper.seed_mapping") as mock_seed, \
             patch("jobpulse.form_engine.field_mapper._ensure_label_db"):
            mock_seed.return_value = (
                {"first name": "Test", "email": "test@example.com"},
                [],
            )
            scored, llm_calls = await map_fields_with_confidence(
                page_url="https://example.com/apply",
                fields=fields,
                profile=profile,
                custom_answers={},
                platform="generic",
                known_domain=False,
                correction_warning="",
            )
            assert all(fm.confidence >= 0.9 for fm in scored)
            assert llm_calls == 0
