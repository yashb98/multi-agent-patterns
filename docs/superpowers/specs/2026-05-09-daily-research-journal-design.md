# Daily Research Journal v1 — Design Spec

**Date:** 2026-05-09
**Status:** Design — pending user review before implementation plan
**Owner:** Yash Bishnoi
**Scope:** v1 of a personal curated research-paper digest, narrowed to ML/DL/finetune-LLM/SLM/VLM, with verification and 1100–1300-word summaries delivered daily.

---

## 1. Purpose

A daily, single-user pipeline that surfaces 8–12 research papers from a defined ML/LLM/SLM/VLM domain, verifies each against external sources, and produces a 1100–1300-word structured summary so the user can keep current without reading raw arXiv listings. The system runs autonomously on a daily cron, publishes to Notion + Telegram, and prioritizes summary correctness over breadth.

This spec covers v1 only. Instagram content generation, implementation-helper tooling, and personal-blog publishing are explicit non-goals (see §13).

## 2. Success criteria

| Metric | Target |
|---|---|
| Daily volume reaching the journal | 8–12 papers |
| Hallucination rate (sampled, weekly audit) | < 2% |
| Time from arXiv publication → journal entry | < 24 hours |
| Coverage gap vs HF Daily Papers (weekly) | < 30% |
| Operational cost (LLM + APIs) | < $30/month |
| User completion rate (papers marked "Read" in Notion) | tracked, surfaced as drift signal |

## 3. Relationship to existing code

The codebase contains two parallel arxiv/blog pipelines (audit 2026-05-08):

- **OLD**: `jobpulse/arxiv_agent.py` + `jobpulse/blog_generator.py` + `jobpulse/paper_discovery.py` (1,702 LOC). Wired into the 7:57am cron, dispatcher, and 4 webhook endpoints.
- **NEW**: `jobpulse/papers/` package (2,726 LOC across 9 files). Wired into the Monday 8:33am cron + 1 webhook endpoint.

**This spec builds exclusively on the NEW `jobpulse/papers/` package.** No new code goes into `arxiv_agent.py` or `blog_generator.py`.

**Cron migration:** The 7:57am `arxiv` cron continues to run `arxiv_agent.send_daily_digest()` during v1 implementation (no behavior change). Once the journal pipeline ships and is stable for one week, a follow-up change repoints the cron to the new pipeline and removes `arxiv_agent.py` + `blog_generator.py` + `paper_discovery.py`. That deprecation is out of scope for v1; tracked as a v1.1 follow-up.

**Schema reuse:** Both pipelines write to `data/papers.db`. The NEW `papers/store.py` schema is a strict superset of the OLD one (verified in audit). The journal pipeline reuses `papers/store.py` and adds 3 new columns:

```sql
ALTER TABLE papers ADD COLUMN domain_tag       TEXT DEFAULT '';      -- "core" | "tangent" | "out"
ALTER TABLE papers ADD COLUMN verification     TEXT DEFAULT '';      -- JSON of 5 badge checks
ALTER TABLE papers ADD COLUMN summary_long     TEXT DEFAULT '';      -- 1100–1300w structured summary
```

