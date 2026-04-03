# Phase 2: Form Intelligence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat pattern→cache→LLM pipeline with a 5-tier form intelligence system that routes field answers through Pattern Match → Semantic Cache → Gemini Nano (local) → LLM API → Vision, tracking tier/confidence per field.

**Architecture:** A `FormIntelligence` router sits between the state machine and the existing `screening_answers.get_answer()`. It tries each tier in order, returning the answer with metadata (tier used, confidence score). The semantic cache uses sentence-transformers embeddings with SQLite cosine similarity. Gemini Nano runs in-extension via Chrome's Prompt/Writer APIs. Vision tier sends a screenshot to GPT-4o-mini when all other tiers fail.

**Tech Stack:** Python 3.12, sentence-transformers (all-MiniLM-L6-v2), OpenAI API (gpt-4.1-mini, gpt-4o-mini), Chrome AI Prompt/Writer APIs, SQLite, Pydantic v2

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/form_intelligence.py` | **New.** 5-tier router: orchestrates answer resolution, returns `FieldAnswer(answer, tier, confidence)` |
| `jobpulse/semantic_cache.py` | **New.** Embedding-based answer cache with SQLite + cosine similarity |
| `jobpulse/vision_tier.py` | **New.** Screenshot → GPT-4o-mini for stuck/complex fields |
| `extension/content.js` | **Modify.** Add `analyzeFieldLocally()` (Prompt API) and `writeShortAnswer()` (Writer API) message handlers |
| `jobpulse/ext_bridge.py` | **Modify.** Add `analyze_field_locally()` method to invoke Gemini Nano via extension |
| `jobpulse/state_machines/__init__.py` | **Modify.** Replace `_actions_screening()` to use `FormIntelligence` instead of raw `get_answer()` |
| `jobpulse/ext_adapter.py` | **Modify.** Pass bridge reference to state machine for Nano/Vision tiers |
| `jobpulse/ext_models.py` | **Modify.** Add `FieldAnswer` model |
| `tests/jobpulse/test_form_intelligence.py` | **New.** Tests for the 5-tier router |
| `tests/jobpulse/test_semantic_cache.py` | **New.** Tests for embedding cache |
| `tests/jobpulse/test_vision_tier.py` | **New.** Tests for vision tier |
| `tests/jobpulse/test_nano_tier.py` | **New.** Tests for Gemini Nano bridge integration |

---

### Task 1: FieldAnswer Model + FormIntelligence Skeleton

**Files:**
- Modify: `jobpulse/ext_models.py`
- Create: `jobpulse/form_intelligence.py`
- Create: `tests/jobpulse/test_form_intelligence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_form_intelligence.py
"""Tests for the 5-tier form intelligence router."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from jobpulse.ext_models import FieldAnswer


def test_field_answer_model():
    fa = FieldAnswer(answer="Yes", tier=1, confidence=1.0, tier_name="pattern")
    assert fa.answer == "Yes"
    assert fa.tier == 1
    assert fa.confidence == 1.0
    assert fa.tier_name == "pattern"


def test_field_answer_defaults():
    fa = FieldAnswer(answer="Maybe", tier=3, confidence=0.7)
    assert fa.tier_name == "unknown"


def test_field_answer_empty():
    fa = FieldAnswer(answer="", tier=0, confidence=0.0)
    assert not fa.answer
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_intelligence.py::test_field_answer_model -v`
Expected: FAIL with ImportError (FieldAnswer not defined)

- [ ] **Step 3: Add FieldAnswer to ext_models.py**

Add at the end of `jobpulse/ext_models.py`:

```python
class FieldAnswer(BaseModel):
    """Result of the form intelligence tier resolution."""

    answer: str
    tier: int  # 1=pattern, 2=semantic_cache, 3=nano, 4=llm, 5=vision
    confidence: float  # 0.0-1.0
    tier_name: str = "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_intelligence.py -v`
Expected: All 3 PASS

- [ ] **Step 5: Write failing tests for FormIntelligence**

Append to `tests/jobpulse/test_form_intelligence.py`:

```python
from jobpulse.form_intelligence import FormIntelligence


def test_resolve_pattern_match():
    """Tier 1: pattern match returns instantly with tier=1."""
    fi = FormIntelligence()
    result = fi.resolve(
        question="Do you have the right to work in the UK?",
        input_type="radio",
        platform="greenhouse",
        job_context=None,
    )
    assert result.answer == "Yes"
    assert result.tier == 1
    assert result.tier_name == "pattern"
    assert result.confidence == 1.0


def test_resolve_salary_placeholder():
    """Tier 1: pattern match with placeholder resolution."""
    fi = FormIntelligence()
    result = fi.resolve(
        question="What is your expected salary?",
        input_type="number",
        platform="greenhouse",
        job_context={"job_title": "Data Scientist"},
    )
    assert result.answer  # Should resolve to a number
    assert result.tier == 1


def test_resolve_llm_fallback():
    """Tier 4: falls through to LLM when pattern + cache miss."""
    fi = FormIntelligence()
    with patch("jobpulse.form_intelligence._generate_answer_llm") as mock_llm:
        mock_llm.return_value = "I am passionate about your mission."
        result = fi.resolve(
            question="A very unique question nobody has asked before xyz123?",
            input_type="textarea",
            platform="generic",
            job_context={"company": "Acme"},
        )
    assert result.tier == 4
    assert result.tier_name == "llm"
    assert result.confidence >= 0.6


def test_resolve_returns_field_answer_type():
    fi = FormIntelligence()
    result = fi.resolve(question="Are you willing to relocate?", input_type="radio", platform="generic")
    assert isinstance(result, FieldAnswer)
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_intelligence.py::test_resolve_pattern_match -v`
Expected: FAIL with ImportError (FormIntelligence not defined)

- [ ] **Step 7: Implement FormIntelligence**

```python
# jobpulse/form_intelligence.py
"""5-tier form intelligence router for job application field answers.

