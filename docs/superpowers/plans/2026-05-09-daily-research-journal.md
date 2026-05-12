# Daily Research Journal v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a daily, single-user pipeline that produces 8–12 verified 1100–1300-word ML/LLM/SLM/VLM paper summaries to Notion + Telegram.

**Architecture:** Extend the existing `jobpulse/papers/` package. Add 5 new modules (domain_filter, results_filter, verifier, journal_summarizer, journal_audit), 1 new prompt-registry domain (`journal`), 4 columns to `data/papers.db`, and 2 new cron entries. The OLD `arxiv_agent.py` path is left untouched in v1; deprecation is a v1.1 follow-up.

**Tech Stack:** Python 3.11+, Pydantic, httpx (async), SQLite, langchain-openai (Together AI via OpenAI-compatible endpoint), pypdf, Voyage 3 Large embeddings, existing `cognitive_llm_call` + `PromptRegistry` + `MemoryManager` + `OptimizationEngine`.

**Spec:** `docs/superpowers/specs/2026-05-09-daily-research-journal-design.md`

**Calibration before merge:** Tasks 9 (domain) and 12 (results) must hit their thresholds (≥90% / ≤10% FN) on calibration fixtures before later tasks merge.

---

## Phase 1 — Foundation

### Task 1: Add Together AI provider to `shared/agents.py`

**Files:**
- Modify: `shared/agents.py` (provider resolver + `_make_together_llm` + `_MultiProviderLLM` switch)
- Modify: `jobpulse/config.py` (add `TOGETHER_API_KEY`, `TOGETHER_MODEL`, `TOGETHER_CODER_MODEL`)
- Test: `tests/shared/test_agents_together.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_agents_together.py
import os
import pytest
from unittest.mock import patch

from shared import agents


def test_resolve_provider_together(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "together")
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
    # Reset cached provider
    agents._LLM_PROVIDER = None
    assert agents._resolve_provider() == "together"


def test_get_model_name_together(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "together")
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
    monkeypatch.setenv("TOGETHER_MODEL", "Qwen/Qwen3-30B-A3B-Instruct")
    agents._LLM_PROVIDER = None
    assert agents.get_model_name() == "Qwen/Qwen3-30B-A3B-Instruct"


def test_make_together_llm_uses_openai_base(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")
    llm = agents._make_together_llm(
        temperature=0.3, model="Qwen/Qwen3-30B-A3B-Instruct",
        timeout=30.0, max_tokens=2000,
    )
    assert "together.xyz" in str(llm.openai_api_base)
    assert llm.model_name == "Qwen/Qwen3-30B-A3B-Instruct"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/shared/test_agents_together.py -v
```
Expected: FAIL — `_resolve_provider` returns "openai" or "local", not "together".

- [ ] **Step 3: Add config knobs**

In `jobpulse/config.py`, after the `OPENAI_API_KEY` line:

```python
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_MODEL = os.getenv("TOGETHER_MODEL", "Qwen/Qwen3-30B-A3B-Instruct")
TOGETHER_CODER_MODEL = os.getenv("TOGETHER_CODER_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")
```

- [ ] **Step 4: Add provider in `shared/agents.py`**

In `_resolve_provider()` (line ~135), add the explicit branch before the auto-detect:

```python
def _resolve_provider() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "auto").lower()
    if explicit in ("local", "openai", "together"):
        return explicit
    # auto: probe Ollama, fall back to OpenAI
    if _probe_ollama():
        logger.info("Ollama detected at %s — using local LLM (%s)", _OLLAMA_HOST, _LOCAL_MODEL)
        return "local"
    logger.info("Ollama not reachable — falling back to cloud (older models)")
    return "openai"
```

After `_make_local_llm` (line ~218), add:

```python
def _make_together_llm(temperature: float, model: str, timeout: float, max_tokens: int) -> ChatOpenAI:
    """Build a Together AI LLM via OpenAI-compatible endpoint."""
    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        raise RuntimeError("TOGETHER_API_KEY not set; cannot use LLM_PROVIDER=together")
    effective_model = os.environ.get("TOGETHER_MODEL", model) if model == "gpt-5-mini" else model
    return ChatOpenAI(
        model=effective_model,
        temperature=temperature,
        request_timeout=timeout,
        max_tokens=max_tokens,
        openai_api_base="https://api.together.xyz/v1",
        openai_api_key=api_key,
    )
```

In `get_model_name()` (line ~167), add the together branch:

```python
def get_model_name(default: str = "gpt-5-mini") -> str:
    _ensure_provider()
    if _is_local:
        return _LOCAL_MODEL
    if _LLM_PROVIDER == "together":
        return os.environ.get("TOGETHER_MODEL", default if default != "gpt-5-mini" else "Qwen/Qwen3-30B-A3B-Instruct")
    if _use_fallback_models:
        return _FALLBACK_MODELS.get(default, default)
    return default
```

In `_MultiProviderLLM` (line ~253), wire `_make_together_llm` into the dispatch — find the existing if/elif on `_LLM_PROVIDER` inside the class and add:

```python
elif _LLM_PROVIDER == "together":
    return _make_together_llm(self.temperature, self.model, self.timeout, self.max_tokens)
```

In `get_llm()` (line ~340), apply the same dispatch addition.

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/shared/test_agents_together.py -v
```
Expected: PASS, all 3 tests.

- [ ] **Step 6: Commit**

```bash
git add shared/agents.py jobpulse/config.py tests/shared/test_agents_together.py
git commit -m "feat(agents): add Together AI provider for Qwen3-30B-A3B"
```

---

### Task 2: Extend `papers/store.py` schema with 4 new columns

**Files:**
- Modify: `jobpulse/papers/store.py` (add columns to `_HF_COLUMNS` migration list)
- Test: `tests/papers/test_store_journal_columns.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_store_journal_columns.py
import sqlite3
from pathlib import Path
from jobpulse.papers.store import PaperStore


def test_journal_columns_created(tmp_path: Path):
    db = tmp_path / "papers.db"
    store = PaperStore(db_path=db)
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(papers)")}
    for required in ("domain_tag", "verification", "summary_long", "rank_reason"):
        assert required in cols, f"missing column {required}"


def test_legacy_db_migrated(tmp_path: Path):
    """A pre-journal DB (only legacy columns) gets the new columns added on open."""
    db = tmp_path / "papers.db"
    legacy = sqlite3.connect(db)
    legacy.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                   "authors TEXT NOT NULL, abstract TEXT NOT NULL, categories TEXT NOT NULL, "
                   "pdf_url TEXT NOT NULL, arxiv_url TEXT NOT NULL, published_at TEXT NOT NULL, "
                   "discovered_at TEXT NOT NULL)")
    legacy.commit()
    legacy.close()
    PaperStore(db_path=db)  # should ALTER on open
    cols = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(papers)")}
    assert "domain_tag" in cols
    assert "summary_long" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/papers/test_store_journal_columns.py -v
```
Expected: FAIL — `domain_tag` not in columns.

- [ ] **Step 3: Add columns to `_HF_COLUMNS` in `jobpulse/papers/store.py`**

At the bottom of `_HF_COLUMNS` (line ~30):

```python
    ("domain_tag", "TEXT DEFAULT ''"),
    ("verification", "TEXT DEFAULT ''"),
    ("summary_long", "TEXT DEFAULT ''"),
    ("rank_reason", "TEXT DEFAULT ''"),
```

Add the same columns to `_CREATE_TABLE` SQL block (line ~32) so fresh DBs include them — append before the closing `)`:

```sql
    domain_tag          TEXT DEFAULT '',
    verification        TEXT DEFAULT '',
    summary_long        TEXT DEFAULT '',
    rank_reason         TEXT DEFAULT ''
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/papers/test_store_journal_columns.py -v
```
Expected: PASS, both tests.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/store.py tests/papers/test_store_journal_columns.py
git commit -m "feat(papers/store): add domain_tag/verification/summary_long/rank_reason columns"
```

---

### Task 3: Add Pydantic models to `research_journal/models.py` (NEW root package)

**Files:**
- Create: `research_journal/__init__.py` (empty for now; will export `JournalPipeline` in Task 30)
- Create: `research_journal/models.py` (NEW — journal-specific types)
- Test: `tests/research_journal/__init__.py` (empty)
- Test: `tests/research_journal/test_models.py` (new)
- Test: `tests/research_journal/conftest.py` (new — shared fixtures)

**Note:** `Paper`, `RankedPaper`, `FactCheckResult`, `BlogPost`, `Chart`, `ReadingStats` stay in `jobpulse/papers/models.py` as cross-cutting paper types. Only the new journal-specific types live in `research_journal/models.py`. Field additions to `Paper` (e.g. `affiliations` in Task 13, `rank_reason` on `RankedPaper` in Task 16) remain in `jobpulse/papers/models.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_models.py
from research_journal.models import (
    DomainTag, BenchResult, ExtractedFacts, VerificationBadge, PaperTypeClassification,
)


def test_domain_tag_literal():
    assert DomainTag.__args__ == ("core", "tangent", "out")


def test_bench_result_round_trip():
    b = BenchResult(name="MMLU", metric="accuracy", value=72.3, baseline=68.1)
    assert b.delta == 4.2


def test_verification_badge_score_property():
    badge = VerificationBadge(
        has_results=True, peer_reviewed=False, has_repo=True,
        independent_citations=False, claims_grounded=True,
        reasons={"peer_reviewed": "venue 'arxiv preprint' not in PEER_REVIEWED_VENUES"},
    )
    assert badge.score == 3


def test_paper_type_classification_includes_has_results():
    pt = PaperTypeClassification(
        has_results=True, paper_type="research",
        reason="benchmarks MMLU + ablation table", confidence=0.92,
    )
    assert pt.has_results is True
    assert pt.paper_type == "research"


def test_extracted_facts_requires_excerpts():
    """raw_excerpts is load-bearing for the hallucination guard — must be non-empty."""
    facts = ExtractedFacts(
        problem="x", method_steps=["a"], architecture_details={"k": "v"},
        benchmarks=[], ablations=[], limitations=[], key_insight="z",
        raw_excerpts=["a verbatim quote from the paper"],
    )
    assert len(facts.raw_excerpts) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/research_journal/test_models.py -v
```
Expected: FAIL — `DomainTag`/`BenchResult`/etc. don't exist.

- [ ] **Step 3: Create `research_journal/models.py`**

```python
"""Journal-specific Pydantic models. Cross-cutting paper types live in jobpulse/papers/models.py."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DomainTag = Literal["core", "tangent", "out"]
PaperType = Literal["research", "survey", "position", "tutorial", "workshop"]


class BenchResult(BaseModel):
    name: str           # benchmark name, e.g. "MMLU"
    metric: str         # e.g. "accuracy", "F1"
    value: float
    baseline: float | None = None

    @property
    def delta(self) -> float | None:
        if self.baseline is None:
            return None
        return round(self.value - self.baseline, 3)


class ExtractedFacts(BaseModel):
    problem: str
    method_steps: list[str]
    architecture_details: dict[str, str]
    benchmarks: list[BenchResult]
    ablations: list[str]
    limitations: list[str]
    key_insight: str
    raw_excerpts: list[str] = Field(min_length=1)


class VerificationBadge(BaseModel):
    has_results: bool
    peer_reviewed: bool
    has_repo: bool
    independent_citations: bool
    claims_grounded: bool
    reasons: dict[str, str] = Field(default_factory=dict)

    @property
    def score(self) -> int:
        return sum([
            self.has_results, self.peer_reviewed, self.has_repo,
            self.independent_citations, self.claims_grounded,
        ])


class PaperTypeClassification(BaseModel):
    has_results: bool
    paper_type: PaperType
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/research_journal/test_models.py -v
```
Expected: PASS, all 5 tests.

- [ ] **Step 4b: Create `research_journal/__init__.py` (empty placeholder)**

```python
"""research_journal — daily curated ML/LLM/SLM/VLM research feed.

Pipeline: ingest -> domain classify -> hard-filter (results) -> rank -> verify
        -> summarize (3-agent: Extract -> Write -> Hallucination Guard)
        -> publish to Notion + Telegram.

The orchestrator JournalPipeline is exported from .pipeline (added in Task 30).
"""
```

Also create empty `tests/research_journal/__init__.py` and `tests/research_journal/conftest.py`:

```python
# tests/research_journal/conftest.py
"""Shared fixtures for research_journal tests."""
import pytest
```

- [ ] **Step 5: Commit**

```bash
git add research_journal/__init__.py research_journal/models.py \
        tests/research_journal/__init__.py tests/research_journal/conftest.py \
        tests/research_journal/test_models.py
git commit -m "feat(research_journal): bootstrap package + journal-specific Pydantic models"
```

---

## Phase 2 — Domain classifier

### Task 4: Anchor sets fixture (v0)

**Files:**
- Create: `tests/fixtures/research_journal/anchor_sets.json`
- Create: `tests/fixtures/research_journal/__init__.py` (empty)

- [ ] **Step 1: Create the anchor fixture**

```json
{
  "core": [
    "instruction tuning of large language models",
    "RLHF reward model alignment",
    "DPO direct preference optimization",
    "PPO for language model fine-tuning",
    "speculative decoding for LLM inference",
    "long-context attention mechanisms",
    "mixture-of-experts routing in LLMs",
    "PEFT LoRA QLoRA parameter-efficient fine-tuning",
    "vision language model alignment and grounding",
    "in-context learning behavior of LLMs",
    "test-time compute and chain-of-thought scaling",
    "retrieval-augmented generation for LLMs",
    "tool use in language model agents",
    "model distillation for small language models",
    "small language model training",
    "mechanistic interpretability of transformer LLMs",
    "model merging and weight averaging",
    "LLM evaluation benchmarks and harness design",
    "constitutional AI and self-correction",
    "synthetic data generation for LLM training",
    "speculative sampling and draft models",
    "KV cache compression and quantization",
    "reasoning chains and verifier models",
    "agent planning with LLMs",
    "multimodal vision-language pretraining"
  ],
  "tangent": [
    "transformer architecture for medical imaging",
    "language model for robotics control policies",
    "LLM for protein and molecular design",
    "multi-agent simulation with language models",
    "LLM in education tutoring systems",
    "language model for low-resource translation",
    "LLM for scientific discovery in chemistry",
    "language model for software engineering autonomy",
    "LLM safety red-teaming",
    "LLM-driven UI agents and browser automation"
  ],
  "out": [
    "molecular dynamics simulation",
    "self-driving lane and object detection",
    "graph neural network for chemistry property prediction",
    "convolutional architecture for satellite imagery",
    "reinforcement learning for game playing without language",
    "computer vision for medical pathology slides",
    "speech recognition acoustic modeling",
    "recommendation systems with collaborative filtering",
    "time series forecasting for finance",
    "NeRF and 3D reconstruction"
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/research_journal/anchor_sets.json tests/fixtures/research_journal/__init__.py
git commit -m "feat(papers/journal): v0 anchor sets for domain classifier"
```

