"""Tests for community-first paper discovery."""
import sqlite3
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestDedup:
    """Test arXiv ID deduplication across sources."""

    def test_dedup_by_arxiv_id(self):
        from jobpulse.paper_discovery import dedup_by_arxiv_id

        papers = [
            {"arxiv_id": "2406.01234", "title": "Paper A", "source": "reddit", "community_buzz": 50},
            {"arxiv_id": "2406.01234", "title": "Paper A", "source": "hackernews", "community_buzz": 100},
            {"arxiv_id": "2406.05678", "title": "Paper B", "source": "huggingface", "community_buzz": 30},
        ]
        result = dedup_by_arxiv_id(papers)
        assert len(result) == 2
        paper_a = next(p for p in result if p["arxiv_id"] == "2406.01234")
        assert paper_a["community_buzz"] == 150  # aggregated across sources

    def test_dedup_empty(self):
        from jobpulse.paper_discovery import dedup_by_arxiv_id
        assert dedup_by_arxiv_id([]) == []


class TestNitterHealthTracker:
    """Test Nitter instance health tracking and rotation."""

    def test_record_success(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        tracker.record_attempt("https://nitter.net", success=True, response_code=200, latency_ms=300)
        assert tracker.get_success_rate("https://nitter.net") == 1.0

    def test_record_failure_rotates(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        for _ in range(3):
            tracker.record_attempt("https://nitter.net", success=False, response_code=403, latency_ms=0)

        best = tracker.get_best_instance()
        assert best != "https://nitter.net"  # Should rotate away

    def test_should_skip_when_all_blocked(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker, NITTER_INSTANCES

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        for instance in NITTER_INSTANCES:
            for _ in range(5):
                tracker.record_attempt(instance, success=False, response_code=403, latency_ms=0)

        assert tracker.should_skip_x() is True

    def test_reset_after_success(self, tmp_path):
        from jobpulse.paper_discovery import NitterHealthTracker

        tracker = NitterHealthTracker(db_path=tmp_path / "nitter_health.db")
        for _ in range(3):
            tracker.record_attempt("https://nitter.net", success=False, response_code=403, latency_ms=0)
        tracker.record_attempt("https://nitter.net", success=True, response_code=200, latency_ms=500)

        assert tracker.get_success_rate("https://nitter.net") > 0


class TestDiscoverTrending:
    """Test the main discovery pipeline with mocked sources."""

    @patch("jobpulse.paper_discovery.fetch_huggingface_daily")
    @patch("jobpulse.paper_discovery.fetch_reddit_papers")
    @patch("jobpulse.paper_discovery.fetch_hackernews_papers")
    @patch("jobpulse.paper_discovery.fetch_papers_with_code")
    @patch("jobpulse.paper_discovery.fetch_x_via_searxng")
    @patch("jobpulse.paper_discovery.enrich_from_semantic_scholar")
    def test_full_pipeline(self, mock_enrich, mock_x, mock_pwc, mock_hn, mock_reddit, mock_hf):
        from jobpulse.paper_discovery import discover_trending_papers

        mock_hf.return_value = [
            {"arxiv_id": "2406.01234", "title": "Cool Paper", "source": "huggingface", "community_buzz": 80},
        ]
        mock_reddit.return_value = [
            {"arxiv_id": "2406.01234", "title": "Cool Paper", "source": "reddit", "community_buzz": 40},
            {"arxiv_id": "2406.09999", "title": "Other Paper", "source": "reddit", "community_buzz": 20},
        ]
        mock_hn.return_value = []
        mock_pwc.return_value = []
        mock_x.return_value = []
        mock_enrich.side_effect = lambda papers: papers  # passthrough

        result = discover_trending_papers()
        assert len(result) == 2
        cool = next(p for p in result if p["arxiv_id"] == "2406.01234")
        assert cool["community_buzz"] == 120  # 80 + 40

    @patch("jobpulse.paper_discovery.fetch_huggingface_daily")
    @patch("jobpulse.paper_discovery.fetch_reddit_papers")
    @patch("jobpulse.paper_discovery.fetch_hackernews_papers")
    @patch("jobpulse.paper_discovery.fetch_papers_with_code")
    @patch("jobpulse.paper_discovery.fetch_x_via_searxng")
    @patch("jobpulse.paper_discovery.fetch_arxiv_rss_fallback")
    def test_fallback_to_rss(self, mock_rss, mock_x, mock_pwc, mock_hn, mock_reddit, mock_hf):
        from jobpulse.paper_discovery import discover_trending_papers

        mock_hf.return_value = []
        mock_reddit.return_value = []
        mock_hn.return_value = []
        mock_pwc.return_value = []
        mock_x.return_value = []
        mock_rss.return_value = [
            {"arxiv_id": "2406.11111", "title": "RSS Paper", "source": "arxiv_rss", "community_buzz": 0},
        ]

        result = discover_trending_papers()
        assert len(result) == 1
        assert result[0]["source"] == "arxiv_rss"
        mock_rss.assert_called_once()