The hallucination guard runs a regen if grounding fails on first attempt (see §5.6.3). Both guard input and output are stored in `summary_long` only after the guard passes. No partial state leaks to the user.

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  ① Source ingest  — arXiv + OpenReview + HF Daily Papers           │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ② Domain classifier  — ML/LLM/SLM/VLM/finetune                    │
│     • Pass 1: embedding similarity vs anchor set                    │
│     • Pass 2: LLM classification on borderline (0.55–0.65)         │
│     Output: domain_tag ∈ {core, tangent, out}                       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ③ Hard filter  — has empirical results?                            │
│     LLM scan of abstract for numerical claims + benchmark mentions  │
│     If FAIL → drop (does not enter journal)                         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ④ Ranker  — extends papers/ranker.py                               │
│     • Lab-track-record boost                                        │
│     • Recency × repo-activity                                       │
│     • De-boost surveys/position papers                              │
│     • LLM rank justifications attached to top-N                     │
│     Output: top 8–12, ranked, with reason string per pick           │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ⑤ Verification engine  — composite badge (5 checks)                │
│     Peer review · Working repo · ≥3 indep. citations ·              │
│     Claims grounded · Has empirical results (already passed in ③)   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ⑥ Summary writer  — 3-agent pipeline                               │
│     Extractor → Writer → Hallucination Guard                        │
│     Output: 1100–1300w in fixed 6-section structure                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ⑦ Delivery  — Notion DB + Telegram digest                          │
└─────────────────────────────────────────────────────────────────────┘
```

## 5. Component specs

### 5.1 Source ingest — `jobpulse/papers/fetcher.py` (extend)

**Existing behavior preserved.** `PaperFetcher.fetch_all()` already pulls arXiv + HF Daily Papers + Semantic Scholar trending + 3 community sources. v1 changes:

- **Add**: OpenReview ingestion (`_fetch_openreview`) — accepted papers only from ICLR/NeurIPS/COLM/EMNLP
- **Drop from v1 ingestion**: Reddit, Hacker News, Bluesky community sources — too noisy for quality-first journal

**Best-effort caveats:**
- OpenReview API has no official Python SDK and rate-limits at ~30 req/min. Implementation uses `httpx` with exponential backoff, gracefully degrades to arXiv+HF-only if OpenReview returns errors for >50% of requests in a 5-min window. Logged at WARN.
- HF Daily Papers has no public API. Existing scraper in `papers/fetcher.py::_fetch_huggingface` reused as-is; degrades to arXiv-only on scrape failure.

**Daily volume input:** Expect 100–300 raw papers/day pre-filter.

### 5.2 Domain classifier — `jobpulse/papers/domain_filter.py` (NEW)

**Purpose:** Decide whether a paper belongs in the ML/LLM/SLM/VLM/finetune domain. Output is one of:
- `core` — clearly in-domain
- `tangent` — adjacent (LLMs applied to medicine, robotics, etc.) — kept but tagged
- `out` — out-of-domain — dropped

**Two-pass design (no regex per `.claude/rules/seven-principles.md` §8):**

```python
def classify_domain(paper: Paper) -> tuple[Literal["core", "tangent", "out"], float, str]:
    """Returns (tag, confidence, reason)."""
    # Pass 1: embedding similarity vs anchor set
    text = f"{paper.title}. {paper.abstract}"
    sim_core = max_cosine(text, ANCHOR_CORE)        # ~25 phrases
    sim_tangent = max_cosine(text, ANCHOR_TANGENT)  # ~10 phrases
    sim_out = max_cosine(text, ANCHOR_OUT)          # ~10 reject phrases

    if sim_core >= 0.65 and sim_core > sim_out:
        return "core", sim_core, f"matched core anchor ({best_match})"
    if sim_out >= 0.70 and sim_out > sim_core:
        return "out", sim_out, f"matched reject anchor ({best_match})"
    if 0.55 <= sim_core < 0.65:
        # Pass 2: LLM borderline classifier
        return _llm_classify(paper)
    if sim_tangent >= 0.60:
        return "tangent", sim_tangent, f"adjacent: {best_match}"
    return "out", sim_core, "below all thresholds"