---

### Task 5: Embedding-based domain classifier (Pass 1)

**Files:**
- Create: `research_journal/domain_filter.py`
- Test: `tests/research_journal/test_domain_filter.py` (new — Pass 1 only)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_domain_filter.py
import json
from pathlib import Path
import pytest

from research_journal.domain_filter import DomainClassifier
from jobpulse.papers.models import Paper

FIX = Path(__file__).parent.parent / "fixtures" / "journal"


@pytest.fixture
def classifier(monkeypatch):
    # Force deterministic behavior in unit test — see test_classifier_pass1
    monkeypatch.setenv("JOURNAL_DOMAIN_CACHE", "0")
    return DomainClassifier(anchors_path=FIX / "anchor_sets.json")


def _paper(title: str, abstract: str = "test abstract") -> Paper:
    return Paper(
        arxiv_id="0000.00000", title=title, authors=["X"],
        abstract=abstract, categories=["cs.CL"],
        pdf_url="", arxiv_url="", published_at="2026-01-01",
    )


def test_loads_anchors_from_fixture(classifier):
    assert len(classifier.anchors_core) == 25
    assert len(classifier.anchors_tangent) == 10
    assert len(classifier.anchors_out) == 10


def test_pass1_obvious_core(classifier, monkeypatch):
    # Fake the embedder so we don't make network calls in unit tests
    def fake_max_cosine(text, anchors):
        if "RLHF" in text or "DPO" in text:
            return 0.85
        return 0.10
    monkeypatch.setattr(classifier, "_max_cosine", fake_max_cosine)
    tag, conf, reason = classifier._pass1(_paper("DPO for LLM alignment"))
    assert tag == "core"
    assert conf >= 0.65


def test_pass1_obvious_out(classifier, monkeypatch):
    def fake_max_cosine(text, anchors):
        if "molecular" in text.lower():
            return 0.85
        return 0.10
    monkeypatch.setattr(classifier, "_max_cosine", fake_max_cosine)
    tag, conf, reason = classifier._pass1(_paper("Molecular dynamics simulation"))
    assert tag == "out"


def test_pass1_borderline_returns_none(classifier, monkeypatch):
    """Confidence between 0.55 and 0.65 falls through to Pass 2."""
    monkeypatch.setattr(classifier, "_max_cosine", lambda t, a: 0.60)
    tag, conf, reason = classifier._pass1(_paper("borderline"))
    assert tag is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/research_journal/test_domain_filter.py -v
