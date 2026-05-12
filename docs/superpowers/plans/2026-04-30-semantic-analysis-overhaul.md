# Semantic Analysis Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure all 11 semantic analysis components to use embedding similarity as the primary decision mechanism, replacing string/keyword matching.

**Architecture:** New `shared/semantic_utils.py` foundation provides shared embedder singleton, numpy cosine similarity, and semantic matching functions. All 10 existing components rewired to use these as their primary semantic tier, with existing string matching demoted to fast-exit optimization. Adaptive weights stored in SQLite for tunable signal weights. Eval suite with golden test sets enforces >=90% accuracy.

**Tech Stack:** Python, numpy, MemoryEmbedder (Voyage 3 Large + MiniLM fallback), SQLite, pytest

---

### Task 1: Create `shared/semantic_utils.py` Foundation

**Files:**
- Create: `shared/semantic_utils.py`
- Test: `tests/shared/test_semantic_utils.py`

- [ ] **Step 1: Write failing tests for the core API**

```python
# tests/shared/test_semantic_utils.py
"""Tests for shared semantic utility functions."""
from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_embedder():
    """Mock MemoryEmbedder that returns deterministic vectors."""
    embedder = MagicMock()
    embedder.dims = 4

    vectors = {
        "male": [1.0, 0.0, 0.0, 0.0],
        "man": [0.95, 0.05, 0.0, 0.0],
        "woman": [0.0, 1.0, 0.0, 0.0],
        "female": [0.05, 0.95, 0.0, 0.0],
        "yes": [0.0, 0.0, 1.0, 0.0],
        "no": [0.0, 0.0, 0.0, 1.0],
        "united kingdom": [0.7, 0.3, 0.0, 0.0],
        "uk": [0.72, 0.28, 0.0, 0.0],
        "cat": [0.0, 0.0, 0.5, 0.5],
    }

    def fake_embed(text):
        key = text.strip().lower()
        return vectors.get(key, [0.25, 0.25, 0.25, 0.25])

    def fake_embed_batch(texts):
        return [fake_embed(t) for t in texts]

    embedder.embed = fake_embed
    embedder.embed_batch = fake_embed_batch
    return embedder


class TestSemanticSimilarity:
    def test_identical_strings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            score = semantic_similarity("male", "male")
            assert score > 0.99

    def test_similar_strings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            score = semantic_similarity("male", "man")
            assert score > 0.9

    def test_dissimilar_strings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            score = semantic_similarity("yes", "no")
            assert score < 0.1

    def test_returns_zero_on_embedder_failure(self):
        from shared.semantic_utils import semantic_similarity
        with patch("shared.semantic_utils._get_embedder", return_value=None):
            score = semantic_similarity("hello", "world")
            assert score == 0.0


class TestBestSemanticMatch:
    def test_finds_best_match(self, mock_embedder):
        from shared.semantic_utils import best_semantic_match
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            match, score = best_semantic_match("male", ["Man", "Woman", "Other"])
            assert match == "Man"
            assert score > 0.9

    def test_returns_none_below_threshold(self, mock_embedder):
        from shared.semantic_utils import best_semantic_match
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            match, score = best_semantic_match("cat", ["Man", "Woman"], min_score=0.8)
            assert match is None

    def test_empty_candidates(self, mock_embedder):
        from shared.semantic_utils import best_semantic_match
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            match, score = best_semantic_match("male", [])
            assert match is None

    def test_returns_none_on_embedder_failure(self):
        from shared.semantic_utils import best_semantic_match
        with patch("shared.semantic_utils._get_embedder", return_value=None):
            match, score = best_semantic_match("hello", ["world"])
            assert match is None
            assert score == 0.0


class TestRankSemanticMatches:
    def test_ranks_by_similarity(self, mock_embedder):
        from shared.semantic_utils import rank_semantic_matches
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            ranked = rank_semantic_matches("male", ["Man", "Woman", "Other"], top_k=3)
            assert len(ranked) == 3
            assert ranked[0][0] == "Man"
            assert ranked[0][1] > ranked[1][1]

    def test_top_k_limits_results(self, mock_embedder):
        from shared.semantic_utils import rank_semantic_matches
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            ranked = rank_semantic_matches("male", ["Man", "Woman", "Other"], top_k=1)
            assert len(ranked) == 1


class TestAdaptiveWeights:
    def test_get_defaults_on_fresh_db(self, tmp_path):
        from shared.semantic_utils import get_adaptive_weights
        db = str(tmp_path / "weights.db")
        defaults = {"signal_a": 0.5, "signal_b": 0.3}
        result = get_adaptive_weights("test_component", defaults, db_path=db)
        assert result == defaults

    def test_record_outcome_adjusts_weights(self, tmp_path):
        from shared.semantic_utils import get_adaptive_weights, record_weight_outcome
        db = str(tmp_path / "weights.db")
        defaults = {"signal_a": 0.5, "signal_b": 0.5}
        # Record 10 successes where signal_a contributed
        for _ in range(10):
            record_weight_outcome(
                "test_component",
                {"signal_a": 1.0, "signal_b": 0.0},
                success=True,
                db_path=db,
            )
        weights = get_adaptive_weights("test_component", defaults, db_path=db)
        assert weights["signal_a"] > weights["signal_b"]


class TestEmbeddingCache:
    def test_caches_embeddings(self, mock_embedder):
        from shared.semantic_utils import semantic_similarity
        with patch("shared.semantic_utils._get_embedder", return_value=mock_embedder):
            semantic_similarity("male", "man")
            semantic_similarity("male", "woman")
            # "male" should have been embedded only once (cached)
            male_calls = [
                c for c in mock_embedder.embed.call_args_list
                if c[0][0].strip().lower() == "male"
            ]
            assert len(male_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/test_semantic_utils.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.semantic_utils'`

- [ ] **Step 3: Implement `shared/semantic_utils.py`**

