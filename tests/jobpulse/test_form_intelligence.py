"""Comprehensive tests for FormIntelligence — all 5 tiers, async paths, cache errors.

Covers:
- Tier 1 pattern match with placeholders
- Tier 1 LLM-required patterns (answer=None)
- Tier 2 semantic cache hit, miss, and exception
- Tier 3 Gemini Nano via bridge (async only)
- Tier 4 LLM fallback, cache store failure
- Tier 5 Vision (async only, low-confidence trigger)
- Sync vs async resolution path differences
- Empty/whitespace input handling
- FieldAnswer model defaults and validation
- _generate_answer_llm wrapper delegation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.ext_models import FieldAnswer
from jobpulse.form_intelligence import FormIntelligence


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def mock_cache():
    cache = MagicMock()
    cache.find_similar.return_value = None
    cache.store = MagicMock()
    return cache


@pytest.fixture
def mock_bridge():
    bridge = AsyncMock()
    bridge.analyze_field_locally = AsyncMock(return_value=None)
    return bridge


@pytest.fixture
def fi(mock_cache, mock_bridge):
    return FormIntelligence(semantic_cache=mock_cache, bridge=mock_bridge)


@pytest.fixture
def fi_no_cache():
    return FormIntelligence(semantic_cache=None, bridge=None)


# =========================================================================
# Tier 1: Pattern match
# =========================================================================


class TestTier1Pattern:
    def test_known_pattern_returns_tier_1(self, fi):
        result = fi.resolve("Are you authorized to work in the UK?")
        assert result.tier == 1
        assert result.confidence == 1.0
        assert result.answer == "Yes"

    def test_salary_number_type(self, fi):
        result = fi.resolve(
            "What is your salary expectation?",
            {"job_title": "Data Scientist"},
            input_type="number",
        )
        assert result.tier == 1
        assert result.answer.isdigit()

    def test_pattern_none_falls_through_to_llm(self, fi):
        """Patterns with answer=None skip Tier 1 and go to LLM."""
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "I'm excited about this opportunity"
            result = fi.resolve("Why do you want to apply?")
            assert result.tier == 4  # fell through to LLM
            mock_llm.assert_called_once()


# =========================================================================
# Tier 2: Semantic cache
# =========================================================================


class TestTier2SemanticCache:
    def test_cache_hit(self, fi, mock_cache):
        """Cache hit returns tier 2 with similarity score."""
        mock_cache.find_similar.return_value = ("Cached answer", 0.92)
        result = fi.resolve("Some novel question")
        assert result.tier == 2
        assert result.answer == "Cached answer"
        assert result.confidence == pytest.approx(0.92)

    def test_cache_miss_falls_to_llm(self, fi, mock_cache):
        """Cache miss continues to next tier."""
        mock_cache.find_similar.return_value = None
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM answer"
            result = fi.resolve("Something completely new")
            assert result.tier == 4

    def test_cache_exception_falls_to_llm(self, fi, mock_cache):
        """Cache error is caught, continues to LLM."""
        mock_cache.find_similar.side_effect = RuntimeError("DB corrupted")
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM fallback"
            result = fi.resolve("Question after cache crash")
            assert result.tier == 4

    def test_no_cache_skips_tier_2(self, fi_no_cache):
        """No semantic cache → skip Tier 2 entirely."""
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Answer"
            result = fi_no_cache.resolve("Novel question")
            # Should go Tier 1 miss → Tier 2 skip → Tier 4 LLM
            assert result.tier == 4


# =========================================================================
# Tier 3: Gemini Nano (async only)
# =========================================================================


class TestTier3Nano:
    @pytest.mark.asyncio
    async def test_nano_returns_answer(self, fi, mock_bridge):
        """Nano provides answer → returns tier 3."""
        mock_bridge.analyze_field_locally.return_value = "Nano answer"
        result = await fi.resolve_async("Custom question about experience")
        assert result.tier == 3
        assert result.answer == "Nano answer"
        assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_nano_returns_none_falls_to_llm(self, fi, mock_bridge):
        """Nano returns None → falls to LLM."""
        mock_bridge.analyze_field_locally.return_value = None
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM answer"
            result = await fi.resolve_async("Custom question")
            assert result.tier == 4

    @pytest.mark.asyncio
    async def test_nano_exception_falls_to_llm(self, fi, mock_bridge):
        """Nano throws → caught, falls to LLM."""
        mock_bridge.analyze_field_locally.side_effect = RuntimeError("Nano unavailable")
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM fallback"
            result = await fi.resolve_async("Question after nano crash")
            assert result.tier == 4

    @pytest.mark.asyncio
    async def test_nano_skipped_in_sync_path(self, fi, mock_bridge):
        """Sync resolve() never calls Nano even if bridge exists."""
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Sync answer"
            result = fi.resolve("Custom question")
            mock_bridge.analyze_field_locally.assert_not_called()

    @pytest.mark.asyncio
    async def test_nano_skipped_when_no_bridge(self, mock_cache):
        """No bridge → skip Tier 3."""
        fi_nb = FormIntelligence(semantic_cache=mock_cache, bridge=None)
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Answer"
            result = await fi_nb.resolve_async("Question")
            assert result.tier == 4


# =========================================================================
# Tier 4: LLM
# =========================================================================


class TestTier4LLM:
    def test_llm_stores_in_cache(self, fi, mock_cache):
        """LLM result is stored in semantic cache."""
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Generated"
            result = fi.resolve("Novel question")
            mock_cache.store.assert_called_once_with("Novel question", "Generated")
            assert result.tier == 4
            assert result.confidence == 0.7

    def test_llm_cache_store_failure_doesnt_crash(self, fi, mock_cache):
        """Cache store failure is caught, answer still returned."""
        mock_cache.store.side_effect = RuntimeError("Write failed")
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Answer despite cache error"
            result = fi.resolve("Question")
            assert result.answer == "Answer despite cache error"
            assert result.tier == 4


# =========================================================================
# Tier 5: Vision (async only)
# =========================================================================


class TestTier5Vision:
    @pytest.mark.asyncio
    async def test_vision_triggered_on_low_confidence(self, fi, mock_bridge):
        """Tier 5 runs when LLM confidence < 0.8 and screenshot provided."""
        mock_bridge.analyze_field_locally.return_value = None
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Weak LLM answer"
            with patch("jobpulse.vision_tier.analyze_field_screenshot", new_callable=AsyncMock) as mock_vision:
                mock_vision.return_value = "Vision answer"
                result = await fi.resolve_async(
                    "Ambiguous question",
                    screenshot_b64="base64screenshotdata",
                )
                assert result.tier == 5
                assert result.answer == "Vision answer"
                assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_vision_not_triggered_without_screenshot(self, fi, mock_bridge):
        """Tier 5 skipped when no screenshot provided."""
        mock_bridge.analyze_field_locally.return_value = None
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM answer"
            result = await fi.resolve_async("Question")
            assert result.tier == 4  # no vision

    @pytest.mark.asyncio
    async def test_vision_exception_returns_llm_answer(self, fi, mock_bridge):
        """Vision crash → returns LLM answer."""
        mock_bridge.analyze_field_locally.return_value = None
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM fallback"
            with patch("jobpulse.vision_tier.analyze_field_screenshot", new_callable=AsyncMock) as mock_vision:
                mock_vision.side_effect = RuntimeError("Vision API down")
                result = await fi.resolve_async(
                    "Question",
                    screenshot_b64="data",
                )
                assert result.tier == 4

    @pytest.mark.asyncio
    async def test_vision_returns_none_keeps_llm_answer(self, fi, mock_bridge):
        """Vision returns None → keeps LLM answer."""
        mock_bridge.analyze_field_locally.return_value = None
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "LLM answer"
            with patch("jobpulse.vision_tier.analyze_field_screenshot", new_callable=AsyncMock) as mock_vision:
                mock_vision.return_value = None
                result = await fi.resolve_async(
                    "Question",
                    screenshot_b64="data",
                )
                assert result.tier == 4


# =========================================================================
# Empty / whitespace
# =========================================================================


class TestEmptyInput:
    def test_empty_sync(self, fi):
        result = fi.resolve("")
        assert result.answer == ""
        assert result.confidence == 0.0

    def test_whitespace_sync(self, fi):
        result = fi.resolve("   ")
        assert result.answer == ""

    @pytest.mark.asyncio
    async def test_empty_async(self, fi):
        result = await fi.resolve_async("")
        assert result.answer == ""

    @pytest.mark.asyncio
    async def test_whitespace_async(self, fi):
        result = await fi.resolve_async("  \t  ")
        assert result.answer == ""


# =========================================================================
# FieldAnswer model
# =========================================================================


class TestFieldAnswerModel:
    def test_tier_name_mapping(self, fi):
        """Verify tier names are correctly set."""
        result = fi.resolve("Do you require visa sponsorship?")
        assert result.tier == 1
        assert result.tier_name == "pattern"

    def test_llm_tier_name(self, fi):
        with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
            mock_llm.return_value = "Answer"
            result = fi.resolve("Unknown novel question xyz")
            assert result.tier_name == "llm"

    def test_field_answer_defaults(self):
        """tier_name defaults to 'unknown' when not provided."""
        fa = FieldAnswer(answer="No", tier=4, confidence=0.7)
        assert fa.tier_name == "unknown"

    def test_field_answer_empty_answer(self):
        """FieldAnswer accepts an empty answer string."""
        fa = FieldAnswer(answer="", tier=1, confidence=0.0, tier_name="pattern")
        assert fa.answer == ""

    def test_resolve_returns_field_answer_type(self, fi):
        """resolve() always returns a FieldAnswer instance."""
        with patch("jobpulse.form_intelligence._generate_answer_llm", return_value="Some answer"):
            result = fi.resolve("Tell me something completely unprecedented.")
        assert isinstance(result, FieldAnswer)


# =========================================================================
# _generate_answer_llm wrapper
# =========================================================================


class TestGenerateAnswerLLM:
    def test_wrapper_delegates(self):
        """_generate_answer_llm delegates to _generate_answer."""
        from jobpulse.form_intelligence import _generate_answer_llm

        with patch(
            "jobpulse.form_intelligence._generate_answer", return_value="delegated"
        ) as mock_gen:
            result = _generate_answer_llm("some question", {"job_title": "Engineer"})

        mock_gen.assert_called_once_with("some question", {"job_title": "Engineer"})
        assert result == "delegated"