```
Expected: FAIL — `DomainClassifier` doesn't exist.

- [ ] **Step 3: Create `research_journal/domain_filter.py`**

```python
"""Domain classifier — narrows raw paper feed to ML/LLM/SLM/VLM/finetune."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from jobpulse.papers.models import Paper
from research_journal.models import DomainTag
from shared.logging_config import get_logger

logger = get_logger(__name__)

_THRESHOLD_CORE = 0.65
_THRESHOLD_OUT = 0.70
_THRESHOLD_TANGENT = 0.60
_BORDERLINE_LOW = 0.55


class DomainClassifier:
    """Two-pass: embedding similarity (Pass 1) → LLM borderline (Pass 2)."""

    def __init__(self, anchors_path: Path | None = None) -> None:
        if anchors_path is None:
            anchors_path = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "journal" / "anchor_sets.json"
        data = json.loads(anchors_path.read_text())
        self.anchors_core: list[str] = data["core"]
        self.anchors_tangent: list[str] = data["tangent"]
        self.anchors_out: list[str] = data["out"]

    def classify(self, paper: Paper) -> tuple[DomainTag, float, str]:
        tag, conf, reason = self._pass1(paper)
        if tag is not None:
            return tag, conf, reason
        return self._pass2(paper)

    def _pass1(self, paper: Paper) -> tuple[DomainTag | None, float, str]:
        text = f"{paper.title}. {paper.abstract}"
        sim_core, best_core = self._max_cosine_with_match(text, self.anchors_core)
        sim_out, best_out = self._max_cosine_with_match(text, self.anchors_out)
        sim_tangent, best_tangent = self._max_cosine_with_match(text, self.anchors_tangent)

        if sim_core >= _THRESHOLD_CORE and sim_core > sim_out:
            return "core", sim_core, f"matched core anchor: {best_core!r} ({sim_core:.2f})"
        if sim_out >= _THRESHOLD_OUT and sim_out > sim_core:
            return "out", sim_out, f"matched reject anchor: {best_out!r} ({sim_out:.2f})"
        if _BORDERLINE_LOW <= sim_core < _THRESHOLD_CORE:
            return None, sim_core, "borderline — defer to LLM"
        if sim_tangent >= _THRESHOLD_TANGENT:
            return "tangent", sim_tangent, f"adjacent: {best_tangent!r} ({sim_tangent:.2f})"
        return "out", sim_core, f"below all thresholds (core={sim_core:.2f})"

    def _pass2(self, paper: Paper) -> tuple[DomainTag, float, str]:
        # Implemented in Task 6
        raise NotImplementedError

    def _max_cosine_with_match(self, text: str, anchors: list[str]) -> tuple[float, str]:
        scores = [(self._max_cosine_pair(text, a), a) for a in anchors]
        score, anchor = max(scores, key=lambda p: p[0])
        return score, anchor

    def _max_cosine(self, text: str, anchors: list[str]) -> float:
        return self._max_cosine_with_match(text, anchors)[0]

    def _max_cosine_pair(self, text: str, anchor: str) -> float:
        from shared.memory_layer._embedder import embed_text
        import numpy as np
        v_text = np.asarray(embed_text(text), dtype=float)
        v_anchor = np.asarray(embed_text(anchor), dtype=float)
        denom = (np.linalg.norm(v_text) * np.linalg.norm(v_anchor)) or 1.0
        return float(np.dot(v_text, v_anchor) / denom)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/research_journal/test_domain_filter.py -v
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add research_journal/domain_filter.py tests/research_journal/test_domain_filter.py
git commit -m "feat(papers/journal): domain classifier Pass 1 (embedding similarity)"
```

---

### Task 6: LLM-based borderline classifier (Pass 2)

**Files:**
- Create: `shared/prompts/templates/journal/domain_classify.yaml`
- Modify: `research_journal/domain_filter.py` (replace `_pass2` body)
- Test: `tests/research_journal/test_domain_filter.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/research_journal/test_domain_filter.py`:

```python
def test_pass2_uses_llm(classifier, monkeypatch):
    monkeypatch.setattr(
        "research_journal.domain_filter._llm_classify_borderline",
        lambda paper: ("core", 0.78, "LLM: discusses LoRA fine-tuning"),
    )
    paper = _paper("New approach", abstract="We use LoRA on Llama-3...")
    tag, conf, reason = classifier._pass2(paper)
    assert tag == "core"
    assert "LLM:" in reason
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/research_journal/test_domain_filter.py::test_pass2_uses_llm -v
```
Expected: FAIL — `NotImplementedError`.

- [ ] **Step 3: Create the prompt template**

```yaml
# shared/prompts/templates/journal/domain_classify.yaml
domain: journal
name: domain_classify
version: "1.0.0"
system_prompt: |
  You are a research-paper triager. Given a paper's title and abstract, decide if it
  belongs in the ML/LLM/SLM/VLM/finetune domain.

  Output one of:
    - "core"    — directly about LLMs, SLMs, VLMs, fine-tuning, RLHF/DPO/PPO,
                  PEFT/LoRA, MoE, long-context, speculative decoding, RAG,
                  in-context learning, model distillation, mech-interp of LLMs,
                  test-time compute, agents/tool-use grounded in LLMs.
    - "tangent" — adjacent: LLMs APPLIED to medicine, robotics, biology, chemistry,
                  education, low-resource translation; LLM safety; LLM UI agents.
    - "out"     — not about language models at all (CV-only, RL-only games,
                  NeRF, time-series forecasting, recommendation systems, etc.).

user_template: |
  Title: {title}
  Abstract: {abstract}
  Categories: {categories}

  Return JSON: {{"tag": "core" | "tangent" | "out", "confidence": float 0..1, "reason": "one sentence"}}

output_schema:
  type: object
  properties:
    tag: {type: string, enum: [core, tangent, out]}
    confidence: {type: number, minimum: 0.0, maximum: 1.0}
    reason: {type: string, maxLength: 200}
  required: [tag, confidence, reason]
```

- [ ] **Step 4: Implement `_pass2` and the LLM helper**

In `research_journal/domain_filter.py`, replace the `_pass2` stub and add the helper:

```python
    def _pass2(self, paper: Paper) -> tuple[DomainTag, float, str]:
        return _llm_classify_borderline(paper)


def _llm_classify_borderline(paper: Paper) -> tuple[DomainTag, float, str]:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt
    import json as _json

    prompt = get_prompt("journal", "domain_classify")
    rendered = prompt.render(
        title=paper.title,
        abstract=paper.abstract[:2000],
        categories=", ".join(paper.categories),
    )
    raw = cognitive_llm_call(task=rendered, domain="journal_domain", stakes="low") or "{}"
    try:
        data = _json.loads(_strip_codefence(raw))
        tag = data.get("tag", "out")
        if tag not in ("core", "tangent", "out"):
            tag = "out"
        return tag, float(data.get("confidence", 0.5)), f"LLM: {data.get('reason', '')[:200]}"
    except (ValueError, _json.JSONDecodeError) as exc:
        logger.warning("Pass-2 LLM JSON parse failed (%s); defaulting to 'out'", exc)
        return "out", 0.0, f"LLM parse failed: {exc}"


def _strip_codefence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/research_journal/test_domain_filter.py -v
```
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add research_journal/domain_filter.py shared/prompts/templates/journal/domain_classify.yaml tests/research_journal/test_domain_filter.py
git commit -m "feat(papers/journal): domain classifier Pass 2 (LLM borderline)"
```

---

### Task 7: `classify_domain` orchestration + caching

**Files:**
- Modify: `research_journal/domain_filter.py` (add module-level `classify_domain`)
- Test: `tests/research_journal/test_domain_filter.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_classify_domain_module_function(monkeypatch):
    """Top-level helper used by the pipeline."""
    from jobpulse.papers import domain_filter
    monkeypatch.setattr(
        domain_filter.DomainClassifier, "classify",
        lambda self, paper: ("core", 0.81, "matched 'RLHF' (0.81)"),
    )
    paper = Paper(arxiv_id="x", title="t", authors=["a"], abstract="a",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    tag, conf, reason = domain_filter.classify_domain(paper)
    assert tag == "core"
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_domain_filter.py::test_classify_domain_module_function -v
```
Expected: FAIL.

- [ ] **Step 3: Add module-level helper**

Append to `research_journal/domain_filter.py`:

```python
_DEFAULT_CLASSIFIER: DomainClassifier | None = None


def _get_default() -> DomainClassifier:
    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is None:
        _DEFAULT_CLASSIFIER = DomainClassifier()
    return _DEFAULT_CLASSIFIER


def classify_domain(paper: Paper) -> tuple[DomainTag, float, str]:
    return _get_default().classify(paper)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/research_journal/test_domain_filter.py -v
```
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add research_journal/domain_filter.py tests/research_journal/test_domain_filter.py
git commit -m "feat(papers/journal): module-level classify_domain helper"
```

---

### Task 8: Domain-classifier calibration fixture (40 papers)

**Files:**
- Create: `tests/fixtures/research_journal/domain_calibration.json`

- [ ] **Step 1: Write the calibration set**

20 `core`, 10 `tangent`, 10 `out`. Each entry has `arxiv_id`, `title`, `abstract` (truncated to 500 chars), `expected_tag`. Source from real recent arXiv papers.

```json
{
  "core": [
    {"arxiv_id": "2401.06401", "title": "Mixture-of-LoRAs: An Efficient Multitask Tuning for Large Language Models", "abstract": "We propose Mixture-of-LoRAs (MoA), an efficient multitask tuning method that uses LoRA experts with a learned router for large language models. Each task is associated with a specialized LoRA expert; the router selects the appropriate expert(s) per token. We evaluate on 8 reasoning benchmarks including MMLU, GSM8K, and HumanEval, finding MoA improves over single-LoRA baselines by 4.2 points on average.", "expected_tag": "core"},
    {"arxiv_id": "2402.04291", "title": "Self-Discover: Large Language Models Self-Compose Reasoning Structures", "abstract": "We introduce Self-Discover, a framework for LLMs to self-discover task-intrinsic reasoning structures... improvements of up to 32% on BIG-Bench Hard, MATH, and Thinking for Doing.", "expected_tag": "core"}
  ],
  "_NOTE_": "Add 18 more core, 10 tangent, 10 out — implementation step expands this template",
  "tangent": [],
  "out": []
}
```

The first task in the implementation session **must expand this fixture to the full 40 papers** by sampling from the existing `data/papers.db` (use papers older than 30 days for stability), labeling them by hand. The 2-paper template above shows the schema only.

- [ ] **Step 2: Expand to 40 papers (manual labeling, ~40 min)**

Run this helper to surface candidates:

```bash
python -c "
import sqlite3, json
conn = sqlite3.connect('data/papers.db')
rows = conn.execute('SELECT arxiv_id, title, substr(abstract,1,500), categories FROM papers WHERE published_at < date(\"now\", \"-30 days\") ORDER BY RANDOM() LIMIT 60').fetchall()
print(json.dumps([dict(zip(['arxiv_id','title','abstract','categories'], r)) for r in rows], indent=2))
"
```

Manually label 20 core / 10 tangent / 10 out. Save to `tests/fixtures/research_journal/domain_calibration.json`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/research_journal/domain_calibration.json
git commit -m "test(papers/journal): 40-paper domain-classifier calibration fixture"
```

---

### Task 9: Domain-classifier calibration test (LIVE)

**Files:**
- Test: `tests/research_journal/test_domain_calibration.py` (new, marked `@pytest.mark.live`)

- [ ] **Step 1: Write the live calibration test**

```python
# tests/research_journal/test_domain_calibration.py
import json
from pathlib import Path
import pytest

from research_journal.domain_filter import DomainClassifier
from jobpulse.papers.models import Paper

CAL = Path(__file__).parent.parent / "fixtures" / "journal" / "domain_calibration.json"


@pytest.mark.live
def test_domain_calibration_meets_thresholds():
    data = json.loads(CAL.read_text())
    classifier = DomainClassifier()
    correct_core_or_out = 0
    total_core_or_out = 0
    correct_tangent = 0
    total_tangent = 0

    for expected_tag in ("core", "tangent", "out"):
        for entry in data[expected_tag]:
            paper = Paper(
                arxiv_id=entry["arxiv_id"], title=entry["title"],
                authors=["X"], abstract=entry["abstract"],
                categories=entry.get("categories", "cs.CL").split(",")
                          if isinstance(entry.get("categories", ""), str)
                          else entry.get("categories", ["cs.CL"]),
                pdf_url="", arxiv_url="", published_at="2026-01-01",
            )
            actual_tag, _, _ = classifier.classify(paper)
            if expected_tag in ("core", "out"):
                total_core_or_out += 1
                if actual_tag == expected_tag:
                    correct_core_or_out += 1
            else:  # tangent
                total_tangent += 1
                if actual_tag == expected_tag:
                    correct_tangent += 1

    accuracy_strict = correct_core_or_out / total_core_or_out
    accuracy_tangent = correct_tangent / total_tangent if total_tangent else 1.0
    assert accuracy_strict >= 0.90, f"core/out accuracy {accuracy_strict:.2%} < 90%"
    assert accuracy_tangent >= 0.75, f"tangent accuracy {accuracy_tangent:.2%} < 75%"
```

- [ ] **Step 2: Run live (requires TOGETHER_API_KEY + Voyage embeddings)**

```bash
pytest tests/research_journal/test_domain_calibration.py -v -m live
```
Expected: PASS. If FAIL, iterate on the anchor set in `tests/fixtures/research_journal/anchor_sets.json` until thresholds pass.

- [ ] **Step 3: Commit (only after thresholds pass)**

```bash
git add tests/research_journal/test_domain_calibration.py
git commit -m "test(papers/journal): live calibration test for domain classifier"
```

---

## Phase 3 — Results filter (the hard filter)

### Task 10: `results_filter.py` with combined `has_results` + `paper_type`

**Files:**
- Create: `research_journal/results_filter.py`
- Create: `shared/prompts/templates/journal/results_filter.yaml`
- Test: `tests/research_journal/test_results_filter.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_results_filter.py
import pytest
from research_journal.results_filter import classify_results
from jobpulse.papers.models import Paper
from research_journal.models import PaperTypeClassification


def _paper(title: str, abstract: str) -> Paper:
    return Paper(arxiv_id="0", title=title, authors=["X"], abstract=abstract,
                 categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")


def test_passes_research_with_numbers(monkeypatch):
    fake = PaperTypeClassification(
        has_results=True, paper_type="research",
        reason="reports +3.2 on MMLU", confidence=0.92,
    )
    monkeypatch.setattr("research_journal.results_filter._llm_classify", lambda p: fake)
    out = classify_results(_paper("X", "We achieve +3.2 on MMLU and 84.1 F1 on BoolQ."))
    assert out.has_results is True
    assert out.paper_type == "research"


def test_drops_position_paper(monkeypatch):
    fake = PaperTypeClassification(
        has_results=False, paper_type="position",
        reason="argument; no experiments", confidence=0.95,
    )
    monkeypatch.setattr("research_journal.results_filter._llm_classify", lambda p: fake)
    out = classify_results(_paper("Position: We Need Better Evaluation", "We argue..."))
    assert out.has_results is False
    assert out.paper_type == "position"


def test_low_confidence_falls_through(monkeypatch):
    """Confidence < 0.6 → mark has_results=True (don't hard-drop on uncertainty)."""
    fake = PaperTypeClassification(
        has_results=False, paper_type="research",
        reason="unclear", confidence=0.45,
    )
    monkeypatch.setattr("research_journal.results_filter._llm_classify", lambda p: fake)
    out = classify_results(_paper("X", "..."))
    # The filter should NOT hard-drop on low confidence — return original but mark unknown
    assert out.has_results is True or out.confidence >= 0.6


def test_empty_abstract_drops(monkeypatch):
    out = classify_results(_paper("Title only", ""))
    assert out.has_results is False
    assert "empty abstract" in out.reason.lower()
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_results_filter.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the prompt template**

```yaml
# shared/prompts/templates/journal/results_filter.yaml
domain: journal
name: results_filter
version: "1.0.0"
system_prompt: |
  Classify a research paper on two axes:

  (1) has_results — true ONLY if the abstract mentions at least one of:
        - specific numerical results (e.g., "+5.3% on MMLU", "95.4 F1")
        - named benchmarks (MMLU, HumanEval, GSM8K, MT-Bench, BIG-Bench, ...)
        - ablation experiments
        - comparison tables with baselines
      false otherwise.

  (2) paper_type — one of:
        - research   — novel method + empirical evaluation
        - survey     — review of prior work; may include benchmark numbers
        - position   — opinion / argument paper without new experiments
        - tutorial   — educational walkthrough
        - workshop   — workshop summary / extended abstract

user_template: |
  Title: {title}
  Abstract: {abstract}

  Return JSON:
  {{"has_results": bool, "paper_type": "research"|"survey"|"position"|"tutorial"|"workshop", "reason": "one sentence", "confidence": float 0..1}}

output_schema:
  type: object
  properties:
    has_results: {type: boolean}
    paper_type: {type: string, enum: [research, survey, position, tutorial, workshop]}
    reason: {type: string, maxLength: 200}
    confidence: {type: number, minimum: 0.0, maximum: 1.0}
  required: [has_results, paper_type, reason, confidence]
```

- [ ] **Step 4: Implement the filter**

```python
# research_journal/results_filter.py
"""Hard filter — drops papers without empirical results."""

from __future__ import annotations

import json as _json

from jobpulse.papers.models import Paper
from research_journal.models import PaperTypeClassification
from shared.logging_config import get_logger

logger = get_logger(__name__)


def classify_results(paper: Paper) -> PaperTypeClassification:
    if not paper.abstract or len(paper.abstract.strip()) < 30:
        return PaperTypeClassification(
            has_results=False, paper_type="research",
            reason="empty abstract — cannot assess", confidence=0.0,
        )
    try:
        result = _llm_classify(paper)
    except Exception as exc:
        logger.warning("results_filter LLM failed (%s); keeping paper with has_results=True", exc)
        return PaperTypeClassification(
            has_results=True, paper_type="research",
            reason=f"classifier failed: {exc}", confidence=0.0,
        )

    # Low-confidence: don't hard-drop. Mark has_results=True so paper survives the filter.
    if result.confidence < 0.6:
        return PaperTypeClassification(
            has_results=True, paper_type=result.paper_type,
            reason=f"low-confidence ({result.confidence:.2f}); kept by default — {result.reason}",
            confidence=result.confidence,
        )
    return result


def _llm_classify(paper: Paper) -> PaperTypeClassification:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "results_filter")
    rendered = prompt.render(title=paper.title, abstract=paper.abstract[:3000])
    raw = cognitive_llm_call(task=rendered, domain="journal_results", stakes="low") or "{}"
    data = _json.loads(_strip_codefence(raw))
    return PaperTypeClassification(**data)


def _strip_codefence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/research_journal/test_results_filter.py -v
```
Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
git add research_journal/results_filter.py shared/prompts/templates/journal/results_filter.yaml tests/research_journal/test_results_filter.py
git commit -m "feat(papers/journal): results filter — has_results + paper_type"
```

---

### Task 11: Results-filter calibration fixture (40 papers)

**Files:**
- Create: `tests/fixtures/research_journal/results_calibration.json`

- [ ] **Step 1: Write the schema + 2-paper template**

```json
{
  "with_results": [
    {"arxiv_id": "2401.06401", "title": "Mixture-of-LoRAs", "abstract": "...evaluate on 8 benchmarks including MMLU, GSM8K, HumanEval, finding MoA improves over single-LoRA baselines by 4.2 points on average...", "expected_has_results": true, "expected_paper_type": "research"}
  ],
  "without_results": [
    {"arxiv_id": "2403.99999", "title": "Position: Why We Need Better Eval", "abstract": "We argue current LLM eval is fundamentally broken. We discuss four failure modes and outline a research agenda...", "expected_has_results": false, "expected_paper_type": "position"}
  ],
  "_NOTE_": "Implementation expands this to 20 with_results + 20 without_results from data/papers.db + manual hand-picks."
}
```

- [ ] **Step 2: Expand to 40 papers (~30 min)**

Mix of: 15 research papers with clear results, 5 surveys with benchmark numbers (still `with_results=true`); 8 position/opinion papers, 6 tutorials, 6 workshop summaries, 0 with empty abstracts.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/research_journal/results_calibration.json
git commit -m "test(papers/journal): results-filter calibration fixture"
```

---

### Task 12: Results-filter calibration test (LIVE) — must pass before merge

**Files:**
- Test: `tests/research_journal/test_results_calibration.py` (new, `@pytest.mark.live`)

- [ ] **Step 1: Write the live calibration test**

```python
# tests/research_journal/test_results_calibration.py
import json
from pathlib import Path
import pytest

from research_journal.results_filter import classify_results
from jobpulse.papers.models import Paper

CAL = Path(__file__).parent.parent / "fixtures" / "journal" / "results_calibration.json"


@pytest.mark.live
def test_results_filter_false_negative_rate():
    data = json.loads(CAL.read_text())
    false_negatives = 0  # papers WITH results that the filter incorrectly says don't have results
    total_with = 0
    false_positives = 0
    total_without = 0

    for entry in data["with_results"]:
        total_with += 1
        paper = _to_paper(entry)
        out = classify_results(paper)
        if not out.has_results:
            false_negatives += 1

    for entry in data["without_results"]:
        total_without += 1
        paper = _to_paper(entry)
        out = classify_results(paper)
        if out.has_results and out.confidence >= 0.6:
            false_positives += 1

    fnr = false_negatives / total_with
    fpr = false_positives / total_without
    assert fnr <= 0.10, f"false-negative rate {fnr:.2%} > 10% (papers with results misclassified)"
    assert fpr <= 0.20, f"false-positive rate {fpr:.2%} > 20%"


def _to_paper(entry: dict) -> Paper:
    return Paper(
        arxiv_id=entry["arxiv_id"], title=entry["title"], authors=["X"],
        abstract=entry["abstract"], categories=["cs.CL"],
        pdf_url="", arxiv_url="", published_at="2026-01-01",
    )
```

- [ ] **Step 2: Run live**

```bash
pytest tests/research_journal/test_results_calibration.py -v -m live
```
Expected: PASS. If FAIL, tune prompt in `shared/prompts/templates/journal/results_filter.yaml` until FNR ≤ 10% and FPR ≤ 20%.

- [ ] **Step 3: Commit (only after thresholds pass)**

```bash
git add tests/research_journal/test_results_calibration.py
git commit -m "test(papers/journal): live calibration for results filter (FNR ≤ 10%)"
```

---

## Phase 4 — Ranker extensions

### Task 13: Lab-track-record boost in `papers/ranker.py`

**Files:**
- Modify: `jobpulse/papers/ranker.py` (add `_lab_boost` + integrate into `fast_score`)
- Test: `tests/papers/test_ranker_lab_boost.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_ranker_lab_boost.py
import pytest
from jobpulse.papers.ranker import _lab_boost
from jobpulse.papers.models import Paper


def _paper(authors: list[str], affiliations: list[str] | None = None) -> Paper:
    p = Paper(arxiv_id="0", title="t", authors=authors, abstract="a",
              categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    if affiliations is not None:
        p.affiliations = affiliations  # type: ignore[attr-defined]
    return p


def test_no_recognized_lab_zero_boost():
    assert _lab_boost(_paper(["Random Researcher"], ["University of Foo"])) == 0.0


def test_one_recognized_lab_returns_05():
    p = _paper(["A", "B"], ["Some College", "Anthropic"])
    assert _lab_boost(p) == pytest.approx(0.5)


def test_first_author_recognized_returns_1():
    p = _paper(["A", "B"], ["DeepMind", "Some College"])
    assert _lab_boost(p) == pytest.approx(1.0)


def test_two_recognized_labs_returns_15():
    p = _paper(["A", "B"], ["Anthropic", "Stanford"])
    assert _lab_boost(p) == pytest.approx(1.5)


def test_fuzzy_match_handles_abbreviation():
    p = _paper(["A"], ["Meta AI Research, FAIR"])
    assert _lab_boost(p) >= 0.5  # FAIR matches via Levenshtein
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/papers/test_ranker_lab_boost.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add `Paper.affiliations` field**

In `jobpulse/papers/models.py`, add to `Paper`:

```python
    affiliations: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add `_lab_boost` to `jobpulse/papers/ranker.py`**

At the bottom of the file (or near `fast_score`):

```python
_RECOGNIZED_LABS: set[str] = {
    "Anthropic", "DeepMind", "Google Research", "Meta AI", "FAIR",
    "Mistral", "Qwen", "Alibaba", "DeepSeek", "Allen Institute for AI", "AI2",
    "HuggingFace", "Stanford", "Princeton", "MIT", "CMU", "Berkeley",
    "OpenAI", "Microsoft Research", "NVIDIA Research",
}


def _lab_boost(paper) -> float:
    affs = getattr(paper, "affiliations", None) or []
    if not affs:
        return 0.0
    matched_indices = [i for i, a in enumerate(affs) if _matches_recognized_lab(a)]
    if not matched_indices:
        return 0.0
    if len(matched_indices) >= 2:
        return 1.5
    if 0 in matched_indices or len(affs) - 1 in matched_indices:
        return 1.0
    return 0.5


def _matches_recognized_lab(affiliation: str) -> bool:
    a_lower = affiliation.lower()
    for lab in _RECOGNIZED_LABS:
        if lab.lower() in a_lower:
            return True
    # Levenshtein < 3 for short affiliations
    if len(affiliation) <= 30:
        for lab in _RECOGNIZED_LABS:
            if _levenshtein(affiliation.lower(), lab.lower()) < 3:
                return True
    return False


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]
```

In `fast_score()`, add the lab boost (find the line `score += cat_bonus` and after the existing bonuses, before the final `return`):

```python
    score += _lab_boost(paper)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/papers/test_ranker_lab_boost.py -v
```
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/ranker.py jobpulse/papers/models.py tests/papers/test_ranker_lab_boost.py
git commit -m "feat(papers/ranker): lab-track-record boost (Anthropic/DM/FAIR/...)"
```

---

### Task 14: Repo-activity boost (+1.0 if commit within 14 days)

**Files:**
- Modify: `jobpulse/papers/ranker.py` (add `_repo_activity_boost`)
- Test: `tests/papers/test_ranker_repo_boost.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_ranker_repo_boost.py
from datetime import datetime, timedelta, timezone
import pytest
from jobpulse.papers.ranker import _repo_activity_boost


def test_no_repo_url_zero():
    assert _repo_activity_boost(github_url="", last_commit_iso="") == 0.0


def test_recent_commit_full_boost(monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    assert _repo_activity_boost(github_url="https://github.com/x/y", last_commit_iso=recent) == 1.0


def test_old_commit_no_boost():
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert _repo_activity_boost(github_url="https://github.com/x/y", last_commit_iso=old) == 0.0
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/papers/test_ranker_repo_boost.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add `_repo_activity_boost`**

In `jobpulse/papers/ranker.py`:

```python
from datetime import datetime, timedelta, timezone


def _repo_activity_boost(github_url: str, last_commit_iso: str) -> float:
    if not github_url or not last_commit_iso:
        return 0.0
    try:
        last = datetime.fromisoformat(last_commit_iso.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    if datetime.now(timezone.utc) - last <= timedelta(days=14):
        return 1.0
    return 0.0
```

The integration into `fast_score()` happens during the verifier wiring (Task 20), where `last_commit_iso` is computed from the GitHub API. For now, the function exists and is unit-tested.

- [ ] **Step 4: Run + pass**

```bash
pytest tests/papers/test_ranker_repo_boost.py -v
```
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/ranker.py tests/papers/test_ranker_repo_boost.py
git commit -m "feat(papers/ranker): repo-activity boost (+1.0 if commit within 14 days)"
```

---

### Task 15: Paper-type de-boost (consume `PaperTypeClassification`)

**Files:**
- Modify: `jobpulse/papers/ranker.py` (add `_paper_type_deboost`)
- Test: `tests/papers/test_ranker_type_deboost.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_ranker_type_deboost.py
import pytest
from jobpulse.papers.ranker import _paper_type_deboost


@pytest.mark.parametrize("paper_type,expected", [
    ("research", 0.0),
    ("survey", -1.0),
    ("tutorial", -1.5),
    ("position", -2.0),
    ("workshop", -1.5),
    ("unknown", 0.0),
])
def test_paper_type_deboost_table(paper_type, expected):
    assert _paper_type_deboost(paper_type) == pytest.approx(expected)
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/papers/test_ranker_type_deboost.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add `_paper_type_deboost`**

In `jobpulse/papers/ranker.py`:

```python
_TYPE_DEBOOST: dict[str, float] = {
    "research": 0.0,
    "survey": -1.0,
    "tutorial": -1.5,
    "position": -2.0,
    "workshop": -1.5,
}


def _paper_type_deboost(paper_type: str) -> float:
    return _TYPE_DEBOOST.get(paper_type, 0.0)
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/papers/test_ranker_type_deboost.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/ranker.py tests/papers/test_ranker_type_deboost.py
git commit -m "feat(papers/ranker): paper-type de-boost (consume results_filter classification)"
```

---

### Task 16: LLM `rank_reason` per top-N pick

**Files:**
- Modify: `jobpulse/papers/ranker.py` (add `attach_rank_reasons`)
- Create: `shared/prompts/templates/journal/rank_reason.yaml`
- Test: `tests/papers/test_ranker_reasons.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_ranker_reasons.py
from jobpulse.papers.ranker import attach_rank_reasons
from jobpulse.papers.models import RankedPaper, FactCheckResult


def test_attach_reasons_calls_llm_per_paper(monkeypatch):
    monkeypatch.setattr(
        "jobpulse.papers.ranker._llm_rank_reason",
        lambda paper, lens: f"REASON FOR {paper.arxiv_id}",
    )
    papers = [_rp("a"), _rp("b")]
    out = attach_rank_reasons(papers, lens="daily")
    assert out[0].rank_reason == "REASON FOR a"
    assert out[1].rank_reason == "REASON FOR b"


def _rp(arxiv_id: str) -> RankedPaper:
    return RankedPaper(
        arxiv_id=arxiv_id, title=f"Paper {arxiv_id}", authors=["X"], abstract="a",
        categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01",
        impact_score=8.0, summary="s",
    )
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/papers/test_ranker_reasons.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the prompt template**

```yaml
# shared/prompts/templates/journal/rank_reason.yaml
domain: journal
name: rank_reason
version: "1.0.0"
system_prompt: |
  You are explaining why a paper ranked in the top-N for a daily research journal
  focused on LLM/SLM/VLM/finetuning. In ONE sentence (≤25 words), give a concrete
  reason citing: novelty, lab pedigree, working repo, benchmark gain, or community signal.
  Avoid generic praise like "interesting" or "important".

user_template: |
  Title: {title}
  Abstract: {abstract}
  Score: {score} (lab_boost={lab_boost}, repo_boost={repo_boost})
  Lens: {lens}

  One-sentence reason for the top-N pick:
output_schema:
  type: object
  properties:
    reason: {type: string, maxLength: 200}
  required: [reason]
```

- [ ] **Step 4: Add `attach_rank_reasons`**

Add to `jobpulse/papers/ranker.py` (and add `rank_reason: str = ""` to `RankedPaper` in `models.py` if not already present):

```python
def attach_rank_reasons(papers: list, lens: str = "daily") -> list:
    for p in papers:
        p.rank_reason = _llm_rank_reason(p, lens)
    return papers


def _llm_rank_reason(paper, lens: str) -> str:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt
    import json as _json

    prompt = get_prompt("journal", "rank_reason")
    rendered = prompt.render(
        title=paper.title, abstract=paper.abstract[:1500],
        score=getattr(paper, "impact_score", 0.0),
        lab_boost=_lab_boost(paper),
        repo_boost=_repo_activity_boost(getattr(paper, "github_url", ""),
                                         getattr(paper, "_last_commit_iso", "")),
        lens=lens,
    )
    raw = cognitive_llm_call(task=rendered, domain="journal_rank_reason", stakes="low") or "{}"
    try:
        return _json.loads(_strip_codefence(raw)).get("reason", "")[:200]
    except (ValueError, _json.JSONDecodeError):
        return ""


def _strip_codefence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()
```

In `models.py`, add to `RankedPaper`:

```python
    rank_reason: str = ""
```

- [ ] **Step 5: Run + pass**

```bash
pytest tests/papers/test_ranker_reasons.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/ranker.py jobpulse/papers/models.py shared/prompts/templates/journal/rank_reason.yaml tests/papers/test_ranker_reasons.py
git commit -m "feat(papers/ranker): per-pick LLM rank_reason"
```

---

## Phase 5 — Verification engine

### Task 17: `peer_reviewed` check

**Files:**
- Create: `research_journal/verifier.py`
- Test: `tests/research_journal/test_verifier_peer_review.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_verifier_peer_review.py
from research_journal.verifier import check_peer_reviewed


def test_neurips_venue_passes(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier.semantic_scholar_lookup",
        lambda arxiv_id: {"venue": "NeurIPS 2025", "is_peer_reviewed": True},
    )
    ok, reason = check_peer_reviewed("2401.06401")
    assert ok is True
    assert "NeurIPS" in reason


def test_arxiv_only_fails(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier.semantic_scholar_lookup",
        lambda arxiv_id: {"venue": "arXiv", "is_peer_reviewed": False},
    )
    ok, reason = check_peer_reviewed("2401.99999")
    assert ok is False


def test_s2_unavailable_returns_unknown(monkeypatch):
    monkeypatch.setattr("research_journal.verifier.semantic_scholar_lookup", lambda x: None)
    ok, reason = check_peer_reviewed("2401.99999")
    assert ok is None  # tri-state: True/False/None=unknown
    assert "unavailable" in reason.lower()
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_verifier_peer_review.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create `verifier.py` with `check_peer_reviewed`**

```python
# research_journal/verifier.py
"""Verification engine — composite badge of 5 checks."""

from __future__ import annotations

from typing import Optional

from shared.external_verifiers import semantic_scholar_lookup, PEER_REVIEWED_VENUES
from shared.logging_config import get_logger

logger = get_logger(__name__)


def check_peer_reviewed(arxiv_id: str) -> tuple[Optional[bool], str]:
    """Returns (True/False/None, reason). None = S2 unavailable."""
    data = semantic_scholar_lookup(arxiv_id)
    if data is None:
        return None, "Semantic Scholar unavailable"
    if data.get("is_peer_reviewed"):
        return True, f"venue: {data.get('venue', 'unknown')}"
    venue = (data.get("venue") or "").lower()
    if any(v in venue for v in PEER_REVIEWED_VENUES):
        return True, f"venue: {data.get('venue')}"
    return False, f"venue '{data.get('venue', 'arXiv')}' not in PEER_REVIEWED_VENUES"
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_verifier_peer_review.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/verifier.py tests/research_journal/test_verifier_peer_review.py
git commit -m "feat(papers/verifier): peer_reviewed check via Semantic Scholar"
```

---

### Task 18: `has_repo` check + GitHub cache (24h TTL)

**Files:**
- Modify: `research_journal/verifier.py` (add `check_has_repo` + `_RepoCache`)
- Test: `tests/research_journal/test_verifier_repo.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_verifier_repo.py
from pathlib import Path
import pytest
from research_journal.verifier import check_has_repo, _RepoCache


def test_passes_when_repo_active(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: {"stars": 320, "last_commit_iso": "2026-04-25T10:00:00Z"},
    )
    ok, reason, last_commit = check_has_repo("https://github.com/x/y", cache=cache)
    assert ok is True
    assert "320 stars" in reason


def test_fails_low_stars(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: {"stars": 3, "last_commit_iso": "2026-04-25T10:00:00Z"},
    )
    ok, reason, _ = check_has_repo("https://github.com/x/y", cache=cache)
    assert ok is False


def test_cache_hit_does_not_call_github(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    cache.set("https://github.com/x/y", {"stars": 100, "last_commit_iso": "2026-04-25T10:00:00Z"})
    calls = []
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: calls.append(url) or {},
    )
    check_has_repo("https://github.com/x/y", cache=cache)
    assert calls == []  # cache hit, no API call


def test_no_url_returns_false(tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    ok, reason, _ = check_has_repo("", cache=cache)
    assert ok is False


def test_api_failure_returns_unknown(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: (_ for _ in ()).throw(RuntimeError("rate limit")),
    )
    ok, reason, _ = check_has_repo("https://github.com/x/y", cache=cache)
    assert ok is None
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_verifier_repo.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement `_RepoCache` + `check_has_repo`**

Append to `research_journal/verifier.py`:

```python
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from jobpulse.config import DATA_DIR

_CACHE_TTL_SECONDS = 24 * 3600


class _RepoCache:
    """SQLite-backed 24h cache for GitHub repo metadata."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = DATA_DIR / "github_cache.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS repo_health "
                "(url TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER NOT NULL)"
            )

    def get(self, url: str) -> dict | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM repo_health WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        payload, fetched_at = row
        if time.time() - fetched_at > _CACHE_TTL_SECONDS:
            return None
        return json.loads(payload)

    def set(self, url: str, data: dict) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO repo_health (url, payload, fetched_at) VALUES (?, ?, ?)",
                (url, json.dumps(data), int(time.time())),
            )


_DEFAULT_CACHE: _RepoCache | None = None


def _get_default_cache() -> _RepoCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = _RepoCache()
    return _DEFAULT_CACHE


def check_has_repo(github_url: str, cache: _RepoCache | None = None) -> tuple[Optional[bool], str, str]:
    """Returns (True/False/None, reason, last_commit_iso)."""
    if not github_url:
        return False, "no repo URL", ""
    cache = cache or _get_default_cache()
    cached = cache.get(github_url)
    if cached is None:
        try:
            cached = _fetch_github_repo_meta(github_url)
            cache.set(github_url, cached)
        except Exception as exc:
            logger.warning("GitHub API failed for %s: %s", github_url, exc)
            return None, f"GitHub API error: {exc}", ""
    stars = cached.get("stars", 0)
    last_commit = cached.get("last_commit_iso", "")
    if stars < 10:
        return False, f"only {stars} stars", last_commit
    # Last-commit recency check is the ranker's job (_repo_activity_boost).
    # Verifier just confirms repo exists and has minimal community signal.
    return True, f"{stars} stars, last commit {last_commit[:10]}", last_commit


def _fetch_github_repo_meta(github_url: str) -> dict:
    """GET /repos/{owner}/{repo} via GitHub API (uses GITHUB_TOKEN if set)."""
    import httpx
    import os

    parts = github_url.rstrip("/").split("/")
    if "github.com" not in github_url or len(parts) < 5:
        raise ValueError(f"not a GitHub URL: {github_url}")
    owner, repo = parts[-2], parts[-1]
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
        r.raise_for_status()
        meta = r.json()
        return {
            "stars": meta.get("stargazers_count", 0),
            "last_commit_iso": meta.get("pushed_at", ""),
        }
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_verifier_repo.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/verifier.py tests/research_journal/test_verifier_repo.py
git commit -m "feat(papers/verifier): has_repo check with 24h SQLite cache"
```

---

### Task 19: `independent_citations` check

**Files:**
- Modify: `research_journal/verifier.py`
- Test: `tests/research_journal/test_verifier_citations.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_verifier_citations.py
from research_journal.verifier import check_independent_citations


def test_passes_with_3_independent(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier._fetch_citing_paper_labs",
        lambda arxiv_id, author_labs: ["LabA", "LabB", "LabC", "LabD"],
    )
    ok, reason = check_independent_citations("2401.06401", author_labs={"LabX"})
    assert ok is True


def test_fails_with_2(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier._fetch_citing_paper_labs",
        lambda arxiv_id, author_labs: ["LabA", "LabB"],
    )
    ok, _ = check_independent_citations("2401.99999", author_labs={"LabX"})
    assert ok is False


def test_s2_failure_returns_unknown(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier._fetch_citing_paper_labs",
        lambda arxiv_id, author_labs: (_ for _ in ()).throw(RuntimeError("S2 down")),
    )
    ok, _ = check_independent_citations("2401.99999", author_labs={"LabX"})
    assert ok is None
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_verifier_citations.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `research_journal/verifier.py`:

```python
def check_independent_citations(arxiv_id: str, author_labs: set[str]) -> tuple[Optional[bool], str]:
    """Returns (True/False/None, reason). True = ≥3 distinct labs ≠ author labs."""
    try:
        citing_labs = _fetch_citing_paper_labs(arxiv_id, author_labs)
    except Exception as exc:
        return None, f"S2 citations API failed: {exc}"
    distinct = set(citing_labs) - set(author_labs)
    if len(distinct) >= 3:
        return True, f"{len(distinct)} independent labs"
    return False, f"only {len(distinct)} independent labs"


def _fetch_citing_paper_labs(arxiv_id: str, author_labs: set[str]) -> list[str]:
    """Fetch S2 citations and extract author affiliations."""
    import httpx

    url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}/citations"
    params = {"fields": "authors.affiliations", "limit": 50}
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    labs: list[str] = []
    for citing in data.get("data", []):
        for author in citing.get("citingPaper", {}).get("authors", []):
            for aff in author.get("affiliations", []) or []:
                labs.append(aff)
    return labs
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_verifier_citations.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/verifier.py tests/research_journal/test_verifier_citations.py
git commit -m "feat(papers/verifier): independent_citations via S2 (≥3 distinct labs)"
```

---

### Task 20: Compose `verify_paper` → `VerificationBadge`

**Files:**
- Modify: `research_journal/verifier.py`
- Test: `tests/research_journal/test_verifier_compose.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_verifier_compose.py
from research_journal.verifier import verify_paper
from jobpulse.papers.models import Paper


def test_compose_aggregates_5_checks(monkeypatch):
    monkeypatch.setattr("research_journal.verifier.check_peer_reviewed",
                        lambda aid: (True, "NeurIPS"))
    monkeypatch.setattr("research_journal.verifier.check_has_repo",
                        lambda url, cache=None: (True, "320 stars", "2026-04-25T00:00:00Z"))
    monkeypatch.setattr("research_journal.verifier.check_independent_citations",
                        lambda aid, labs: (True, "5 labs"))

    paper = Paper(arxiv_id="2401.06401", title="t", authors=["A"], abstract="a",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01",
                  github_url="https://github.com/x/y")
    badge = verify_paper(paper, has_results=True)
    assert badge.score == 4   # has_results, peer_reviewed, has_repo, indep_citations (claims_grounded set later)
    assert badge.peer_reviewed is True
    assert badge.has_repo is True


def test_compose_handles_unknown(monkeypatch):
    """API-unavailable checks contribute False (not True), but stored in `reasons`."""
    monkeypatch.setattr("research_journal.verifier.check_peer_reviewed",
                        lambda aid: (None, "S2 down"))
    monkeypatch.setattr("research_journal.verifier.check_has_repo",
                        lambda url, cache=None: (None, "GitHub down", ""))
    monkeypatch.setattr("research_journal.verifier.check_independent_citations",
                        lambda aid, labs: (None, "S2 down"))

    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="a",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    badge = verify_paper(paper, has_results=True)
    assert badge.peer_reviewed is False
    assert "unknown" in badge.reasons["peer_reviewed"].lower() or "down" in badge.reasons["peer_reviewed"].lower()
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_verifier_compose.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement composer**

Append to `research_journal/verifier.py`:

```python
from jobpulse.papers.models import Paper
from research_journal.models import VerificationBadge


def verify_paper(paper: Paper, has_results: bool) -> VerificationBadge:
    """Compose 4 of the 5 badge checks. claims_grounded is set later by the hallucination guard."""
    pr_ok, pr_reason = check_peer_reviewed(paper.arxiv_id)
    repo_ok, repo_reason, last_commit = check_has_repo(getattr(paper, "github_url", ""))
    author_labs = set(getattr(paper, "affiliations", []) or [])
    cit_ok, cit_reason = check_independent_citations(paper.arxiv_id, author_labs)

    # Stash last_commit on the paper so the ranker's _repo_activity_boost can find it
    paper._last_commit_iso = last_commit  # type: ignore[attr-defined]

    return VerificationBadge(
        has_results=has_results,
        peer_reviewed=bool(pr_ok),
        has_repo=bool(repo_ok),
        independent_citations=bool(cit_ok),
        claims_grounded=False,   # filled by hallucination guard later
        reasons={
            "has_results": "passed §5.3 filter" if has_results else "no empirical results",
            "peer_reviewed": _tri_state_reason(pr_ok, pr_reason),
            "has_repo": _tri_state_reason(repo_ok, repo_reason),
            "independent_citations": _tri_state_reason(cit_ok, cit_reason),
            "claims_grounded": "pending hallucination guard",
        },
    )


def _tri_state_reason(ok: Optional[bool], reason: str) -> str:
    if ok is None:
        return f"unknown — {reason}"
    return reason
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_verifier_compose.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/verifier.py tests/research_journal/test_verifier_compose.py
git commit -m "feat(papers/verifier): compose VerificationBadge from 4 checks"
```

---

## Phase 6 — Summary writer (3-agent pipeline)

### Task 21: PDF download + Extractor agent

**Files:**
- Create: `research_journal/summarizer.py` (Extractor only — Writer + Guard in 22-26)
- Create: `shared/prompts/templates/journal/extract_facts.yaml`
- Test: `tests/research_journal/test_summarizer_extractor.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_summarizer_extractor.py
from research_journal.summarizer import extract_facts
from jobpulse.papers.models import Paper
from research_journal.models import ExtractedFacts, BenchResult


def test_extract_uses_abstract_when_pdf_fails(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer._download_pdf_text",
                        lambda url: "")
    monkeypatch.setattr(
        "research_journal.summarizer._llm_extract",
        lambda text: ExtractedFacts(
            problem="x", method_steps=["a", "b"],
            architecture_details={"backbone": "Llama-3-8B"},
            benchmarks=[BenchResult(name="MMLU", metric="acc", value=72.3)],
            ablations=["ablation 1"], limitations=["lim 1"],
            key_insight="k", raw_excerpts=["abstract excerpt"],
        ),
    )
    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="A meaty abstract.",
                  categories=["cs.CL"], pdf_url="https://example.com/x.pdf",
                  arxiv_url="", published_at="2026-01-01")
    facts = extract_facts(paper)
    assert facts.benchmarks[0].name == "MMLU"
    assert len(facts.raw_excerpts) >= 1


def test_extract_full_pdf_path(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer._download_pdf_text",
                        lambda url: "Full PDF text here. Lots of content.")
    captured_text = []
    def fake_extract(text):
        captured_text.append(text)
        return ExtractedFacts(
            problem="x", method_steps=["a"], architecture_details={},
            benchmarks=[], ablations=[], limitations=[], key_insight="k",
            raw_excerpts=["full text excerpt"],
        )
    monkeypatch.setattr("research_journal.summarizer._llm_extract", fake_extract)
    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="abs",
                  categories=["cs.CL"], pdf_url="https://example.com/x.pdf",
                  arxiv_url="", published_at="2026-01-01")
    extract_facts(paper)
    assert "Full PDF text" in captured_text[0]
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_summarizer_extractor.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the prompt template**

```yaml
# shared/prompts/templates/journal/extract_facts.yaml
domain: journal
name: extract_facts
version: "1.0.0"
system_prompt: |
  You are extracting structured facts from a research paper. Your output is consumed
  by an automated writer + hallucination guard, so:
    - Every benchmark number you report MUST appear verbatim in the source text.
    - The raw_excerpts list MUST contain 5-10 verbatim spans (≤200 chars each)
      that you used as evidence. The hallucination guard checks the writer's
      claims against these excerpts — if a claim isn't traceable here, it gets flagged.
    - Do NOT invent ablation names, architecture choices, or hyperparameters.
    - Use empty fields ([]/{}/"") when data is unavailable.
user_template: |
  PAPER TEXT (abstract or full PDF):
  ---
  {paper_text}
  ---

  Return a single JSON object matching this schema (no extra prose):
  {{
    "problem": "2-3 sentences",
    "method_steps": ["step 1", "step 2", ...],
    "architecture_details": {{"key": "value"}},
    "benchmarks": [{{"name": "MMLU", "metric": "accuracy", "value": 72.3, "baseline": 68.1}}],
    "ablations": ["bullet 1", "bullet 2"],
    "limitations": ["bullet 1"],
    "key_insight": "1 sentence",
    "raw_excerpts": ["verbatim span 1", "verbatim span 2", ...]
  }}
output_schema:
  type: object
  required: [problem, method_steps, architecture_details, benchmarks,
            ablations, limitations, key_insight, raw_excerpts]
```

- [ ] **Step 4: Implement Extractor**

```python
# research_journal/summarizer.py
"""3-agent journal summary pipeline: Extract → Write → Hallucination Guard."""

from __future__ import annotations

import json as _json
from typing import Optional

import httpx

from jobpulse.papers.models import Paper
from research_journal.models import BenchResult, ExtractedFacts, VerificationBadge
from shared.logging_config import get_logger

logger = get_logger(__name__)


def extract_facts(paper: Paper) -> ExtractedFacts:
    """Pull structured facts from the paper PDF (or abstract fallback)."""
    pdf_text = _download_pdf_text(paper.pdf_url) if paper.pdf_url else ""
    if not pdf_text:
        logger.info("PDF unavailable for %s; falling back to abstract", paper.arxiv_id)
        pdf_text = paper.abstract
    return _llm_extract(pdf_text[:30_000])  # cap to avoid context overflow


def _download_pdf_text(url: str) -> str:
    if not url:
        return ""
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            content = r.content
    except Exception as exc:
        logger.warning("PDF download failed for %s: %s", url, exc)
        return ""
    try:
        from pypdf import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages[:30])
    except Exception as exc:
        logger.warning("PDF parse failed for %s: %s", url, exc)
        return ""


def _llm_extract(paper_text: str) -> ExtractedFacts:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "extract_facts")
    rendered = prompt.render(paper_text=paper_text)
    raw = cognitive_llm_call(task=rendered, domain="journal_extract", stakes="medium") or "{}"
    data = _json.loads(_strip_codefence(raw))
    # Coerce benchmarks
    bench = [BenchResult(**b) if isinstance(b, dict) else b for b in data.get("benchmarks", [])]
    data["benchmarks"] = bench
    if not data.get("raw_excerpts"):
        data["raw_excerpts"] = ["[no excerpts extracted — guard will block ungrounded claims]"]
    return ExtractedFacts(**data)


def _strip_codefence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()
```

- [ ] **Step 5: Run + pass**

```bash
pytest tests/research_journal/test_summarizer_extractor.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research_journal/summarizer.py shared/prompts/templates/journal/extract_facts.yaml tests/research_journal/test_summarizer_extractor.py
git commit -m "feat(papers/journal): Extractor agent (PDF + abstract fallback)"
```

---

### Task 22: Writer agent (6-section structure)

**Files:**
- Modify: `research_journal/summarizer.py` (add `write_summary`)
- Create: `shared/prompts/templates/journal/write_summary.yaml`
- Test: `tests/research_journal/test_summarizer_writer.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_summarizer_writer.py
from research_journal.summarizer import write_summary
from research_journal.models import ExtractedFacts, BenchResult, Paper


def _facts() -> ExtractedFacts:
    return ExtractedFacts(
        problem="LLMs hallucinate.", method_steps=["s1", "s2"],
        architecture_details={"backbone": "Llama-3-8B"},
        benchmarks=[BenchResult(name="MMLU", metric="acc", value=72.3, baseline=68.1)],
        ablations=["ablation A"], limitations=["lim A"],
        key_insight="An insight", raw_excerpts=["excerpt 1"],
    )


def _paper() -> Paper:
    return Paper(arxiv_id="x", title="Title", authors=["A"], abstract="abs",
                 categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")


def test_writer_returns_six_sections(monkeypatch):
    monkeypatch.setattr(
        "research_journal.summarizer._llm_write",
        lambda paper, facts: (
            "## TL;DR\nA " + ("word " * 49) +
            "\n\n## Problem\n" + ("p " * 200) +
            "\n\n## Method\n" + ("m " * 450) +
            "\n\n## Key insight\n" + ("k " * 100) +
            "\n\n## Results\n" + ("r " * 350) +
            "\n\n## Limitations\n" + ("l " * 100)
        ),
    )
    md = write_summary(_paper(), _facts())
    for header in ("## TL;DR", "## Problem", "## Method", "## Key insight", "## Results", "## Limitations"):
        assert header in md


def test_writer_word_count_in_range(monkeypatch):
    monkeypatch.setattr(
        "research_journal.summarizer._llm_write",
        lambda p, f: "## TL;DR\n" + ("w " * 1200),  # too short - regen logic exercised
    )
    md = write_summary(_paper(), _facts(), max_attempts=1)
    # max_attempts=1 disables regen; just verifies it returns
    assert isinstance(md, str)
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_summarizer_writer.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the prompt template**

```yaml
# shared/prompts/templates/journal/write_summary.yaml
domain: journal
name: write_summary
version: "1.0.0"
system_prompt: |
  You are writing a 1100-1300 word technical summary of a research paper for a daily
  research journal. Strict rules:

  STRUCTURE (use these exact section headers, in this order):
    ## TL;DR             — 50 words, standalone hook
    ## Problem           — 200 words
    ## Method            — 400-500 words; describe architecture, training, hyperparameters
    ## Key insight       — 100 words
    ## Results           — 350 words; benchmark numbers, comparisons, ablations
    ## Limitations       — 100 words

  STYLE:
    - Factual, technical. NO editorializing. NO "why this matters to you" commentary.
    - Bullet lists allowed in Method and Results. Use specific numbers.
    - Never invent values not in the extracted facts.
user_template: |
  Paper: {title}
  Authors: {authors}

  EXTRACTED FACTS (use these as your source of truth; do not invent beyond them):
  {facts_json}

  Write the 6-section summary now in markdown.
output_schema:
  type: string
```

- [ ] **Step 4: Implement Writer**

Append to `research_journal/summarizer.py`:

```python
import re

_SECTION_HEADERS = ("TL;DR", "Problem", "Method", "Key insight", "Results", "Limitations")
_TARGETS = {"TL;DR": 50, "Problem": 200, "Method": 450, "Key insight": 100, "Results": 350, "Limitations": 100}
_TOLERANCE = 0.25


def write_summary(paper: Paper, facts: ExtractedFacts, max_attempts: int = 2) -> str:
    """Generate 1100-1300w summary with one regen on word-count violation."""
    last_md = ""
    for attempt in range(max_attempts):
        last_md = _llm_write(paper, facts)
        if _word_count_compliant(last_md):
            return last_md
        logger.info("write_summary attempt %d failed word-count check; retrying", attempt + 1)
    logger.warning("write_summary accepted off-target output after %d attempts", max_attempts)
    return last_md


def _llm_write(paper: Paper, facts: ExtractedFacts) -> str:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "write_summary")
    rendered = prompt.render(
        title=paper.title,
        authors=", ".join(paper.authors[:5]),
        facts_json=facts.model_dump_json(indent=2),
    )
    return cognitive_llm_call(task=rendered, domain="journal_write", stakes="medium") or ""


def _word_count_compliant(md: str) -> bool:
    sections = _split_sections(md)
    for header, target in _TARGETS.items():
        words = len(sections.get(header, "").split())
        lo, hi = int(target * (1 - _TOLERANCE)), int(target * (1 + _TOLERANCE))
        if not (lo <= words <= hi):
            return False
    return True


def _split_sections(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and m.group(1).strip() in _SECTION_HEADERS:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out
```

- [ ] **Step 5: Run + pass**

```bash
pytest tests/research_journal/test_summarizer_writer.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research_journal/summarizer.py shared/prompts/templates/journal/write_summary.yaml tests/research_journal/test_summarizer_writer.py
git commit -m "feat(papers/journal): Writer agent with 6-section structure + word-count regen"
```

---

### Task 23: Hallucination guard — claim extraction

**Files:**
- Modify: `research_journal/summarizer.py`
- Create: `shared/prompts/templates/journal/extract_claims.yaml`
- Test: `tests/research_journal/test_summarizer_claims.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_summarizer_claims.py
from research_journal.summarizer import extract_claims_from_summary


def test_extracts_numeric_sentences(monkeypatch):
    monkeypatch.setattr(
        "research_journal.summarizer._llm_extract_claims",
        lambda md: [
            "MoA improves over single-LoRA baselines by 4.2 points on MMLU.",
            "Training takes 12 GPU-hours on 8× A100.",
        ],
    )
    md = "## Results\nMoA improves... Training takes 12 GPU-hours..."
    claims = extract_claims_from_summary(md)
    assert len(claims) == 2
    assert any("MMLU" in c for c in claims)
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_summarizer_claims.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the prompt template**

```yaml
# shared/prompts/templates/journal/extract_claims.yaml
domain: journal
name: extract_claims
version: "1.0.0"
system_prompt: |
  Return ALL sentences from the markdown that contain at least one of:
    - a number, percentage, or numeric range
    - a benchmark name (MMLU, HumanEval, GSM8K, MT-Bench, ...)
    - a specific architectural choice (model name, layer count, parameter count)
  Output: a JSON array of strings (no extra prose).
user_template: |
  MARKDOWN:
  {summary_md}
output_schema:
  type: array
  items: {type: string}
```

- [ ] **Step 4: Implement claim extraction**

Append to `research_journal/summarizer.py`:

```python
def extract_claims_from_summary(summary_md: str) -> list[str]:
    return _llm_extract_claims(summary_md)


def _llm_extract_claims(summary_md: str) -> list[str]:
    from shared.agents import cognitive_llm_call
    from shared.prompts import get_prompt

    prompt = get_prompt("journal", "extract_claims")
    raw = cognitive_llm_call(
        task=prompt.render(summary_md=summary_md[:12_000]),
        domain="journal_claims", stakes="low",
    ) or "[]"
    try:
        claims = _json.loads(_strip_codefence(raw))
        return [str(c) for c in claims if isinstance(c, str)]
    except (ValueError, _json.JSONDecodeError):
        logger.warning("claim extraction returned invalid JSON; treating as no claims")
        return []
```

- [ ] **Step 5: Run + pass**

```bash
pytest tests/research_journal/test_summarizer_claims.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research_journal/summarizer.py shared/prompts/templates/journal/extract_claims.yaml tests/research_journal/test_summarizer_claims.py
git commit -m "feat(papers/journal): hallucination guard — claim extraction"
```

---

### Task 24: Hallucination guard — deterministic grounding

**Files:**
- Modify: `research_journal/summarizer.py`
- Test: `tests/research_journal/test_summarizer_grounding.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_summarizer_grounding.py
from research_journal.summarizer import is_claim_grounded
from research_journal.models import ExtractedFacts, BenchResult


def _facts(excerpts: list[str], benches: list[BenchResult] | None = None) -> ExtractedFacts:
    return ExtractedFacts(
        problem="x", method_steps=["s"], architecture_details={},
        benchmarks=benches or [], ablations=[], limitations=[],
        key_insight="k", raw_excerpts=excerpts,
    )


def test_substring_match_grounds():
    facts = _facts(["the model achieves 72.3% on MMLU and 84.1 F1 on BoolQ"])
    assert is_claim_grounded("MoA achieves 72.3% on MMLU.", facts) is True


def test_numeric_match_in_benchmark():
    facts = _facts(
        excerpts=["table not in excerpts"],
        benches=[BenchResult(name="GSM8K", metric="acc", value=89.7)],
    )
    assert is_claim_grounded("On GSM8K, accuracy reaches 89.7%.", facts) is True


def test_unrelated_claim_fails():
    facts = _facts(["totally different content"])
    # Without an embedder hook, embedding similarity is rejected — check the deterministic
    # paths (substring + numeric extraction) only here.
    assert is_claim_grounded("Trained on 1.5T tokens.", facts) is False


def test_embedding_similarity_threshold(monkeypatch):
    facts = _facts(["paraphrased version"])
    monkeypatch.setattr(
        "research_journal.summarizer._embedding_similarity",
        lambda a, b: 0.91,
    )
    assert is_claim_grounded("Different wording but same meaning.", facts) is True
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_summarizer_grounding.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement grounding**

Append to `research_journal/summarizer.py`:

```python
import re as _re

_NUMBER_RE = _re.compile(r"-?\d+(?:\.\d+)?")
_EMBEDDING_THRESHOLD = 0.85


def is_claim_grounded(claim: str, facts: ExtractedFacts) -> bool:
    norm_claim = _normalize(claim)
    excerpts_norm = [_normalize(e) for e in facts.raw_excerpts]

    # Tier 1: substring containment
    for ex in excerpts_norm:
        if norm_claim in ex or _significant_overlap(norm_claim, ex):
            return True

    # Tier 2: numeric match against benchmark values OR excerpts
    nums_in_claim = {float(m) for m in _NUMBER_RE.findall(claim)}
    if nums_in_claim:
        bench_values = {b.value for b in facts.benchmarks}
        if nums_in_claim & bench_values:
            return True
        for ex in facts.raw_excerpts:
            ex_nums = {float(m) for m in _NUMBER_RE.findall(ex)}
            if nums_in_claim & ex_nums:
                return True

    # Tier 3: embedding similarity
    for ex in facts.raw_excerpts:
        if _embedding_similarity(claim, ex) >= _EMBEDDING_THRESHOLD:
            return True
    return False


def _normalize(text: str) -> str:
    return _re.sub(r"\s+", " ", text.lower().strip())


def _significant_overlap(claim: str, excerpt: str) -> bool:
    """At least 60% of claim's content words appear in excerpt."""
    claim_words = {w for w in claim.split() if len(w) > 4}
    if len(claim_words) < 3:
        return False
    return len(claim_words & set(excerpt.split())) / len(claim_words) >= 0.6


def _embedding_similarity(a: str, b: str) -> float:
    try:
        from shared.memory_layer._embedder import embed_text
        import numpy as np
        va = np.asarray(embed_text(a), dtype=float)
        vb = np.asarray(embed_text(b), dtype=float)
        denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
        return float(np.dot(va, vb) / denom)
    except Exception as exc:
        logger.warning("embedding similarity failed: %s", exc)
        return 0.0
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_summarizer_grounding.py -v
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add research_journal/summarizer.py tests/research_journal/test_summarizer_grounding.py
git commit -m "feat(papers/journal): hallucination guard — deterministic grounding (3-tier)"
```

---

### Task 25: Hallucination guard — regen loop + `claims_grounded` integration

**Files:**
- Modify: `research_journal/summarizer.py` (add `guard_summary` + `summarize_paper` orchestrator)
- Test: `tests/research_journal/test_summarizer_guard_loop.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_summarizer_guard_loop.py
from research_journal.summarizer import guard_summary, summarize_paper
from jobpulse.papers.models import Paper
from research_journal.models import ExtractedFacts, BenchResult, VerificationBadge


def _facts() -> ExtractedFacts:
    return ExtractedFacts(
        problem="x", method_steps=["s"], architecture_details={},
        benchmarks=[BenchResult(name="MMLU", metric="acc", value=72.3)],
        ablations=[], limitations=[], key_insight="k",
        raw_excerpts=["the model achieves 72.3 on MMLU"],
    )


def test_guard_passes_when_all_grounded(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer.extract_claims_from_summary",
                        lambda md: ["the model achieves 72.3 on MMLU"])
    md = "## Results\nThe model achieves 72.3 on MMLU."
    grounded, failed = guard_summary(md, _facts())
    assert grounded is True
    assert failed == []


def test_guard_flags_ungrounded_claims(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer.extract_claims_from_summary",
                        lambda md: ["Trained on 1.5T tokens.", "Achieves 72.3 on MMLU."])
    md = "fake summary"
    grounded, failed = guard_summary(md, _facts())
    assert grounded is False
    assert "1.5T tokens" in failed[0]


def test_summarize_paper_regenerates_then_accepts(monkeypatch):
    """If the guard fails the first time, writer is called again with avoid hints."""
    write_calls = []
    def fake_write(paper, facts, avoid=None):
        write_calls.append(avoid or [])
        return "v" + str(len(write_calls))
    monkeypatch.setattr("research_journal.summarizer._write_with_avoid", fake_write)
    monkeypatch.setattr("research_journal.summarizer.extract_facts", lambda p: _facts())
    monkeypatch.setattr(
        "research_journal.summarizer.extract_claims_from_summary",
        lambda md: ["Trained on 1.5T tokens."],
    )
    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="abs",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    summary, claims_grounded = summarize_paper(paper)
    assert len(write_calls) == 2  # initial + 1 regen
    assert claims_grounded is False  # second attempt also fails
    assert summary == "v2"
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_summarizer_guard_loop.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement orchestrator**

Append to `research_journal/summarizer.py`:

```python
def guard_summary(summary_md: str, facts: ExtractedFacts, sample_size: int = 5) -> tuple[bool, list[str]]:
    """Return (all_grounded, list_of_failed_claims)."""
    claims = extract_claims_from_summary(summary_md)
    if not claims:
        return True, []
    sample = claims[:sample_size] if len(claims) > sample_size else claims
    failed = [c for c in sample if not is_claim_grounded(c, facts)]
    # Spec: >1 fails grounding → fail
    return (len(failed) <= 1), failed


def _write_with_avoid(paper: Paper, facts: ExtractedFacts, avoid: list[str] | None = None) -> str:
    """Wrapper around write_summary that injects 'avoid these patterns' on regen."""
    if not avoid:
        return write_summary(paper, facts)
    avoid_block = "\n\nAVOID THESE UNGROUNDED PATTERNS:\n- " + "\n- ".join(avoid)
    # Hack: append the avoid_block to the facts.problem to force the writer to see it.
    # (Cleaner alternative: pass through prompt; this keeps the existing prompt unchanged.)
    facts2 = facts.model_copy(update={"problem": facts.problem + avoid_block})
    return write_summary(paper, facts2)


def summarize_paper(paper: Paper, max_regens: int = 1) -> tuple[str, bool]:
    """End-to-end: extract → write → guard (with one regen on failure).

    Returns (markdown_summary, claims_grounded).
    """
    facts = extract_facts(paper)
    summary = _write_with_avoid(paper, facts)
    grounded, failed = guard_summary(summary, facts)
    attempts = 1
    while not grounded and attempts <= max_regens:
        logger.info("hallucination guard failed for %s (attempts=%d); regenerating with %d avoid hints",
                    paper.arxiv_id, attempts, len(failed))
        summary = _write_with_avoid(paper, facts, avoid=failed)
        grounded, failed = guard_summary(summary, facts)
        attempts += 1
    if not grounded:
        logger.error("hallucination guard failed twice for %s; publishing with claims_grounded=False",
                     paper.arxiv_id)
    return summary, grounded
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_summarizer_guard_loop.py -v
```
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add research_journal/summarizer.py tests/research_journal/test_summarizer_guard_loop.py
git commit -m "feat(papers/journal): hallucination guard regen loop + summarize_paper orchestrator"
```

---

## Phase 7 — Source ingest

### Task 26: OpenReview fetcher

**Files:**
- Modify: `jobpulse/papers/fetcher.py` (add `_fetch_openreview`)
- Test: `tests/papers/test_fetcher_openreview.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_fetcher_openreview.py
import pytest
import respx
import httpx
from jobpulse.papers.fetcher import PaperFetcher


@pytest.mark.asyncio
async def test_openreview_returns_papers(monkeypatch):
    fake = {
        "notes": [
            {
                "id": "abc", "content": {
                    "title": {"value": "ICLR Paper Foo"},
                    "abstract": {"value": "We propose..."},
                    "authors": {"value": ["A", "B"]},
                    "pdf": {"value": "/pdf?id=abc"},
                },
                "cdate": 1714000000000,
            }
        ]
    }
    async def fake_get(self, url, **kwargs):
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return fake
        return _R()
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    fetcher = PaperFetcher()
    papers = await fetcher._fetch_openreview()
    assert len(papers) >= 1
    assert papers[0].title == "ICLR Paper Foo"


@pytest.mark.asyncio
async def test_openreview_failure_returns_empty(monkeypatch):
    async def fake_get(self, url, **kwargs):
        raise httpx.HTTPError("rate limit")
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    fetcher = PaperFetcher()
    papers = await fetcher._fetch_openreview()
    assert papers == []  # graceful degrade
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/papers/test_fetcher_openreview.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add `_fetch_openreview`**

Append a method to `PaperFetcher` in `jobpulse/papers/fetcher.py`:

```python
    async def _fetch_openreview(self, venues: tuple[str, ...] = ("ICLR.cc/2026/Conference",
                                                                   "NeurIPS.cc/2025/Conference",
                                                                   "COLM.cc/2025")) -> list[Paper]:
        """Fetch accepted papers from OpenReview. Best-effort; returns [] on failure."""
        out: list[Paper] = []
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": _USER_AGENT}) as client:
            for venue in venues:
                try:
                    r = await client.get(
                        "https://api2.openreview.net/notes",
                        params={"content.venueid": venue, "limit": 50},
                    )
                    r.raise_for_status()
                    data = r.json()
                    for note in data.get("notes", []):
                        c = note.get("content", {})
                        title = (c.get("title") or {}).get("value", "")
                        abstract = (c.get("abstract") or {}).get("value", "")
                        authors = (c.get("authors") or {}).get("value", []) or []
                        pdf = (c.get("pdf") or {}).get("value", "")
                        cdate = note.get("cdate", 0)
                        published_at = datetime.fromtimestamp(cdate / 1000, tz=timezone.utc).date().isoformat() if cdate else ""
                        if title and abstract:
                            out.append(Paper(
                                arxiv_id=note.get("id", "openreview-" + note.get("number", "")),
                                title=title, abstract=abstract, authors=authors,
                                categories=[venue.split(".")[0]],
                                pdf_url=f"https://openreview.net{pdf}" if pdf else "",
                                arxiv_url=f"https://openreview.net/forum?id={note.get('id', '')}",
                                published_at=published_at, source="arxiv",
                            ))
                except Exception as exc:
                    logger.warning("OpenReview fetch failed for %s: %s", venue, exc)
        return out
```

(Add `from datetime import datetime, timezone` to the existing imports if not present.)

- [ ] **Step 4: Wire into `fetch_all`**

In `PaperFetcher.fetch_all`, add `self._fetch_openreview()` to the `asyncio.gather(...)` call and concatenate results into `all_papers`.

- [ ] **Step 5: Run + pass**

```bash
pytest tests/papers/test_fetcher_openreview.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher_openreview.py
git commit -m "feat(papers/fetcher): OpenReview ingestion (ICLR/NeurIPS/COLM, best-effort)"
```

---

### Task 27: Drop noisy sources from default `fetch_all`

**Files:**
- Modify: `jobpulse/papers/fetcher.py`
- Test: `tests/papers/test_fetcher_quiet.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/papers/test_fetcher_quiet.py
import pytest
from jobpulse.papers.fetcher import PaperFetcher


@pytest.mark.asyncio
async def test_default_fetch_excludes_reddit_hn_bluesky(monkeypatch):
    called: list[str] = []

    async def _stub_method(name):
        async def fn(*a, **k):
            called.append(name)
            return []
        return fn

    fetcher = PaperFetcher()
    monkeypatch.setattr(fetcher, "_fetch_arxiv", await _stub_method("arxiv"))
    monkeypatch.setattr(fetcher, "_fetch_huggingface", await _stub_method("hf"))
    monkeypatch.setattr(fetcher, "_fetch_s2_trending", await _stub_method("s2"))
    monkeypatch.setattr(fetcher, "_fetch_openreview", await _stub_method("openreview"))
    # These should NOT be called by default in v1
    monkeypatch.setattr(fetcher, "_fetch_hackernews", await _stub_method("hn"))
    monkeypatch.setattr(fetcher, "_fetch_reddit", await _stub_method("reddit"))
    monkeypatch.setattr(fetcher, "_fetch_bluesky", await _stub_method("bsky"))

    await fetcher.fetch_all()
    assert "arxiv" in called and "hf" in called and "openreview" in called
    assert "hn" not in called and "reddit" not in called and "bsky" not in called
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/papers/test_fetcher_quiet.py -v
```
Expected: FAIL — all 6 sources are still called by default.

- [ ] **Step 3: Edit `fetch_all`**

In `jobpulse/papers/fetcher.py::PaperFetcher.fetch_all`, replace the existing `asyncio.gather` to include only `_fetch_arxiv`, `_fetch_huggingface`, `_fetch_s2_trending`, `_fetch_openreview`. Add an opt-in flag `include_community: bool = False` that re-enables HN/Reddit/Bluesky for the legacy `daily_digest()` codepath.

```python
    async def fetch_all(self, max_results: int = 50, include_community: bool = False) -> list[Paper]:
        core_tasks = [
            self._fetch_arxiv(max_results=max_results),
            self._fetch_huggingface(),
            self._fetch_s2_trending(),
            self._fetch_openreview(),
        ]
        results = await asyncio.gather(*core_tasks)
        all_papers = [p for sublist in results for p in sublist]
        if include_community:
            comm = await asyncio.gather(self._fetch_hackernews(), self._fetch_reddit(), self._fetch_bluesky())
            all_papers += [p for sublist in comm for p in sublist]
        merged = self._deduplicate_and_merge_all(all_papers)
        # … existing tier-3 fallback unchanged
        return merged
```

The existing `daily_digest` callers can pass `include_community=True` to retain prior behavior; the new `daily_journal()` (Task 30) leaves it `False`.

- [ ] **Step 4: Run + pass**

```bash
pytest tests/papers/test_fetcher_quiet.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/papers/fetcher.py tests/papers/test_fetcher_quiet.py
git commit -m "feat(papers/fetcher): default fetch_all excludes HN/Reddit/Bluesky (opt-in via include_community)"
```

---

## Phase 8 — Delivery

### Task 28: Notion delivery — `research_journal/delivery.py` (free function)

**Files:**
- Create: `research_journal/delivery.py` (NEW — journal-specific delivery composition)
- Test: `tests/research_journal/test_delivery.py` (new)

**Note:** Per the directory split, journal-specific Notion + Telegram composition lives in `research_journal/delivery.py`. The existing `jobpulse/papers/notion_publisher.py::NotionPublisher` provides only generic Notion-client primitives and is consumed via composition (we use `publisher.client` + `publisher.database_id`).

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_delivery.py
from unittest.mock import MagicMock
from jobpulse.papers.notion_publisher import NotionPublisher
from jobpulse.papers.models import RankedPaper
from research_journal.models import VerificationBadge
from research_journal.delivery import publish_journal_to_notion


def test_publish_journal_creates_one_page_per_paper():
    pub = NotionPublisher(database_id="db-123", api_key="k")
    pub.client = MagicMock()
    pub.client.pages.create.return_value = {"id": "page-x"}

    paper = RankedPaper(
        arxiv_id="x", title="Title", authors=["A"], abstract="abs",
        categories=["cs.CL"], pdf_url="", arxiv_url="https://arxiv.org/abs/x",
        published_at="2026-01-01", impact_score=8.0, summary="s",
    )
    badge = VerificationBadge(
        has_results=True, peer_reviewed=True, has_repo=True,
        independent_citations=False, claims_grounded=True,
    )

    publish_journal_to_notion(
        publisher=pub,
        items=[(paper, badge, "## TL;DR\nfoo\n\n## Problem\nbar\n", "core")],
        digest_date="2026-05-09",
    )
    assert pub.client.pages.create.call_count == 1
    args, kwargs = pub.client.pages.create.call_args
    props = kwargs["properties"]
    assert props["Title"]["title"][0]["text"]["content"] == "Title"
    assert props["Domain tag"]["select"]["name"] == "core"
    assert props["Badge"]["number"] == 4
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_delivery.py -v
```
Expected: FAIL — `research_journal.delivery` doesn't exist.

- [ ] **Step 3: Create `research_journal/delivery.py`**

```python
"""Journal-specific delivery: Notion page composition + Telegram digest builder.

Uses jobpulse.papers.notion_publisher.NotionPublisher for raw Notion-client primitives
(authentication, page-create) and composes journal-specific properties / blocks here.
"""

from __future__ import annotations

from jobpulse.papers.models import RankedPaper
from jobpulse.papers.notion_publisher import NotionPublisher
from research_journal.models import VerificationBadge
from shared.logging_config import get_logger

logger = get_logger(__name__)


def publish_journal_to_notion(
    publisher: NotionPublisher,
    items: list[tuple],   # (RankedPaper, VerificationBadge, summary_md, domain_tag)
    digest_date: str,
) -> list[str]:
    """Create one Notion page per item in the Journal database. Returns page IDs."""
    page_ids: list[str] = []
    for paper, badge, summary_md, domain_tag in items:
        props = {
            "Title": {"title": [{"text": {"content": paper.title[:200]}}]},
            "Date": {"date": {"start": digest_date}},
            "Domain tag": {"select": {"name": domain_tag}},
            "Badge": {"number": badge.score},
            "Badge breakdown": {"multi_select": [
                {"name": k} for k, v in {
                    "has_results": badge.has_results,
                    "peer_reviewed": badge.peer_reviewed,
                    "has_repo": badge.has_repo,
                    "independent_citations": badge.independent_citations,
                    "claims_grounded": badge.claims_grounded,
                }.items() if v
            ]},
            "Rank reason": {"rich_text": [{"text": {"content": getattr(paper, "rank_reason", "")[:2000]}}]},
            "Authors": {"rich_text": [{"text": {"content": ", ".join(paper.authors[:8])[:2000]}}]},
            "arXiv link": {"url": paper.arxiv_url or None},
            "Repo link": {"url": getattr(paper, "github_url", "") or None},
            "Read": {"checkbox": False},
            "Saved for impl": {"checkbox": False},
        }
        children = _summary_to_blocks(summary_md)
        try:
            resp = publisher.client.pages.create(
                parent={"database_id": publisher.database_id},
                properties=props,
                children=children,
            )
            page_ids.append(resp["id"])
        except Exception as exc:
            logger.warning("Notion page create failed for %s: %s", paper.arxiv_id, exc)
    return page_ids


def _summary_to_blocks(md: str) -> list[dict]:
    """Convert the 6-section markdown summary into Notion heading_2 + paragraph blocks."""
    blocks: list[dict] = []
    for line in md.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
            })
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:1900]}}]},
            })
    return blocks
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_delivery.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/delivery.py tests/research_journal/test_delivery.py
git commit -m "feat(research_journal/delivery): publish_journal_to_notion (free function)"
```

---

### Task 29: Telegram morning digest builder — same `research_journal/delivery.py`

**Files:**
- Modify: `research_journal/delivery.py` (extend with `build_journal_telegram_digest`)
- Test: `tests/research_journal/test_delivery_telegram.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_delivery_telegram.py
from research_journal.delivery import build_journal_telegram_digest
from jobpulse.papers.models import RankedPaper
from research_journal.models import VerificationBadge