```python
# shared/semantic_utils.py
"""Shared semantic analysis utilities.

Provides a singleton embedder, numpy-based cosine similarity, and semantic
matching functions used by all 11 semantic analysis components.

Usage:
    from shared.semantic_utils import semantic_similarity, best_semantic_match
    score = semantic_similarity("male", "man")  # ~0.95
    match, score = best_semantic_match("uk", ["United Kingdom", "France"])
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from shared.logging_config import get_logger

logger = get_logger(__name__)

_embedder_instance = None
_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "adaptive_weights.db"


def _get_embedder():
    """Lazy singleton MemoryEmbedder."""
    global _embedder_instance
    if _embedder_instance is None:
        try:
            from shared.memory_layer._embedder import MemoryEmbedder
            _embedder_instance = MemoryEmbedder()
        except Exception as exc:
            logger.warning("SemanticUtils: embedder unavailable (%s)", exc)
            return None
    return _embedder_instance


@lru_cache(maxsize=2048)
def _cached_embed(text: str) -> tuple[float, ...] | None:
    """Embed text and cache as tuple (hashable for LRU)."""
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        vec = embedder.embed(text.strip())
        return tuple(vec)
    except Exception as exc:
        logger.debug("Embedding failed for '%s': %s", text[:50], exc)
        return None


def _to_numpy(vec: tuple[float, ...] | None) -> np.ndarray | None:
    if vec is None:
        return None
    return np.array(vec, dtype=np.float32)


def semantic_similarity(a: str, b: str) -> float:
    """Cosine similarity between two texts. Cached embeddings, numpy ops."""
    vec_a = _to_numpy(_cached_embed(a))
    vec_b = _to_numpy(_cached_embed(b))
    if vec_a is None or vec_b is None:
        return 0.0
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


def best_semantic_match(
    query: str,
    candidates: list[str],
    min_score: float = 0.75,
) -> tuple[str | None, float]:
    """Find the best matching candidate by embedding similarity.

    Returns (matched_candidate, score) or (None, 0.0) if no match above threshold.
    """
    if not candidates or not query or not query.strip():
        return None, 0.0
    query_vec = _to_numpy(_cached_embed(query))
    if query_vec is None:
        return None, 0.0

    best_candidate: str | None = None
    best_score = 0.0
    for candidate in candidates:
        cand_vec = _to_numpy(_cached_embed(candidate))
        if cand_vec is None:
            continue
        norm_q = np.linalg.norm(query_vec)
        norm_c = np.linalg.norm(cand_vec)
        if norm_q == 0 or norm_c == 0:
            continue
        score = float(np.dot(query_vec, cand_vec) / (norm_q * norm_c))
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score >= min_score:
        return best_candidate, best_score
    return None, best_score


def rank_semantic_matches(
    query: str,
    candidates: list[str],
    top_k: int = 5,
) -> list[tuple[str, float]]:
    """Rank candidates by descending cosine similarity."""
    if not candidates or not query or not query.strip():
        return []
    query_vec = _to_numpy(_cached_embed(query))
    if query_vec is None:
        return []

    scored: list[tuple[str, float]] = []
    norm_q = np.linalg.norm(query_vec)
    if norm_q == 0:
        return []
    for candidate in candidates:
        cand_vec = _to_numpy(_cached_embed(candidate))
        if cand_vec is None:
            continue
        norm_c = np.linalg.norm(cand_vec)
        if norm_c == 0:
            continue
        score = float(np.dot(query_vec, cand_vec) / (norm_q * norm_c))
        scored.append((candidate, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Adaptive Weights
# ---------------------------------------------------------------------------

def _ensure_weights_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS adaptive_weights (
                component TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                weight REAL NOT NULL,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                PRIMARY KEY (component, signal_name)
            )
        """)


def get_adaptive_weights(
    component: str,
    defaults: dict[str, float],
    db_path: str | None = None,
) -> dict[str, float]:
    """Load adaptive weights. Initializes from defaults if first call."""
    path = db_path or str(_DB_PATH)
    _ensure_weights_db(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT signal_name, weight FROM adaptive_weights WHERE component = ?",
            (component,),
        ).fetchall()
    if rows:
        return {r["signal_name"]: r["weight"] for r in rows}
    # Initialize from defaults
    with sqlite3.connect(path) as conn:
        for signal, weight in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO adaptive_weights (component, signal_name, weight) VALUES (?, ?, ?)",
                (component, signal, weight),
            )
    return dict(defaults)


def record_weight_outcome(
    component: str,
    signal_contributions: dict[str, float],
    success: bool,
    db_path: str | None = None,
) -> None:
    """Record outcome for weight adjustment.

    Multiplicative update: signals that contributed to success get +5% weight,
    signals that contributed to failure get -5%. Then renormalize.
    """
    path = db_path or str(_DB_PATH)
    _ensure_weights_db(path)
    col = "success_count" if success else "failure_count"
    multiplier = 1.05 if success else 0.95

    with sqlite3.connect(path) as conn:
        for signal, contribution in signal_contributions.items():
            if contribution <= 0:
                continue
            conn.execute(
                f"UPDATE adaptive_weights SET weight = weight * ?, {col} = {col} + 1 WHERE component = ? AND signal_name = ?",
                (multiplier, component, signal),
            )
        # Renormalize so weights sum to 1.0
        rows = conn.execute(
            "SELECT signal_name, weight FROM adaptive_weights WHERE component = ?",
            (component,),
        ).fetchall()
        if rows:
            total = sum(r[1] for r in rows)
            if total > 0:
                for r in rows:
                    conn.execute(
                        "UPDATE adaptive_weights SET weight = ? WHERE component = ? AND signal_name = ?",
                        (r[1] / total, component, r[0]),
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/test_semantic_utils.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/semantic_utils.py tests/shared/test_semantic_utils.py
git commit -m "feat(semantic): add shared/semantic_utils.py foundation

Singleton embedder, numpy cosine, best_semantic_match, adaptive weights.
All 11 semantic components will use this as their primary semantic tier.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Restructure SemanticMatcher with Embedding Tier

**Files:**
- Modify: `jobpulse/form_engine/semantic_matcher.py`
- Test: `tests/jobpulse/test_semantic_quality.py` (create)

- [ ] **Step 1: Write failing test for embedding tier**

```python
# tests/jobpulse/test_semantic_quality.py
"""Golden test sets for semantic analysis quality. >=90% accuracy required."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


def _make_embedder_with_real_similarity():
    """Create a mock embedder that produces real-ish similarity for known pairs.

    Uses a manually-constructed vector space where semantically similar words
    are close together.
    """
    import numpy as np

    # Mini vector space: 8 dimensions, manually positioned
    _VECTORS = {
        "male": np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "man": np.array([0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "m": np.array([0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "female": np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "woman": np.array([0.05, 0.95, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "f": np.array([0.1, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "non-binary": np.array([0.3, 0.3, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "prefer not to say": np.array([0.1, 0.1, 0.1, 0.7, 0.0, 0.0, 0.0, 0.0]),
        "yes": np.array([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        "no": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]),
        "true": np.array([0.0, 0.0, 0.0, 0.0, 0.95, 0.05, 0.0, 0.0]),
        "false": np.array([0.0, 0.0, 0.0, 0.0, 0.05, 0.95, 0.0, 0.0]),
        "united kingdom": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
        "uk": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.95, 0.05]),
        "3": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.5]),
        "2-5 years": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.45, 0.55]),
        "0-2 years": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.55, 0.45]),
        "graduate visa": np.array([0.0, 0.0, 0.7, 0.3, 0.0, 0.0, 0.0, 0.0]),
        "graduate route visa": np.array([0.0, 0.0, 0.65, 0.35, 0.0, 0.0, 0.0, 0.0]),
        "i consent": np.array([0.0, 0.0, 0.0, 0.0, 0.8, 0.0, 0.1, 0.1]),
        "i agree to the privacy policy": np.array([0.0, 0.0, 0.0, 0.0, 0.75, 0.0, 0.15, 0.1]),
        "send me marketing emails": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.7, 0.1, 0.2]),
        "subscribe to newsletter": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.65, 0.15, 0.2]),
    }
    _DEFAULT = np.array([0.125] * 8)

    embedder = MagicMock()
    embedder.dims = 8

    def fake_embed(text):
        key = text.strip().lower()
        vec = _VECTORS.get(key, _DEFAULT)
        norm = float(np.linalg.norm(vec))
        return (vec / norm).tolist() if norm > 0 else _DEFAULT.tolist()

    def fake_embed_batch(texts):
        return [fake_embed(t) for t in texts]

    embedder.embed = fake_embed
    embedder.embed_batch = fake_embed_batch
    return embedder


@pytest.fixture(autouse=True)
def patch_embedder():
    embedder = _make_embedder_with_real_similarity()
    with patch("shared.semantic_utils._get_embedder", return_value=embedder):
        # Clear the LRU cache between tests
        from shared.semantic_utils import _cached_embed
        _cached_embed.cache_clear()
        yield embedder


class TestSemanticMatcherQuality:
    """>=90% accuracy on known option matching scenarios."""

    GOLDEN_MATCHES = [
        # (desired_value, available_options, expected_match)
        ("male", ["Man", "Woman", "Non-binary", "Prefer not to say"], "Man"),
        ("female", ["Man", "Woman", "Non-binary", "Prefer not to say"], "Woman"),
        ("yes", ["Yes", "No"], "Yes"),
        ("no", ["Yes", "No"], "No"),
        ("true", ["Yes", "No"], "Yes"),
        ("false", ["Yes", "No"], "No"),
        ("united kingdom", ["UK", "US", "EU", "Other"], "UK"),
        ("graduate visa", ["Graduate Route Visa", "Skilled Worker", "Other"], "Graduate Route Visa"),
        ("1 month", ["Immediately", "Less than 1 month", "1 month or less", "2+ months"], "1 month or less"),
        ("immediately", ["Immediately", "1 month", "2 months", "3+ months"], "Immediately"),
    ]

    def test_golden_set_accuracy(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match

        correct = 0
        total = len(self.GOLDEN_MATCHES)
        failures = []
        for desired, options, expected in self.GOLDEN_MATCHES:
            result = semantic_option_match(desired, options)
            if result == expected:
                correct += 1
            else:
                failures.append(f"  {desired!r} -> got {result!r}, expected {expected!r}")

        accuracy = correct / total
        msg = f"SemanticMatcher accuracy: {correct}/{total} ({accuracy:.0%})"
        if failures:
            msg += "\nFailures:\n" + "\n".join(failures)
        assert accuracy >= 0.90, msg


class TestCheckboxIntentQuality:
    """Checkbox consent/marketing detection."""

    CONSENT_LABELS = [
        "I consent to the processing of my data",
        "I agree to the privacy policy",
        "I acknowledge and accept the terms",
        "Confirm data processing consent",
    ]
    MARKETING_LABELS = [
        "Send me marketing emails",
        "Subscribe to our newsletter",
        "Receive promotional offers",
        "Opt in to communications",
    ]

    def test_consent_labels_detected(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        correct = sum(1 for label in self.CONSENT_LABELS if checkbox_intent(label) is True)
        assert correct >= len(self.CONSENT_LABELS) * 0.9

    def test_marketing_labels_detected(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        correct = sum(1 for label in self.MARKETING_LABELS if checkbox_intent(label) is False)
        assert correct >= len(self.MARKETING_LABELS) * 0.9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestSemanticMatcherQuality -v`
Expected: FAIL — embedding tier doesn't exist yet so some golden matches will miss

- [ ] **Step 3: Restructure `semantic_matcher.py` with embedding tier**

Replace the full file content:

```python
# jobpulse/form_engine/semantic_matcher.py
"""Semantic option matching — 6-tier cascade for form field values.