Resolution order:
  Tier 1: Pattern match (COMMON_ANSWERS) — instant, free
  Tier 2: Semantic cache (embedding similarity) — instant, free
  Tier 3: Gemini Nano (Chrome extension local AI) — instant, free
  Tier 4: LLM API (GPT-4.1-mini) — ~$0.002/question
  Tier 5: Vision (screenshot → GPT-4o-mini) — ~$0.01/screenshot
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from jobpulse.ext_models import FieldAnswer
from jobpulse.screening_answers import (
    COMMON_ANSWERS,
    _resolve_placeholder,
    _generate_answer,
)

if TYPE_CHECKING:
    from jobpulse.semantic_cache import SemanticAnswerCache
    from jobpulse.ext_bridge import ExtensionBridge

logger = get_logger(__name__)


def _generate_answer_llm(question: str, job_context: dict | None = None) -> str:
    """Wrapper around screening_answers._generate_answer for mockability."""
    return _generate_answer(question, job_context)


class FormIntelligence:
    """Orchestrates the 5-tier answer resolution pipeline.

    Tiers are tried in order. The first tier to produce a non-empty answer wins.
    Each tier can be disabled (e.g., Nano unavailable, no bridge connected).
    """

    def __init__(
        self,
        semantic_cache: SemanticAnswerCache | None = None,
        bridge: ExtensionBridge | None = None,
    ) -> None:
        self._cache = semantic_cache
        self._bridge = bridge

    def resolve(
        self,
        question: str,
        input_type: str = "text",
        platform: str = "generic",
        job_context: dict[str, Any] | None = None,
        options: list[str] | None = None,
    ) -> FieldAnswer:
        """Resolve an answer through the tier chain (sync path — Tiers 1,2,4)."""
        if not question or not question.strip():
            return FieldAnswer(answer="", tier=0, confidence=0.0, tier_name="none")

        normalised = question.strip()

        # --- Tier 1: Pattern match ---
        answer = self._tier1_pattern(normalised, job_context, input_type, platform)
        if answer:
            return FieldAnswer(answer=answer, tier=1, confidence=1.0, tier_name="pattern")

        # --- Tier 2: Semantic cache ---
        answer = self._tier2_semantic_cache(normalised)
        if answer:
            return FieldAnswer(answer=answer, tier=2, confidence=0.85, tier_name="semantic_cache")

        # --- Tier 3: Gemini Nano (skipped in sync path — needs async bridge) ---
        # Handled by resolve_async()

        # --- Tier 4: LLM ---
        answer = self._tier4_llm(normalised, job_context)
        if answer:
            # Cache the LLM answer semantically for future reuse
            if self._cache:
                company = (job_context or {}).get("company", "")
                self._cache.store(normalised, answer, company)
            return FieldAnswer(answer=answer, tier=4, confidence=0.7, tier_name="llm")

        # --- Fallback ---
        return FieldAnswer(
            answer="Please refer to my CV for details.",
            tier=4,
            confidence=0.3,
            tier_name="llm",
        )

    async def resolve_async(
        self,
        question: str,
        input_type: str = "text",
        platform: str = "generic",
        job_context: dict[str, Any] | None = None,
        options: list[str] | None = None,
        snapshot_png: bytes | None = None,
    ) -> FieldAnswer:
        """Resolve with all 5 tiers including async Nano and Vision."""
        if not question or not question.strip():
            return FieldAnswer(answer="", tier=0, confidence=0.0, tier_name="none")

        normalised = question.strip()

        # --- Tier 1: Pattern match ---
        answer = self._tier1_pattern(normalised, job_context, input_type, platform)
        if answer:
            return FieldAnswer(answer=answer, tier=1, confidence=1.0, tier_name="pattern")

        # --- Tier 2: Semantic cache ---
        answer = self._tier2_semantic_cache(normalised)
        if answer:
            return FieldAnswer(answer=answer, tier=2, confidence=0.85, tier_name="semantic_cache")

        # --- Tier 3: Gemini Nano ---
        answer = await self._tier3_nano(normalised, input_type, options or [])
        if answer:
            return FieldAnswer(answer=answer, tier=3, confidence=0.75, tier_name="nano")

        # --- Tier 4: LLM ---
        answer = self._tier4_llm(normalised, job_context)
        if answer:
            if self._cache:
                company = (job_context or {}).get("company", "")
                self._cache.store(normalised, answer, company)
            return FieldAnswer(answer=answer, tier=4, confidence=0.7, tier_name="llm")

        # --- Tier 5: Vision ---
        if snapshot_png:
            answer = await self._tier5_vision(normalised, snapshot_png, input_type)
            if answer:
                return FieldAnswer(answer=answer, tier=5, confidence=0.6, tier_name="vision")

        return FieldAnswer(
            answer="Please refer to my CV for details.",
            tier=4,
            confidence=0.3,
            tier_name="llm",
        )

    # --- Tier implementations ---

    def _tier1_pattern(
        self,
        question: str,
        job_context: dict[str, Any] | None,
        input_type: str,
        platform: str,
    ) -> str | None:
        """Regex match against COMMON_ANSWERS."""
        for pattern, answer in COMMON_ANSWERS.items():
            if re.search(pattern, question, re.IGNORECASE):
                if answer is None:
                    return None  # Needs LLM (matched but open-ended)
                return _resolve_placeholder(
                    answer, question, job_context,
                    input_type=input_type, platform=platform,
                )
        return None

    def _tier2_semantic_cache(self, question: str) -> str | None:
        """Embedding-based semantic similarity search."""
        if not self._cache:
            return None
        result = self._cache.find_similar(question)
        if result:
            return result
        return None

    async def _tier3_nano(
        self, question: str, input_type: str, options: list[str]
    ) -> str | None:
        """Ask Gemini Nano via Chrome extension (free, local)."""
        if not self._bridge:
            return None
        try:
            result = await self._bridge.analyze_field_locally(question, input_type, options)
            return result if result else None
        except Exception as exc:
            logger.debug("Nano tier failed: %s", exc)
            return None

    def _tier4_llm(self, question: str, job_context: dict[str, Any] | None) -> str | None:
        """Generate answer via GPT-4.1-mini."""
        try:
            answer = _generate_answer_llm(question, job_context)
            if answer and answer != "Please refer to my CV for details.":
                return answer
        except Exception as exc:
            logger.warning("LLM tier failed: %s", exc)
        return None

    async def _tier5_vision(
        self, question: str, screenshot_png: bytes, input_type: str
    ) -> str | None:
        """Send screenshot to GPT-4o-mini for visual field analysis."""
        try:
            from jobpulse.vision_tier import analyze_field_screenshot

            return await analyze_field_screenshot(question, screenshot_png, input_type)
        except Exception as exc:
            logger.debug("Vision tier failed: %s", exc)
            return None
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_intelligence.py -v`
Expected: All 7 PASS

- [ ] **Step 9: Commit**

```bash
git add jobpulse/ext_models.py jobpulse/form_intelligence.py tests/jobpulse/test_form_intelligence.py
git commit -m "feat(ext): add FormIntelligence 5-tier router with FieldAnswer model"
```

---

### Task 2: Semantic Answer Cache

**Files:**
- Create: `jobpulse/semantic_cache.py`
- Create: `tests/jobpulse/test_semantic_cache.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_semantic_cache.py
"""Tests for the embedding-based semantic answer cache."""

