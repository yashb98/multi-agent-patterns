# arXiv Ranking & Fact Checking Quality Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve arXiv paper ranking quality (better signal, less noise) and add fact-checking verification for paper claims, with full test coverage for both systems.

**Architecture:** Add test suite for arxiv_agent (currently 0 tests), improve ranking prompt with multi-pass scoring, add confidence validation to fact_checker, wire fact-checking into arXiv paper summaries so claims like "3x faster than BERT" get verified before being sent to Telegram.

**Tech Stack:** Python, pytest, SQLite, OpenAI API (gpt-4o-mini), monkeypatch for mocks

---

## File Structure

| File | Responsibility |
|------|---------------|
| `tests/test_arxiv_agent.py` | **Create** — Full test suite for arxiv ranking |
| `tests/test_fact_checker.py` | **Modify** — Add missing coverage (confidence bounds, cache hits, batch mixed) |
| `jobpulse/arxiv_agent.py` | **Modify** — Improve ranking prompt, fix JSON parsing brittleness, add fact-check integration |
| `shared/fact_checker.py` | **Modify** — Fix confidence validation, verdict normalization, skip-type enforcement |

---

### Task 1: arXiv Agent — Test Suite Foundation

**Files:**
- Create: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for paper fetching and parsing**

```python
"""Tests for jobpulse/arxiv_agent.py"""

import json
import sqlite3
from unittest.mock import patch, MagicMock
import pytest


SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2026.12345v1</id>
    <title>Attention Is Still All You Need: A New Architecture</title>
    <summary>We propose a novel transformer variant that achieves 92% on MMLU.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <published>2026-03-28T00:00:00Z</published>
    <link href="http://arxiv.org/abs/2026.12345v1" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2026.12345v1" title="pdf" rel="related" type="application/pdf"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.AI"/>
  </entry>
</feed>"""


@pytest.fixture
def arxiv_db(tmp_path):
    """Isolated SQLite database for arxiv tests."""
    db_path = tmp_path / "papers.db"
    import jobpulse.arxiv_agent as agent
    original = agent.DB_PATH
    agent.DB_PATH = str(db_path)
    agent._init_db()
    yield db_path
    agent.DB_PATH = original


def test_fetch_papers_parses_xml_correctly():
    """arXiv XML response is parsed into paper dicts with correct fields."""
    import jobpulse.arxiv_agent as agent

    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_ARXIV_XML
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        papers = agent.fetch_papers(max_results=5)

    assert len(papers) == 1
    assert papers[0]["title"] == "Attention Is Still All You Need: A New Architecture"
    assert "Alice Smith" in papers[0]["authors"]
    assert papers[0]["arxiv_id"] == "2026.12345v1"


def test_fetch_papers_returns_empty_on_network_error():
    """Network failure returns empty list, not exception."""
    import jobpulse.arxiv_agent as agent

    with patch("httpx.get", side_effect=Exception("timeout")):
        papers = agent.fetch_papers(max_results=5)

    assert papers == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: FAIL — tests should fail because DB_PATH or fetch_papers signature may differ from assumptions

- [ ] **Step 3: Fix test to match actual code signatures**

Read `jobpulse/arxiv_agent.py` and adjust test to match the actual function names, parameters, and DB_PATH variable name. The test structure stays the same — only adapt to the real API.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_arxiv_agent.py
git commit -m "test(arxiv): add foundation tests for paper fetching"
```

---

### Task 2: arXiv Agent — Test LLM Ranking

**Files:**
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for LLM ranking with mocked OpenAI**

