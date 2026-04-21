---
paths: ["**/*.py"]
description: "MANDATORY 7-principle engineering checklist for ALL code changes"
---

# Seven Engineering Principles (MANDATORY)

Every new feature, function, file, or code change MUST satisfy these 7 principles.
This is not aspirational — it is a hard gate. Violations found in audit 2026-04-20.

---

## 1. System Design

**Rule**: Clear module boundaries, proper dependency direction, no import-time side effects.

Checkpoints:
- [ ] shared/ NEVER imports from jobpulse/, patterns/, or mindgraph_app/
- [ ] No `sys.path.insert()` — use proper package imports
- [ ] No module-level code that makes network calls, opens DB connections, or reads files — use lazy init on first use
- [ ] No module-level mutable singletons mutated from multiple call sites (race condition)
- [ ] No duplicated logic — if the same function exists in 2+ places, extract to shared/
- [ ] Functions under 100 lines — decompose if longer (scan_pipeline.generate_materials was 244 lines)
- [ ] Data flows in one direction through the pipeline — no circular dependencies

Known violations (FIXED 2026-04-20 unless noted):
- ~~`shared/agents.py:116`~~ — _ensure_provider() lazy init, no import-time Ollama probe ✅
- ~~`shared/rate_monitor.py:44`~~ — _ensure_db() lazy init, no import-time DB ✅
- ~~`jobpulse/budget_agent.py`, `db.py`~~ — lazy init via _ensure_budget_db() / _ensure_db() ✅
- ~~`ext_adapter.py`~~ — delegates to jd_analyzer.detect_ats_platform() ✅
- ~~`generate_cv.py` + `generate_cover_letter.py`~~ — _sanitize_pdf extracted to cv_templates/__init__.py ✅
- ~~All pattern files~~ — sys.path.insert removed from all 6 patterns ✅

## 2. Tool and Contract Design

**Rule**: Typed interfaces, consistent return contracts, centralized factories.

Checkpoints:
- [ ] ALL LLM calls go through `get_llm()` / `smart_llm_call()` — NEVER `ChatOpenAI()`, `OpenAI()`, `litellm.completion()` directly
- [ ] Functions that return dicts MUST use TypedDict or dataclass — no untyped `dict` returns
- [ ] Playwright page parameters typed as `Page` not `Any`
- [ ] ABC/Protocol classes enforce required attributes via `__init_subclass__`
- [ ] Error returns use DispatchError/AgentError — not bare strings
- [ ] State types match: don't pass `MapReduceState` to functions expecting `AgentState`

Known violations (FIXED 2026-04-20 unless noted):
- `mindgraph_app/extractor.py:81`, `retriever.py:256` — `litellm.completion()` direct (REMAINING — mindgraph uses litellm multi-provider)
- `shared/llm_fallback.py:51` — `OpenAI()` direct (REMAINING — fallback provider needs raw client)
- `gmail_agent.py:87`, `email_preclassifier.py` — `client.chat.completions.create()` direct (REMAINING)
- ~~`ats_adapters/base.py`~~ — fill_and_submit now returns FillSubmitResult TypedDict ✅
- ~~`form_engine/page_filler.py:16`, `detector.py:14`~~ — typed as Page/ElementHandle via TYPE_CHECKING ✅
- `patterns/map_reduce.py:140`, `plan_and_execute.py:293` — state type mismatch (REMAINING)

## 3. Retrieval Engineering

**Rule**: Efficient data fetching, proper caching, no N+1 queries.

Checkpoints:
- [ ] SQLite connections use context managers (`with`) or connection pooling — never manual open/close
- [ ] No N+1 queries — batch fetch when iterating over a collection
- [ ] Cache expensive lookups — don't read from disk on every function call
- [ ] Use parameterized queries with indexes — no `LIKE '%..%'` for primary lookups
- [ ] Don't re-cache already-cached values on cache hits
- [ ] Lazy-load expensive resources (embeddings, models) on first use, not import time