```

**Anchor sets (illustrative — final v0 list produced at plan-time, see §13.1):**

- `ANCHOR_CORE` (~25 phrases) — illustrative seeds: "instruction tuning", "RLHF / DPO / PPO", "speculative decoding", "long-context attention", "MoE routing", "PEFT / LoRA / QLoRA", "vision language model alignment", "in-context learning", "test-time compute", "RAG", "tool use in LLMs", "model distillation", "small language model", "mechanistic interpretability of LLMs", "model merging", "LLM evaluation"
- `ANCHOR_TANGENT` (~10 phrases) — illustrative seeds: "transformer for medical imaging", "LLM for robotics control", "language model for protein design", "multi-agent simulation"
- `ANCHOR_OUT` (~10 phrases) — illustrative seeds: "molecular dynamics", "self-driving lane detection", "graph neural network for chemistry", "convolutional architecture for satellite imagery"

The seed lists above are not the final anchor sets — they exist to illustrate the kind of phrase being matched. The v0 lists are produced by the implementation plan and reviewed by the user before merge.

Embeddings via `shared/memory_layer/_embedder.py` (Voyage 3 Large + MiniLM fallback already in codebase).

**LLM borderline classifier prompt:** stored at `shared/prompts/journal_domain_classify.yaml`, loaded via the existing `PromptRegistry`. Returns structured `{tag, confidence, reason}` JSON.

**Calibration:** A fixture at `tests/fixtures/journal/domain_calibration.json` holds 40 hand-labeled papers (20 core, 10 tangent, 10 out). Test asserts ≥90% agreement on `core` vs `out`; tangent classification is allowed to be fuzzier (≥75% agreement).

### 5.3 Hard filter — has empirical results? — `jobpulse/papers/results_filter.py` (NEW)

**Purpose:** Drop papers without empirical results (position papers, surveys, opinion pieces, theory-only with no eval). This is the only HARD filter in the pipeline — failures are not displayed, they are removed.

**Implementation:** Single LLM call on `(title, abstract)` returning structured JSON. The same call also tags the paper-type so the ranker can de-boost surveys/position papers without a separate classifier:

```yaml
# shared/prompts/journal_results_filter.yaml
system: |
  You are a research-paper triager. Classify a paper on two axes:

  (1) has_results — true if the abstract mentions at least one of:
        - specific numerical results (e.g., "+5.3% on MMLU", "95.4 F1")
        - named benchmarks (MMLU, HumanEval, GSM8K, MT-Bench, …)
        - ablation experiments
        - comparison tables with baselines
      false if the abstract describes only motivation, theory without
      evaluation, or future work.

  (2) paper_type — one of:
        - research   (novel method + empirical evaluation)
        - survey     (review of prior work; may include benchmark numbers)
        - position   (opinion / argument paper without new experiments)
        - tutorial   (educational walkthrough)
        - workshop   (workshop summary / extended abstract)
output_schema:
  has_results: bool
  paper_type: Literal["research", "survey", "position", "tutorial", "workshop"]
  reason: str          # one sentence
  confidence: float    # 0.0–1.0
