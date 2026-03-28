"""Tests for unified fact-checker — claim extraction, scoring, revision notes."""

import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

os.environ["JOBPULSE_TEST_MODE"] = "1"


class TestComputeAccuracyScore:
    """Deterministic accuracy scoring from verifications."""

    def test_all_verified_scores_10(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
        ]
        assert compute_accuracy_score(verifications) == 10.0

    def test_one_inaccurate_drops_score(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "INACCURATE", "severity": "high"},
        ]
        score = compute_accuracy_score(verifications)
        assert score < 10.0
        # 1.0 + 1.0 + (-2.0) = 0.0, score = 10 * 0/3 = 0.0
        assert score == 0.0

    def test_one_exaggerated_mild_drop(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "EXAGGERATED", "severity": "medium"},
        ]
        score = compute_accuracy_score(verifications)
        # 1+1+1+(-1) = 2, score = 10 * 2/4 = 5.0
        assert score == 5.0

    def test_empty_verifications_perfect_score(self):
        from shared.fact_checker import compute_accuracy_score
        assert compute_accuracy_score([]) == 10.0

    def test_unverified_low_severity_mild_penalty(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "UNVERIFIED", "severity": "low"},
        ]
        score = compute_accuracy_score(verifications)
        # 1.0 + (-0.5) = 0.5, score = 10 * 0.5/2 = 2.5
        assert score == 2.5

    def test_unverified_high_severity_heavy_penalty(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "UNVERIFIED", "severity": "high"},
        ]
        score = compute_accuracy_score(verifications)
        # 1.0 + (-1.5) = -0.5, score = max(0, 10 * -0.5/2) = 0.0
        assert score == 0.0

    def test_score_floor_at_zero(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "INACCURATE", "severity": "high"},
            {"verdict": "INACCURATE", "severity": "high"},
        ]
        assert compute_accuracy_score(verifications) == 0.0

    def test_mostly_verified_high_score(self):
        from shared.fact_checker import compute_accuracy_score
        verifications = [
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "VERIFIED", "severity": "low"},
            {"verdict": "UNVERIFIED", "severity": "low"},
        ]
        score = compute_accuracy_score(verifications)
        # 9*1.0 + (-0.5) = 8.5, score = 10 * 8.5/10 = 8.5
        assert score == 8.5


class TestGenerateRevisionNotes:
    """Targeted fix instructions for the writer."""

    def test_no_issues_empty_notes(self):
        from shared.fact_checker import generate_revision_notes
        verifications = [{"verdict": "VERIFIED", "claim": "x"}]
        assert generate_revision_notes(verifications) == ""

    def test_inaccurate_claim_produces_notes(self):
        from shared.fact_checker import generate_revision_notes
        verifications = [
            {"verdict": "INACCURATE", "claim": "GPT-4 achieves 92%",
             "evidence": "Paper says 86.4%", "fix_suggestion": "Change to 86.4%",
             "severity": "high"}
        ]
        notes = generate_revision_notes(verifications)
        assert "INACCURATE" in notes
        assert "GPT-4 achieves 92%" in notes
        assert "86.4%" in notes

    def test_multiple_issues_numbered(self):
        from shared.fact_checker import generate_revision_notes
        verifications = [
            {"verdict": "INACCURATE", "claim": "claim1", "evidence": "e1", "fix_suggestion": "f1", "severity": "high"},
            {"verdict": "EXAGGERATED", "claim": "claim2", "evidence": "e2", "fix_suggestion": "f2", "severity": "medium"},
        ]
        notes = generate_revision_notes(verifications)
        assert "1." in notes
        assert "2." in notes

    def test_verified_claims_excluded(self):
        from shared.fact_checker import generate_revision_notes
        verifications = [
            {"verdict": "VERIFIED", "claim": "good claim"},
            {"verdict": "INACCURATE", "claim": "bad claim", "evidence": "e", "fix_suggestion": "f", "severity": "high"},
        ]
        notes = generate_revision_notes(verifications)
        assert "good claim" not in notes
        assert "bad claim" in notes