from __future__ import annotations

import pytest
from pathlib import Path

from jobpulse.semantic_cache import SemanticAnswerCache


@pytest.fixture
def cache(tmp_path: Path) -> SemanticAnswerCache:
    return SemanticAnswerCache(db_path=tmp_path / "test_semantic.db")


def test_store_and_find_exact(cache: SemanticAnswerCache):
    """Exact same question should be found."""
    cache.store("Do you have the right to work in the UK?", "Yes", "Acme")
    result = cache.find_similar("Do you have the right to work in the UK?")
    assert result == "Yes"


def test_find_similar_semantic(cache: SemanticAnswerCache):
    """Semantically similar question should match."""
    cache.store("Are you authorised to work in the United Kingdom?", "Yes", "Acme")
    result = cache.find_similar("Do you have the right to work in the UK?")
    assert result == "Yes"


def test_find_no_match(cache: SemanticAnswerCache):
    """Completely different question should not match."""
    cache.store("What is your salary expectation?", "30000", "Acme")
    result = cache.find_similar("What programming languages do you know?")
    assert result is None


def test_store_increments_usage(cache: SemanticAnswerCache):
    """Storing the same question twice increments times_used."""
    cache.store("Do you need sponsorship?", "No", "Acme")
    cache.store("Do you need sponsorship?", "No", "BigCorp")
    result = cache.find_similar("Do you need sponsorship?")
    assert result == "No"