```

`paper_type` is consumed by the ranker (§5.4) to de-boost non-`research` types. This replaces the title-regex approach considered earlier — semantic classification belongs in an LLM call per `.claude/rules/seven-principles.md` §8.

**Edge cases:**
- Empty abstract → drop, log at WARN
- Confidence < 0.6 → fall through to ranker (don't hard-drop on low confidence)
- LLM call fails (timeout, rate limit) → keep paper, mark `verification.has_results = "unknown"`, surface in Notion (don't silently drop)

**Calibration set:** `tests/fixtures/journal/results_calibration.json` with 20 known-positive (papers with empirical results) and 20 known-negative (surveys, position papers, theory-only). Tolerance: ≤10% false-negative rate on calibration set before merge.

### 5.4 Ranker — `jobpulse/papers/ranker.py` (extend)

Existing `fast_score()` and `llm_rank()` retained. Three additions:

1. **Lab-track-record boost (added to `fast_score`):**
   ```python
   _RECOGNIZED_LABS = {
     "Anthropic", "DeepMind", "Google Research", "Meta AI", "FAIR",
     "Mistral", "Qwen", "Alibaba", "DeepSeek", "Allen Institute for AI", "AI2",
     "HuggingFace", "Stanford", "Princeton", "MIT", "CMU", "Berkeley",
     "OpenAI", "Microsoft Research", "NVIDIA Research",
   }
   ```
   Boost rule: `+0.5` if any author affiliation matches a recognized lab; `+1.0` if first author or last author is from a recognized lab; `+1.5` if multiple recognized labs collaborate. Match author affiliations from arXiv metadata; fuzzy match (Levenshtein < 3) to handle abbreviations.

2. **Recency × repo-activity boost:** if paper has GitHub repo with commit in last 14 days, +1.0.

3. **Paper-type de-boost:** consumes the `paper_type` field already produced by §5.3:
   - `research` → no change
   - `survey` → −1.0 (still useful, but less central)
   - `tutorial` → −1.5
   - `position` → −2.0 (typically already dropped by `has_results` filter, but defense-in-depth)
   - `workshop` → −1.5

   No regex; the classification was performed by the LLM call in §5.3 and the ranker simply consumes the structured output.

**LLM rank justifications:** When `llm_rank()` returns top-N, also attach `rank_reason: str` per pick. Stored in DB (new column `rank_reason`). Displayed in Notion under the badge.

**Volume edge cases:**
- If filtered output < 8 papers/day: surface all of them; do not relax filters.
- If filtered output > 12 papers/day: cap at 12, ranked-best-first; overflow stored in DB with `status='deferred'` for next-day weekly review.

### 5.5 Verification engine — `jobpulse/papers/verifier.py` (NEW)

Composite of 5 checks, returned as a `VerificationBadge` Pydantic model:

```python
class VerificationBadge(BaseModel):
    has_results: bool         # already evaluated in §5.3 (hard filter)
    peer_reviewed: bool
    has_repo: bool
    independent_citations: bool   # ≥3 from non-co-author labs
    claims_grounded: bool         # filled by ⑥ Hallucination Guard
    score: int  # 0–5
    reasons: dict[str, str]  # per-check reason string
```

**Check implementations:**

| Check | Source | Pass condition | Cache |
|---|---|---|---|
| `peer_reviewed` | Semantic Scholar API | venue ∈ `PEER_REVIEWED_VENUES` (existing list in `shared/external_verifiers.py`) | 30 days |
| `has_repo` | GitHub API + S2 `openAccessPdf.url` for repo links | repo URL responds 200, ≥10 stars, last commit < 90 days ago | 24 hours |
| `independent_citations` | Semantic Scholar `references` + `citations` | ≥3 citing papers from labs ≠ paper's author labs | 7 days |
| `claims_grounded` | (filled by §5.6.3) | hallucination guard passed | per-paper |
| `has_results` | (filled by §5.3) | hard filter passed | per-paper |

**Auth & rate limits:**
- GitHub API: uses `GITHUB_TOKEN` env var (already in `jobpulse/config.py`); per-repo cache in `data/github_cache.db` (new table `repo_health` with TTL 24h)
- Semantic Scholar: existing circuit breaker in `shared/circuit_breaker.py::s2_breaker` reused; on circuit-open, badge marks unverified checks as `unknown` rather than `False`.

**Display rule:** A check returning `unknown` (API failure) shows ⚪ in the badge, distinct from ❌ (verified-false).

### 5.6 Summary writer — `jobpulse/papers/journal_summarizer.py` (NEW)

Three-agent pipeline. Lighter than the existing 6-agent `blog_pipeline.py`. All LLM calls route through `cognitive_llm_call(domain="journal_summary")`.

#### 5.6.1 Extractor

**Purpose:** Pull structured facts from the paper (PDF + abstract) into a typed scaffold the writer cannot ignore.

**Input:** `Paper` (with `pdf_url`, `abstract`)
**Output:**
```python
class ExtractedFacts(BaseModel):
    problem: str             # 2–3 sentences
    method_steps: list[str]  # 5–10 steps
    architecture_details: dict[str, str]   # arch / training / hyperparams
    benchmarks: list[BenchResult]          # name, metric, value, baseline
    ablations: list[str]                   # bullet points
    limitations: list[str]
    key_insight: str         # 1 sentence
    raw_excerpts: list[str]  # 5–10 verbatim PDF spans for grounding