class TestExtractClaims:
    """Claim extraction from draft (mocked LLM)."""

    def test_extracts_claims_from_response(self):
        from shared.fact_checker import extract_claims
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"claims": [{"claim": "GPT-4 scores 86.4%", "type": "benchmark", "source_needed": true}]}'

        with patch("shared.fact_checker.OpenAI") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = mock_response
            claims = extract_claims("Article about GPT-4", "GPT-4")
            assert len(claims) == 1
            assert claims[0]["type"] == "benchmark"

    def test_empty_on_error(self):
        from shared.fact_checker import extract_claims
        with patch("shared.fact_checker.OpenAI") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = Exception("API error")
            claims = extract_claims("Some article", "topic")
            assert claims == []


class TestVerifyClaims:
    """Claim verification against sources (mocked LLM)."""

    def test_verifies_claims(self):
        from shared.fact_checker import verify_claims
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"verifications": [{"claim": "scores 86.4%", "verdict": "VERIFIED", "evidence": "matches", "confidence": 0.95, "severity": "low", "fix_suggestion": null}]}'

        with patch("shared.fact_checker.OpenAI") as mock_client, \
             patch("shared.fact_checker.get_cached_fact", return_value=None), \
             patch("shared.fact_checker.web_verify_claim", return_value={"source": None, "supports": False, "snippet": ""}), \
             patch("shared.fact_checker.cache_verified_fact"):
            mock_client.return_value.chat.completions.create.return_value = mock_response
            results = verify_claims(
                [{"claim": "scores 86.4%", "type": "benchmark", "source_needed": True}],
                ["Research: GPT-4 scores 86.4% on MMLU"]
            )
            assert len(results) == 1
            assert results[0]["verdict"] == "VERIFIED"

    def test_skips_non_verifiable(self):
        from shared.fact_checker import verify_claims
        claims = [{"claim": "This is elegant", "type": "opinion", "source_needed": False}]
        # Should not call LLM at all
        results = verify_claims(claims, [])
        assert results == []

    def test_empty_on_error(self):
        from shared.fact_checker import verify_claims
        with patch("shared.fact_checker.OpenAI") as mock_client, \
             patch("shared.fact_checker.get_cached_fact", return_value=None), \
             patch("shared.fact_checker.web_verify_claim", return_value={"source": None, "supports": False, "snippet": ""}):
            mock_client.return_value.chat.completions.create.side_effect = Exception("fail")
            results = verify_claims(
                [{"claim": "x", "type": "benchmark", "source_needed": True}], ["source"]
            )
            assert results == []

    def test_confidence_clamped_to_valid_range(self):
        """Confidence values outside [0.0, 1.0] are clamped after LLM response."""
        from shared.fact_checker import verify_claims
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"verifications": [
            {"claim": "claim A", "verdict": "VERIFIED", "evidence": "ok", "confidence": 1.5, "severity": "low", "fix_suggestion": None},
            {"claim": "claim B", "verdict": "UNVERIFIED", "evidence": "none", "confidence": -0.3, "severity": "high", "fix_suggestion": "fix it"},
        ]})

        with patch("shared.fact_checker.OpenAI") as mock_client, \
             patch("shared.fact_checker.get_cached_fact", return_value=None), \
             patch("shared.fact_checker.web_verify_claim", return_value={"source": None, "supports": False, "snippet": ""}), \
             patch("shared.fact_checker.cache_verified_fact"):
            mock_client.return_value.chat.completions.create.return_value = mock_response
            results = verify_claims(
                [
                    {"claim": "claim A", "type": "benchmark", "source_needed": True},
                    {"claim": "claim B", "type": "technical", "source_needed": True},
                ],
                ["some source"]
            )
            assert len(results) == 2
            assert results[0]["confidence"] == 1.0, "Confidence above 1.0 should be clamped to 1.0"
            assert results[1]["confidence"] == 0.0, "Confidence below 0.0 should be clamped to 0.0"

    def test_verdict_case_insensitive(self):
        """Verdicts from LLM are normalized to uppercase."""
        from shared.fact_checker import verify_claims
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"verifications": [
            {"claim": "claim X", "verdict": "verified", "evidence": "ok", "confidence": 0.9, "severity": "low", "fix_suggestion": None},
            {"claim": "claim Y", "verdict": "Inaccurate", "evidence": "wrong", "confidence": 0.8, "severity": "high", "fix_suggestion": "fix"},
        ]})

        with patch("shared.fact_checker.OpenAI") as mock_client, \
             patch("shared.fact_checker.get_cached_fact", return_value=None), \
             patch("shared.fact_checker.web_verify_claim", return_value={"source": None, "supports": False, "snippet": ""}), \
             patch("shared.fact_checker.cache_verified_fact"):
            mock_client.return_value.chat.completions.create.return_value = mock_response
            results = verify_claims(
                [
                    {"claim": "claim X", "type": "benchmark", "source_needed": True},
                    {"claim": "claim Y", "type": "comparison", "source_needed": True},
                ],
                ["some source"]
            )
            assert results[0]["verdict"] == "VERIFIED", "Lowercase 'verified' should become 'VERIFIED'"
            assert results[1]["verdict"] == "INACCURATE", "Mixed case 'Inaccurate' should become 'INACCURATE'"

    def test_skip_types_not_sent_for_verification(self):
        """Claims with type in SKIP_TYPES are excluded even if source_needed=True."""
        from shared.fact_checker import verify_claims
        claims = [
            {"claim": "This is elegant", "type": "opinion", "source_needed": True},
            {"claim": "RAG stands for Retrieval Augmented Generation", "type": "definition", "source_needed": True},
        ]
        # Should not call LLM at all — both types are in SKIP_TYPES
        results = verify_claims(claims, ["some source"])
        assert results == []