```python
SAMPLE_RANKING_RESPONSE = json.dumps([
    {"rank": 1, "paper_num": 0, "score": 9.2, "reason": "Novel architecture",
     "key_technique": "sparse attention routing", "category_tag": "LLM"},
    {"rank": 2, "paper_num": 2, "score": 8.5, "reason": "Practical efficiency",
     "key_technique": "quantized inference", "category_tag": "Efficiency"},
])


def test_llm_rank_returns_sorted_papers():
    """LLM ranking returns papers sorted by score descending."""
    import jobpulse.arxiv_agent as agent

    candidates = [
        {"title": f"Paper {i}", "abstract": f"Abstract {i}", "arxiv_id": f"2026.{i}"}
        for i in range(5)
    ]

    with patch("jobpulse.arxiv_agent.openai_client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = SAMPLE_RANKING_RESPONSE
        mock_client.chat.completions.create.return_value = mock_response

        ranked = agent.llm_rank_broad(candidates, top_n=2)

    assert len(ranked) == 2
    assert ranked[0]["score"] >= ranked[1]["score"]


def test_llm_rank_fallback_on_api_error():
    """When LLM fails, returns papers by recency (no crash)."""
    import jobpulse.arxiv_agent as agent

    candidates = [
        {"title": f"Paper {i}", "abstract": f"Abstract {i}", "arxiv_id": f"2026.{i}"}
        for i in range(5)
    ]

    with patch("jobpulse.arxiv_agent.openai_client") as mock_client:
        mock_client.chat.completions.create.side_effect = Exception("rate limited")

        ranked = agent.llm_rank_broad(candidates, top_n=3)

    assert len(ranked) == 3  # Falls back to top N by recency


def test_llm_rank_handles_malformed_json():
    """Malformed JSON from LLM triggers fallback, not crash."""
    import jobpulse.arxiv_agent as agent

    candidates = [
        {"title": f"Paper {i}", "abstract": f"Abstract {i}", "arxiv_id": f"2026.{i}"}
        for i in range(5)
    ]

    with patch("jobpulse.arxiv_agent.openai_client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json {{"
        mock_client.chat.completions.create.return_value = mock_response

        ranked = agent.llm_rank_broad(candidates, top_n=3)

    assert len(ranked) == 3  # Fallback by recency
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_arxiv_agent.py -v -k "llm_rank"`
Expected: FAIL — mocking path may need adjustment

- [ ] **Step 3: Fix mocking paths to match actual code**