```

`raw_excerpts` is **load-bearing** — the hallucination guard checks the writer's claims against these spans, not against an LLM judgment.

**Implementation:** PDF download + text extraction via `pypdf` (already in `requirements.txt`). LLM call uses Qwen3-Coder-30B-A3B for technical accuracy. Failure to download PDF → fall back to abstract-only extraction; mark `summary_long` field with `[abstract-only]` prefix.

#### 5.6.2 Writer

**Purpose:** Generate the 1100–1300w summary in a fixed 6-section structure.

**Input:** `ExtractedFacts`
**Output:** Markdown text in this exact structure (enforced by post-validation):

```
## TL;DR
<50 words>

## Problem
<200 words>

## Method
<400–500 words>

## Key insight
<100 words>

## Results
<350 words>

## Limitations
<100 words>
```

**Style:** Factual, technical, no editorializing, no "why this matters to you" commentary. Bullet lists allowed in Method/Results. Numbers must be present in Results section.

**LLM:** Qwen3-30B-A3B-Instruct via `cognitive_llm_call(domain="journal_summary", stakes="medium")`.

**Word-count enforcement:** Post-generation, count words per section. If any section is >25% off target, regenerate once with explicit constraint feedback. After 2 attempts, accept the closest result and log at WARN.

#### 5.6.3 Hallucination Guard

**Purpose:** Verify every numeric/specific claim in the summary traces to either an `ExtractedFacts.raw_excerpt` or to a `BenchResult`. This is the `claims_grounded` badge check.

**Implementation:**
1. Extract candidate claims from the summary using a structured LLM call: "Return all sentences containing a number, percentage, benchmark name, or specific architectural choice."
2. For each claim, attempt grounding in this order:
   - **Substring containment** in any `raw_excerpt` (case-insensitive, whitespace-normalized) → grounded
   - **Numeric match**: extract number from claim, find it in `BenchResult` values or `raw_excerpt` → grounded
   - **Embedding similarity** (Voyage 3 Large) ≥ 0.85 vs any excerpt → grounded
3. Sample 5 claims (or all if fewer than 5). If >1 fails grounding → regenerate the summary with the failed claims appended to the writer prompt as "AVOID THESE UNGROUNDED PATTERNS".
4. Second failure → set `claims_grounded = False`, store summary as-is, surface badge ⚪ on the offending check, log at ERROR.

**Critical:** The guard's grounding check is **not** an LLM judgment call — it is deterministic substring + numeric + embedding similarity. This avoids the "same hallucination passes both writer and guard" failure mode.

### 5.7 Delivery — `jobpulse/papers/notion_publisher.py` (extend) + Telegram

**Notion DB schema (new "Daily Research Journal" database):**

| Column | Type | Source |
|---|---|---|
| Title | Title | Paper.title |
| Date | Date | digest_date |
| Domain tag | Select | `core` \| `tangent` |
| Badge | Number (0–5) | VerificationBadge.score |
| Badge breakdown | Multi-select | check names that passed |
| Rank reason | Rich text | from §5.4 LLM justification |
| Authors | Rich text | Paper.authors |
| arXiv link | URL | |
| Repo link | URL (if exists) | |
| Read | Checkbox | user-set |
| Saved for impl | Checkbox | user-set (out of v1 scope, but column exists for v2) |

Each entry's body contains the full 1100–1300-word summary as Notion blocks (Heading 2 per section).

**Tangent papers** appear in the same database with `domain_tag=tangent`. A pre-saved Notion view filters them to a separate page; main journal view hides them by default.

**Telegram morning digest (8:00 AM via cron):**
- Title: "🧪 Daily Research Journal — N papers (M tangent)"
- One line per `core` paper: `{badge_emojis} <title> — <rank_reason 1-line>` with Notion link
- Tangent papers: collapsed line at end: "+ N tangent papers in Notion"
- No tangent papers in the Telegram digest body to avoid noise.

## 6. Cron schedule

Add to `scripts/install_cron.py`:

```
# Daily Research Journal (8:00am — runs after the existing 7:57am arxiv digest)
0 8 * * * {RUNNER} journal-daily >> {PROJECT_DIR}/logs/journal.log 2>&1