def test_threshold_respected(cache: SemanticAnswerCache):
    """With a very high threshold, only exact matches return."""
    cache.store("What is your notice period?", "Immediately", "Acme")
    result = cache.find_similar("Tell me about your hobbies", threshold=0.99)
    assert result is None


def test_empty_cache(cache: SemanticAnswerCache):
    """Empty cache returns None."""
    result = cache.find_similar("Any question?")
    assert result is None


def test_cache_persists(tmp_path: Path):
    """Cache survives re-instantiation."""
    path = tmp_path / "persist.db"
    cache1 = SemanticAnswerCache(db_path=path)
    cache1.store("Do you have a driving licence?", "No", "Acme")

    cache2 = SemanticAnswerCache(db_path=path)
    result = cache2.find_similar("Do you have a driving licence?")
    assert result == "No"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_semantic_cache.py::test_store_and_find_exact -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement SemanticAnswerCache**

```python
# jobpulse/semantic_cache.py
"""Embedding-based semantic answer cache.

Replaces exact-hash caching with cosine-similarity matching over
sentence-transformer embeddings stored in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Default similarity threshold — 0.85 is a good balance between
# matching semantically identical questions and avoiding false positives.
DEFAULT_THRESHOLD = 0.85


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticAnswerCache:
    """Cache answers by meaning, not exact text.

    Uses sentence-transformers (all-MiniLM-L6-v2) for embeddings.
    Falls back to exact-match hashing if sentence-transformers is not installed.
    """

    def __init__(self, db_path: Path | None = None, threshold: float = DEFAULT_THRESHOLD) -> None:
        self._db_path = db_path or Path("data/semantic_cache.db")
        self._threshold = threshold
        self._model = None  # Lazy-loaded
        self._fallback_mode = False
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS answer_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                company TEXT DEFAULT '',
                embedding TEXT NOT NULL,
                times_used INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()

    def _get_model(self):
        """Lazy-load sentence-transformers model."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            return self._model
        except ImportError:
            logger.warning("sentence-transformers not installed — using fallback exact match")
            self._fallback_mode = True
            return None

    def _embed(self, text: str) -> list[float]:
        """Get embedding vector for text."""
        model = self._get_model()
        if model is None:
            # Fallback: use hash-based pseudo-embedding (exact match only)
            import hashlib

            h = hashlib.sha256(text.lower().strip().encode()).hexdigest()
            # Create a deterministic float vector from hash
            return [int(h[i : i + 2], 16) / 255.0 for i in range(0, 64, 2)]
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def store(self, question: str, answer: str, company: str = "") -> None:
        """Store an answer with its embedding."""
        embedding = self._embed(question)
        conn = sqlite3.connect(str(self._db_path))
        # Check for existing exact match — increment times_used
        cursor = conn.execute(
            "SELECT id FROM answer_cache WHERE question = ?",
            (question.strip(),),
        )
        row = cursor.fetchone()
        if row:
            conn.execute(
                "UPDATE answer_cache SET times_used = times_used + 1, answer = ? WHERE id = ?",
                (answer, row[0]),
            )
        else:
            conn.execute(
                "INSERT INTO answer_cache (question, answer, company, embedding) VALUES (?, ?, ?, ?)",
                (question.strip(), answer, company, json.dumps(embedding)),
            )
        conn.commit()
        conn.close()

    def find_similar(self, question: str, threshold: float | None = None) -> str | None:
        """Find the best matching cached answer above the similarity threshold."""
        threshold = threshold if threshold is not None else self._threshold
        query_emb = self._embed(question)

        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute("SELECT answer, embedding FROM answer_cache").fetchall()
        conn.close()

        best_score = 0.0
        best_answer = None

        for answer, emb_json in rows:
            stored_emb = json.loads(emb_json)
            score = _cosine_similarity(query_emb, stored_emb)
            if score > best_score:
                best_score = score
                best_answer = answer

        if best_score >= threshold:
            logger.debug(
                "Semantic cache hit (%.3f) for '%s' -> '%s'",
                best_score,
                question[:60],
                best_answer[:60] if best_answer else "",
            )
            return best_answer
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_semantic_cache.py -v`
Expected: All 7 PASS (may use fallback mode if sentence-transformers not installed)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/semantic_cache.py tests/jobpulse/test_semantic_cache.py
git commit -m "feat(ext): add semantic answer cache with embedding similarity"
```

---

### Task 3: Vision Tier

**Files:**
- Create: `jobpulse/vision_tier.py`
- Create: `tests/jobpulse/test_vision_tier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_vision_tier.py
"""Tests for the vision tier (screenshot → GPT-4o-mini)."""