Known violations (FIXED 2026-04-20 unless noted):
- `skill_graph_store.py:191` — N+1 queries (REMAINING)
- ~~`email_preclassifier.py:302`~~ — rules now cached in memory with mtime check ✅
- ~~`scan_learning.py:105-214`~~ — all connections now use `with self._get_conn()` ✅
- ~~`screening_answers.py:428`~~ — removed redundant cache_answer() on cache hit ✅
- `job_db.py:102`, `fact_checker.py:103` — connection-per-call (REMAINING)
- `mindgraph_app/storage.py:91-129` — connection per upsert (REMAINING)

## 4. Reliability Engineering

**Rule**: Resource cleanup, guarded external calls, bounded loops, graceful degradation.

Checkpoints:
- [ ] ALL Playwright instances in `try/finally` with cleanup — no leak on exception
- [ ] ALL SQLite connections use `with` context managers
- [ ] ALL LLM calls wrapped in retry with timeout — `smart_llm_call()` provides this
- [ ] ALL `json.loads()` on LLM output wrapped in `try/except` with fallback
- [ ] Loops MUST have a max iteration bound — no unbounded while loops
- [ ] Circuit breaker consulted BEFORE retry attempts
- [ ] File writes use atomic write pattern or file locking for concurrent access
- [ ] No bare `except Exception: pass` — always log with context

Known violations (FIXED 2026-04-20 unless noted):
- ~~`smartrecruiters.py:102`~~ — pw.stop() now in finally block ✅
- ~~`ext_adapter.py:110`~~ — driver now closed on all paths including dry_run ✅
- ~~`native_form_filler.py:405,453`~~ — LLM calls now wrapped with try/except, timeout=30 ✅
- ~~`native_form_filler.py:413`~~ — json.loads wrapped with JSONDecodeError handler ✅
- ~~`dynamic_swarm.py`~~ — task_analyzer_node now increments iteration each cycle ✅
- ~~`shared/llm_retry.py`~~ — _CircuitBreaker consulted before retries, trips after 5 consecutive failures ✅
- ~~`shared/memory_layer/_stores.py`~~ — all 3 stores use _atomic_json_write (temp + rename) ✅
- ~~`budget_tracker.py:109`~~ — _save_new_store uses fcntl.flock for atomic read-modify-write ✅

## 5. Security and Safety

**Rule**: No PII in source, no injection vectors, validated external input.

Checkpoints:
- [ ] NO PII (email, phone, address) hardcoded in source — use env vars or encrypted config
- [ ] NO string interpolation in `page.evaluate()` JS — use Playwright's argument passing
- [ ] NO arbitrary URL fetching without SSRF protection (validate scheme + host)
- [ ] NO unauthenticated destructive endpoints
- [ ] NO user input passed to shell execution without allowlist validation
- [ ] NO credentials in subprocess command-line args (visible in `ps`)
- [ ] Token/credential files MUST have `0o600` permissions
- [ ] ALL SQL uses parameterized queries — no f-string SQL
- [ ] URL parameters MUST be properly encoded (`urllib.parse.urlencode`)
- [ ] Prompt defense strips ALL injection-relevant tags including `agent_output`

Known violations (FIXED 2026-04-20 unless noted):
- ~~`applicator.py:40-59`~~ — PII moved to env vars via config.py ✅
- ~~`generate_cv.py:82-88`~~ — IDENTITY now built from config.py ✅
- ~~`smartrecruiters.py:348`~~ — JS injection fixed via Playwright arg passing ✅
- ~~`mindgraph_app/api.py:43,63`~~ — SSRF guard added (_validate_url) ✅
- ~~`mindgraph_app/api.py:209`~~ — clear() now requires ?confirm=yes-delete-all ✅
- ~~`dispatcher.py:404`~~ — defense-in-depth: _is_allowed() checked at dispatcher before execute() ✅
- `setup_integrations.py:40,65` — tokens in curl CLI args (REMAINING — low risk, local-only script)
- ~~`setup_integrations.py:213`~~ — token file now `chmod 0o600` ✅
- ~~`install_cron.py:102`~~ — now uses markers and merges, preserves non-JobPulse entries ✅
- ~~`shared/telegram_client.py:73`~~ — now uses urlencode ✅
- ~~`shared/prompt_defense.py`~~ — agent_output tag added to strip regex ✅

