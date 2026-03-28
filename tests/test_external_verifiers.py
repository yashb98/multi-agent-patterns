"""Tests for shared/external_verifiers.py"""

import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest


SAMPLE_S2_RESPONSE = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "authors": [{"authorId": "1", "name": "Ashish Vaswani"}],
    "citationCount": 90000,
    "referenceCount": 42,
    "venue": "NeurIPS",
    "publicationDate": "2017-06-12",
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762", "status": "GREEN"},
    "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222.3295349"},
}


class TestSemanticScholarVerifier:
    def test_lookup_returns_paper_metadata(self):
        from shared.external_verifiers import semantic_scholar_lookup

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = SAMPLE_S2_RESPONSE
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = semantic_scholar_lookup("1706.03762")

        assert result is not None
        assert result["citation_count"] == 90000
        assert result["venue"] == "NeurIPS"
        assert result["publication_date"] == "2017-06-12"
        assert result["authors"][0] == "Ashish Vaswani"
        assert result["is_peer_reviewed"] is True
        assert result["reference_count"] == 42
        assert result["doi"] == "10.5555/3295222.3295349"

    def test_lookup_returns_none_on_404(self):
        from shared.external_verifiers import semantic_scholar_lookup

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.raise_for_status.side_effect = Exception("404")
            mock_get.return_value = mock_resp

            result = semantic_scholar_lookup("9999.99999")

        assert result is None

    def test_lookup_returns_none_on_rate_limit(self):
        from shared.external_verifiers import semantic_scholar_lookup

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.raise_for_status.side_effect = Exception("429")
            mock_get.return_value = mock_resp

            result = semantic_scholar_lookup("1706.03762")

        assert result is None

    def test_verify_attribution_claim(self):
        from shared.external_verifiers import verify_claim_with_s2

        s2_data = {
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "venue": "NeurIPS",
            "publication_date": "2017-06-12",
            "citation_count": 90000,
            "is_peer_reviewed": True,
        }
        claim = {"claim": "proposed by Vaswani et al.", "type": "attribution"}

        result = verify_claim_with_s2(claim, s2_data)

        assert result["verdict"] == "VERIFIED"
        assert result["source"] == "semantic_scholar"
        assert result["confidence"] == 0.9

    def test_verify_date_claim_inaccurate(self):
        from shared.external_verifiers import verify_claim_with_s2

        s2_data = {
            "authors": ["Author"],
            "venue": "ICML",
            "publication_date": "2023-07-15",
            "citation_count": 100,
            "is_peer_reviewed": True,
        }
        claim = {"claim": "published in 2022", "type": "date"}

        result = verify_claim_with_s2(claim, s2_data)

        assert result["verdict"] == "INACCURATE"
        assert "2023" in result["evidence"]
        assert result["fix_suggestion"] == "Correct year to 2023"


# ── GitHub Repo Health Tests ──

SAMPLE_REPO_DATA = {
    "stargazers_count": 1200,
    "forks_count": 340,
    "archived": False,
    "license": {"spdx_id": "MIT"},
    "pushed_at": datetime.now(timezone.utc).isoformat(),
}

SAMPLE_CONTENTS = [
    {"name": "README.md", "type": "file"},
    {"name": "tests", "type": "dir"},
    {"name": "pyproject.toml", "type": "file"},
    {"name": "src", "type": "dir"},
]