# Weekly quality audit — hallucination rate + coverage check (Sunday 9:00pm)
0 21 * * 0 {RUNNER} journal-quality-audit >> {PROJECT_DIR}/logs/journal.log 2>&1
```

CLI handlers in `jobpulse/runner.py`:

```python
elif command == "journal-daily":
    from jobpulse.papers import PapersPipeline
    asyncio.run(PapersPipeline().daily_journal())

elif command == "journal-quality-audit":
    from jobpulse.papers.journal_audit import run_weekly_audit
    run_weekly_audit()
```

`daily_journal()` is a new method on the existing `PapersPipeline` class (does not break `daily_digest` or `weekly_digest`). `run_weekly_audit()` lives in a new `jobpulse/papers/journal_audit.py` module (added to Appendix A).

## 7. Data flow

```
07:57am  arxiv_agent (OLD pipeline)         — runs unchanged during v1 transition
08:00am  journal-daily (NEW pipeline)
         │
         ├─ fetch_all()                     — papers/fetcher.py (extended)
         ├─ classify_domain() → core/tangent/out
         ├─ filter_by_results() → drop "out" + "no results"
         ├─ rank() → top 8–12 with rank_reason
         ├─ verify() → 5-check badge (parallel API calls)
         ├─ for each paper:
         │     extract() → ExtractedFacts
         │     write() → summary_long
         │     guard() → claims_grounded check
         ├─ store(papers) → data/papers.db
         ├─ publish_journal_notion() → Notion DB
         └─ publish_journal_telegram() → 8:00am digest