## 6. Evaluation and Observability

**Rule**: Track costs, log decisions, expose metrics, never silently fail.

Checkpoints:
- [ ] ALL LLM calls tracked via `track_llm_usage()` — including streaming calls
- [ ] ALL pattern runs include `compute_cost_summary()` in output
- [ ] Cost table covers ALL providers used (OpenAI, Anthropic, Voyage, Ollama)
- [ ] Decision points logged (why this path was chosen, what score triggered it)
- [ ] Error degradation returns structured context, not "Data unavailable" strings
- [ ] Verification/validation results logged with before/after values
- [ ] Memory and learning recorded for ALL score ranges, not just high scores
- [ ] Learning actions tracked via OptimizationEngine.before_learning_action() / after_learning_action()

Known violations (FIXED 2026-04-20 unless noted):
- ~~`shared/agents.py:254`~~ — `_StreamResponse` now estimates token usage from content length ✅
- ~~`shared/cost_tracker.py:17-25`~~ — added Anthropic, Voyage, Ollama pricing ✅
- `patterns/peer_debate.py`, `map_reduce.py`, `plan_and_execute.py`, `dynamic_swarm.py` — no `compute_cost_summary()` (REMAINING — dynamic_swarm already has it)
- ~~`peer_debate.py:288-316`~~ — now records experience for ALL score ranges ✅
- `weekly_report.py`, `morning_briefing.py` — silent "Data unavailable" degradation (REMAINING)
- `form_engine/page_filler.py` — no logging at routing decisions (REMAINING)

## 7. Product Thinking

**Rule**: Handle edge cases, fail gracefully for users, never bypass safety workflows.

Checkpoints:
- [ ] ALL applications use `dry_run=True` first — NEVER hardcode `dry_run=False`
- [ ] ALL successful submissions call `confirm_application()` — no exceptions
- [ ] Error messages are user-actionable, not internal stack traces
- [ ] Platform-specific paths have fallbacks for unknown platforms
- [ ] Font paths are OS-aware, not hardcoded to macOS
- [ ] Setup/install scripts have rollback and partial-retry capability
- [ ] Destructive operations are idempotent or protected by markers
- [ ] File uploads validate size and never grab wrong input element

Known violations (FIXED 2026-04-20 unless noted):
- ~~`scripts/apply_now.py:329`~~ — now defaults to dry_run=True, pass --submit to override ✅
- `scripts/apply_now.py` — missing `confirm_application()` call (REMAINING)
- `generate_cv.py:61` — hardcoded macOS font paths (REMAINING — needs cross-platform font discovery)
- ~~`scripts/install_cron.py:102`~~ — now uses markers, preserves non-JobPulse entries ✅
- `scripts/setup_integrations.py` — no rollback if one integration fails (REMAINING)
- ~~`mindgraph_app/api.py:232`~~ — error responses now use generic messages, details logged ✅
- ~~`file_filler.py:55`~~ — scoped file input: parent container → accept attribute → generic fallback ✅
- ~~`screening_answers.py:509`~~ — context-aware fallback: salary/notice/visa answers from WORK_AUTH ✅

---

## Enforcement

When reviewing or writing code, verify against the above before committing.
Every PR-worthy change should pass the relevant checkpoints.
If a principle doesn't apply (e.g., no retrieval in a pure formatter), note "N/A" — don't skip silently.
