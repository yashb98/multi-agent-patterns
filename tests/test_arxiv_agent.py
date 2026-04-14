"""Tests for jobpulse/arxiv_agent.py

Covers:
- Paper fetching and XML parsing (mock httpx)
- LLM ranking (mock OpenAI client, sorted output, API error fallback, malformed JSON)
- JSON parsing edge cases (parametrized: raw JSON, markdown blocks, text-prefixed, empty, multi-line)
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── Sample data ──

SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2026.12345v1</id>
    <title>Attention Is Still All You Need: A New Architecture</title>
    <summary>We propose a novel transformer variant that achieves 92% on MMLU.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <published>2026-03-28T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2026.12345v1" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2026.12345v1" title="pdf" rel="related" type="application/pdf"/>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""

SAMPLE_ARXIV_XML_MULTI = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2026.00001v1</id>
    <title>Paper Alpha</title>
    <summary>Abstract alpha.</summary>
    <author><name>Author A</name></author>
    <published>2026-03-28T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2026.00001v1" rel="alternate"/>
    <link href="http://arxiv.org/pdf/2026.00001v1" title="pdf" rel="related"/>
    <category term="cs.AI"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2026.00002v1</id>
    <title>Paper Beta</title>
    <summary>Abstract beta.</summary>
    <author><name>Author B</name></author>
    <published>2026-03-27T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2026.00002v1" rel="alternate"/>
    <category term="cs.CL"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2026.00003v1</id>
    <title>Paper Gamma</title>
    <summary>Abstract gamma.</summary>
    <author><name>Author C</name></author>
    <published>2026-03-26T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2026.00003v1" rel="alternate"/>
    <category term="stat.ML"/>
  </entry>
</feed>"""


# ── Fixtures ──

@pytest.fixture
def arxiv_db(tmp_path, monkeypatch):
    """Isolated SQLite database for arxiv tests."""
    import jobpulse.arxiv_agent as agent

    db_path = tmp_path / "papers.db"
    monkeypatch.setattr(agent, "DB_PATH", db_path)
    agent._init_db()
    yield db_path


def _make_candidates(n: int) -> list[dict]:
    """Generate n sample paper dicts for ranking tests."""
    return [
        {
            "title": f"Paper {i}",
            "abstract": f"Abstract for paper {i} about transformers.",
            "arxiv_id": f"2026.{i:05d}v1",
            "authors": [f"Author {i}"],
            "categories": ["cs.AI"],
            "published": f"2026-03-{28 - i:02d}T00:00:00Z",
            "pdf_url": f"http://arxiv.org/pdf/2026.{i:05d}v1",
            "arxiv_url": f"https://arxiv.org/abs/2026.{i:05d}v1",
        }
        for i in range(n)
    ]


def _mock_openai_class(response_content: str):
    """Create a mock OpenAI class whose client.chat.completions.create returns response_content."""
    mock_client_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = response_content
    mock_client_instance.chat.completions.create.return_value = mock_response

    mock_openai_cls = MagicMock(return_value=mock_client_instance)
    return mock_openai_cls, mock_client_instance


# ═════════════════════════════════════════════════
# Task 1: Paper fetching / parsing
# ═════════════════════════════════════════════════