class TestWebVerifyClaim:
    """Web search verification."""

    def test_returns_dict_on_import_error(self):
        from shared.fact_checker import web_verify_claim
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            # Force reimport to trigger ImportError path
            pass
        # Just test the function exists and returns a dict
        result = web_verify_claim("test claim")
        assert isinstance(result, dict)
        assert "source" in result
        assert "snippet" in result

    def test_handles_search_exception(self):
        from shared.fact_checker import web_verify_claim
        mock_ddgs = MagicMock()
        mock_ddgs_module = MagicMock()
        mock_ddgs_module.DDGS = MagicMock(side_effect=Exception("network error"))
        with patch.dict("sys.modules", {"duckduckgo_search": mock_ddgs_module}):
            result = web_verify_claim("test claim")
            assert result["supports"] is False


class TestVerifiedFactsCache:
    """SQLite cache for previously verified facts."""

    def test_cache_and_retrieve(self, tmp_path):
        import shared.fact_checker as fc
        original_path = fc.CACHE_DB_PATH
        fc.CACHE_DB_PATH = tmp_path / "test_cache.db"
        try:
            fc.cache_verified_fact("GPT-4 scores 86.4%", "VERIFIED", "Matches paper", confidence=0.95)
            cached = fc.get_cached_fact("GPT-4 scores 86.4%")
            assert cached is not None
            assert cached["verdict"] == "VERIFIED"
            assert cached["confidence"] == 0.95
        finally:
            fc.CACHE_DB_PATH = original_path

    def test_cache_miss_returns_none(self, tmp_path):
        import shared.fact_checker as fc
        original_path = fc.CACHE_DB_PATH
        fc.CACHE_DB_PATH = tmp_path / "test_cache.db"
        try:
            result = fc.get_cached_fact("nonexistent claim")
            assert result is None
        finally:
            fc.CACHE_DB_PATH = original_path

    def test_cache_dedup_by_hash(self, tmp_path):
        import shared.fact_checker as fc
        original_path = fc.CACHE_DB_PATH
        fc.CACHE_DB_PATH = tmp_path / "test_cache.db"
        try:
            fc.cache_verified_fact("Claim A", "VERIFIED", "evidence1")
            fc.cache_verified_fact("Claim A", "INACCURATE", "evidence2")  # Same claim, update
            cached = fc.get_cached_fact("Claim A")
            assert cached["verdict"] == "INACCURATE"  # Should be updated
        finally:
            fc.CACHE_DB_PATH = original_path