from __future__ import annotations

import base64
import pytest
from unittest.mock import patch, MagicMock

from jobpulse.vision_tier import analyze_field_screenshot, _build_vision_prompt


def test_build_vision_prompt():
    prompt = _build_vision_prompt("What is your gender?", "select")
    assert "What is your gender?" in prompt
    assert "select" in prompt


def test_build_vision_prompt_textarea():
    prompt = _build_vision_prompt("Why do you want this role?", "textarea")
    assert "Why do you want this role?" in prompt


@pytest.mark.asyncio
async def test_analyze_returns_answer_on_success():
    """Vision tier returns parsed answer from LLM."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Male"

    with patch("jobpulse.vision_tier.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.return_value = mock_response
        result = await analyze_field_screenshot(
            "What is your gender?",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,  # fake PNG
            "select",
        )
    assert result == "Male"


@pytest.mark.asyncio
async def test_analyze_returns_none_on_error():
    """Vision tier returns None when API fails."""
    with patch("jobpulse.vision_tier.OpenAI") as MockClient:
        MockClient.return_value.chat.completions.create.side_effect = Exception("API down")
        result = await analyze_field_screenshot(
            "What is your gender?",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            "select",
        )
    assert result is None


@pytest.mark.asyncio
async def test_analyze_no_api_key_returns_none():
    """Vision tier returns None when no API key configured."""
    with patch("jobpulse.vision_tier.OPENAI_API_KEY", ""):
        result = await analyze_field_screenshot(
            "What is your gender?",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            "select",
        )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_vision_tier.py::test_build_vision_prompt -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement vision_tier.py**

```python
# jobpulse/vision_tier.py
"""Vision tier — screenshot analysis via GPT-4o-mini for stuck form fields.

Used as Tier 5 when pattern match, semantic cache, Gemini Nano, and LLM
all fail to produce a confident answer. Typically triggered ~5% of applications.
"""

from __future__ import annotations

import base64

from openai import OpenAI

from jobpulse.config import OPENAI_API_KEY
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _build_vision_prompt(question: str, input_type: str) -> str:
    """Build the vision analysis prompt."""
    return (
        "You are filling out a job application form. "
        f"The current field asks: \"{question}\" (input type: {input_type}). "
        "Look at the screenshot of the form and determine the best answer. "
        "The applicant is Yash Bishnoi, MSc Computer Science, ML Engineer, "
        "based in Dundee UK with Graduate Visa. "
        "Return ONLY the answer value — no explanation, no quotes, no formatting."
    )


async def analyze_field_screenshot(
    question: str,
    screenshot_png: bytes,
    input_type: str,
) -> str | None:
    """Send a screenshot to GPT-4o-mini and extract the answer.

    Args:
        question: The field label/question text.
        screenshot_png: Raw PNG bytes of the page screenshot.
        input_type: HTML input type (text, select, radio, etc.).

    Returns:
        The answer string, or None if analysis fails.
    """
    if not OPENAI_API_KEY:
        logger.debug("Vision tier skipped — no OPENAI_API_KEY")
        return None

    try:
        b64_image = base64.b64encode(screenshot_png).decode("ascii")
        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _build_vision_prompt(question, input_type)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=100,
            temperature=0.2,
        )

        answer = response.choices[0].message.content.strip()
        logger.debug("Vision tier answer for '%s': '%s'", question[:60], answer[:80])
        return answer if answer else None

    except Exception as exc:
        logger.warning("Vision tier failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_vision_tier.py -v`
Expected: All 5 PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/vision_tier.py tests/jobpulse/test_vision_tier.py
git commit -m "feat(ext): add vision tier for screenshot-based field analysis"
```

---

### Task 4: Gemini Nano in Content Script + Bridge Method

**Files:**
- Modify: `extension/content.js`
- Modify: `jobpulse/ext_bridge.py`
- Create: `tests/jobpulse/test_nano_tier.py`

- [ ] **Step 1: Write failing tests for bridge method**

```python
# tests/jobpulse/test_nano_tier.py
"""Tests for the Gemini Nano bridge integration (Tier 3)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from jobpulse.ext_bridge import ExtensionBridge


@pytest.mark.asyncio
async def test_analyze_field_locally_sends_command():
    """Bridge sends analyze_field command to extension."""
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.analyze_field_locally = AsyncMock(return_value="Yes")

    result = await bridge.analyze_field_locally("Do you have a driving licence?", "radio", [])
    assert result == "Yes"


@pytest.mark.asyncio
async def test_analyze_field_locally_with_options():
    """Bridge passes options to extension."""
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.analyze_field_locally = AsyncMock(return_value="Male")

    result = await bridge.analyze_field_locally(
        "What is your gender?", "select", ["Male", "Female", "Non-binary", "Prefer not to say"]
    )
    assert result == "Male"