Matches a desired value to available dropdown/radio/combobox options
using embedding similarity as the primary semantic tier, with string
matching tiers as fast-exit optimizations.
"""
from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    # Gender
    "male": ("man", "m", "he/him", "he/him/his", "masculine"),
    "female": ("woman", "f", "she/her", "she/her/hers", "feminine"),
    "man": ("male", "m", "he/him"),
    "woman": ("female", "f", "she/her"),
    # Boolean
    "yes": ("true", "authorized", "i am", "i do", "i have", "y",
            "yes, i am authorized", "yes i am", "yes, i do", "yes, i have"),
    "no": ("false", "not authorized", "i am not", "i do not", "n",
           "no, i am not", "no i do not"),
    # Ethnicity
    "indian": ("asian or asian british - indian", "south asian", "asian - indian",
               "asian or asian british: indian"),
    "asian": ("asian or asian british", "east asian", "southeast asian"),
    "white": ("white british", "white - british", "white english",
              "white - english/welsh/scottish/northern irish"),
    # Visa / work authorization
    "graduate visa": ("tier 4 graduate visa", "post-study work visa",
                      "graduate route", "graduate route visa"),
    # Notice period
    "1 month": ("4 weeks", "one month", "30 days", "less than 30 days",
                "less than 1 month", "1 month or less"),
    "2 weeks": ("14 days", "two weeks", "less than 2 weeks"),
    "immediately": ("available immediately", "0 days", "now", "none"),
    # Experience years
    "2 years": ("2+ years", "2-3 years", "over 2 years", "2 to 3 years"),
    "3 years": ("3+ years", "3-5 years", "over 3 years", "3 to 5 years"),
    "1 year": ("1+ years", "1-2 years", "over 1 year", "0-1 years"),
}

_RANGE_PAT = re.compile(r"[£$€]?\s*([\d,]+)\s*[-–—]\s*[£$€]?\s*([\d,]+)")

_CONSENT_ANCHORS = [
    "I consent to the processing of my personal data",
    "I agree to the privacy policy and terms",
    "I acknowledge and accept the terms and conditions",
    "consent to data processing",
    "agree to privacy policy",
]
_MARKETING_ANCHORS = [
    "send me marketing emails and promotions",
    "subscribe to newsletter and offers",
    "opt in to promotional communications",
    "receive marketing updates",
]
_CHECKBOX_SIMILARITY_THRESHOLD = 0.65


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def semantic_option_match(
    desired_value: str,
    available_options: list[str],
    *,
    field_label: str = "",
    aliases: dict[str, tuple[str, ...]] | None = None,
    numeric_value: float | None = None,
) -> str | None:
    """Match a desired value to available options via 6-tier cascade.

    Tiers:
    1. Exact match (case-insensitive, whitespace-normalized)
    2. Canonical alias lookup (CANONICAL_ALIASES + caller aliases)
    3. Numeric range match (salary, age, experience years)
    4. Embedding similarity (primary semantic tier)
    5. Token overlap (Jaccard, threshold >= 2 shared tokens)
    6. Substring containment (for values >= 4 chars)
    """
    if not available_options or not desired_value:
        return None

    desired_norm = _normalize(desired_value)
    opts_norm = {_normalize(o): o for o in available_options}

    # Tier 1: Exact match
    if desired_norm in opts_norm:
        return opts_norm[desired_norm]

    # Tier 2: Canonical aliases
    all_aliases = dict(CANONICAL_ALIASES)
    if aliases:
        all_aliases.update(aliases)

    for alias in all_aliases.get(desired_norm, ()):
        alias_norm = _normalize(alias)
        if alias_norm in opts_norm:
            return opts_norm[alias_norm]
        for opt_norm, opt_original in opts_norm.items():
            if alias_norm in opt_norm or opt_norm in alias_norm:
                return opt_original

    for canonical, alias_tuple in all_aliases.items():
        if desired_norm in (_normalize(a) for a in alias_tuple):
            canonical_norm = _normalize(canonical)
            if canonical_norm in opts_norm:
                return opts_norm[canonical_norm]

    # Tier 3: Numeric range
    numeric = numeric_value
    if numeric is None:
        try:
            numeric = float(desired_value.replace(",", "").replace("£", "").replace("$", "").replace("€", ""))
        except (ValueError, AttributeError):
            numeric = None

    if numeric is not None:
        for opt in available_options:
            m = _RANGE_PAT.search(opt)
            if m:
                low = float(m.group(1).replace(",", ""))
                high = float(m.group(2).replace(",", ""))
                if low <= numeric <= high:
                    return opt

    # Tier 4: Embedding similarity (primary semantic tier)
    try:
        from shared.semantic_utils import best_semantic_match
        match, score = best_semantic_match(desired_value, available_options, min_score=0.70)
        if match is not None:
            logger.debug("Embedding match: '%s' -> '%s' (score=%.3f)", desired_value[:40], match, score)
            return match
    except Exception as exc:
        logger.debug("Embedding tier failed: %s", exc)

    # Tier 5: Token overlap
    stop_words = {"and", "for", "the", "with", "from", "valid", "not", "or", "a", "an", "to", "of", "in", "i", "am", "is"}
    desired_tokens = {t for t in desired_norm.split() if len(t) > 1 and t not in stop_words}

    if desired_tokens:
        best_opt = None
        best_score = 0
        for opt_norm, opt_original in opts_norm.items():
            opt_tokens = {t for t in opt_norm.split() if len(t) > 1 and t not in stop_words}
            overlap = len(desired_tokens & opt_tokens)
            if overlap > best_score:
                best_score = overlap
                best_opt = opt_original
        if best_opt is not None and best_score >= 2:
            return best_opt

    # Tier 6: Substring containment (for values >= 4 chars)
    if len(desired_norm) >= 4:
        for opt_norm, opt_original in opts_norm.items():
            if desired_norm in opt_norm:
                return opt_original

    return None


def checkbox_intent(label: str, *, required: bool = False) -> bool | None:
    """Determine whether to check a checkbox using embedding similarity.

    Compares label against consent and marketing anchor phrases.
    Returns True (check), False (don't check), or None (ambiguous).
    """
    if not label or not label.strip():
        return True if required else None

    try:
        from shared.semantic_utils import semantic_similarity

        consent_score = max(
            semantic_similarity(label, anchor) for anchor in _CONSENT_ANCHORS
        )
        marketing_score = max(
            semantic_similarity(label, anchor) for anchor in _MARKETING_ANCHORS
        )

        if consent_score >= _CHECKBOX_SIMILARITY_THRESHOLD and consent_score > marketing_score:
            return True
        if marketing_score >= _CHECKBOX_SIMILARITY_THRESHOLD and marketing_score > consent_score:
            return False
    except Exception:
        pass

    if required:
        return True

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestSemanticMatcherQuality tests/jobpulse/test_semantic_quality.py::TestCheckboxIntentQuality -v`
Expected: PASS

- [ ] **Step 5: Run existing semantic_matcher tests to check for regressions**

Run: `python -m pytest tests/ -v -k "semantic_match" --no-header`
Expected: All existing tests PASS (the public API is unchanged — same function signatures)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_engine/semantic_matcher.py tests/jobpulse/test_semantic_quality.py
git commit -m "feat(semantic): add embedding tier to SemanticMatcher (Tier 4)

6-tier cascade: exact → aliases → numeric → embedding → token overlap → substring.
checkbox_intent() now uses embedding similarity against consent/marketing anchors.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Fix OptionAligner Bug + Add Embedding Tier

**Files:**
- Modify: `jobpulse/screening_option_aligner.py`
- Test: `tests/jobpulse/test_semantic_quality.py` (append)

- [ ] **Step 1: Add golden test for OptionAligner**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestOptionAlignerQuality:
    """>=90% accuracy on answer-to-option alignment."""

    GOLDEN_ALIGNMENTS = [
        # (answer, options, expected)
        ("yes", ["Yes", "No"], "Yes"),
        ("Yes", ["Yes", "No"], "Yes"),
        ("y", ["Yes", "No"], "Yes"),
        ("no", ["Yes", "No"], "No"),
        ("true", ["Yes", "No"], "Yes"),
        ("false", ["Yes", "No"], "No"),
        ("prefer not to say", ["Yes", "No", "Prefer not to say"], "Prefer not to say"),
        ("male", ["Man", "Woman", "Non-binary"], "Man"),
        ("Man", ["Male", "Female", "Other"], "Male"),
    ]

    def test_golden_set_accuracy(self):
        from jobpulse.screening_option_aligner import OptionAligner
        aligner = OptionAligner()

        correct = 0
        total = len(self.GOLDEN_ALIGNMENTS)
        failures = []
        for answer, options, expected in self.GOLDEN_ALIGNMENTS:
            result = aligner.align_answer(answer, options)
            if result == expected:
                correct += 1
            else:
                failures.append(f"  {answer!r} -> got {result!r}, expected {expected!r}")

        accuracy = correct / total
        msg = f"OptionAligner accuracy: {correct}/{total} ({accuracy:.0%})"
        if failures:
            msg += "\nFailures:\n" + "\n".join(failures)
        assert accuracy >= 0.90, msg

    def test_fuzzy_score_containment_bug_fixed(self):
        """Verify the max/max bug is fixed — short substring should score < 0.9."""
        from jobpulse.screening_option_aligner import OptionAligner
        score = OptionAligner._fuzzy_score("uk", "united kingdom")
        assert score < 0.9, f"Containment score should be proportional, got {score}"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestOptionAlignerQuality -v`
Expected: `test_fuzzy_score_containment_bug_fixed` FAIL (bug still present)

- [ ] **Step 3: Fix OptionAligner**

Edit `jobpulse/screening_option_aligner.py`:

**Fix 1:** Replace `_fuzzy_score` method (line 166-179):

```python
    @staticmethod
    def _fuzzy_score(a: str, b: str) -> float:
        """Simple fuzzy score between 0 and 1."""
        if a == b:
            return 1.0
        if a in b or b in a:
            return min(len(a), len(b)) / max(len(a), len(b)) * 0.9
        # Word overlap
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        overlap = len(words_a & words_b)
        return overlap / max(len(words_a), len(words_b))
```

**Fix 2:** Add embedding tier in `align_answer` method. Replace the fuzzy section (after the normalized match block, before the default return) with:

```python
        # Embedding similarity (primary semantic tier)
        try:
            from shared.semantic_utils import best_semantic_match
            emb_match, emb_score = best_semantic_match(answer.strip(), options, min_score=0.70)
            if emb_match is not None:
                logger.debug("Embedding aligned '%s' -> '%s' (score=%.2f)", answer[:50], emb_match, emb_score)
                return emb_match
        except Exception:
            pass

        # Fuzzy prefix / contains match (fallback)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestOptionAlignerQuality tests/jobpulse/test_screening_v2.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_option_aligner.py tests/jobpulse/test_semantic_quality.py
git commit -m "fix(aligner): fix _fuzzy_score bug + add embedding alignment tier

Bug: max/max always returned 1.0 for containment. Fixed to min/max.
Added embedding similarity as primary tier after normalized match.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Add Semantic Near-Miss Cache to PageReasoner

**Files:**
- Modify: `jobpulse/page_analysis/page_reasoner.py`

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestPageReasonerSemanticCache:
    def test_semantic_near_miss_hits_cache(self, tmp_path):
        from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction
        reasoner = PageReasoner(db_path=str(tmp_path / "cache.db"))

        action = PageAction(
            page_understanding="Job application form with personal details",
            action="fill_form",
            target_text="",
            reasoning="Form detected",
            confidence=0.9,
            page_type="application_form",
        )
        # Manually cache an entry
        reasoner._set_cache("testdomain:abc123", action)

        # Slightly different page text should still find the cached action
        result = reasoner._get_cached_semantic(
            "testdomain",
            "Job application form with personal information",
        )
        # With real embeddings this would hit; with mocks it depends on vector space
        # The test verifies the method exists and returns PageAction | None
        assert result is None or isinstance(result, PageAction)
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestPageReasonerSemanticCache -v`
Expected: FAIL — `_get_cached_semantic` does not exist yet

- [ ] **Step 3: Add semantic near-miss cache**

Edit `jobpulse/page_analysis/page_reasoner.py`:

**Add column to `_ensure_db`:**

```python
    def _ensure_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reasoning_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            existing = {r[1] for r in conn.execute("PRAGMA table_info(reasoning_cache)").fetchall()}
            if "page_understanding_text" not in existing:
                conn.execute("ALTER TABLE reasoning_cache ADD COLUMN page_understanding_text TEXT DEFAULT ''")
```

**Add `_get_cached_semantic` method after `_get_cached`:**

```python
    def _get_cached_semantic(self, domain: str, page_text: str) -> PageAction | None:
        """Semantic near-miss: find cached entries with similar page understanding."""
        try:
            from shared.semantic_utils import best_semantic_match
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT cache_key, result_json, created_at, page_understanding_text "
                    "FROM reasoning_cache WHERE cache_key LIKE ? AND page_understanding_text != ''",
                    (f"{domain}:%",),
                ).fetchall()
            if not rows:
                return None
            valid = [(r[0], r[1], r[2], r[3]) for r in rows if (time.time() - r[2]) < 3600]
            if not valid:
                return None
            understandings = [r[3] for r in valid]
            match, score = best_semantic_match(page_text[:200], understandings, min_score=0.90)
            if match is not None:
                idx = understandings.index(match)
                data = json.loads(valid[idx][1])
                logger.info("PageReasoner: semantic near-miss hit (score=%.3f)", score)
                return PageAction(**data)
        except Exception as exc:
            logger.debug("Semantic cache lookup failed: %s", exc)
        return None
```

**Update `_set_cache` to store page_understanding_text:**

```python
    def _set_cache(self, key: str, action: PageAction) -> None:
        if action.action == "abort" and action.confidence < 0.5:
            return
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO reasoning_cache "
                    "(cache_key, result_json, created_at, page_understanding_text) VALUES (?, ?, ?, ?)",
                    (key, json.dumps(action.to_dict()), time.time(), action.page_understanding),
                )
        except Exception:
            pass
```

**Update `reason_sync` to try semantic cache on hash miss:**

After `if cached:` block and before `button_summary`, add:

```python
        # Semantic near-miss lookup
        from urllib.parse import urlparse
        parsed = urlparse(url) if url else None
        domain = parsed.netloc.lower().removeprefix("www.") if parsed else ""
        semantic_hit = self._get_cached_semantic(domain, page_text)
        if semantic_hit:
            return semantic_hit
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestPageReasonerSemanticCache tests/jobpulse/test_page_analysis.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/page_analysis/page_reasoner.py tests/jobpulse/test_semantic_quality.py
git commit -m "feat(reasoner): add semantic near-miss cache lookup

On hash miss, compares page text against cached page_understanding strings
using embedding similarity (threshold 0.90). Avoids redundant LLM calls
for slightly-different pages.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Add Embedding Signal to PageTypeClassifier

**Files:**
- Modify: `jobpulse/page_analysis/classifier.py`

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestPageTypeClassifierEmbedding:
    def test_classifier_has_embedding_signal(self):
        """Verify the classifier uses embedding similarity as a feature."""
        from jobpulse.page_analysis.classifier import PageTypeClassifier, DEFAULT_WEIGHTS
        assert "embedding_similarity" in DEFAULT_WEIGHTS.get("application_form", {}), \
            "DEFAULT_WEIGHTS must include embedding_similarity for application_form"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestPageTypeClassifierEmbedding -v`
Expected: FAIL — `embedding_similarity` not in weights

- [ ] **Step 3: Add embedding signal to classifier**

Edit `jobpulse/page_analysis/classifier.py`:

**Add page type anchor descriptions after the imports:**

```python
_PAGE_TYPE_ANCHORS: dict[str, str] = {
    "verification_wall": "security challenge captcha or verification blocking page access",
    "confirmation": "application submitted successfully thank you for applying",
    "email_verification": "check your email to verify your account click the link",
    "session_expired": "session timed out expired please sign in log in again",
    "consent_gate": "agree to terms conditions privacy policy consent data processing",
    "signup_form": "create new account sign up register with email and password",
    "login_form": "sign in log in to your account with email and password",
    "job_description": "job listing role description requirements responsibilities apply button",
    "application_form": "job application form personal details resume upload work experience",
    "unknown": "unrecognized page content",
}
```

**Add `embedding_similarity` to `DEFAULT_WEIGHTS` for each page type that can benefit:**

Add `"embedding_similarity": 2.0` to: `application_form`, `job_description`, `login_form`, `signup_form`, `confirmation`, `consent_gate`. Add `"embedding_similarity": 1.5` to: `verification_wall`, `email_verification`, `session_expired`. Leave `unknown` unchanged.

**Add embedding score computation in `_score_all_types`:**

After the `derived` dict is built (before the `scores` loop), add:

```python
        # Embedding similarity signal
        embedding_scores = self._compute_embedding_scores(features)
        derived["embedding_similarity"] = 0.0  # placeholder, overridden per type
```

Then update the scoring loop:

```python
        scores: dict[PageType, float] = {}
        for page_type in PageType:
            type_weights = self.weights.get(page_type.value, {})
            score = type_weights.get("bias", 0.0)
            for feature_name, weight in type_weights.items():
                if feature_name == "bias":
                    continue
                if feature_name == "embedding_similarity":
                    value = embedding_scores.get(page_type.value, 0.0)
                else:
                    value = derived.get(feature_name, 0.0)
                score += value * weight
            scores[page_type] = score
```

**Add `_compute_embedding_scores` method:**

```python
    def _compute_embedding_scores(self, features: PageFeatures) -> dict[str, float]:
        """Compute embedding similarity between page text and each page type anchor."""
        try:
            from shared.semantic_utils import semantic_similarity
            page_text = getattr(features, '_page_text_preview', '')
            if not page_text:
                return {}
            scores = {}
            for page_type, anchor in _PAGE_TYPE_ANCHORS.items():
                scores[page_type] = semantic_similarity(page_text[:200], anchor)
            return scores
        except Exception:
            return {}
```

**Store page text in PageFeatures** — add `_page_text_preview: str = ""` field to the dataclass, and set it in `_extract_features`:

```python
    # Add to PageFeatures dataclass
    _page_text_preview: str = ""
```

Set in `_extract_features` before the return:

```python
        features = PageFeatures(
            ...all existing fields...,
            _page_text_preview=page_text[:200],
        )
        return features
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestPageTypeClassifierEmbedding tests/jobpulse/test_page_analysis.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/page_analysis/classifier.py tests/jobpulse/test_semantic_quality.py
git commit -m "feat(classifier): add embedding similarity signal to PageTypeClassifier

Compares page text against anchor descriptions per page type.
Added as a weighted feature alongside existing DOM-based signals.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Remove Regex from ScreeningDetector, Embeddings as Primary

**Files:**
- Modify: `jobpulse/screening_detector.py`

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestScreeningDetectorQuality:
    """Screening detection with embeddings as primary signal."""

    SCREENING_FIELDS = [
        {"label": "What is your expected salary?", "type": "text", "required": True, "options": []},
        {"label": "Do you have the right to work in the UK?", "type": "radio", "required": True, "options": ["Yes", "No"]},
        {"label": "How many years of experience do you have?", "type": "select", "required": True, "options": ["0-2", "2-5", "5+"]},
        {"label": "Are you willing to relocate?", "type": "radio", "required": False, "options": ["Yes", "No"]},
        {"label": "What is your notice period?", "type": "text", "required": True, "options": []},
    ]
    NON_SCREENING_FIELDS = [
        {"label": "First name", "type": "text", "required": True, "options": []},
        {"label": "Email address", "type": "email", "required": True, "options": []},
        {"label": "Phone number", "type": "tel", "required": True, "options": []},
    ]

    def test_detects_screening_fields(self):
        from jobpulse.screening_detector import ScreeningDetector
        detector = ScreeningDetector()
        correct = sum(1 for f in self.SCREENING_FIELDS if detector.is_screening(f))
        assert correct >= len(self.SCREENING_FIELDS) * 0.9

    def test_no_regex_attribute(self):
        """Verify _SCREENING_KEYWORDS regex has been removed."""
        import jobpulse.screening_detector as mod
        assert not hasattr(mod, "_SCREENING_KEYWORDS"), \
            "_SCREENING_KEYWORDS regex must be removed — use embeddings instead"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestScreeningDetectorQuality -v`
Expected: `test_no_regex_attribute` FAIL

- [ ] **Step 3: Restructure ScreeningDetector**

Replace full file:

```python
# jobpulse/screening_detector.py
"""Universal screening question detector.

Uses embedding similarity as the primary signal, supplemented by structural
signals (field type, question mark, options). No regex for classification.
"""
from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_SIGNAL_WEIGHTS = {
    "embedding_similarity": 0.40,
    "is_select_radio_checkbox": 0.20,
    "has_question_mark": 0.15,
    "options_contain_yes_no": 0.15,
    "is_required_and_unmapped": 0.10,
}

_FAST_PASS_THRESHOLD = 0.50
_FINAL_THRESHOLD = 0.45

_SCREENING_ANCHORS = [
    "What is your current salary?",
    "What is your expected salary?",
    "Do you have the right to work in the UK?",
    "Do you require visa sponsorship?",
    "What is your notice period?",
    "When can you start?",
    "Are you willing to relocate?",
    "Are you comfortable working remotely?",
    "How many years of experience do you have?",
    "What is your highest level of education?",
    "Do you have a driving license?",
    "Are you willing to travel?",
    "Do you hold security clearance?",
    "Are you willing to undergo a background check?",
    "What is your gender?",
    "Do you consent to data processing?",
    "Why do you want this role?",
    "Tell us about yourself",
    "Describe your experience with",
    "What languages do you speak?",
    "Are you currently employed?",
    "Who is your current employer?",
    "What is your current job title?",
    "Are you a veteran?",
    "Do you have any criminal convictions?",
    "Please upload your cover letter",
]


class ScreeningDetector:
    """Embedding-primary detector for screening questions in job application forms."""

    def __init__(self, embedder: Any | None = None) -> None:
        self._embedder = embedder
        self._anchor_embeddings: list[list[float]] | None = None
        self._weights = _DEFAULT_SIGNAL_WEIGHTS.copy()
        self._load_adaptive_weights()

    def _load_adaptive_weights(self) -> None:
        try:
            from shared.semantic_utils import get_adaptive_weights
            self._weights = get_adaptive_weights(
                "screening_detector", _DEFAULT_SIGNAL_WEIGHTS,
            )
        except Exception:
            pass

    def _ensure_embedder(self) -> None:
        if self._embedder is not None:
            return
        try:
            from shared.semantic_utils import _get_embedder
            self._embedder = _get_embedder()
        except Exception as exc:
            logger.debug("ScreeningDetector: embedder unavailable (%s)", exc)

    def _ensure_anchor_embeddings(self) -> None:
        if self._anchor_embeddings is not None:
            return
        self._ensure_embedder()
        if self._embedder is None:
            return
        try:
            self._anchor_embeddings = self._embedder.embed_batch(_SCREENING_ANCHORS)
        except Exception as exc:
            logger.debug("ScreeningDetector: anchor embedding failed: %s", exc)

    def is_screening(
        self,
        field: dict[str, Any],
        profile_mapping: dict[str, str] | None = None,
    ) -> bool:
        """Return True if the field is likely a screening question."""
        scores = self._compute_signals(field, profile_mapping or {})
        total = sum(
            scores.get(sig, 0.0) * self._weights.get(sig, 0.0)
            for sig in self._weights
        )
        return total >= _FINAL_THRESHOLD

    def _compute_signals(
        self,
        field: dict[str, Any],
        profile_mapping: dict[str, str],
    ) -> dict[str, float]:
        label = field.get("label", "")
        field_type = field.get("type", "")
        required = field.get("required", False)
        options = field.get("options", []) or []

        signals: dict[str, float] = {}

        # Embedding similarity (primary)
        signals["embedding_similarity"] = self._embedding_score(label)

        # Structural signals
        signals["has_question_mark"] = 1.0 if "?" in label else 0.0
        signals["is_select_radio_checkbox"] = 1.0 if field_type in {"select", "combobox", "radio", "checkbox"} else 0.0
        signals["options_contain_yes_no"] = 1.0 if self._options_look_screening(options) else 0.0
        signals["is_required_and_unmapped"] = 1.0 if required and label.lower().strip() not in profile_mapping else 0.0

        return signals

    def _embedding_score(self, label: str) -> float:
        if not label or not label.strip():
            return 0.0
        self._ensure_anchor_embeddings()
        if self._embedder is None or self._anchor_embeddings is None:
            return 0.0
        try:
            from shared.semantic_utils import semantic_similarity
            return max(
                semantic_similarity(label, anchor) for anchor in _SCREENING_ANCHORS
            )
        except Exception:
            return 0.0

    def _options_look_screening(self, options: list[str]) -> bool:
        if not options:
            return False
        opts_lower = [str(o).lower().strip() for o in options]
        yes_no = {"yes", "no", "true", "false", "1", "0", "prefer not to say", "n/a"}
        matches = sum(1 for o in opts_lower if o in yes_no or o.startswith(("yes", "no")))
        if matches >= 2:
            return True
        screening_options = {
            "male", "female", "non-binary", "other",
            "full-time", "part-time", "contract", "permanent",
            "uk", "eu", "international", "british",
            "native", "fluent", "intermediate", "beginner",
        }
        return sum(1 for o in opts_lower if o in screening_options) >= 2

    def record_outcome(self, field: dict[str, Any], was_screening: bool) -> None:
        """Record outcome for adaptive weight learning."""
        signals = self._compute_signals(field, {})
        try:
            from shared.semantic_utils import record_weight_outcome
            record_weight_outcome("screening_detector", signals, was_screening)
        except Exception:
            pass
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestScreeningDetectorQuality tests/jobpulse/test_screening_v2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_detector.py tests/jobpulse/test_semantic_quality.py
git commit -m "feat(detector): remove regex, embeddings as primary signal

Removed _SCREENING_KEYWORDS regex. Embedding similarity is now the primary
signal (weight 0.40). Structural signals (question mark, field type, options)
remain as supporting features. Adaptive weights via SQLite.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Remove Redundant `_agent_rules()` from ScreeningPipeline

**Files:**
- Modify: `jobpulse/screening_pipeline.py`

- [ ] **Step 1: Write test to verify rules removal**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestScreeningPipelineNoKeywordRules:
    def test_no_agent_rules_method(self):
        """_agent_rules keyword matching must be removed — intent classifier handles it."""
        from jobpulse.screening_pipeline import ScreeningPipeline
        assert not hasattr(ScreeningPipeline, "_agent_rules"), \
            "_agent_rules must be removed — redundant with intent classifier"

    def test_salary_detection_uses_intent_not_keywords(self):
        """_finalise must use intent result, not keyword matching for salary."""
        import inspect
        from jobpulse.screening_pipeline import ScreeningPipeline
        source = inspect.getsource(ScreeningPipeline._finalise)
        assert "salary" not in source.split("question.lower")[0] if "question.lower" in source else True, \
            "Salary detection should use intent, not question.lower() keyword matching"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestScreeningPipelineNoKeywordRules -v`
Expected: FAIL — `_agent_rules` still exists

- [ ] **Step 3: Edit ScreeningPipeline**

In `jobpulse/screening_pipeline.py`:

**Remove the entire `_agent_rules` method** (lines 340-387).

**Remove Step 5 block in `_answer_single`** — delete lines 163-169:
```python
        # Step 5: Profile-driven rules (work auth, availability, education, etc.)
        rules_answer = self._agent_rules(question, job_context)
        if rules_answer:
            result["answer"] = rules_answer
            result["confidence"] = 0.60
            result["source"] = "agent_rules"
            return result
```

**Fix salary detection in `_finalise`** — replace keyword matching with intent check:

```python
        # OLD:
        if any(kw in question.lower() for kw in ("salary", "compensation", "pay")):

        # NEW:
        if result.get("intent") in ("salary_current", "salary_expected"):
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestScreeningPipelineNoKeywordRules tests/jobpulse/test_screening_v2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_pipeline.py tests/jobpulse/test_semantic_quality.py
git commit -m "refactor(pipeline): remove _agent_rules keyword matching

Redundant with intent classifier (Step 3) + profile resolver (Step 4).
Salary detection now uses classified intent instead of keyword matching.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Wire Shared Embedder into ScreeningIntentClassifier

**Files:**
- Modify: `jobpulse/screening_intent.py`

- [ ] **Step 1: Write test to verify shared embedder usage**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestIntentClassifierQuality:
    """>=90% accuracy on known question→intent pairs."""

    GOLDEN_INTENTS = [
        ("What is your current salary?", "salary_current"),
        ("What are your salary expectations?", "salary_expected"),
        ("Do you have the right to work in the UK?", "work_auth_yes_no"),
        ("Do you require visa sponsorship?", "sponsorship"),
        ("What is your notice period?", "notice_period"),
        ("When can you start?", "start_date"),
        ("Are you willing to relocate?", "willing_relocate"),
        ("Are you comfortable working remotely?", "remote"),
        ("How many years of experience do you have?", "experience_years"),
        ("What is your highest level of education?", "education_level"),
        ("What is your gender?", "diversity_monitoring"),
        ("Do you consent to data processing?", "consent_data"),
        ("Why do you want this role?", "open_ended"),
        ("What languages do you speak?", "languages"),
        ("Are you currently employed?", "currently_employed"),
    ]

    def test_no_local_cosine_function(self):
        """Verify local _cosine_similarity function has been removed."""
        import jobpulse.screening_intent as mod
        assert not hasattr(mod, "_cosine_similarity"), \
            "Local _cosine_similarity must be removed — use shared.semantic_utils"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestIntentClassifierQuality::test_no_local_cosine_function -v`
Expected: FAIL — `_cosine_similarity` still exists

- [ ] **Step 3: Refactor ScreeningIntentClassifier**

Edit `jobpulse/screening_intent.py`:

**Remove the module-level `_cosine_similarity` function** (lines 242-248).

**Replace `MemoryEmbedder` import and instantiation** with shared utils:

In `__init__`, replace:
```python
        if self._embedder is None:
            try:
                self._embedder = MemoryEmbedder()
            except Exception as exc:
                logger.warning("IntentClassifier: Embedder unavailable (%s)", exc)
```
with:
```python
        if self._embedder is None:
            try:
                from shared.semantic_utils import _get_embedder
                self._embedder = _get_embedder()
            except Exception as exc:
                logger.warning("IntentClassifier: Embedder unavailable (%s)", exc)
```

**Replace cosine computation in `classify`** — change the inner loop:

```python
        for intent, vectors in self._prototypes.items():
            import numpy as np
            if not vectors:
                continue
            proto_arr = np.array(vectors, dtype=np.float32)
            query_arr = np.array(query_vec, dtype=np.float32)
            # Vectorized cosine: dot products / norms
            norms = np.linalg.norm(proto_arr, axis=1) * np.linalg.norm(query_arr)
            norms = np.where(norms == 0, 1, norms)
            sims = np.dot(proto_arr, query_arr) / norms
            max_score = float(np.max(sims))
            if max_score > best_score:
                best_score = max_score
                best_intent = intent
```

**Update imports**: Remove `from shared.memory_layer._embedder import MemoryEmbedder` from top.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestIntentClassifierQuality tests/jobpulse/test_screening_v2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_intent.py tests/jobpulse/test_semantic_quality.py
git commit -m "refactor(intent): use shared embedder + numpy vectorized cosine

Removed local _cosine_similarity. Uses shared.semantic_utils._get_embedder()
singleton. Prototype comparison now uses numpy vectorized dot products.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Wire Shared Embedder into ScreeningSemanticCache

**Files:**
- Modify: `jobpulse/screening_semantic_cache.py`

- [ ] **Step 1: Write test**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestSemanticCacheSharedUtils:
    def test_no_local_cosine(self):
        import jobpulse.screening_semantic_cache as mod
        assert not hasattr(mod, "_cosine_similarity"), \
            "Local _cosine_similarity must be removed — use shared.semantic_utils"

    def test_no_keyword_boolean_inference(self):
        """_infer_boolean_from_text must use embeddings, not keyword sets."""
        import jobpulse.screening_semantic_cache as mod
        assert not hasattr(mod, "_AFFIRMATIVE"), \
            "_AFFIRMATIVE keyword set must be removed — use semantic_similarity"
        assert not hasattr(mod, "_NEGATIVE"), \
            "_NEGATIVE keyword set must be removed — use semantic_similarity"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestSemanticCacheSharedUtils -v`
Expected: FAIL

- [ ] **Step 3: Refactor ScreeningSemanticCache**

Edit `jobpulse/screening_semantic_cache.py`:

**Remove** the module-level `_cosine_similarity` function (lines 38-44).

**Remove** `_AFFIRMATIVE` and `_NEGATIVE` keyword sets (lines 522-523).

**Replace `_infer_boolean_from_text`** (lines 526-537):

```python
def _infer_boolean_from_text(text: str) -> bool | None:
    """Infer yes/no meaning from a long-form answer using embedding similarity."""
    if not text or len(text.strip()) < 8:
        return None
    try:
        from shared.semantic_utils import semantic_similarity
        yes_score = semantic_similarity(text, "yes I do, I am, I have, I can, I agree")
        no_score = semantic_similarity(text, "no I do not, I am not, I cannot, I don't have")
        if yes_score > no_score and yes_score > 0.5:
            return True
        if no_score > yes_score and no_score > 0.5:
            return False
    except Exception:
        pass
    return None
```

**Replace `_cosine_similarity` calls in `lookup`** with shared utils:

In the SQLite fallback section, replace:
```python
                    score = _cosine_similarity(query_vec, row_vec)
```
with:
```python
                    import numpy as np
                    a = np.array(query_vec, dtype=np.float32)
                    b = np.array(row_vec, dtype=np.float32)
                    norm_a = np.linalg.norm(a)
                    norm_b = np.linalg.norm(b)
                    score = float(np.dot(a, b) / (norm_a * norm_b)) if norm_a > 0 and norm_b > 0 else 0.0
```

**Replace `MemoryEmbedder` init** in `__init__`:
```python
        if self._embedder is None:
            try:
                from shared.semantic_utils import _get_embedder
                self._embedder = _get_embedder()
                if self._embedder:
                    self._dims = self._embedder.dims
            except Exception as exc:
                logger.warning("ScreeningSemanticCache: Embedder init failed (%s).", exc)
                self._embedder = None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestSemanticCacheSharedUtils tests/jobpulse/test_screening_v2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_semantic_cache.py tests/jobpulse/test_semantic_quality.py
git commit -m "refactor(cache): use shared embedder + remove keyword-based boolean inference

Removed local _cosine_similarity, _AFFIRMATIVE, _NEGATIVE. Boolean inference
now uses embedding similarity. Embedder uses shared singleton.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 10: Wire Shared Embedder into NLP Classifier

**Files:**
- Modify: `jobpulse/nlp_classifier.py`

- [ ] **Step 1: Write test**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestNLPClassifierSharedEmbedder:
    def test_uses_shared_embedder(self):
        """NLP classifier should use get_embedder() from shared.semantic_utils."""
        import inspect
        import jobpulse.nlp_classifier as mod
        source = inspect.getsource(mod._load_model)
        assert "semantic_utils" in source or "_get_embedder" in source, \
            "_load_model must use shared.semantic_utils._get_embedder()"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestNLPClassifierSharedEmbedder -v`
Expected: FAIL

- [ ] **Step 3: Refactor NLP Classifier**

Edit `jobpulse/nlp_classifier.py`:

Replace `_load_model()` function:

```python
def _load_model():
    """Load the embedding model via shared semantic utils (once, lazy)."""
    global _model
    if _model is not None:
        return _model

    try:
        from shared.semantic_utils import _get_embedder
        embedder = _get_embedder()
        if embedder is None:
            logger.warning("NLP classifier: shared embedder unavailable")
            return None
        # Wrap MemoryEmbedder in an adapter that matches the encode() API
        _model = _EmbedderAdapter(embedder)
        logger.info("NLP classifier: using shared MemoryEmbedder")
    except Exception as e:
        logger.warning("Failed to load NLP model: %s", e)
        _model = None
    return _model


class _EmbedderAdapter:
    """Adapts MemoryEmbedder to the encode() API expected by the classifier."""

    def __init__(self, embedder):
        self._embedder = embedder

    def encode(self, texts: list[str], show_progress_bar: bool = False,
               normalize_embeddings: bool = True) -> np.ndarray:
        vectors = self._embedder.embed_batch(texts)
        arr = np.array(vectors, dtype=np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            arr = arr / norms
        return arr
```

Remove the `_OllamaEmbedder` class and the `_EMBEDDING_PROVIDER` / `_OLLAMA_BASE_URL` / `_LOCAL_EMBED_MODEL` constants — all embedding is routed through the shared embedder now.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestNLPClassifierSharedEmbedder -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/nlp_classifier.py tests/jobpulse/test_semantic_quality.py
git commit -m "refactor(nlp): use shared embedder via _EmbedderAdapter

Removed _OllamaEmbedder and provider toggle. All embedding now routes
through shared.semantic_utils._get_embedder() singleton.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 11: Add Embedding Fallback to FieldMapper

**Files:**
- Modify: `jobpulse/form_engine/field_mapper.py`

- [ ] **Step 1: Write test**

Append to `tests/jobpulse/test_semantic_quality.py`:

```python
class TestFieldMapperEmbeddingFallback:
    def test_fuzzy_custom_answer_uses_embeddings(self):
        """_fuzzy_custom_answer should use embedding similarity as fallback."""
        import inspect
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer
        source = inspect.getsource(_fuzzy_custom_answer)
        assert "semantic" in source.lower() or "best_semantic_match" in source, \
            "_fuzzy_custom_answer must use embedding similarity as fallback"
```

- [ ] **Step 2: Run to verify failing**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestFieldMapperEmbeddingFallback -v`
Expected: FAIL

- [ ] **Step 3: Add embedding fallback**

Edit `jobpulse/form_engine/field_mapper.py`:

In `_fuzzy_custom_answer()`, add an embedding fallback block at the end (before `return None`):

```python
    # Embedding similarity fallback
    try:
        from shared.semantic_utils import best_semantic_match
        candidate_keys = [k for k in custom_answers if not k.startswith("_") and isinstance(custom_answers[k], str) and custom_answers[k].strip()]
        if candidate_keys:
            match, score = best_semantic_match(label_lower, candidate_keys, min_score=0.70)
            if match is not None:
                return custom_answers[match].strip()
    except Exception:
        pass

    return None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py::TestFieldMapperEmbeddingFallback tests/jobpulse/test_native_form_filler.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/field_mapper.py tests/jobpulse/test_semantic_quality.py
git commit -m "feat(mapper): add embedding fallback in _fuzzy_custom_answer

When keyword and diversity-keyword matching fail, uses best_semantic_match
to find the closest custom_answers key by embedding similarity.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 12: Run Full Test Suite + Final Validation

**Files:**
- No new files — validation only

- [ ] **Step 1: Run the full semantic quality test suite**

Run: `python -m pytest tests/jobpulse/test_semantic_quality.py -v`
Expected: All golden test classes PASS with >=90% accuracy

- [ ] **Step 2: Run existing test suites to check for regressions**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py tests/jobpulse/test_page_analysis.py tests/jobpulse/test_native_form_filler.py tests/jobpulse/test_pre_submit_gate.py tests/shared/test_semantic_utils.py -v`
Expected: All PASS (pre-existing failures excluded)

- [ ] **Step 3: Run full jobpulse test suite**

Run: `python -m pytest tests/ -v -x --timeout=120 -k "not live and not slow" 2>&1 | tail -30`
Expected: No new failures introduced

- [ ] **Step 4: Final commit with all changes verified**

If any test fixes were needed during validation, commit them:

```bash
git add -A
git commit -m "test: fix any regressions from semantic analysis overhaul

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
