# Codebase Quality Audit — SOLID, OOP & Architecture

**Date:** 2026-04-09
**Scope:** Full codebase (~81,000 LOC, 306 Python files, 18 databases)
**Method:** Static analysis via CodeGraph MCP + manual code review

---

## Table of Contents

1. [God Classes & God Modules](#1-god-classes--god-modules)
2. [DRY Violations & Code Duplication](#2-dry-violations--code-duplication)
3. [Dependency Injection & Coupling](#3-dependency-injection--coupling)
4. [Missing Abstractions & Interfaces](#4-missing-abstractions--interfaces)
5. [Error Handling Inconsistency](#5-error-handling-inconsistency)
6. [Global Mutable State](#6-global-mutable-state)
7. [Test Quality & Coverage Gaps](#7-test-quality--coverage-gaps)
8. [Design Pattern Issues](#8-design-pattern-issues)
9. [Dead Code](#9-dead-code)
10. [Configuration Scatter](#10-configuration-scatter)
11. [Circular Dependencies](#11-circular-dependencies)
12. [Complexity Hotspots](#12-complexity-hotspots)
13. [Prioritized Action Plan](#13-prioritized-action-plan)

---

## 1. God Classes & God Modules

### 1.1 `ApplicationOrchestrator` — 1,100 lines, single class

**File:** `jobpulse/application_orchestrator.py`
**Violates:** Single Responsibility (S), Open/Closed (O)

Handles cookie dismissal, SSO, login, navigation, form filling, file uploads, stuck detection, anti-detection timing, screening questions, page detection, and confirmation detection — all in one class. No methods surfaced in the module summary (all are private/internal), meaning the class has massive internal complexity with no seams for testing.

**Fix:** Extract into focused collaborators:
- `CookieDismisser` (already partially exists)
- `SSOHandler` (already partially exists)
- `NavigationEngine` — navigation learning, stuck detection, next-button finding
- `FormFillingEngine` — field filling, screening Q&A, file uploads
- `AntiDetectionManager` — human-like delays, timing randomization
- Keep `ApplicationOrchestrator` as a thin coordinator wiring these together

### 1.2 `budget_agent.py` — 1,891 lines, 39 functions

**File:** `jobpulse/budget_agent.py`
**Violates:** Single Responsibility (S), Interface Segregation (I)

Six distinct responsibilities in one module:
1. **Transaction management** — `add_transaction()`, `log_transaction()`, `undo_last_transaction()`
2. **Budget planning** — `set_planned_budget()`, `set_budget()`, `check_budget_alerts()`
3. **Recurring payments** — `add_recurring()`, `process_recurring()`, `list_recurring()`, `remove_recurring()`
4. **Hours tracking** — `log_hours()`, `get_hours_summary()`, `undo_hours()`, `_rebuild_notion_timesheet()`
5. **Salary/savings** — `confirm_savings_transfer()`, `_get_or_create_salary_page()`, `_add_row_to_salary_page()`
6. **Notion sync** — `sync_expense_to_notion()`, `_update_table_row()`, `_update_section_totals()`

Plus NLP parsing: `classify_transaction()`, `parse_transaction()`, `_parse_date_from_text()`, `_words_to_numbers()`

**Fix:** Split into:
- `TransactionService` — CRUD + undo + classification
- `RecurringService` — recurring payment lifecycle
- `HoursTracker` — work hours + Notion timesheet
- `BudgetPlanner` — budgets, alerts, weekly summaries
- `NotionBudgetSync` — all Notion API integration
- `BudgetNLPParser` — natural language parsing (date, amounts, categories)

### 1.3 `dispatcher.py` — 885 lines, 48 functions

**File:** `jobpulse/dispatcher.py`
**Violates:** Single Responsibility (S), Open/Closed (O)

48 `_handle_*` functions that are effectively a giant switch statement. Adding a new intent requires modifying this file (violates Open/Closed). Many handlers are 2-3 lines that just delegate to another module.

**Fix:** Handler registry pattern. Each handler is a standalone function registered with a decorator. The dispatcher becomes a lookup table, not a 900-line file.

### 1.4 `job_autopilot.py` — 1,180 lines, imports 16 modules

**File:** `jobpulse/job_autopilot.py`
**Violates:** Single Responsibility (S), Dependency Inversion (D)

Orchestrates the entire 9-layer job pipeline (L1-L9) with direct imports from every layer. Any change to any sub-module potentially breaks it.

**Fix:** Pipeline abstraction — each layer implements a `PipelineStep` protocol. The autopilot wires steps together without importing internals.

### 1.5 `CodeIntelligence` — 1,715 lines, 35 methods

**File:** `shared/code_intelligence.py`
**Violates:** Single Responsibility (S), Interface Segregation (I)

The entire code intelligence platform in one class: AST indexing, text/markdown indexing, FTS5 search, Voyage AI embeddings, PageRank, community detection, incremental reindexing, symbol lookup, impact analysis, test coverage mapping, call path finding, batch lookup, boundary checking, refactoring suggestions, rename preview, dead code detection, complexity hotspots, similar function discovery, grep with enrichment, module summaries, git analysis.

**Fix:** Extract into:
- `CodeIndexService` — indexing, reindexing, embeddings
- `CodeSearchService` — grep, semantic search, find_symbol, callers/callees
- `CodeAnalysisService` — risk, impact, coverage, dead code, hotspots
- `RefactoringAdvisor` — suggest_extract, rename_preview, similar_functions

### 1.6 `CodeGraph` — 806 lines, 23 methods

**File:** `shared/code_graph.py`
**Violates:** Single Responsibility (S)

Mixes SQLite schema management, AST parsing, call edge resolution (import-aware disambiguation), risk scoring, impact radius (BFS), PageRank, community detection (Louvain-style), and query API.

**Fix:** Extract `ASTIndexer` (parsing + edges), `GraphAlgorithms` (PageRank, communities, BFS), `RiskScorer`.

### 1.7 `memory_layer.py` — 1,057 lines, 7 classes in one file

**File:** `shared/memory_layer.py`
**Violates:** Single Responsibility at file level

Contains `ShortTermMemory`, `EpisodicMemory`, `SemanticMemory`, `ProceduralMemory`, `PatternMemory`, `AgentRouter`, and `MemoryManager` — all crammed into one module.

**Fix:** Split into `memory/` package: `short_term.py`, `episodic.py`, `semantic.py`, `procedural.py`, `pattern.py`, `router.py`, `manager.py`.

### 1.8 `tool_integration.py` — 838 lines, 12 classes

**File:** `shared/tool_integration.py`
**Violates:** Single Responsibility at file level

Packs audit logging, permission management, 6 tool implementations (WebSearch, Terminal, Gmail, etc.), and tool execution orchestration into one file. Individual tools are fine (~50-80 lines), but the file mixes framework with implementations.

**Fix:** Move each tool to `tools/web_search.py`, `tools/terminal.py`, etc. Keep `ToolExecutor` and `AuditLog` as core framework.

### 1.9 `ScanLearningEngine` — 460 lines, 13 methods

**File:** `jobpulse/scan_learning.py`
**Violates:** Single Responsibility (S)

Mixes persistence (SQLite, 17 signal types), statistical correlation, LLM pattern analysis, cooldown management (exponential backoff), risk factor computation, and adaptive parameter calculation.

**Fix:** Extract `ScanEventStore`, `CorrelationEngine`, `CooldownManager`, `AdaptiveParams`.

### 1.10 Other Large Files

| File | Lines | Issue |
|------|-------|-------|
| `jobpulse/arxiv_agent.py` | 818 | Paper fetching + ranking + Notion sync + blog generation |
| `shared/fact_checker.py` | 460+ | Claim extraction + web verification + caching + scoring |

---

### 1.11 Long Functions (>80 lines)

| Function | File | Lines | What It Does |
|----------|------|-------|-------------|
| `_fill_application` | `application_orchestrator.py:596` | ~270 | Multi-page form loop: state detection, two-phase fill, action execution, MV3 recovery, anti-detection, validation, stuck detection |
| `apply_job` | `applicator.py` | 233 | Rate limiting + gate checks + CV gen + adapter submission + Notion update |
| `_navigate_to_form` | `application_orchestrator.py:239` | ~150 | Learned sequence replay + fresh detection loop with SSO/login handling |
| `reindex_file` | `code_intelligence.py:595` | ~193 | 9-step pipeline: exclusion, old data, caller discovery, deletion, re-parse, insert, diff, risk, search index |
| `grep_search` | `code_intelligence.py:1693` | ~117 | Regex compilation, file walking, matching, context extraction, graph enrichment |
| `log_transaction` | `budget_agent.py:721` | ~117 | 5-step pipeline: parse, classify, extract items, store, sync Notion, format |
| `classify_transaction` | `budget_agent.py:563` | ~112 | 4-stage classification: store inference, phrase match, keyword match, LLM fallback |
| `_two_phase_fill` | `application_orchestrator.py:906` | ~110 | Deterministic fill, combobox reveal, LLM fill, file upload dedup |
| `_resolve_call_edges` | `code_graph.py:447` | ~96 | Lookup tables + import-aware disambiguation with 5 fallback strategies |
| `impact_radius` | `code_graph.py:586` | ~115 | Seed collection, hub threshold, adjacency preload, BFS, marshaling |
| `_cache_risk_scores` | `code_intelligence.py:382` | ~95 | Bulk SQL for fan-in, cross-file callers, test coverage + in-memory scoring |
| `log_hours` | `budget_agent.py:1524` | ~93 | Parse hours, extract date, calculate tax, store, Notion page, format |
| `_parse_date_from_text` | `budget_agent.py:1387` | ~83 | Regex matching for 6 date formats (yesterday, day names, month+day, etc.) |

---

## 2. DRY Violations & Code Duplication

### 2.1 Dispatcher AGENT_MAP — duplicated in 2 files

Both `dispatcher.py` (lines 82-128) and `swarm_dispatcher.py` (lines 390-438) maintain identical 42-entry AGENT_MAP dictionaries. The CLAUDE.md rule "NEVER update only one dispatcher" exists *because* this duplication has caused production bugs.

**Fix:** Extract `HANDLER_REGISTRY` to a shared module. Both dispatchers import it.

### 2.2 `_get_conn()` — 10 identical copies

9 files + 1 alias define the same 4-line pattern:

```python
def _get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

**Files:** `budget_agent.py`, `budget_tracker.py`, `event_logger.py`, `process_logger.py`, `skill_gap_tracker.py`, `arxiv_agent.py`, `ab_testing.py`, `rate_monitor.py`, `code_intel_cli.py`, `swarm_dispatcher.py` (`_get_exp_conn`)

**Fix:** Single `shared/db.py::get_db_conn(db_path: Path) -> sqlite3.Connection`

### 2.3 `_notion_api()` — 5 copies (3 degraded)

| File | Lines | Has Retry | Has 401 Handling |
|------|-------|-----------|-----------------|
| `jobpulse/notion_agent.py:13` | 40 | Yes (3 attempts) | Yes |
| `jobpulse/job_notion_sync.py:68` | 17 | No | No |
| `jobpulse/company_blocklist.py:94` | 17 | No | No |
| `jobpulse/skill_tracker_notion.py:30` | 17 | No | No |
| `jobpulse/papers/notion_publisher.py:23` | 40 | Yes | Yes |

The 3 degraded copies are **missing retry logic** — a reliability bug, not just a DRY issue.

**Fix:** Single `shared/notion_client.py` with retry + 401 handling. All modules import from it.

### 2.4 Telegram API URL construction — 12+ files

`https://api.telegram.org/bot{token}/...` constructed inline in: `telegram_bots.py`, `telegram_agent.py`, `telegram_stream.py`, `voice_handler.py`, `webhook_server.py`, `tool_integration.py`, and 6+ scripts.

**Fix:** `shared/telegram_client.py` with a `TelegramClient` class.

### 2.5 DB Schema — 50+ CREATE TABLE across 25 modules, no migration framework

Each module manages its own SQLite schema independently. There is no shared migration framework, schema registry, or version tracking.

**Fix:** Centralize schema definitions per database. Consider a lightweight migration pattern (not a full ORM — just versioned CREATE TABLE + ALTER TABLE scripts per .db file).

---

## 3. Dependency Injection & Coupling

### 3.1 25+ Direct `OpenAI()` Calls Bypassing `get_llm()`

The rule "All LLM instantiation goes through `get_llm()`" is **massively violated**. 25+ files create `OpenAI()` clients directly:

`persona_evolution.py`, `conversation.py`, `generate_cover_letter.py`, `skill_extractor.py`, `email_preclassifier.py`, `pre_submit_gate.py`, `chart_generator.py`, `budget_agent.py`, `blog_pipeline.py`, `ranker.py`, `scan_learning.py`, `gate4_quality.py`, `vision_tier.py`, `command_router.py`, `form_analyzer.py`, `diagnoser.py`, `native_form_filler.py`, `screening_answers.py`, `blog_generator.py`, `gmail_agent.py`, `notion_agent.py`, `voice_handler.py`, `fact_checker.py`, plus `enhanced_swarm.py` and `experiential_learning.py` with direct `ChatOpenAI()`.

**Impact:** Every direct call is a potential inconsistency in model version, timeout, retry behavior, and cost tracking. This also **blocks Langfuse integration** — there is no single point to inject observability.

**Fix:** Enforce all LLM access through `get_llm()` or a new `LLMProvider` protocol. The raw `OpenAI()` calls need a parallel factory (`get_openai_client()`). Both factories become the injection point for callbacks/observability.

### 3.2 Hard-coded Database Paths — 11 files

Relative `data/` paths instead of deriving from `config.DATA_DIR`:

| File | Hard-coded Path |
|------|----------------|
| `navigation_learner.py:18` | `"data/navigation_learning.db"` |
| `semantic_cache.py:21` | `Path("data/semantic_cache.db")` |
| `account_manager.py:18` | `"data/ats_accounts.db"` |
| `code_intelligence.py:105` | `"data/code_intelligence.db"` |
| `experiential_learning.py:99` | `"data/experience_memory.db"` |
| `dynamic_swarm.py:68` | `"data/experience_memory.db"` |
| `enhanced_swarm.py:80` | `"data/experience_memory.db"` |
| `peer_debate.py:68` | `"data/experience_memory.db"` |
| `skill_graph_store.py:22` | `data/skill_synonyms.json` |
| `last_action.py:14` | `Path(...) / "data" / "last_action.json"` |
| `code_intel_mcp.py:590` | `"data/code_intelligence.db"` |

**Impact:** Breaks if working directory is not project root. Makes testing require monkeypatching.

**Fix:** All paths derive from `config.DATA_DIR` or accept path as constructor argument.

### 3.3 `ApplicationOrchestrator` Instantiates All Dependencies

The orchestrator directly creates `AccountManager`, `CookieDismisser`, `NavigationLearner`, `PageAnalyzer`, `SSOHandler`, `NativeFormFiller`, etc. in its constructor. No dependency injection — impossible to test with mocks without monkeypatching.

**Fix:** Accept collaborators via constructor injection. Factory function for production wiring.

---

## 4. Missing Abstractions & Interfaces

### Only 4 Protocol/ABC definitions in 81,000 LOC

| Existing | File |
|----------|------|
| `DriverProtocol` | `jobpulse/driver_protocol.py` |
| `BaseATSAdapter` | `jobpulse/ats_adapters/base.py` |
| `PlatformAdapter` | `jobpulse/platforms/base.py` |
| `StreamCallback` | `shared/streaming.py` |

### Missing abstractions (high impact)

| Service | Current State | Needed |
|---------|--------------|--------|
| LLM access | 25+ scattered `OpenAI()` + `get_llm()` | `LLMProvider` protocol |
| Notion API | 5 copies of `_notion_api()` | `NotionClient` class |
| Telegram API | 12+ files construct URLs | `TelegramClient` class |
| Database access | 10 copies of `_get_conn()` | `DatabaseManager` or shared utility |
| Google services | Direct API calls in each agent | `GoogleClient` (Drive, Gmail, Calendar) |
| Screening answers | Direct function calls | `ScreeningProvider` protocol |

---

## 5. Error Handling Inconsistency

### 5.1 Broad `except Exception` — 364 occurrences

- **jobpulse/**: 309 across 88 files
- **shared/**: 55 across 14 files

Top offenders:
| File | Count |
|------|-------|
| `job_autopilot.py` | 23 |
| `job_api.py` | 16 |
| `github_profile_sync.py` | 14 |
| `arxiv_agent.py` | 10 |
| `verification_detector.py` | 9 |

### 5.2 Swallowed Exceptions — 53 total

- **28** `except ... pass` — silent failure, hides bugs
- **25** `except ... continue` — skips items silently in loops

Key files: `application_orchestrator.py`, `budget_agent.py`, `job_autopilot.py`, `job_scanner.py`, `ext_bridge.py`, `agents.py`, `fact_checker.py`

### 5.3 `DispatchError` Only Used in One File

`DispatchError` is defined in `dispatcher.py` with structured fields (`errorCategory`, `isRetryable`, `partialResults`). But `swarm_dispatcher.py` returns raw `f"Error: {e}"` strings. Agent modules inconsistently return strings vs dicts.

### 5.4 Inconsistent Agent Return Types

| Pattern | Files |
|---------|-------|
| Returns `f"Error: ..."` string | `file_ops.py`, `git_ops.py`, `notion_papers_agent.py`, `remote_shell.py` |
| Returns `{"success": False, "error": "..."}` dict | `playwright_driver.py`, `analytics_api.py`, `health_api.py` |
| Returns `DispatchError` object | `dispatcher.py` only |

**Fix:** Define `AgentResult` protocol. All agents return structured results. The dispatcher converts to user-facing strings at the boundary.

---

## 6. Global Mutable State

| File | Variable | Risk |
|------|----------|------|
| `ats_adapters/__init__.py:28` | `_ext_adapter` singleton | 100+ lines lazy init, no reset for tests |
| `conversation.py:11` | `_history: list[dict]` | Module-global chat history, unbounded |
| `approval.py:16` | `_pending: Optional[dict]` | Concurrent approvals silently overwrite |
| `file_ops.py:22` | `_page_state: dict` | Global pagination, not per-session |
| `dispatcher.py:549` | `_last_undo_mode` | Mutable dict, no concurrency guard |
| `code_intel_mcp.py:609` | `_ci_instance` singleton | No thread safety |
| `dynamic_swarm.py:68` | `_experience_memory` singleton | Hard-coded DB path |
| `enhanced_swarm.py:80` | `_experience_memory` singleton | Same |
| `peer_debate.py:68` | `_experience_memory` singleton | Same |
| `telegram_stream.py:17-18` | `_BOT_TOKEN`, `_CHAT_ID` | Cached at import, cannot change |

**Fix:** Replace module-level singletons with dependency-injected instances. Use factory functions that accept configuration. Provide `reset()` methods for test isolation.

---

## 7. Test Quality & Coverage Gaps

### 7.1 ~30 Untested Modules

High-risk modules with **zero test coverage**:

| Module | Risk | Why |
|--------|------|-----|
| `job_api.py` | Critical | 16 broad `except Exception`, handles API endpoints |
| `telegram_agent.py` | High | Main Telegram message handler |
| `approval.py` | High | Approval flow with global mutable state |
| `conversation.py` | High | Chat state management |
| `relay_bridge.py` | High | Bridge communication |
| `email_review.py` | High | Email review pipeline |
| `persona_evolution.py` | High | 3 broad exceptions, LLM-heavy |
| `skill_tracker_notion.py` | High | Degraded Notion API copy |
| `calendar_agent.py` | Medium | External API integration |
| `morning_briefing.py` | Medium | Daily digest assembly |
| `webhook_server.py` | Medium | HTTP server |
| `weekly_report.py` | Medium | Report generation |
| `voice_handler.py` | Medium | Whisper integration |
| `multi_bot_listener.py` | Medium | Multi-bot coordination |

### 7.2 Monkeypatch Usage — Healthy

25 total across 8 files, max 6 per file. This is reasonable for 81K LOC, indicating test infrastructure is generally well-structured where tests exist.

### 7.3 Minor Test Isolation Violation

`tests/test_nlp_classifier.py` (lines 166-187) reads `data/intent_examples.json` directly without tmp_path. Read-only, but creates working-directory dependency.

---

## 8. Design Pattern Issues

### 8.1 Well-Implemented Patterns (keep these)

| Pattern | Location | Quality |
|---------|----------|---------|
| Strategy (form fillers) | `form_engine/page_filler.py` → 7 filler modules | Clean |
| Strategy (state machines) | `state_machines/__init__.py` → 11 platforms | Clean |
| Template Method | `PlatformStateMachine.detect_state()` → `_detect_platform_state()` | Textbook |
| Protocol (driver swap) | `DriverProtocol` with structural subtyping | Good |
| Decorator (tracking) | `TrackedDriver` wrapping any driver | Clean |
| Factory (state machines) | `get_state_machine(platform)` | Clean |

### 8.2 Pattern Issues

**Liskov Substitution Violation — Driver Protocol:**
- `PlaywrightDriver.screenshot()` returns `bytes`
- `ExtensionBridge.screenshot()` returns `dict` with base64
- `PlaywrightDriver.fill()` takes extra `label` kwarg not in protocol
- Callers must handle both return shapes

**Dead Code in Base Adapter:**
- `BaseATSAdapter.answer_screening_questions()` (140 lines) uses Playwright `page` objects directly — incompatible with current extension-only architecture

**No Polymorphism in CV Generators:**
- `generate_cv.py` and `generate_cover_letter.py` are standalone procedural modules
- No shared interface, no base class
- Acceptable for single-user, but blocks template extensibility

---

## 9. Dead Code

**13.1% of functions have zero callers** — 2,087 out of 15,931 functions, ~63,000 removable lines.

Discounting worktree duplicates, confirmed dead in main tree:
- `run_agentic_loop` — dead across multiple files
- `vision_navigate` — 208 lines, uncalled
- `BaseATSAdapter.answer_screening_questions()` — 140 lines, incompatible with current architecture

**Fix:** Run dead code analysis on main tree only, verify with grep, remove confirmed dead code in a dedicated cleanup PR.

---

## 10. Configuration Scatter

### `config.py` is well-structured but bypassed

12+ env vars read directly in `jobpulse/` modules instead of through `config.py`:

| File | Env Var | Also in config.py? |
|------|---------|-------------------|
| `conversation.py:85` | `CONVERSATION_MODEL` | Yes (duplicated) |
| `persona_evolution.py` | `OPENAI_API_KEY` | Yes |
| `native_form_filler.py` | `OPENAI_API_KEY` | Yes |
| `notion_agent.py:379` | `OPENAI_API_KEY` | Yes |
| `multi_listener.py:48,59` | `SLACK_BOT_TOKEN`, `DISCORD_BOT_TOKEN` | Yes |
| `swarm_dispatcher.py:234-240` | `RLM_BACKEND`, `RLM_ROOT_MODEL`, `RLM_MAX_ITERATIONS`, `RLM_MAX_BUDGET` | No |
| `budget_agent.py:1248,1852` | `HOURLY_RATE` (read twice) | No |
| `runner.py:389` | `PLAYWRIGHT_CDP_PORT` | No |

**Fix:** All jobpulse/ env var reads go through `config.py`. Add missing vars (`RLM_*`, `HOURLY_RATE`, `PLAYWRIGHT_CDP_PORT`).

---

## 11. Circular Dependencies

Two confirmed cycles:

1. **`mindgraph_app/api.py` <-> `mindgraph_app/retriever.py`**
   - api.py imports retriever for deep_query, retrieve
   - retriever.py imports api.py (unclear why — likely for shared models)
   - **Fix:** Extract shared models to `mindgraph_app/models.py`

2. **`jobpulse/budget_agent.py` <-> `jobpulse/budget_tracker.py`**
   - budget_agent imports budget_tracker for Notion sync
   - budget_tracker imports budget_agent for transaction data
   - **Fix:** Clear layering: `budget_agent` (logic) -> `budget_tracker` (persistence). Move shared types to a models module.

---

## 12. Complexity Hotspots

Functions with highest blast radius (fan-in x risk):

| Function | Fan-in | Risk | Danger Score |
|----------|--------|------|-------------|
| `screening_answers.get_answer` | 1,151 | 0.45 | 518 |
| `email_preclassifier.preclassify` | 338 | 0.45 | 152 |
| `verification_detector.detect_verification_wall` | 324 | 0.45 | 146 |
| `papers/store.PaperStore.search` | 424 | 0.30 | 127 |
| `papers/ranker.fast_score` | 312 | 0.30 | 94 |
| `skill_gap_tracker.record_gap` | 279 | 0.30 | 84 |
| `form_engine/detector.detect_input_type` | 256 | 0.30 | 77 |
| `email_review.process_review_reply` | 162 | 0.45 | 73 |
| `healthcheck.write_heartbeat` | 118 | 0.60 | 71 |
| `ats_scorer.score_ats` | 233 | 0.30 | 70 |

These need the strongest test coverage and should be behind interfaces so implementation can be hardened without touching callers.

---

## 13. Prioritized Action Plan

### Tier 1 — High Impact, Fixes Systemic Issues

| # | Change | Impact | Files Affected | Effort |
|---|--------|--------|---------------|--------|
| 1 | **Centralize LLM access** — Create `get_openai_client()` factory alongside `get_llm()`. Enforce all 25+ direct `OpenAI()` calls through factories. | Enables Langfuse, consistent model/retry/cost tracking | 25+ files | Medium |
| 2 | **Unify dispatcher AGENT_MAP** — Extract shared `HandlerRegistry`, import in both dispatchers | Eliminates documented source of production bugs | 2 files | Small |
| 3 | **Extract shared DB utility** — `shared/db.py::get_db_conn(path)` replacing 10 `_get_conn()` copies | DRY, testable, consistent WAL/Row config | 10 files | Small |
| 4 | **Centralize Notion client** — Single `shared/notion_client.py` with retry + 401 handling | Fixes 3 degraded copies missing retry (reliability bug) | 5 files | Small |

### Tier 2 — Structural Improvements

| # | Change | Impact | Files Affected | Effort |
|---|--------|--------|---------------|--------|
| 5 | **Break up `ApplicationOrchestrator`** — Extract NavigationEngine, FormFillingEngine, ActionDispatcher, AntiDetectionManager | Testable, maintainable application pipeline | 1 file -> 5 files | Large |
| 6 | **Break up `budget_agent.py`** — Split into TransactionService, RecurringService, HoursTracker, BudgetPlanner, NotionBudgetSync, BudgetNLPParser | Independently testable, clear ownership | 1 file -> 6 files | Large |
| 7 | **Break up `CodeIntelligence`** — Split into CodeIndexService, CodeSearchService, CodeAnalysisService, RefactoringAdvisor | ISP compliance, focused responsibilities | 1 file -> 4 files | Large |
| 8 | **Break up `CodeGraph`** — Extract ASTIndexer, GraphAlgorithms, RiskScorer | Testable graph operations | 1 file -> 3 files | Medium |
| 9 | **Split `memory_layer.py`** — 7 classes into `memory/` package | One class per file, clear boundaries | 1 file -> 7 files | Medium |
| 10 | **Break circular dependencies** — Extract shared models for mindgraph + budget | Clean module boundaries | 4 files | Small |
| 11 | **Centralize Telegram client** — `shared/telegram_client.py` | DRY, consistent API usage | 12+ files | Medium |
| 12 | **Fix hard-coded DB paths** — Derive from `config.DATA_DIR` or inject | Testable, relocatable | 11 files | Small |

### Tier 3 — Quality & Hygiene

| # | Change | Impact | Files Affected | Effort |
|---|--------|--------|---------------|--------|
| 13 | **Define `AgentResult` protocol** — Structured returns from all agents | Consistent error handling, testable | 20+ files | Medium |
| 14 | **Narrow broad exceptions** — Replace `except Exception` with specific types, log swallowed exceptions | Better debugging, fewer hidden failures | 88 files (incremental) | Large |
| 15 | **Add tests for 30 untested modules** — Prioritize `job_api.py`, `telegram_agent.py`, `approval.py` | Coverage for highest-risk gaps | 30 new test files | Large |
| 16 | **Fix Liskov Substitution in DriverProtocol** — Normalize `screenshot()` return type | Clean driver swapping | 3 files | Small |
| 17 | **Remove dead code** — Verified 0-caller functions | Reduced cognitive load, faster CI | Incremental | Medium |
| 18 | **Centralize config reads** — All env vars through `config.py` | Single source of truth for configuration | 12+ files | Small |
| 19 | **Replace global singletons with injected instances** — `_ext_adapter`, `_experience_memory`, etc. | Testable, thread-safe | 10 files | Medium |
| 20 | **Split `tool_integration.py`** — Move each tool to `tools/` package | File-level SRP | 1 file -> 8 files | Medium |

### Recommended Execution Order

**Phase 1 (foundation):** Items 1-4 — small/medium effort, immediate reliability and consistency gains. Item 1 directly unblocks Langfuse integration.

**Phase 2 (structure):** Items 5-12 — break apart god classes/modules, fix circular deps, centralize clients. Do these one module at a time with full test coverage per split.

**Phase 3 (quality):** Items 13-20 — systematic hardening. Can be done incrementally alongside feature work.

---

## Metrics Summary

| Metric | Current | Target |
|--------|---------|--------|
| Broad `except Exception` | 364 | < 50 (specific types) |
| Swallowed exceptions | 53 | 0 |
| Direct `OpenAI()` bypasses | 25+ | 0 |
| `_get_conn()` copies | 10 | 1 (shared utility) |
| `_notion_api()` copies | 5 (3 degraded) | 1 |
| Untested modules | ~30 | < 5 |
| Dead code % | 13.1% | < 5% |
| Circular dependencies | 2 | 0 |
| Max file length | 1,891 lines | < 500 lines |
| Max function length | 233 lines | < 80 lines |
| Protocol/ABC definitions | 4 | 10+ |