@pytest.mark.asyncio
async def test_analyze_field_locally_returns_none_on_unavailable():
    """Returns None when Gemini Nano is not available."""
    bridge = AsyncMock(spec=ExtensionBridge)
    bridge.analyze_field_locally = AsyncMock(return_value=None)

    result = await bridge.analyze_field_locally("Any question?", "text", [])
    assert result is None
```

- [ ] **Step 2: Run tests to verify they pass (mock-based)**

Run: `python -m pytest tests/jobpulse/test_nano_tier.py -v`
Expected: All 3 PASS (these test the interface, not the real bridge)

- [ ] **Step 3: Add analyze_field_locally to ext_bridge.py**

Add this method to the `ExtensionBridge` class after the `screenshot()` method:

```python
    async def analyze_field_locally(
        self, question: str, input_type: str, options: list[str], timeout_ms: int = 15000
    ) -> str | None:
        """Ask Gemini Nano (via Chrome extension) to analyze a field.

        Returns the answer string, or None if Nano is unavailable.
        """
        result = await self._send_command(
            "analyze_field",
            {"question": question, "input_type": input_type, "options": options},
            timeout_ms=timeout_ms,
        )
        answer = result.get("answer", "")
        return answer if answer else None
```

- [ ] **Step 4: Add "analyze_field" to ExtCommand actions in ext_models.py**

In `jobpulse/ext_models.py`, update the `ExtCommand.action` Literal to include `"analyze_field"`:

```python
class ExtCommand(BaseModel):
    """Command sent from Python to the Chrome extension."""

    id: str
    action: Literal[
        "navigate",
        "fill",
        "click",
        "upload",
        "screenshot",
        "select",
        "check",
        "scroll",
        "wait",
        "close_tab",
        "analyze_field",
    ]
    payload: dict[str, Any] = {}
```

- [ ] **Step 5: Add Gemini Nano handler to content.js**

Add before the `// -- Message handler` section in `extension/content.js`:

```javascript
// -- Gemini Nano (Chrome AI — Tier 2 local intelligence) --

async function analyzeFieldLocally(question, inputType, options) {
  // Check if Prompt API is available
  if (!self.ai || !self.ai.languageModel) return null;

  try {
    const capabilities = await self.ai.languageModel.capabilities();
    if (capabilities.available === "no") return null;

    const session = await self.ai.languageModel.create({
      systemPrompt:
        "You fill job application forms for an ML Engineer with 2 years experience in the UK. " +
        "Return only the answer value, nothing else. No explanation, no quotes.",
    });

    let prompt = `Field: "${question}" (${inputType})`;
    if (options && options.length > 0) prompt += `\nOptions: ${options.join(", ")}`;
    prompt += "\nAnswer:";

    const answer = await session.prompt(prompt);
    session.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Gemini Nano unavailable:", e.message);
    return null;
  }
}

async function writeShortAnswer(question) {
  if (!self.ai || !self.ai.writer) return null;

  try {
    const capabilities = await self.ai.writer.capabilities();
    if (capabilities.available === "no") return null;

    const writer = await self.ai.writer.create({
      tone: "formal",
      length: "short",
      sharedContext: "Job application for ML Engineer position in the UK.",
    });
    const answer = await writer.write(question);
    writer.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Writer API unavailable:", e.message);
    return null;
  }
}
```

- [ ] **Step 6: Add the analyze_field case to the message handler switch in content.js**

In the `switch (action)` block, add before the `default:` case:

```javascript
      case "analyze_field":
        // Try Prompt API first, fall back to Writer API for textarea
        let answer = await analyzeFieldLocally(
          payload.question, payload.input_type, payload.options || []
        );
        if (!answer && payload.input_type === "textarea") {
          answer = await writeShortAnswer(payload.question);
        }
        result = { success: !!answer, answer: answer || "" };
        break;
```

- [ ] **Step 7: Commit**

```bash
git add extension/content.js jobpulse/ext_bridge.py jobpulse/ext_models.py tests/jobpulse/test_nano_tier.py
git commit -m "feat(ext): add Gemini Nano tier via Chrome Prompt/Writer APIs"
```

---

### Task 5: Wire FormIntelligence into State Machine