def _rp(arxiv_id: str, title: str, score: float = 8.0) -> RankedPaper:
    return RankedPaper(
        arxiv_id=arxiv_id, title=title, authors=["A"], abstract="abs",
        categories=["cs.CL"], pdf_url="", arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-01-01", impact_score=score, summary="",
        rank_reason="novel attention pattern, working repo with 1.2k stars",
    )


def test_digest_lists_core_only_in_main_body():
    badge = VerificationBadge(has_results=True, peer_reviewed=True, has_repo=True,
                              independent_citations=True, claims_grounded=True)
    items = [
        (_rp("x", "Core Paper A"), badge, "core"),
        (_rp("y", "Core Paper B"), badge, "core"),
        (_rp("z", "Tangent Paper"), badge, "tangent"),
    ]
    msg = build_journal_telegram_digest(items, page_url_for=lambda aid: f"https://notion.so/{aid}")
    # Core papers in body, tangent collapsed
    assert "Core Paper A" in msg
    assert "Core Paper B" in msg
    assert "Tangent Paper" not in msg.split("+ 1 tangent")[0]
    assert "tangent" in msg.lower()


def test_digest_uses_emoji_badges():
    badge = VerificationBadge(has_results=True, peer_reviewed=False, has_repo=True,
                              independent_citations=False, claims_grounded=True)
    items = [(_rp("x", "X"), badge, "core")]
    msg = build_journal_telegram_digest(items, page_url_for=lambda aid: "u")
    assert "3/5" in msg or "🟢🟢🟢" in msg
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_delivery_telegram.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add the builder to `research_journal/delivery.py`**