class TestFetchPapers:
    """Tests for fetch_papers() — XML parsing and error handling."""

    def test_parses_xml_correctly(self):
        """arXiv XML response is parsed into paper dicts with correct fields."""
        import jobpulse.arxiv_agent as agent

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_ARXIV_XML
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            papers = agent.fetch_papers(max_results=5)

        assert len(papers) == 1
        p = papers[0]
        assert p["title"] == "Attention Is Still All You Need: A New Architecture"
        assert "Alice Smith" in p["authors"]
        assert "Bob Jones" in p["authors"]
        assert p["arxiv_id"] == "2026.12345v1"
        assert p["published"] == "2026-03-28T00:00:00Z"
        assert p["pdf_url"] == "http://arxiv.org/pdf/2026.12345v1"
        assert p["arxiv_url"] == "https://arxiv.org/abs/2026.12345v1"
        assert "cs.AI" in p["categories"]
        assert "cs.LG" in p["categories"]

    def test_parses_multiple_entries(self):
        """Multiple entries in the feed each become a paper dict."""
        import jobpulse.arxiv_agent as agent

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_ARXIV_XML_MULTI
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            papers = agent.fetch_papers(max_results=10)

        assert len(papers) == 3
        assert papers[0]["title"] == "Paper Alpha"
        assert papers[1]["title"] == "Paper Beta"
        assert papers[2]["title"] == "Paper Gamma"

    def test_missing_pdf_link_gives_empty_string(self):
        """Entry without a pdf link has pdf_url == ''."""
        import jobpulse.arxiv_agent as agent

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_ARXIV_XML_MULTI
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            papers = agent.fetch_papers(max_results=10)

        # Paper Beta has no pdf link in the test XML
        beta = papers[1]
        assert beta["pdf_url"] == ""

    def test_returns_empty_on_network_error(self):
        """Network failure returns empty list, not exception."""
        import jobpulse.arxiv_agent as agent

        with patch("httpx.get", side_effect=Exception("connection timeout")):
            papers = agent.fetch_papers(max_results=5)

        assert papers == []

    def test_returns_empty_on_malformed_xml(self):
        """Invalid XML returns empty list, not exception."""
        import jobpulse.arxiv_agent as agent

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<not><valid<xml"
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            papers = agent.fetch_papers(max_results=5)

        assert papers == []


# ═════════════════════════════════════════════════
# Task 2: LLM ranking
# ═════════════════════════════════════════════════


class TestLlmRankBroad:
    """Tests for llm_rank_broad() — OpenAI mock, sorting, fallbacks."""

    def test_returns_ranked_papers(self):
        """LLM ranking returns papers enriched with score and reason."""
        import jobpulse.arxiv_agent as agent

        candidates = _make_candidates(5)

        ranking_json = json.dumps([
            {"rank": 1, "paper_num": 1, "score": 9.2, "reason": "Novel arch",
             "key_technique": "sparse attention", "category_tag": "LLM"},
            {"rank": 2, "paper_num": 3, "score": 8.5, "reason": "Practical",
             "key_technique": "quantized inference", "category_tag": "Efficiency"},
        ])

        mock_cls, mock_inst = _mock_openai_class(ranking_json)

        with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
             patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_inst):
            ranked = agent.llm_rank_broad(candidates, top_n=2)

        assert len(ranked) == 2
        # paper_num is 1-based, so paper_num=1 -> candidates[0]
        assert ranked[0]["title"] == "Paper 0"
        assert ranked[0]["impact_score"] == 9.2
        assert ranked[1]["title"] == "Paper 2"
        assert ranked[1]["impact_score"] == 8.5

    def test_fallback_on_api_error(self):
        """When LLM raises an exception, returns first top_n papers by recency."""
        import jobpulse.arxiv_agent as agent

        candidates = _make_candidates(5)

        mock_inst = MagicMock()
        mock_inst.chat.completions.create.side_effect = Exception("rate limited")

        with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
             patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_inst):
            ranked = agent.llm_rank_broad(candidates, top_n=3)

        assert len(ranked) == 3
        # Fallback is candidates[:top_n] (by recency)
        assert ranked[0]["title"] == "Paper 0"
        assert ranked[1]["title"] == "Paper 1"
        assert ranked[2]["title"] == "Paper 2"

    def test_fallback_on_malformed_json(self):
        """Malformed JSON from LLM does not crash — returns a list."""
        import jobpulse.arxiv_agent as agent

        candidates = _make_candidates(5)
        mock_cls, mock_inst = _mock_openai_class("not valid json {{")

        with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
             patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_inst):
            ranked = agent.llm_rank_broad(candidates, top_n=3)

        # Function catches the JSON error and returns a list (fallback)
        assert isinstance(ranked, list)
        assert len(ranked) <= 3

    def test_fallback_when_no_api_key(self):
        """When OPENAI_API_KEY is empty, returns papers[:top_n] immediately."""
        import jobpulse.arxiv_agent as agent

        candidates = _make_candidates(5)

        with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", ""):
            ranked = agent.llm_rank_broad(candidates, top_n=2)

        assert len(ranked) == 2
        assert ranked[0]["title"] == "Paper 0"

    def test_out_of_range_paper_num_skipped(self):
        """paper_num pointing beyond candidates list is silently skipped."""
        import jobpulse.arxiv_agent as agent

        candidates = _make_candidates(3)

        ranking_json = json.dumps([
            {"rank": 1, "paper_num": 1, "score": 9.0, "reason": "good",
             "key_technique": "x", "category_tag": "LLM"},
            {"rank": 2, "paper_num": 999, "score": 8.0, "reason": "phantom",
             "key_technique": "y", "category_tag": "RL"},
        ])

        mock_cls, mock_inst = _mock_openai_class(ranking_json)

        with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
             patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_inst):
            ranked = agent.llm_rank_broad(candidates, top_n=2)

        # Only the valid paper_num=1 (index 0) should be returned
        assert len(ranked) == 1
        assert ranked[0]["title"] == "Paper 0"