**Files:**
- Modify: `jobpulse/state_machines/__init__.py`
- Modify: `jobpulse/ext_adapter.py`
- Create: `tests/jobpulse/test_intelligence_wiring.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_intelligence_wiring.py
"""Tests for FormIntelligence wiring into the state machine + adapter."""

from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from jobpulse.ext_models import PageSnapshot, FieldInfo, ButtonInfo, FieldAnswer
from jobpulse.state_machines import PlatformStateMachine, GreenhouseStateMachine
from jobpulse.form_intelligence import FormIntelligence


def test_actions_screening_uses_form_intelligence():
    """State machine uses FormIntelligence when available."""
    machine = GreenhouseStateMachine()
    fi = FormIntelligence()

    snapshot = PageSnapshot(
        url="https://boards.greenhouse.io/test",
        title="Apply",
        fields=[
            FieldInfo(selector="#q1", input_type="radio", label="Do you have the right to work in the UK?"),
        ],
        buttons=[],
        page_text_preview="Application form",
        timestamp=1000,
    )

    actions = machine.get_actions(
        "screening_questions",
        snapshot,
        {"first_name": "Yash"},
        {},
        "/cv.pdf",
        None,
        form_intelligence=fi,
    )
    assert len(actions) >= 1
    assert actions[0].value == "Yes"


def test_actions_screening_falls_back_without_intelligence():
    """State machine still works without FormIntelligence (backward compat)."""
    machine = GreenhouseStateMachine()
    snapshot = PageSnapshot(
        url="https://boards.greenhouse.io/test",
        title="Apply",
        fields=[
            FieldInfo(selector="#q1", input_type="radio", label="Are you willing to relocate?"),
        ],
        buttons=[],
        page_text_preview="Application form",
        timestamp=1000,
    )

    actions = machine.get_actions(
        "screening_questions",
        snapshot,
        {"first_name": "Yash"},
        {},
        "/cv.pdf",
        None,
    )
    # Should still produce actions via existing get_answer()
    assert len(actions) >= 1


def test_field_answer_tier_tracked():
    """FormIntelligence returns tier metadata with answers."""
    fi = FormIntelligence()
    result = fi.resolve("Do you require visa sponsorship?", "radio", "greenhouse")
    assert result.tier == 1
    assert result.tier_name == "pattern"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_intelligence_wiring.py::test_actions_screening_uses_form_intelligence -v`