Append to `research_journal/delivery.py` (created in Task 28):

```python
def build_journal_telegram_digest(
    items: list[tuple],   # (RankedPaper, VerificationBadge, domain_tag)
    page_url_for,         # callable: arxiv_id -> Notion page URL
) -> str:
    core = [i for i in items if i[2] == "core"]
    tangent = [i for i in items if i[2] == "tangent"]

    lines = [f"🧪 Daily Research Journal — {len(core)} papers ({len(tangent)} tangent)\n"]
    for paper, badge, _ in core:
        emoji = "🟢" * badge.score + "⚪" * (5 - badge.score)
        url = page_url_for(paper.arxiv_id)
        reason = (paper.rank_reason or "").strip()
        lines.append(f"{emoji} {badge.score}/5 — {paper.title}\n  {reason[:120]}\n  → {url}\n")
    if tangent:
        lines.append(f"\n+ {len(tangent)} tangent papers in Notion\n")
    return "\n".join(lines)
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_delivery_telegram.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/delivery.py tests/research_journal/test_delivery_telegram.py
git commit -m "feat(research_journal/delivery): Telegram digest builder"
```

---

## Phase 9 — Wiring

### Task 30: `JournalPipeline` orchestrator in `research_journal/pipeline.py`

**Files:**
- Create: `research_journal/pipeline.py` (NEW — owns the daily orchestration)
- Modify: `research_journal/__init__.py` (export `JournalPipeline`)
- Test: `tests/research_journal/test_pipeline.py` (new)