class TestRepoHealthChecker:
    def test_healthy_repo(self):
        from shared.external_verifiers import check_repo_health

        def mock_gh_api(endpoint):
            if endpoint.endswith("/contents"):
                return SAMPLE_CONTENTS
            return SAMPLE_REPO_DATA

        with patch("shared.external_verifiers._gh_api", side_effect=mock_gh_api):
            result = check_repo_health("https://github.com/owner/repo")

        assert result["status"] == "REPO_HEALTHY"
        assert result["stars"] == 1200
        assert result["forks"] == 340
        assert result["has_tests"] is True
        assert result["has_readme"] is True
        assert result["has_license"] is True
        assert result["has_requirements"] is True
        assert result["archived"] is False
        assert result["score_adjustment"] == 0.0
        assert "Healthy" in result["summary"]

    def test_unhealthy_repo_no_tests(self):
        from shared.external_verifiers import check_repo_health

        contents_no_tests = [
            {"name": "README.md", "type": "file"},
            {"name": "src", "type": "dir"},
            {"name": "pyproject.toml", "type": "file"},
        ]

        def mock_gh_api(endpoint):
            if endpoint.endswith("/contents"):
                return contents_no_tests
            return SAMPLE_REPO_DATA

        with patch("shared.external_verifiers._gh_api", side_effect=mock_gh_api):
            result = check_repo_health("https://github.com/owner/repo")

        assert result["status"] == "REPO_UNHEALTHY"
        assert result["has_tests"] is False
        assert result["score_adjustment"] == -0.3
        assert "no tests" in result["summary"]

    def test_missing_repo(self):
        from shared.external_verifiers import check_repo_health

        with patch(
            "shared.external_verifiers._gh_api",
            side_effect=RuntimeError("gh api failed: Not Found"),
        ):
            result = check_repo_health("https://github.com/owner/nonexistent")

        assert result["status"] == "REPO_MISSING"
        assert result["score_adjustment"] == -0.5
        assert "not accessible" in result["summary"]

    def test_no_url_returns_na(self):
        from shared.external_verifiers import check_repo_health

        result = check_repo_health(None)
        assert result["status"] == "REPO_NA"
        assert result["score_adjustment"] == 0.0
        assert "No repository URL" in result["summary"]

        result2 = check_repo_health("")
        assert result2["status"] == "REPO_NA"

    def test_stale_repo(self):
        from shared.external_verifiers import check_repo_health

        stale_data = {
            **SAMPLE_REPO_DATA,
            "pushed_at": "2023-01-01T00:00:00Z",
        }

        def mock_gh_api(endpoint):
            if endpoint.endswith("/contents"):
                return SAMPLE_CONTENTS
            return stale_data

        with patch("shared.external_verifiers._gh_api", side_effect=mock_gh_api):
            result = check_repo_health("https://github.com/owner/repo")

        assert result["status"] == "REPO_UNHEALTHY"
        assert result["days_since_push"] > 365
        assert result["score_adjustment"] == -0.3
        assert "stale" in result["summary"]

    @pytest.mark.parametrize(
        "url, expected_owner, expected_repo",
        [
            ("https://github.com/owner/repo", "owner", "repo"),
            ("https://github.com/owner/repo.git", "owner", "repo"),
            ("https://github.com/owner/repo/tree/main", "owner", "repo"),
            ("github.com/owner/repo", "owner", "repo"),
            ("https://github.com/org-name/my_repo.git", "org-name", "my_repo"),
        ],
    )
    def test_extracts_owner_repo_from_url(self, url, expected_owner, expected_repo):
        from shared.external_verifiers import _parse_github_url

        result = _parse_github_url(url)
        assert result is not None
        assert result == (expected_owner, expected_repo)

    def test_parse_invalid_url_returns_none(self):
        from shared.external_verifiers import _parse_github_url

        assert _parse_github_url(None) is None
        assert _parse_github_url("") is None
        assert _parse_github_url("https://gitlab.com/owner/repo") is None
        assert _parse_github_url("not a url at all") is None


# ── Quality Web Search Tests ──


class TestQualityWebSearch:
    def test_scores_academic_sources_higher(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://arxiv.org/abs/2301.00001") > 0.8
        assert score_source_quality("https://openreview.net/forum?id=abc") > 0.8
        assert score_source_quality("https://proceedings.neurips.cc/paper/2023") > 0.8

    def test_scores_blogs_lower(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://medium.com/@user/some-post") < 0.5
        assert score_source_quality("https://towardsdatascience.com/article") < 0.5

    def test_scores_official_docs_medium(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://pytorch.org/docs/stable/") >= 0.6
        assert score_source_quality("https://huggingface.co/docs/transformers") >= 0.6

    def test_scores_empty_url_lowest(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("") == 0.1
        assert score_source_quality(None) == 0.1

    def test_scores_unknown_domain(self):
        from shared.external_verifiers import score_source_quality

        score = score_source_quality("https://randomsite.example.com/page")
        assert score == 0.4

    def test_handles_www_prefix(self):
        from shared.external_verifiers import score_source_quality

        assert score_source_quality("https://www.nature.com/articles/123") > 0.8
        assert score_source_quality("https://www.medium.com/@user/post") < 0.5

    def test_quality_web_verify_returns_best_source(self):
        from shared.external_verifiers import quality_web_verify

        mock_results = [
            {"href": "https://medium.com/@user/transformers-guide", "body": "Blog about transformers"},
            {"href": "https://arxiv.org/abs/1706.03762", "body": "Attention Is All You Need"},
            {"href": "https://stackoverflow.com/q/123", "body": "How to use transformers"},
        ]

        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__ = MagicMock(return_value=mock_ddgs_instance)
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)
        mock_ddgs_instance.text.return_value = mock_results

        MockDDGS = MagicMock(return_value=mock_ddgs_instance)

        # Create a mock module with DDGS attribute
        mock_module = MagicMock()
        mock_module.DDGS = MockDDGS

        with patch.dict("sys.modules", {"duckduckgo_search": mock_module}):
            result = quality_web_verify("transformer architecture")

        assert result["best_source_quality"] == 0.9
        assert "arxiv.org" in result["best_source_url"]
        assert len(result["all_results"]) == 3
        assert len(result["snippets"]) == 3
        # Verify sorted by quality descending
        qualities = [r["quality"] for r in result["all_results"]]
        assert qualities == sorted(qualities, reverse=True)

    def test_quality_web_verify_handles_import_error(self):
        """When duckduckgo_search is not installed, returns empty result."""
        from shared.external_verifiers import quality_web_verify

        # Remove from sys.modules so the import inside the function fails
        import sys
        original = sys.modules.get("duckduckgo_search")
        sys.modules["duckduckgo_search"] = None  # Forces ImportError on `from X import Y`
        try:
            result = quality_web_verify("test query")
        finally:
            if original is not None:
                sys.modules["duckduckgo_search"] = original
            else:
                sys.modules.pop("duckduckgo_search", None)

        assert result["best_source_quality"] == 0.0
        assert result["snippets"] == []
        assert result["all_results"] == []