Read `jobpulse/arxiv_agent.py` to find the actual OpenAI client variable name and how `llm_rank_broad` parses the JSON response. Adjust mock paths accordingly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: 5 PASS (2 from Task 1 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add tests/test_arxiv_agent.py
git commit -m "test(arxiv): add LLM ranking tests with mock OpenAI"
```

---

### Task 3: arXiv Agent — Fix JSON Parsing Brittleness

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for edge-case JSON responses**

```python
@pytest.mark.parametrize("raw_response,expected_count", [
    ('```json\n[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]\n```', 1),
    ('```\n[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]\n```', 1),
    ('[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]', 1),
    ('Here are the results:\n[{"rank":1,"paper_num":0,"score":9.0,"reason":"good","key_technique":"x","category_tag":"LLM"}]', 1),
])
def test_json_parsing_handles_various_llm_formats(raw_response, expected_count):
    """LLM responses wrapped in markdown, prefixed with text, or raw JSON all parse correctly."""
    import jobpulse.arxiv_agent as agent

    candidates = [
        {"title": "Paper 0", "abstract": "Abstract 0", "arxiv_id": "2026.0"}
    ]

    with patch("jobpulse.arxiv_agent.openai_client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = raw_response
        mock_client.chat.completions.create.return_value = mock_response

        ranked = agent.llm_rank_broad(candidates, top_n=1)

    assert len(ranked) == expected_count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arxiv_agent.py -v -k "json_parsing"`
Expected: FAIL on some parametrized cases (text-prefixed, double backticks)

- [ ] **Step 3: Fix JSON parsing in `llm_rank_broad`**

In `jobpulse/arxiv_agent.py`, replace the brittle string-split JSON extraction with a robust parser:

```python
import re

def _extract_json_array(raw: str) -> list:
    """Extract JSON array from LLM response, handling markdown wrappers and text prefixes."""
    # Strip markdown code blocks
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    # Find the first [ ... ] block
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return []
```

Replace the existing JSON parsing logic in `llm_rank_broad` with a call to `_extract_json_array(raw)`.

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "fix(arxiv): robust JSON parsing for LLM ranking responses"
```

---

### Task 4: arXiv Agent — Improve Ranking Prompt (Multi-Criteria Scoring)

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for multi-criteria score breakdown**

```python
SAMPLE_MULTICRITERIA_RESPONSE = json.dumps([
    {
        "rank": 1, "paper_num": 0,
        "scores": {"novelty": 9, "significance": 8, "practical": 9, "breadth": 7},
        "overall": 8.5,
        "reason": "Novel architecture with practical applications",
        "key_technique": "sparse attention routing",
        "category_tag": "LLM"
    },
])


def test_ranking_includes_per_criteria_scores():
    """Ranked papers include individual scores for novelty, significance, practical, breadth."""
    import jobpulse.arxiv_agent as agent

    candidates = [
        {"title": "Paper 0", "abstract": "A new transformer architecture", "arxiv_id": "2026.0"}
    ]

    with patch("jobpulse.arxiv_agent.openai_client") as mock_client:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = SAMPLE_MULTICRITERIA_RESPONSE
        mock_client.chat.completions.create.return_value = mock_response

        ranked = agent.llm_rank_broad(candidates, top_n=1)

    assert "scores" in ranked[0]
    assert ranked[0]["scores"]["novelty"] == 9
    assert ranked[0]["overall"] == 8.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arxiv_agent.py -v -k "per_criteria"`
Expected: FAIL — current code uses flat `score` field, not `scores` dict

- [ ] **Step 3: Update ranking prompt and parser**

In `jobpulse/arxiv_agent.py`, update the `llm_rank_broad` function:

**New prompt** (replace the existing ranking prompt):
```python
RANKING_PROMPT = """You are an AI research curator for a daily digest. Your audience is
AI/ML engineers and researchers who want to stay on top of the field.

From these {count} recent arXiv papers, pick the TOP {top_n} most impactful.

Score each paper on 4 dimensions (0-10 each):
1. NOVELTY — genuinely new idea, architecture, or technique (not incremental)
2. SIGNIFICANCE — could change how people build AI systems
3. PRACTICAL — useful for practitioners today, not just theoretical
4. BREADTH — relevant across multiple AI subfields

Skip: survey papers, minor improvements, dataset-only papers.
Prefer: breakthroughs, new architectures, surprising results, open-source releases.

Return ONLY a JSON array:
[{{"rank": 1, "paper_num": X,
   "scores": {{"novelty": N, "significance": N, "practical": N, "breadth": N}},
   "overall": weighted_average,
   "reason": "One sentence why this matters",
   "key_technique": "5 words max",
   "category_tag": "LLM|Agents|Vision|RL|Efficiency|Safety|Reasoning"}}]

Compute overall as: (novelty*0.3 + significance*0.25 + practical*0.3 + breadth*0.15)
"""
```

**Update parser**: Accept both old `score` field and new `scores`/`overall` fields for backwards compatibility:

```python
for item in parsed:
    if "overall" in item:
        item["score"] = item["overall"]
    elif "scores" in item and "score" not in item:
        s = item["scores"]
        item["score"] = (s.get("novelty", 5) * 0.3 + s.get("significance", 5) * 0.25
                        + s.get("practical", 5) * 0.3 + s.get("breadth", 5) * 0.15)
        item["overall"] = item["score"]
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "feat(arxiv): multi-criteria scoring for paper ranking"
```

---

### Task 5: Fact Checker — Fix Confidence Validation & Verdict Normalization

**Files:**
- Modify: `shared/fact_checker.py`
- Modify: `tests/test_fact_checker.py`

- [ ] **Step 1: Write failing tests for confidence bounds and verdict case**

```python
def test_confidence_clamped_to_valid_range():
    """Confidence values from LLM are clamped to [0.0, 1.0]."""
    from shared.fact_checker import verify_claims

    claims = [{"claim": "GPT-4 has 1.8T params", "type": "technical", "source_needed": True}]

    mock_verifications = {"verifications": [{
        "claim": "GPT-4 has 1.8T params",
        "verdict": "UNVERIFIED",
        "evidence": "No public confirmation",
        "confidence": 1.5,  # Invalid — should be clamped to 1.0
        "severity": "medium",
        "fix_suggestion": "Remove specific parameter count"
    }]}

    with patch("shared.fact_checker._call_llm") as mock_llm:
        mock_llm.return_value = json.dumps(mock_verifications)
        results = verify_claims(claims, "Some source text")

    assert results[0]["confidence"] <= 1.0
    assert results[0]["confidence"] >= 0.0


def test_verdict_case_insensitive():
    """Verdicts like 'Verified', 'verified', 'VERIFIED' all normalize to uppercase."""
    from shared.fact_checker import verify_claims

    claims = [{"claim": "Test claim", "type": "technical", "source_needed": True}]

    mock_verifications = {"verifications": [{
        "claim": "Test claim",
        "verdict": "verified",  # lowercase from LLM
        "evidence": "Confirmed",
        "confidence": 0.9,
        "severity": "low",
        "fix_suggestion": None
    }]}

    with patch("shared.fact_checker._call_llm") as mock_llm:
        mock_llm.return_value = json.dumps(mock_verifications)
        results = verify_claims(claims, "Some source text")

    assert results[0]["verdict"] == "VERIFIED"


def test_skip_types_not_sent_for_verification():
    """Opinion and definition claims are skipped, not sent to LLM."""
    from shared.fact_checker import verify_claims

    claims = [
        {"claim": "This is promising", "type": "opinion", "source_needed": False},
        {"claim": "RAG stands for Retrieval Augmented Generation", "type": "definition", "source_needed": False},
        {"claim": "Achieves 92% on MMLU", "type": "benchmark", "source_needed": True},
    ]

    mock_verifications = {"verifications": [{
        "claim": "Achieves 92% on MMLU",
        "verdict": "VERIFIED",
        "evidence": "Paper reports 92.1%",
        "confidence": 0.95,
        "severity": "low",
        "fix_suggestion": None
    }]}

    with patch("shared.fact_checker._call_llm") as mock_llm:
        mock_llm.return_value = json.dumps(mock_verifications)
        results = verify_claims(claims, "Paper reports 92.1% accuracy on MMLU benchmark")

    # Only the benchmark claim should be in results
    verifiable = [r for r in results if r.get("verdict") != "SKIPPED"]
    assert len(verifiable) == 1
    assert verifiable[0]["claim"] == "Achieves 92% on MMLU"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fact_checker.py -v -k "confidence_clamped or case_insensitive or skip_types"`
Expected: FAIL — confidence not clamped, verdict not normalized, skip types not enforced

- [ ] **Step 3: Fix confidence, verdict, and skip-type handling**

In `shared/fact_checker.py`, in the `verify_claims` function, add post-processing after parsing LLM response:

```python
# Filter out non-verifiable claims before sending to LLM
SKIP_TYPES = {"opinion", "definition"}
verifiable_claims = [c for c in claims if c.get("type") not in SKIP_TYPES]
skipped_claims = [c for c in claims if c.get("type") in SKIP_TYPES]

# ... existing LLM call with verifiable_claims only ...

# Post-process each verification result
for v in verifications:
    # Normalize verdict to uppercase
    v["verdict"] = v.get("verdict", "UNVERIFIED").upper()
    # Clamp confidence to [0.0, 1.0]
    conf = v.get("confidence", 0.5)
    v["confidence"] = max(0.0, min(1.0, float(conf)))

# Add skipped claims back with SKIPPED verdict
for c in skipped_claims:
    verifications.append({
        "claim": c["claim"],
        "verdict": "SKIPPED",
        "evidence": f"Skipped ({c.get('type', 'unknown')} claim)",
        "confidence": 1.0,
        "severity": "low",
        "fix_suggestion": None,
    })
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_fact_checker.py -v`
Expected: All PASS (existing 23 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add shared/fact_checker.py tests/test_fact_checker.py
git commit -m "fix(fact-checker): validate confidence bounds, normalize verdicts, enforce skip types"
```

---

### Task 6: Fact Checker — Cache Hit Test & Batch Mixed Verification

**Files:**
- Modify: `tests/test_fact_checker.py`

- [ ] **Step 1: Write tests for cache hit and mixed batch**

```python
def test_cached_claim_returns_without_llm_call(tmp_path):
    """Previously verified claim is returned from cache without calling LLM."""
    from shared.fact_checker import cache_fact, verify_claims
    import shared.fact_checker as fc

    original_db = fc.CACHE_DB_PATH
    fc.CACHE_DB_PATH = str(tmp_path / "verified_facts.db")
    fc._init_cache_db()

    # Pre-cache a fact
    cache_fact("GPT-4 released in March 2023", "VERIFIED", "OpenAI blog confirms", 0.99)

    claims = [{"claim": "GPT-4 released in March 2023", "type": "date", "source_needed": True}]

    with patch("shared.fact_checker._call_llm") as mock_llm:
        results = verify_claims(claims, "source text")
        # LLM should NOT be called for cached claims
        mock_llm.assert_not_called()

    assert results[0]["verdict"] == "VERIFIED"
    fc.CACHE_DB_PATH = original_db


def test_mixed_cached_and_uncached_claims(tmp_path):
    """Batch with some cached and some new claims: cached skip LLM, new ones hit LLM."""
    from shared.fact_checker import cache_fact, verify_claims
    import shared.fact_checker as fc

    original_db = fc.CACHE_DB_PATH
    fc.CACHE_DB_PATH = str(tmp_path / "verified_facts.db")
    fc._init_cache_db()

    # Pre-cache one fact
    cache_fact("Python is interpreted", "VERIFIED", "Common knowledge", 0.99)

    claims = [
        {"claim": "Python is interpreted", "type": "technical", "source_needed": True},
        {"claim": "Rust is memory safe", "type": "technical", "source_needed": True},
    ]

    mock_verifications = {"verifications": [{
        "claim": "Rust is memory safe",
        "verdict": "VERIFIED",
        "evidence": "Rust guarantees memory safety",
        "confidence": 0.95,
        "severity": "low",
        "fix_suggestion": None
    }]}

    with patch("shared.fact_checker._call_llm") as mock_llm:
        mock_llm.return_value = json.dumps(mock_verifications)
        results = verify_claims(claims, "source text")

    assert len(results) == 2
    # Both should be verified
    verdicts = {r["claim"]: r["verdict"] for r in results}
    assert verdicts["Python is interpreted"] == "VERIFIED"
    assert verdicts["Rust is memory safe"] == "VERIFIED"

    fc.CACHE_DB_PATH = original_db
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fact_checker.py -v -k "cached_claim or mixed_cached"`
Expected: FAIL — cache lookup may not be wired into verify_claims, or DB path variable name differs

- [ ] **Step 3: Adjust tests to match actual function/variable names**

Read `shared/fact_checker.py` and fix the test to use the actual cache function names, DB path variable, and verify_claims flow.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fact_checker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_fact_checker.py
git commit -m "test(fact-checker): add cache hit and mixed batch verification tests"
```

---

### Task 7: Wire Fact-Checking into arXiv Paper Summaries

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for fact-checked paper summary**

```python
def test_paper_summary_includes_fact_check():
    """Paper summaries that contain verifiable claims get fact-checked."""
    import jobpulse.arxiv_agent as agent

    paper = {
        "title": "FastTransformer: 3x Faster Than BERT",
        "abstract": "We propose FastTransformer which achieves 3x speedup over BERT "
                     "on GLUE benchmark while maintaining 95% accuracy.",
        "arxiv_id": "2026.99999",
    }

    mock_summary = (
        "WHAT: FastTransformer achieves 3x speedup over BERT on GLUE.\n"
        "WHY: Significant efficiency improvement for production NLP.\n"
        "HOW: Uses sparse attention with dynamic routing.\n"
        "Practical takeaway: Deploy for latency-sensitive NLP tasks."
    )

    mock_claims = [
        {"claim": "3x speedup over BERT", "type": "comparison", "source_needed": True},
        {"claim": "95% accuracy on GLUE", "type": "benchmark", "source_needed": True},
    ]

    mock_verifications = [
        {"claim": "3x speedup over BERT", "verdict": "VERIFIED", "confidence": 0.9,
         "evidence": "Abstract states 3x", "severity": "low", "fix_suggestion": None},
        {"claim": "95% accuracy on GLUE", "verdict": "EXAGGERATED", "confidence": 0.8,
         "evidence": "Abstract says 95%, but GLUE has multiple subtasks",
         "severity": "medium", "fix_suggestion": "Specify which GLUE subtask"},
    ]

    with patch("jobpulse.arxiv_agent.summarize_paper", return_value=mock_summary), \
         patch("shared.fact_checker.extract_claims", return_value=mock_claims), \
         patch("shared.fact_checker.verify_claims", return_value=mock_verifications):

        result = agent.summarize_and_verify_paper(paper)

    assert "summary" in result
    assert "fact_check" in result
    assert result["fact_check"]["total_claims"] == 2
    assert result["fact_check"]["verified_count"] == 1
    assert result["fact_check"]["accuracy_score"] >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arxiv_agent.py -v -k "fact_check"`
Expected: FAIL — `summarize_and_verify_paper` doesn't exist yet

- [ ] **Step 3: Implement `summarize_and_verify_paper`**

In `jobpulse/arxiv_agent.py`, add:

```python
from shared.fact_checker import extract_claims, verify_claims, compute_accuracy_score


def summarize_and_verify_paper(paper: dict) -> dict:
    """Summarize a paper and fact-check verifiable claims in the summary.

    Returns dict with 'summary' (str) and 'fact_check' (dict with
    total_claims, verified_count, accuracy_score, issues).
    """
    summary = summarize_paper(paper)

    # Extract and verify claims from the summary against the abstract
    source_text = f"Title: {paper['title']}\nAbstract: {paper['abstract']}"
    claims = extract_claims(summary, paper["title"])

    if not claims:
        return {
            "summary": summary,
            "fact_check": {
                "total_claims": 0,
                "verified_count": 0,
                "accuracy_score": 10.0,
                "issues": [],
            },
        }

    verifications = verify_claims(claims, source_text)
    score = compute_accuracy_score(verifications)

    issues = [
        {"claim": v["claim"], "verdict": v["verdict"], "fix": v.get("fix_suggestion")}
        for v in verifications
        if v.get("verdict") in ("INACCURATE", "EXAGGERATED")
    ]

    return {
        "summary": summary,
        "fact_check": {
            "total_claims": len(verifications),
            "verified_count": sum(1 for v in verifications if v["verdict"] == "VERIFIED"),
            "accuracy_score": score,
            "issues": issues,
        },
    }
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "feat(arxiv): fact-check paper summaries against abstracts"
```

---

### Task 8: Wire Fact-Check Results into Telegram Digest

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for digest format with fact-check badges**

```python
def test_format_digest_shows_fact_check_badges():
    """Digest format includes verification badge per paper."""
    import jobpulse.arxiv_agent as agent

    papers = [
        {
            "title": "Paper A", "arxiv_id": "2026.1", "arxiv_url": "http://arxiv.org/abs/2026.1",
            "impact_score": 9.0, "impact_reason": "Novel",
            "summary": "Does X. Matters because Y.", "key_technique": "method A",
            "category_tag": "LLM", "practical_takeaway": "Use for Z.",
            "fact_check": {"total_claims": 3, "verified_count": 3, "accuracy_score": 10.0, "issues": []},
        },
        {
            "title": "Paper B", "arxiv_id": "2026.2", "arxiv_url": "http://arxiv.org/abs/2026.2",
            "impact_score": 8.0, "impact_reason": "Practical",
            "summary": "Does A. Matters because B.", "key_technique": "method B",
            "category_tag": "Efficiency", "practical_takeaway": "Deploy for C.",
            "fact_check": {"total_claims": 2, "verified_count": 1, "accuracy_score": 5.0,
                          "issues": [{"claim": "2x faster", "verdict": "EXAGGERATED", "fix": "Clarify benchmark"}]},
        },
    ]

    output = agent.format_digest(papers)

    # Paper A: all claims verified
    assert "3/3" in output or "verified" in output.lower()
    # Paper B: has issues
    assert "1/2" in output or "EXAGGERATED" in output or "issue" in output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arxiv_agent.py -v -k "fact_check_badges"`
Expected: FAIL — `format_digest` doesn't handle `fact_check` field yet

- [ ] **Step 3: Update `format_digest` to show fact-check results**

In `jobpulse/arxiv_agent.py`, modify the digest formatting function (likely `format_digest` or the equivalent) to append a fact-check line per paper:

```python
# After existing summary lines, add:
fc = paper.get("fact_check")
if fc and fc["total_claims"] > 0:
    verified = fc["verified_count"]
    total = fc["total_claims"]
    if verified == total:
        lines.append(f"   Fact-check: {verified}/{total} claims verified")
    else:
        issues_str = ", ".join(
            f"{i['verdict'].lower()}: \"{i['claim']}\""
            for i in fc.get("issues", [])[:2]  # Show max 2 issues
        )
        lines.append(f"   Fact-check: {verified}/{total} verified — {issues_str}")
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "feat(arxiv): show fact-check badges in Telegram digest"
```

---

### Task 9: Update `build_digest` to Use Fact-Checking Pipeline

**Files:**
- Modify: `jobpulse/arxiv_agent.py`

- [ ] **Step 1: Write integration test for full digest pipeline**

```python
def test_build_digest_runs_fact_check_on_each_paper(arxiv_db):
    """build_digest calls summarize_and_verify_paper for each ranked paper."""
    import jobpulse.arxiv_agent as agent

    mock_papers = [
        {"title": f"Paper {i}", "abstract": f"Abstract {i}", "arxiv_id": f"2026.{i}",
         "authors": ["Author"], "categories": ["cs.AI"], "pdf_url": "",
         "arxiv_url": f"http://arxiv.org/abs/2026.{i}", "published_at": "2026-03-28T00:00:00Z"}
        for i in range(3)
    ]

    mock_ranked = [
        {"rank": i+1, "paper_num": i, "score": 9-i, "overall": 9-i,
         "reason": "Good", "key_technique": "method", "category_tag": "LLM"}
        for i in range(3)
    ]

    with patch("jobpulse.arxiv_agent.fetch_papers", return_value=mock_papers), \
         patch("jobpulse.arxiv_agent.llm_rank_broad", return_value=mock_ranked), \
         patch("jobpulse.arxiv_agent.summarize_and_verify_paper") as mock_verify:

        mock_verify.return_value = {
            "summary": "Test summary.",
            "fact_check": {"total_claims": 1, "verified_count": 1, "accuracy_score": 10.0, "issues": []},
        }

        result = agent.build_digest(top_n=3)

    # summarize_and_verify_paper should be called once per ranked paper
    assert mock_verify.call_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arxiv_agent.py -v -k "build_digest_runs_fact"`
Expected: FAIL — build_digest calls `summarize_paper` directly, not `summarize_and_verify_paper`

- [ ] **Step 3: Update `build_digest` to use `summarize_and_verify_paper`**

In `jobpulse/arxiv_agent.py`, find where `build_digest` calls `summarize_paper` for each paper and replace with `summarize_and_verify_paper`. Store the fact_check result alongside each paper's data:

```python
# Replace:
#   summary = summarize_paper(paper)
# With:
result = summarize_and_verify_paper(paper)
paper["summary"] = result["summary"]
paper["fact_check"] = result["fact_check"]
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/test_arxiv_agent.py tests/test_fact_checker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "feat(arxiv): wire fact-checking into build_digest pipeline"
```

---

### Task 10: Store Fact-Check Results in papers.db

**Files:**
- Modify: `jobpulse/arxiv_agent.py`
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write test for fact-check columns in DB**

```python
def test_paper_fact_check_stored_in_db(arxiv_db):
    """Fact-check results are persisted in the papers table."""
    import jobpulse.arxiv_agent as agent

    agent.store_paper({
        "arxiv_id": "2026.test1", "title": "Test Paper",
        "authors": json.dumps(["Author"]), "abstract": "Abstract",
        "categories": json.dumps(["cs.AI"]), "pdf_url": "",
        "arxiv_url": "http://arxiv.org/abs/2026.test1",
        "published_at": "2026-03-28", "impact_score": 9.0,
        "impact_reason": "Good", "summary": "Summary",
        "key_technique": "method", "practical_takeaway": "Use it",
        "fact_check_score": 10.0, "fact_check_claims": 3,
        "fact_check_verified": 3, "fact_check_issues": "[]",
    })

    conn = sqlite3.connect(str(arxiv_db))
    row = conn.execute("SELECT fact_check_score, fact_check_claims FROM papers WHERE arxiv_id = '2026.test1'").fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 10.0
    assert row[1] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arxiv_agent.py -v -k "fact_check_stored"`
Expected: FAIL — columns don't exist in schema

- [ ] **Step 3: Add fact-check columns to papers table**

In `jobpulse/arxiv_agent.py`, update `_init_db()` to add new columns:

```python
# After existing CREATE TABLE, add migration:
for col, col_type, default in [
    ("fact_check_score", "REAL", "0"),
    ("fact_check_claims", "INTEGER", "0"),
    ("fact_check_verified", "INTEGER", "0"),
    ("fact_check_issues", "TEXT", "''"),
]:
    try:
        cursor.execute(f"ALTER TABLE papers ADD COLUMN {col} {col_type} DEFAULT {default}")
    except sqlite3.OperationalError:
        pass  # Column already exists
```

Update `store_paper` to include the new fields.

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_arxiv_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/arxiv_agent.py tests/test_arxiv_agent.py
git commit -m "feat(arxiv): persist fact-check scores in papers.db"
```

---

### Task 11: Final Integration Test — Full Pipeline

**Files:**
- Modify: `tests/test_arxiv_agent.py`

- [ ] **Step 1: Write end-to-end integration test**

```python
def test_full_arxiv_pipeline_fetch_rank_verify_format(arxiv_db):
    """Full pipeline: fetch → rank → summarize+verify → format produces valid output."""
    import jobpulse.arxiv_agent as agent

    mock_papers = [
        {"title": f"Paper {i}", "abstract": f"Abstract about novel method {i}",
         "arxiv_id": f"2026.{i}", "authors": ["Author"],
         "categories": ["cs.AI"], "pdf_url": "",
         "arxiv_url": f"http://arxiv.org/abs/2026.{i}",
         "published_at": "2026-03-28T00:00:00Z"}
        for i in range(5)
    ]

    mock_ranked = [
        {"rank": 1, "paper_num": 0, "score": 9.0, "overall": 9.0,
         "scores": {"novelty": 9, "significance": 9, "practical": 9, "breadth": 8},
         "reason": "Breakthrough", "key_technique": "new method",
         "category_tag": "LLM"}
    ]

    mock_verify_result = {
        "summary": "WHAT: Novel method.\nWHY: Matters.\nHOW: Technique.\nPractical takeaway: Use it.",
        "fact_check": {"total_claims": 2, "verified_count": 2, "accuracy_score": 10.0, "issues": []},
    }

    with patch("jobpulse.arxiv_agent.fetch_papers", return_value=mock_papers), \
         patch("jobpulse.arxiv_agent.llm_rank_broad", return_value=mock_ranked), \
         patch("jobpulse.arxiv_agent.summarize_and_verify_paper", return_value=mock_verify_result), \
         patch("jobpulse.arxiv_agent.create_notion_pages"):  # Don't hit Notion

        digest = agent.build_digest(top_n=1)

    assert isinstance(digest, dict) or isinstance(digest, str)
    # The digest should contain the paper title or summary
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_arxiv_agent.py -v -k "full_arxiv_pipeline"`
Expected: PASS if all previous tasks completed correctly

- [ ] **Step 3: Run full test suite for both modules**

Run: `pytest tests/test_arxiv_agent.py tests/test_fact_checker.py -v --tb=short`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_arxiv_agent.py
git commit -m "test(arxiv): add full pipeline integration test"
```

---

## Summary

| Task | What | Tests Added |
|------|------|------------|
| 1 | arXiv test foundation (fetch/parse) | 2 |
| 2 | LLM ranking tests | 3 |
| 3 | Fix JSON parsing brittleness | 4 (parametrized) |
| 4 | Multi-criteria scoring prompt | 1 |
| 5 | Fact-checker fixes (confidence, verdict, skip) | 3 |
| 6 | Fact-checker cache tests | 2 |
| 7 | `summarize_and_verify_paper` | 1 |
| 8 | Fact-check badges in digest | 1 |
| 9 | Wire into `build_digest` | 1 |
| 10 | Persist scores in DB | 1 |
| 11 | Full integration test | 1 |
| **Total** | | **~20 new tests** |