**Note:** This is where the feature stops being a JobPulse extension and becomes its own self-contained product. `JournalPipeline` composes `PaperFetcher`, `PaperStore`, `NotionPublisher` from `jobpulse.papers` (cross-cutting paper-domain utilities) with the journal-specific modules (`domain_filter`, `results_filter`, `verifier`, `summarizer`, `delivery`).

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_pipeline.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from jobpulse.papers.models import Paper
from research_journal.models import VerificationBadge, PaperTypeClassification
from research_journal.pipeline import JournalPipeline


@pytest.mark.asyncio
async def test_daily_journal_end_to_end(monkeypatch, tmp_path):
    # Stub each step
    fake_papers = [Paper(arxiv_id=str(i), title=f"P{i}", authors=["A"], abstract="abs",
                         categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
                   for i in range(15)]
    pipeline = JournalPipeline(db_path=tmp_path / "p.db")
    pipeline.fetcher.fetch_all = AsyncMock(return_value=fake_papers)
    pipeline.fetcher.enrich = AsyncMock(return_value=fake_papers)
    monkeypatch.setattr("research_journal.pipeline.classify_domain",
                        lambda p: ("core", 0.8, "matched") if int(p.arxiv_id) < 12 else ("tangent", 0.7, "tangent"))
    monkeypatch.setattr("research_journal.pipeline.classify_results",
                        lambda p: PaperTypeClassification(has_results=True, paper_type="research",
                                                          reason="ok", confidence=0.9))
    monkeypatch.setattr("research_journal.pipeline.summarize_paper",
                        lambda p: ("## TL;DR\nshort\n## Problem\nx\n## Method\ny\n## Key insight\nz\n## Results\nr\n## Limitations\nl", True))
    monkeypatch.setattr("research_journal.pipeline.verify_paper",
                        lambda p, has_results: VerificationBadge(
                            has_results=has_results, peer_reviewed=False, has_repo=False,
                            independent_citations=False, claims_grounded=False,
                        ))
    monkeypatch.setattr("research_journal.pipeline.publish_journal_to_notion",
                        lambda **kw: ["fake-id"])
    monkeypatch.setattr("research_journal.pipeline.send_telegram_message", lambda msg: None)

    result = await pipeline.daily_journal()
    assert result["core_count"] >= 8
    assert result["tangent_count"] >= 1
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_pipeline.py -v
```
Expected: FAIL — `JournalPipeline` doesn't exist.

- [ ] **Step 3: Create `research_journal/pipeline.py`**

```python
"""JournalPipeline — daily orchestrator for the curated research journal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jobpulse.papers.fetcher import PaperFetcher
from jobpulse.papers.models import Paper
from jobpulse.papers.notion_publisher import NotionPublisher
from jobpulse.papers.ranker import attach_rank_reasons
from jobpulse.papers.store import PaperStore
from jobpulse.papers import PaperRanker
from research_journal.delivery import publish_journal_to_notion, build_journal_telegram_digest
from research_journal.domain_filter import classify_domain
from research_journal.results_filter import classify_results
from research_journal.summarizer import summarize_paper
from research_journal.verifier import verify_paper
from shared.logging_config import get_logger
from shared.telegram_client import send_telegram_message

logger = get_logger(__name__)


class JournalPipeline:
    """Daily curated research journal — ML/LLM/SLM/VLM/finetune.

    Composes:
      - jobpulse.papers.fetcher.PaperFetcher  — multi-source paper ingest
      - jobpulse.papers.store.PaperStore      — SQLite persistence
      - jobpulse.papers.PaperRanker           — fast_score + LLM rank
      - jobpulse.papers.notion_publisher.NotionPublisher — generic Notion client
      - research_journal.{domain_filter, results_filter, verifier, summarizer, delivery}
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.fetcher = PaperFetcher()
        self.ranker = PaperRanker()
        self.store = PaperStore(db_path=db_path)
        self.notion = NotionPublisher()

    async def daily_journal(self, target_volume_max: int = 12) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        papers = await self.fetcher.fetch_all()
        logger.info("daily_journal: fetched %d raw papers", len(papers))

        # Stage ② Domain classifier
        tagged: list[tuple[Paper, str]] = []
        for p in papers:
            tag, _, _ = classify_domain(p)
            if tag != "out":
                tagged.append((p, tag))
        logger.info("daily_journal: %d core+tangent after domain filter", len(tagged))

        # Stage ③ Hard filter on empirical results
        survivors: list[tuple[Paper, str, str]] = []  # (paper, domain_tag, paper_type)
        for paper, tag in tagged:
            cls = classify_results(paper)
            if cls.has_results:
                survivors.append((paper, tag, cls.paper_type))
        logger.info("daily_journal: %d survived has_results hard filter", len(survivors))

        # Stage ④ Enrich + rank
        survivor_papers = [s[0] for s in survivors]
        enriched = await self.fetcher.enrich(survivor_papers)
        ranked = self.ranker.llm_rank(enriched, top_n=min(target_volume_max, len(enriched)))
        ranked = attach_rank_reasons(ranked, lens="daily")

        tag_by_id = {s[0].arxiv_id: s[1] for s in survivors}

        # Stages ⑤ Verify + ⑥ Summarize per paper
        published: list[tuple] = []
        for paper in ranked:
            cls_for_results = classify_results(paper)
            badge = verify_paper(paper, has_results=cls_for_results.has_results)
            summary, grounded = summarize_paper(paper)
            badge.claims_grounded = grounded
            domain_tag = tag_by_id.get(paper.arxiv_id, "core")
            published.append((paper, badge, summary, domain_tag))

        # Persist
        for paper, badge, summary, domain_tag in published:
            paper.summary_long = summary           # type: ignore[attr-defined]
            paper.domain_tag = domain_tag          # type: ignore[attr-defined]
            paper.verification = badge.model_dump_json()  # type: ignore[attr-defined]
        self.store.store(ranked, digest_date=today)

        # Stage ⑦ Delivery
        page_ids = publish_journal_to_notion(
            publisher=self.notion, items=published, digest_date=today,
        )
        digest_msg = build_journal_telegram_digest(
            items=[(p, b, t) for (p, b, _, t) in published],
            page_url_for=lambda aid: f"https://www.notion.so/{aid.replace('.', '')}",
        )
        send_telegram_message(digest_msg)

        core_count = sum(1 for _, _, _, t in published if t == "core")
        tangent_count = sum(1 for _, _, _, t in published if t == "tangent")
        return {"core_count": core_count, "tangent_count": tangent_count, "page_ids": page_ids}
```

- [ ] **Step 4: Export `JournalPipeline` from `research_journal/__init__.py`**

Replace the placeholder created in Task 3 with:

```python
"""research_journal — daily curated ML/LLM/SLM/VLM research feed.

Pipeline: ingest -> domain classify -> hard-filter (results) -> rank -> verify
        -> summarize (3-agent: Extract -> Write -> Hallucination Guard)
        -> publish to Notion + Telegram.
"""

from research_journal.pipeline import JournalPipeline

__all__ = ["JournalPipeline"]
```

- [ ] **Step 5: Run + pass**

```bash
pytest tests/research_journal/test_pipeline.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research_journal/pipeline.py research_journal/__init__.py tests/research_journal/test_pipeline.py
git commit -m "feat(research_journal/pipeline): JournalPipeline orchestrator (daily_journal)"
```

---

### Task 31: `journal-daily` CLI handler + cron

**Files:**
- Modify: `jobpulse/runner.py`
- Modify: `scripts/install_cron.py`
- Test: `tests/jobpulse/test_runner_journal.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_runner_journal.py
import sys
from unittest.mock import patch, AsyncMock


def test_runner_journal_daily(monkeypatch):
    fake_pipeline = type("P", (), {"daily_journal": AsyncMock(return_value={"core_count": 9, "tangent_count": 1})})()
    with patch("research_journal.pipeline.JournalPipeline", return_value=fake_pipeline), \
         patch.object(sys, "argv", ["runner", "journal-daily"]):
        from jobpulse import runner
        runner.main()
    fake_pipeline.daily_journal.assert_awaited_once()
```

- [ ] **Step 2: Run + fail**

```bash
pytest tests/jobpulse/test_runner_journal.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add CLI branches in `jobpulse/runner.py`**

Find the existing `elif command == "arxiv":` block (around line 185) and add after it:

```python
    elif command == "journal-daily":
        from research_journal.pipeline import JournalPipeline
        result = asyncio.run(JournalPipeline().daily_journal())
        print(f"Journal: {result['core_count']} core, {result['tangent_count']} tangent")

    elif command == "journal-quality-audit":
        from research_journal.audit import run_weekly_audit
        run_weekly_audit()
```

(Ensure `import asyncio` is at the top of `runner.py`.)

- [ ] **Step 4: Add cron entries in `scripts/install_cron.py`**

In the cron-string template, add (right after the existing `arxiv` cron entry, around line 30):

```
# Daily Research Journal (8:00am — runs after the 7:57am arxiv digest)
0 8 * * * {RUNNER} journal-daily >> {PROJECT_DIR}/logs/journal.log 2>&1

# Weekly Journal quality audit (Sun 9:00pm)
0 21 * * 0 {RUNNER} journal-quality-audit >> {PROJECT_DIR}/logs/journal.log 2>&1
```

- [ ] **Step 5: Run + pass**

```bash
pytest tests/jobpulse/test_runner_journal.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/runner.py scripts/install_cron.py tests/jobpulse/test_runner_journal.py
git commit -m "feat(runner+cron): journal-daily + journal-quality-audit CLI handlers"
```

---

### Task 32: Live integration test on 5 real papers

**Files:**
- Test: `tests/research_journal/test_pipeline_live.py` (new, `@pytest.mark.live`)

- [ ] **Step 1: Write the live integration test**

```python
# tests/research_journal/test_pipeline_live.py
import sqlite3
from pathlib import Path
import pytest


@pytest.mark.live
@pytest.mark.asyncio
async def test_daily_journal_live_5_papers(tmp_path: Path, monkeypatch):
    """Runs the real daily_journal against live arXiv. Caps to 5 papers via classify_domain stub."""
    from research_journal.pipeline import JournalPipeline

    db = tmp_path / "papers.db"
    pipeline = JournalPipeline(db_path=db)

    # Cap volume by short-circuiting domain classifier to "core" for first 5 only
    cnt = {"n": 0}
    real_classify = __import__("research_journal.domain_filter", fromlist=["classify_domain"]).classify_domain
    def capped(p):
        if cnt["n"] >= 5:
            return ("out", 0.0, "capped for live test")
        cnt["n"] += 1
        return real_classify(p)
    monkeypatch.setattr("research_journal.pipeline.classify_domain", capped)

    # Don't actually post to Notion / Telegram in live test
    monkeypatch.setattr("research_journal.pipeline.publish_journal_to_notion", lambda **kw: ["fake-id"])
    monkeypatch.setattr("research_journal.pipeline.send_telegram_message", lambda msg: None)

    result = await pipeline.daily_journal(target_volume_max=5)

    # Assertions: DB rows exist with non-empty summary_long + verification
    rows = sqlite3.connect(db).execute(
        "SELECT arxiv_id, summary_long, verification, domain_tag FROM papers WHERE summary_long != ''"
    ).fetchall()
    assert len(rows) >= 1
    for arxiv_id, summary, verification, domain_tag in rows:
        assert "## TL;DR" in summary
        assert "## Method" in summary
        assert verification.startswith("{")  # JSON
        assert domain_tag in ("core", "tangent")
```

- [ ] **Step 2: Run live**

```bash
pytest tests/research_journal/test_pipeline_live.py -v -m live
```
Expected: PASS. If it fails, debug end-to-end with the failing arxiv_id from logs.

- [ ] **Step 3: Commit**

```bash
git add tests/research_journal/test_pipeline_live.py
git commit -m "test(papers/journal): live e2e test on 5 real papers"
```

---

## Phase 10 — Quality gates

### Task 33: `journal_audit.py` — hallucination rate + coverage check

**Files:**
- Create: `research_journal/audit.py`
- Test: `tests/research_journal/test_audit.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/research_journal/test_audit.py
import sqlite3
from pathlib import Path
from research_journal.audit import compute_hallucination_rate, run_weekly_audit


def test_hallucination_rate_basic(tmp_path: Path):
    db = tmp_path / "papers.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE papers (arxiv_id TEXT, summary_long TEXT, verification TEXT, digest_date TEXT);
      INSERT INTO papers VALUES ('a', '...', '{"claims_grounded": true}', '2026-05-05');
      INSERT INTO papers VALUES ('b', '...', '{"claims_grounded": false}', '2026-05-05');
      INSERT INTO papers VALUES ('c', '...', '{"claims_grounded": true}', '2026-05-05');
    """)
    conn.commit()
    rate = compute_hallucination_rate(db_path=db, days=7)
    assert rate == pytest.approx(1/3)


def test_run_weekly_audit_emits_signal(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("research_journal.audit.compute_hallucination_rate", lambda **kw: 0.05)
    monkeypatch.setattr("research_journal.audit.compute_coverage_gap", lambda **kw: 0.10)
    signals = []
    monkeypatch.setattr("research_journal.audit._emit_signal",
                        lambda **kw: signals.append(kw))
    run_weekly_audit(db_path=tmp_path / "papers.db")
    assert len(signals) >= 1
```

(Add `import pytest` to imports.)

- [ ] **Step 2: Run + fail**

```bash
pytest tests/research_journal/test_audit.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# research_journal/audit.py
"""Weekly quality audit — hallucination rate + coverage gap vs HF Daily Papers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)


def compute_hallucination_rate(db_path: Path | None = None, days: int = 7) -> float:
    """Fraction of papers from the last `days` whose verification.claims_grounded == False."""
    db_path = db_path or DATA_DIR / "papers.db"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT verification FROM papers WHERE digest_date >= ? AND verification != ''",
            (cutoff,),
        ).fetchall()
    if not rows:
        return 0.0
    failed = 0
    for (raw,) in rows:
        try:
            v = json.loads(raw)
            if not v.get("claims_grounded", True):
                failed += 1
        except json.JSONDecodeError:
            continue
    return failed / len(rows)


def compute_coverage_gap(db_path: Path | None = None, days: int = 7) -> float:
    """Fraction of HF Daily Papers' top picks NOT present in our journal in the last `days`."""
    db_path = db_path or DATA_DIR / "papers.db"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        ours = {r[0] for r in conn.execute(
            "SELECT arxiv_id FROM papers WHERE digest_date >= ?", (cutoff,)
        ).fetchall()}
    try:
        from jobpulse.papers.fetcher import PaperFetcher
        import asyncio
        fetcher = PaperFetcher()
        hf = asyncio.run(fetcher._fetch_huggingface())
    except Exception as exc:
        logger.warning("HF Daily Papers fetch failed in audit: %s", exc)
        return 0.0
    hf_ids = {p.arxiv_id for p in hf}
    if not hf_ids:
        return 0.0
    return len(hf_ids - ours) / len(hf_ids)


def run_weekly_audit(db_path: Path | None = None) -> dict:
    rate = compute_hallucination_rate(db_path=db_path)
    gap = compute_coverage_gap(db_path=db_path)
    summary = {"hallucination_rate": rate, "coverage_gap": gap}
    logger.info("journal weekly audit: %s", summary)

    if rate > 0.02:
        _emit_signal(signal_type="failure", domain="journal_summary",
                     metric="hallucination_rate", value=rate, threshold=0.02)
        _alert_telegram(f"⚠️ Journal hallucination rate {rate:.2%} > 2%")
    if gap > 0.30:
        _emit_signal(signal_type="adaptation", domain="journal_classifier",
                     metric="coverage_gap", value=gap, threshold=0.30)

    return summary


def _emit_signal(**kwargs) -> None:
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit_signal(**kwargs)
    except Exception as exc:
        logger.warning("optimization signal emit failed: %s", exc)


def _alert_telegram(msg: str) -> None:
    try:
        from shared.telegram_client import send_telegram_message
        send_telegram_message(msg)
    except Exception as exc:
        logger.warning("telegram alert failed: %s", exc)
```

- [ ] **Step 4: Run + pass**

```bash
pytest tests/research_journal/test_audit.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research_journal/audit.py tests/research_journal/test_audit.py
git commit -m "feat(papers/journal): weekly quality audit (hallucination rate + coverage gap)"
```

---

## Self-Review

After completing all 33 tasks above, run this self-review checklist before declaring v1 done:

**Spec coverage map** — every section of the spec maps to a task:

| Spec section | Implemented in |
|---|---|
| §3 Relationship to existing code | Task 2 (schema reuse), Task 27 (don't break legacy daily_digest) |
| §4 Architecture (7 stages) | Tasks 5-6 (① ingest stays in fetcher), 5-7 (②), 10 (③), 13-16 (④), 17-20 (⑤), 21-25 (⑥), 28-29 (⑦) |
| §5.1 Source ingest + caveats | Tasks 26-27 |
| §5.2 Domain classifier (2-pass) | Tasks 5-9 |
| §5.3 Hard filter has_results + paper_type | Tasks 10-12 |
| §5.4 Ranker extensions | Tasks 13-16 |
| §5.5 Verification engine (5 checks) | Tasks 17-20 (4 checks) + Task 25 (5th from guard) |
| §5.6 Summary writer (3-agent) | Tasks 21-25 |
| §5.7 Notion + Telegram delivery | Tasks 28-29 |
| §6 Cron schedule | Task 31 |
| §8 Quality gates | Task 33 |
| §9 Test strategy + calibration | Tasks 8-9 (domain), 11-12 (results), 32 (live e2e) |
| §10 Deployment & cost (Together AI) | Task 1 |
| §11 Failure modes | Distributed across all tasks (each LLM call has try/except, each API call has unknown-state) |
| §12 Out-of-scope | Verified — no Instagram/impl/mobile/leaderboard tasks present |
| §13 Open questions | Tasks 4 (anchor sets v0), 13 (lab list), 30 (Notion DB provisioning happens in implementation, not in plan) |

**Placeholder scan:** `grep -rE "TBD|TODO|fill in|implement later" docs/superpowers/plans/2026-05-09-daily-research-journal.md` → expected empty.

**Type consistency check:**
- `DomainTag` defined in Task 3 (Literal["core","tangent","out"]) — used consistently in Tasks 5,6,7,30
- `VerificationBadge.score` is `int` (Task 3) — consumed as `number` in Notion props (Task 28) ✓
- `PaperTypeClassification` shape (Task 3) — produced by `classify_results` (Task 10), consumed by `_paper_type_deboost` (Task 15) ✓
- `RankedPaper.rank_reason` (added Task 16) — consumed by Notion publisher (Task 28) and Telegram digest (Task 29) ✓
- `_RepoCache` (Task 18) interface: `.get(url) -> dict | None`, `.set(url, dict)` — consistent within file ✓

If any check fails, fix inline and continue.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-daily-research-journal.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each task is independently testable and TDD-disciplined; ideal for clean handoffs.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

**Which approach?**