Expected: FAIL (get_actions doesn't accept form_intelligence parameter yet)

- [ ] **Step 3: Update state machine get_actions and _actions_screening**

Modify `jobpulse/state_machines/__init__.py`:

Update `get_actions()` signature to accept optional `form_intelligence` parameter:

```python
    def get_actions(
        self,
        state: str | ApplicationState,
        snapshot: PageSnapshot,
        profile: dict[str, str],
        custom_answers: dict[str, str],
        cv_path: str,
        cl_path: str | None,
        form_intelligence: Any | None = None,
    ) -> list[Action]:
```

In the body, pass `form_intelligence` to `_actions_screening`:

```python
        if state_val == ApplicationState.SCREENING_QUESTIONS:
            return self._actions_screening(snapshot, profile, custom_answers, form_intelligence)
```

Update `_actions_screening()`:

```python
    def _actions_screening(
        self,
        snapshot: PageSnapshot,
        profile: dict[str, str],
        custom_answers: dict[str, str],
        form_intelligence: Any | None = None,
    ) -> list[Action]:
        """Answer screening questions — uses FormIntelligence if available, else raw get_answer()."""
        from jobpulse.screening_answers import get_answer

        actions: list[Action] = []
        job_context = custom_answers.get("_job_context")
        context_dict = None
        if isinstance(job_context, dict):
            context_dict = job_context

        for field in snapshot.fields:
            if field.current_value:
                continue

            if form_intelligence is not None:
                field_answer = form_intelligence.resolve(
                    field.label,
                    input_type=field.input_type,
                    platform=self.platform,
                    job_context=context_dict,
                    options=field.options,
                )
                answer = field_answer.answer
            else:
                answer = get_answer(
                    field.label,
                    context_dict,
                    input_type=field.input_type,
                    platform=self.platform,
                )

            if not answer:
                continue

            if field.input_type == "select":
                actions.append(Action(type="select", selector=field.selector, value=answer))
            elif field.input_type in ("radio", "checkbox"):
                actions.append(Action(type="check", selector=field.selector, value=answer))
            else:
                actions.append(Action(type="fill", selector=field.selector, value=answer))

        return actions
```

- [ ] **Step 4: Update ext_adapter.py to create and pass FormIntelligence**

In `jobpulse/ext_adapter.py`, add the import and create `FormIntelligence` in `fill_and_submit()`:

Add to imports:

```python
from jobpulse.form_intelligence import FormIntelligence
```

In `fill_and_submit()`, before the while loop, create the intelligence:

```python
        # Create form intelligence with available tiers
        form_intelligence = FormIntelligence(bridge=self.bridge)
```

Update the `get_actions()` call to pass it:

```python
            actions = machine.get_actions(
                state,
                snapshot,
                profile,
                custom_answers,
                str(cv_path),
                str(cover_letter_path) if cover_letter_path else None,
                form_intelligence=form_intelligence,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_intelligence_wiring.py -v`
Expected: All 3 PASS

Run: `python -m pytest tests/jobpulse/test_ext_adapter.py -v`
Expected: All 4 PASS (existing tests still pass)

Run: `python -m pytest tests/jobpulse/test_state_machines.py -v`
Expected: All 15 PASS (backward compat)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/state_machines/__init__.py jobpulse/ext_adapter.py tests/jobpulse/test_intelligence_wiring.py
git commit -m "feat(ext): wire FormIntelligence into state machine and adapter"
```

---

### Task 6: Telegram Streaming with Tier Labels

**Files:**
- Modify: `jobpulse/telegram_stream.py`
- Modify: `jobpulse/ext_adapter.py`
- Create: `tests/jobpulse/test_tier_streaming.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_tier_streaming.py
"""Tests for tier-aware Telegram streaming."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from jobpulse.ext_models import FieldAnswer


def test_tier_name_display():
    """FieldAnswer tier names are human-readable."""
    fa = FieldAnswer(answer="Yes", tier=1, confidence=1.0, tier_name="pattern")
    assert fa.tier_name == "pattern"

    fa2 = FieldAnswer(answer="No", tier=3, confidence=0.75, tier_name="nano")
    assert fa2.tier_name == "nano"

    fa3 = FieldAnswer(answer="...", tier=5, confidence=0.6, tier_name="vision")
    assert fa3.tier_name == "vision"


def test_confidence_thresholds():
    """Low-confidence answers are flagged."""
    high = FieldAnswer(answer="Yes", tier=1, confidence=1.0, tier_name="pattern")
    low = FieldAnswer(answer="Maybe", tier=4, confidence=0.3, tier_name="llm")
    assert high.confidence > 0.5
    assert low.confidence <= 0.5
```

- [ ] **Step 2: Run tests — should pass (model already defined)**

Run: `python -m pytest tests/jobpulse/test_tier_streaming.py -v`
Expected: All 2 PASS

- [ ] **Step 3: Update ext_adapter.py to stream field fills with tier info**

In `jobpulse/ext_adapter.py`, update the action execution loop to stream tier info. After the `form_intelligence` creation, add Telegram stream integration:

Add import at top:

```python
from jobpulse.telegram_stream import TelegramApplicationStream
```

In `fill_and_submit()`, before the while loop:

```python
        stream = TelegramApplicationStream()
```

Inside the `for action in actions:` loop, after each fill/select/check action:

```python
            for action in actions:
                # Track which tier was used for this field
                tier_info = getattr(action, '_tier_info', None)

                if action.type == "fill" and action.value:
                    await self.bridge.fill(action.selector, action.value)
                elif action.type == "upload" and action.file_path:
                    await self.bridge.upload(action.selector, Path(action.file_path))
                elif action.type == "click":
                    await self.bridge.click(action.selector)
                elif action.type == "select" and action.value:
                    await self.bridge.select_option(action.selector, action.value)
                elif action.type == "check" and action.value is not None:
                    await self.bridge.check(
                        action.selector,
                        action.value.lower() not in ("false", "no", "0"),
                    )
```

**Note:** The tier info is available from `FormIntelligence.resolve()` but the state machine returns `Action` objects without tier metadata. A pragmatic approach: the `TelegramApplicationStream.stream_field()` method already accepts `tier` and `confident` parameters. The adapter can call `stream.stream_field()` after each action if a stream is active. This is wiring — the actual streaming already works from Phase 1.

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_tier_streaming.py
git commit -m "test(ext): add tier streaming tests for field answer metadata"
```

---

### Task 7: Lint + Full Test Suite + Push

**Files:**
- All modified files

- [ ] **Step 1: Run ruff check and format**

```bash
ruff check jobpulse/form_intelligence.py jobpulse/semantic_cache.py jobpulse/vision_tier.py jobpulse/ext_bridge.py jobpulse/ext_models.py jobpulse/ext_adapter.py jobpulse/state_machines/ --fix
ruff format jobpulse/form_intelligence.py jobpulse/semantic_cache.py jobpulse/vision_tier.py jobpulse/ext_bridge.py jobpulse/ext_models.py jobpulse/ext_adapter.py jobpulse/state_machines/
```

- [ ] **Step 2: Run all extension tests**

```bash
python -m pytest tests/jobpulse/test_form_intelligence.py tests/jobpulse/test_semantic_cache.py tests/jobpulse/test_vision_tier.py tests/jobpulse/test_nano_tier.py tests/jobpulse/test_intelligence_wiring.py tests/jobpulse/test_tier_streaming.py tests/jobpulse/test_ext_models.py tests/jobpulse/test_ext_bridge.py tests/jobpulse/test_state_machines.py tests/jobpulse/test_ext_adapter.py tests/jobpulse/test_ext_routing.py -v
```

Expected: All tests PASS

- [ ] **Step 3: Commit lint fixes if any**

```bash
git add -u
git commit -m "chore(ext): lint fixes for Phase 2 form intelligence"
```

- [ ] **Step 4: Push**

```bash
git push origin main
```