```

End-to-end target: < 10 minutes for 100 raw papers → 12 published summaries.

## 8. Quality gates

Three monitors, all logged to `data/optimization.db` via `OptimizationEngine` signals:

1. **Hallucination rate (weekly):**
   - Cron: Sunday 9pm `journal-quality-audit`
   - Samples 5 random summaries from past week, runs the hallucination guard from scratch on each
   - Target: < 2% claim-failure rate. Above 2% → emit `failure` signal, alert via Telegram

2. **Coverage check (weekly):**
   - Cron: Sunday 9pm
   - Diffs the journal's last-7-day output vs HF Daily Papers' top picks for same window
   - If gap > 30%, emit `adaptation` signal — likely classifier or anchor-set drift

3. **Reading completion telemetry:**
   - Daily check: Notion `Read` toggle on prior-day entries
   - If 7-day rolling completion rate < 40%, surface in Telegram weekly report
   - Not a hard signal — interpreted alongside user feedback

## 9. Test strategy

Per `.claude/rules/testing.md`:

- All tests use `tmp_path` for DB fixtures; no test touches `data/*.db`
- Live tests marked `@pytest.mark.live` (require network + API keys)

**Test files:**

| File | Type | Coverage |
|---|---|---|
| `tests/papers/test_domain_filter.py` | unit + calibration | 90% on `core` vs `out`, 75% on `tangent` |
| `tests/papers/test_results_filter.py` | unit + calibration | ≤ 10% false-negative on 40-paper set |
| `tests/papers/test_journal_summarizer.py` | unit | Word-count enforcement, structure compliance |
| `tests/papers/test_hallucination_guard.py` | unit | Substring/numeric/embedding grounding paths |
| `tests/papers/test_verifier.py` | unit + live | Each check independently; circuit breaker behavior |
| `tests/papers/test_journal_pipeline.py` | live integration | Full flow on 5 real papers, marked `@pytest.mark.live` |

**Calibration fixtures:** `tests/fixtures/journal/`
- `domain_calibration.json` — 40 hand-labeled papers (20 core / 10 tangent / 10 out)
- `results_calibration.json` — 40 papers (20 with results / 20 without)

**Wiring verification (per `.claude/rules/jobpulse.md`):** integration test runs the daily journal pipeline against 5 real recent arXiv papers, then asserts:
- `data/papers.db` has 5 new rows with non-empty `summary_long` and `verification`
- Notion DB has 5 new pages
- Telegram message sent (mocked at the `telegram_client.send` boundary, but the message is built from real DB rows)

## 10. Deployment & cost

**Target deployment for Qwen3-30B-A3B (v1):** Together AI API
- Model: `Qwen/Qwen3-30B-A3B-Instruct` (and `Qwen/Qwen3-Coder-30B-A3B-Instruct` for §5.6.1 Extractor)
- Pricing (as of 2026-05-09): ~$0.20/M input, ~$0.60/M output
- Estimated monthly volume: ~10M tokens → **~$10–15/month**

**Wired via:** new provider in `shared/agents.py` switched on `LLM_PROVIDER=together` env var. Falls back to OpenAI if Together returns 5xx for >2 consecutive calls in a 5-min window.

**Why API for v1 (not local):** zero ops, predictable cost, no GPU dependency. Local self-hosting is a v2 optimization once daily token volume + latency requirements are measured. Local would only save money once volume crosses ~50M tokens/month.

**Other costs:**
- Semantic Scholar: free tier, circuit-breaker-protected
- GitHub API: free tier with `GITHUB_TOKEN` (5000 req/hour)
- Notion API: free tier
- Voyage 3 Large embeddings: ~50K embeddings/month → ~$3/month

**Total v1 cost: ~$15–25/month.** This is the honest number — the brainstorm's $25/month assumed already-owned 4090 always-on. v1 deliberately does not assume local hardware.

## 11. Failure modes & graceful degradation

| Failure | Behavior |
|---|---|
| arXiv API down | OpenReview + HF Daily Papers continue; digest runs with reduced volume |
| OpenReview API down | Skip OpenReview, log at WARN, continue |
| Together AI 5xx burst | Fall back to OpenAI; log at WARN |
| GitHub API rate-limited | `has_repo` check returns `unknown`; badge displays ⚪ |
| Semantic Scholar circuit-open | `peer_reviewed` + `independent_citations` return `unknown`; ⚪ |
| PDF download fails | Extractor falls back to abstract-only; summary prefixed `[abstract-only]` |
| Hallucination guard fails twice | Summary published with `claims_grounded=False`; user sees ⚪ on that check |
| All sources fail | Cron run logs ERROR + sends Telegram alert; no Notion entries created |
| Filtered output < 8 papers | All published, no relaxation |
| Filtered output > 12 papers | Top 12 published; rest stored with `status=deferred` |

No silent failures. Every degraded path emits a structured log + (where appropriate) a Telegram alert.

## 12. Out of scope (explicit non-goals for v1)

- Instagram content generator (carousels, reels, captions) — v2
- Implementation helper (clone + scaffold a Colab/Modal env per paper) — v3
- Personal blog publishing — separate feature
- Library release / GitHub PR ingestion (Unsloth/TRL/PEFT/Axolotl/LLaMA-Factory/vLLM) — v2
- Leaderboard ingestion (Open LLM, lmsys arena, LiveCodeBench, …) — v2
- Negative-results / refutation tracking — v2 (deferred per user choice in design discussion)
- Replication graph (paper → citing follow-ups) — v2
- AlphaEvolve-style automated paper replication — far future, possibly own project
- Mobile app / PWA — v2 (Notion mobile app is sufficient for v1)
- Multi-user support — never (this is single-user infrastructure)
- Auto-deprecation of `arxiv_agent.py` / `blog_generator.py` / `paper_discovery.py` — v1.1 follow-up after one week of stable journal operation

## 13. Open questions / decisions to confirm in plan-time

1. Anchor-set composition (§5.2): the initial 25 core / 10 tangent / 10 out phrases need a concrete first draft. The implementation plan will produce the v0 list; user reviews before merge.
2. `_RECOGNIZED_LABS` set (§5.4): final list. Brainstorm draft is in §5.4; plan-time may revise.
3. Notion DB ID: a new "Daily Research Journal" database will be created in the Notion workspace. Setup script will provision it.
4. Cost guardrail: hard-cap monthly LLM spend at $40 (configurable via env var). On approaching cap, daily run logs at WARN; on hitting cap, runs are skipped until next month.

---

## Appendix A — Files added/modified

**New:**
- `jobpulse/papers/domain_filter.py`
- `jobpulse/papers/results_filter.py`
- `jobpulse/papers/verifier.py`
- `jobpulse/papers/journal_summarizer.py`
- `jobpulse/papers/journal_audit.py`
- `shared/prompts/journal_domain_classify.yaml`
- `shared/prompts/journal_results_filter.yaml`
- `shared/prompts/journal_summary_writer.yaml`
- `shared/prompts/journal_hallucination_guard.yaml`
- `tests/papers/test_domain_filter.py`
- `tests/papers/test_results_filter.py`
- `tests/papers/test_journal_summarizer.py`
- `tests/papers/test_hallucination_guard.py`
- `tests/papers/test_verifier.py`
- `tests/papers/test_journal_pipeline.py`
- `tests/fixtures/journal/domain_calibration.json`
- `tests/fixtures/journal/results_calibration.json`

**Modified:**
- `jobpulse/papers/__init__.py` — add `daily_journal()` method to `PapersPipeline`
- `jobpulse/papers/fetcher.py` — add `_fetch_openreview`; drop Reddit/HN/Bluesky from default fetch
- `jobpulse/papers/ranker.py` — add lab-track-record + repo-activity boosts; consume `paper_type` for de-boost; attach `rank_reason`
- `jobpulse/papers/store.py` — add 4 new columns (`domain_tag`, `verification`, `summary_long`, `rank_reason`)
- `jobpulse/papers/notion_publisher.py` — add `publish_journal_notion()` and Telegram digest builder
- `jobpulse/runner.py` — add `journal-daily` and `journal-quality-audit` CLI branches
- `scripts/install_cron.py` — add 8:00am daily + Sunday 9:00pm weekly cron entries
- `shared/agents.py` — add Together AI provider; `LLM_PROVIDER=together` env var
- `requirements.txt` — verify `pypdf` present (already is)

**Touched but not changed (during v1):**
- `jobpulse/arxiv_agent.py` — unchanged; deprecated in v1.1
- `jobpulse/blog_generator.py` — unchanged; deprecated in v1.1
- `jobpulse/paper_discovery.py` — unchanged; deprecated in v1.1

## Appendix B — Glossary

- **Core paper** — passes domain classifier with high confidence; in the LLM/SLM/VLM/finetune domain
- **Tangent paper** — adjacent field, kept for occasional reading, does not appear in Telegram digest
- **Hard filter** — drops the paper from the journal entirely (results filter is the only one)
- **Soft check** — displayed in the badge but does not drop the paper (4 of the 5 verification checks)
- **Hallucination guard** — deterministic grounding check that runs after summary generation
- **Tangent bucket** — the Notion view filtered to `domain_tag=tangent`
- **Grounding** — a claim is "grounded" if it substring-matches, numeric-matches, or embedding-matches an extracted PDF excerpt