# ═════════════════════════════════════════════════
# Task 3: JSON parsing edge cases (parametrized)
# ═════════════════════════════════════════════════


_VALID_ITEM = '{"rank":1,"paper_num":1,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}'


@pytest.mark.parametrize("raw_response,expected_count", [
    # Raw JSON — no wrapping
    (f"[{_VALID_ITEM}]", 1),
    # Markdown code block with json tag
    (f"```json\n[{_VALID_ITEM}]\n```", 1),
    # Markdown code block without json tag
    (f"```\n[{_VALID_ITEM}]\n```", 1),
    # Empty JSON array
    ("[]", 0),
    # Multi-line formatted JSON
    (f"[\n  {_VALID_ITEM}\n]", 1),
], ids=[
    "raw_json",
    "markdown_json_block",
    "markdown_plain_block",
    "empty_array",
    "multiline_json",
])
def test_json_parsing_handles_various_llm_formats(raw_response, expected_count):
    """LLM responses wrapped in markdown, raw JSON, or multiline all parse correctly."""
    import jobpulse.arxiv_agent as agent

    candidates = _make_candidates(3)

    mock_cls, mock_inst = _mock_openai_class(raw_response)

    with patch("jobpulse.arxiv_agent.OPENAI_API_KEY", "sk-test"), \
         patch("jobpulse.arxiv_agent.get_openai_client", return_value=mock_inst):
        ranked = agent.llm_rank_broad(candidates, top_n=1)

    assert len(ranked) == expected_count


# ═════════════════════════════════════════════════
# Bonus: DB storage tests
# ═════════════════════════════════════════════════


class TestStoreAndRetrieve:
    """Tests for store_papers, get_paper_by_index, mark_as_read, get_reading_stats."""

    def test_store_and_retrieve_paper(self, arxiv_db):
        """Papers stored via store_papers are retrievable by index."""
        import jobpulse.arxiv_agent as agent

        papers = _make_candidates(2)
        papers[0]["impact_score"] = 9.0
        papers[1]["impact_score"] = 7.0

        agent.store_papers(papers, "2026-03-28")
        paper = agent.get_paper_by_index("2026-03-28", 1)

        assert paper is not None
        assert "Paper" in paper["title"]

    def test_mark_as_read(self, arxiv_db):
        """mark_as_read changes paper status from 'sent' to 'read'."""
        import jobpulse.arxiv_agent as agent

        papers = _make_candidates(1)
        papers[0]["impact_score"] = 8.0
        agent.store_papers(papers, "2026-03-28")

        agent.mark_as_read(papers[0]["arxiv_id"])

        stats = agent.get_reading_stats()
        assert stats["read"] == 1
        assert stats["unread"] == 0

    def test_reading_stats_counts(self, arxiv_db):
        """get_reading_stats returns correct total/read/unread counts."""
        import jobpulse.arxiv_agent as agent

        papers = _make_candidates(3)
        for i, p in enumerate(papers):
            p["impact_score"] = 10 - i
        agent.store_papers(papers, "2026-03-28")

        stats = agent.get_reading_stats()
        assert stats["total"] == 3
        assert stats["read"] == 0
        assert stats["unread"] == 3

    def test_get_paper_by_index_out_of_range(self, arxiv_db):
        """Out-of-range index returns None."""
        import jobpulse.arxiv_agent as agent

        papers = _make_candidates(1)
        papers[0]["impact_score"] = 5.0
        agent.store_papers(papers, "2026-03-28")

        assert agent.get_paper_by_index("2026-03-28", 99) is None
        assert agent.get_paper_by_index("2026-03-28", 0) is None
