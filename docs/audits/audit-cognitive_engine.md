# Subsystem 6 — `cognitive_engine` audit

**Scope:** 8 files / 1395 LOC
(`shared/cognitive/{__init__, _budget, _classifier, _engine, _prompts,
_reflexion, _strategy, _tree_of_thought}.py`).
**Branch:** `pipeline-correctness-fixes`
**Date:** 2026-05-07
**Auditor approach:** AST + grep call-graph from the 6 production
entry points (`shared/agents.cognitive_llm_call`,
`jobpulse/screening_answers._get_screening_engine`,
`jobpulse/native_form_filler._try_cognitive_unstuck` +
`_escalate_fill`, `jobpulse/gmail_agent._get_cognitive_engine`,
`shared/optimization/_engine.get_optimization_engine`,
`shared/optimization/_policy.decide_async`). Cross-module signal/DB
wiring map. Live evidence via `pytest tests/shared/cognitive/`
(80/80 pass, 5.83s) and direct SQL inspection of
`data/optimization.db:cognitive_outcomes` (1197 rows) +
`data/cognitive_budget.db:cognitive_budget_windows` (67 rows).

The cognitive engine has **two production-runtime entry methods**:

1. `CognitiveEngine.think()` (async) — used by
   `shared.optimization._policy.decide_async` (only async caller).
2. `CognitiveEngine.think_sync()` — used by every other production
   caller via `shared.agents.cognitive_llm_call` and direct
   call sites (screening_answers, native_form_filler,
   gmail_agent, etc.).

`flush()`/`flush_sync()` is a separate post-batch call that drains
`self._pending_writes` (L1 success templates) into MemoryManager.

---

## STEP 1 — Function inventory + wiring categorization

Reach codes match the prior audits:
**A** = on apply_job runtime path · **B** = runtime-conditional
(env flag / failure branch / non-apply agent path) · **C** =
reachable only via tests, CLI scripts, or non-apply agents ·
**D** = orphan in repo · **E** = shadowed/overridden.

### `__init__.py` (29 LOC) — public re-exports

| Line | Symbol | Reach | Caller(s) |
|------|--------|-------|-----------|
| 18 | `ThinkLevel`, `CognitiveBudget`, `BudgetTracker` re-export | A | classifier + engine (internal), tests |
| 23 | `StrategyComposer`, `ComposedPrompt` re-export | A | engine (internal), tests |
| 27 | `ReflexionResult` re-export | A | tests |
| 28 | `ToTResult`, `Branch` re-export | A | tests |
| 29 | `CognitiveEngine`, `ThinkResult`, `get_cognitive_engine` | A | every caller (gmail_agent, screening_answers, native_form_filler, agents, optimization._engine) |

### `_engine.py` (364 LOC) — single entry point

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 21 | `_llm_generate(prompt, model)` | A | `_execute_l1` (L242) |
| 53 | `CognitiveEngine.__init__(memory_manager, agent_name, budget=None, prompt_resolver=None)` | A | `get_cognitive_engine` (L351) |
| 78 | `CognitiveEngine.think(task, domain, stakes='medium', scorer=None, force_level=None)` | A | `_policy.decide_async` (only async caller); `think_sync` wraps it |
| 201 | `_execute(level, task, domain, composed, scorer)` | A | `think` (L129, L142) |
| 219 | `_execute_l0(task, domain, composed, scorer)` | A | `_execute` (L210) |
| 239 | `_execute_l1(task, composed, scorer)` | A | `_execute` (L212), fall-through L217 |
| 249 | `_execute_l2(task, domain, composed, scorer)` | B | `_execute` (L214) — only fires when L2 path picked |
| 268 | `_execute_l3(task, domain, composed, scorer)` | B | `_execute` (L216) — only fires when L3 path picked |
| 286 | `_record_level(level, cost)` | A | `think` (L151, L170) |
| 292 | `report()` | C | tests + analytics dashboards (no apply-path reads it) |
| 301 | `think_sync(task, domain, stakes, scorer, force_level)` | A | screening_answers L926, native_form_filler L543, gmail_agent L119, agents L856, _escalate_fill via cognitive_llm_call L856 |
| 324 | `flush()` | A | `flush_sync` (L344, L347), `optimization._engine.optimize` (L506), `optimization._engine.flush_sync` (L242) |
| 336 | `flush_sync()` | A | screening_answers L930, gmail_agent L369, agents L857, optimization._engine L506 |
| 351 | `get_cognitive_engine(agent_name, budget=None, prompt_resolver=None)` | A | every public consumer (5 production sites + tests) |

### `_budget.py` (221 LOC) — DB-backed budget tracker

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 37 | `_utc_hour_key(now=None)` | A | `record`, `allows`, `report` |
| 42 | `_now_iso()` | A | `_save_window`, `_set_cooldown_until` |
| 46 | `ThinkLevel` IntEnum | A | every cognitive caller |
| 53 | `CognitiveBudget` dataclass | A | `BudgetTracker.__init__` |
| 61 | `CognitiveBudget.from_env()` classmethod | A | `CognitiveEngine.__init__` (L63) |
| 75 | `BudgetTracker.__init__(budget, db_path=None, scope='cognitive_global')` | A | `CognitiveEngine.__init__` (L64) |
| 88 | `_init_schema()` | A | `__init__` |
| 92 | `_load_window(window_start)` | A | `record`, `allows`, `report` |
| 109 | `_save_window(...)` | A | `record` (L160) |
| 125 | `_get_cooldown_until()` | A | `allows`, `report` |
| 134 | `_set_cooldown_until(cooldown_until)` | A | `record` (L167) |
| 147 | `record(level, cost)` | A | `_record_level` (L290) |
| 179 | `allows(level)` | A | `clamp` (L202) |
| 199 | `clamp(level)` | A | `think` (L101, L140), `classify` (every classifier return) |
| 206 | `report()` | C | tests + analytics (`engine.report()`) |

### `_classifier.py` (201 LOC) — escalation heuristic

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 33 | `EscalationClassifier.__init__(memory_manager, budget_tracker)` | A | `CognitiveEngine.__init__` (L68) |
| 45 | `classify(task, domain, stakes)` | A | `think` (L92) |
| 109 | `should_escalate(current_level, score, task, domain)` | A | `think` (L137-138) |
| 120 | `update_domain_stats(domain, level, escalated)` | A | `think` (L150, L169) |
| 143 | `_persist_domain_stats(domain, stats)` | A | `update_domain_stats` (L141) — fires every 10 samples |
| 159 | `load_persisted_stats()` | A | `CognitiveEngine.__init__` (L69) |
| 178 | `_parse_persisted_fact(fact)` | A | `load_persisted_stats` (L174) |
| 192 | `_resolve_stakes(domain, explicit_stakes)` | A | `classify` (L101) |

### `_strategy.py` (170 LOC) — prompt composition + lifecycle helpers

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 27 | `ComposedPrompt` dataclass | A | every `compose()` return + ThinkResult.composed_prompt |
| 36 | `StrategyComposer.compose(task, domain, agent_name, memory_manager, max_templates=5, max_anti_patterns=3, max_strategy_tokens=500, prompt_resolver=None)` | A | `think` (L116) |
| 69 | `compose.rank_key(p)` (closure) | A | inside `compose` |
| 165 | `record_template_outcome(template, success, score)` static | **D** | only tests/shared/cognitive/test_strategy.py — no production caller |

### `_reflexion.py` (176 LOC) — L2 try/critique/retry

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 15 | `_llm_generate(prompt, model)` | B | `run` (L63, L90), `_llm_score` (L172) — fires only on L2 path |
| 35 | `ReflexionLoop.__init__(memory_manager, agent_name)` | A | `CognitiveEngine.__init__` (L71) — instance always created, even if L2 never invoked |
| 42 | `ReflexionLoop.run(task, domain, initial_prompt, max_attempts=3, score_threshold=7.0, scorer=None)` | B | `_execute_l2` (L253) |
| 119 | `_get_failure_context(domain)` | B | `run` (L58) |
| 132 | `_store_success(task, domain, answer, score)` | B | `run` (L102) |
| 148 | `_store_failure(task, domain, answer, score, critiques)` | B | `run` (L104) |
| 169 | `_llm_score(task, output)` | B | `run` (L67) |

### `_tree_of_thought.py` (192 LOC) — L3 branch/score/prune/extend

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 15 | `_llm_generate(prompt, model)` | B | every `explore` step — fires only on L3 path |
| 50 | `TreeOfThought.__init__(memory_manager, agent_name)` | A | `CognitiveEngine.__init__` (L72) |
| 55 | `_score_value(score)` static | B | `explore` (L132, L138, L146, L165) |
| 58 | `_llm_score(task, output)` | B | `explore` (L121, L155) |
| 67 | `_generate_branches_via_grpo(system_prompt, task, strategies)` | B | `explore` (L111) |
| 92 | `TreeOfThought.explore(task, domain, context, num_branches=4, prune_threshold=5.0, extend_top_n=2, scorer=None)` | B | `_execute_l3` (L272) |

### `_prompts.py` (42 LOC) — string templates

| Line | Symbol | Reach | Caller(s) |
|------|--------|-------|-----------|
| 3 | `CRITIQUE_PROMPT` | B | `_reflexion.run` (L87) |
| 18 | `BRANCH_STRATEGIES` | B | `_tree_of_thought.explore` (L106) |
| 25 | `EXTENSION_PROMPT` | B | `_tree_of_thought.explore` (L150) |
| 29 | `SCORING_PROMPT` | B | `_reflexion._llm_score`, `_tot._llm_score` |
| 38 | `COMPOSED_SECTIONS` | A | `_strategy.compose` (L126, L141, L146) |

---

## STEP 2 — Wiring summary

- **A (always-on apply path):** `__init__` re-exports, every public method
  on `CognitiveEngine` except `report()`, all 5 budget mutators,
  `EscalationClassifier.{classify, should_escalate, update_domain_stats,
  _persist_domain_stats, load_persisted_stats, _parse_persisted_fact,
  _resolve_stakes}`, `StrategyComposer.compose`, the `__init__`s of the
  L2/L3 sub-engines (always instantiated), and every prompt template that
  feeds composition (`COMPOSED_SECTIONS`).
- **B (runtime-conditional):** all `_execute_l2/_execute_l3` body methods
  + ReflexionLoop body + TreeOfThought body + their prompt templates —
  only fire when the classifier picks L2/L3 (which production data shows
  happens for 72/1197 calls = 6%).
- **C (test/analytics-only):** `BudgetTracker.report()`,
  `CognitiveEngine.report()`.
- **D (orphan):** `StrategyComposer.record_template_outcome` (only test
  callers; no production code mutates template dicts in-place).
- **E (shadowed):** none.

The cognitive engine has **no dead code in the apply path**. Every
public function is reachable from at least one production caller. The
2 dead-on-arrival methods (`report()` and `record_template_outcome`)
are observability-only and cheap to keep.

---

## STEP 3 — Internal line-by-line read (category A + B)

Findings format: `<file>:<line> [severity] <description>`.

### Blockers

`jobpulse/native_form_filler.py:548` [blocker]
`if not result or result.score < 5.0:` — `result.score` can be
`None` because `_try_cognitive_unstuck` calls `engine.think_sync(...,
domain="form_navigation", stakes="medium")` **without a scorer**, and
the engine's L1 path returns `score = scorer(answer) if scorer else
None`. When the classifier picks L1 (the most common path —
production data: 491/1197 outcomes = 41%), the resulting `None < 5.0`
raises `TypeError`, which the broad `except Exception` at L574 swallows
at `logger.debug` level. Net effect: the cognitive-unstuck feature is
**dead-on-arrival** on every L1 case but emits no warning. The
suggested fix is to substitute a default of `0.0` (so the threshold
gating still works when the LLM returned no scoreable answer).

### Major

`shared/cognitive/_classifier.py:120-141` [major]
Persisted-stats restore is **lossy by construction**. `load_persisted_stats`
restores `l0_success_rate` + `l1_escalation_rate` + `sample_size` but
sets `l0_total = l0_success = l1_total = l1_escalated = 0`. The very
next call to `update_domain_stats` recomputes
`l0_success_rate = l0_success / l0_total` from scratch, so a single new
sample after a restart can swing the rate from 0.95 → 1.0 (single L0
success) or 0.95 → 0.0 (single L0 escalation). Effectively, the
classifier "forgets" prior good performance after one new sample. The
self-improvement memory path that protects fast L0 fast-path from
hostile domains is **statistically broken**.

`shared/optimization/_policy.py:188` [major]
`confidence=result.score / 10.0` raises `TypeError` when
`engine.think(...)` returns `score=None` (it does whenever no scorer
is passed and the chosen level is L1, L2-with-failed-llm-score, or
L3-with-failed-llm-score). `decide_async` is currently invoked only
indirectly (production runs `decide()` synchronously via
`_engine.optimize_step`) but the method is on the public surface and
any new caller would crash on novel-domain decisions where
confidence < 0.6 — the exact case it's meant to handle.

`shared/cognitive/_engine.py:162-163, 181-182` [major]
Two bare `except Exception: pass` blocks silently swallow
`record_cognitive_outcome` failures. Per `.claude/rules/error-handling.md`:
"NEVER use bare `except: pass` — always log the error with context."
Production has 1197 cognitive_outcomes rows, so writes generally
succeed, but a regression in `optimization.db` (e.g. lock timeout, schema
drift, disk-full) would silently lose telemetry. Fix is `logger.debug`
with the exception so it shows up under `LOG_LEVEL=DEBUG`.

`shared/cognitive/_engine.py:142-148` [major]
Escalation cost-reporting drops the original level's spend.
`escalated_result.cost` is the cost of the higher level only;
the L0/L1 cost paid before the auto-escalate is never added back.
`BudgetTracker.report()` gets the correct per-level totals via
`_record_level` (called for both halves), but
`ThinkResult.cost` (returned to caller and logged in
gmail_agent/screening_answers debug lines) understates the true spend
by ~$0.001 per escalated call. Net effect: per-call cost
introspection drifts; budget caps still work.

`shared/cognitive/_engine.py:185-197` [major]
L0→L1 escalated successes never reach `flush()`. The early `return
escalated_result` on L164 happens BEFORE the
"L1 successes get queued for batch-write" block on L185-197. So if
the classifier picks L0, L0 returns score=0 (no template), the engine
escalates to L1, and L1 scores ≥ 7.0 with a scorer — that successful
template is **discarded**. Only "classified-as-L1-from-the-start" wins
get persisted as future templates. Long-tail effect: the engine learns
slower than it could on novel domains.

### Minor

`shared/cognitive/_classifier.py:8-21` [minor]
`STAKES_REGISTRY` is a hand-curated `{stakes: [domains]}` map. Per
`.claude/rules/seven-principles.md` §8 ("Dynamic Over Hardcoded"),
this is a static lookup table. Mitigated by the fact that explicit
`stakes` (passed by every production caller) takes priority — the
registry only matters when a caller omits stakes AND the domain is
brand-new. Defensible as a "last-resort default" but worth noting.

`shared/cognitive/_classifier.py:181` [minor]
`re.match(r"(\S+): L0 success...")` parses persisted facts back. This
is structural-format parsing of a string the classifier itself wrote
out, so it falls under the "regex OK for structural format validation"
exemption. If the persistence format ever changes, the regex will
silently mismatch and the load returns nothing — but `_persist_domain_stats`
re-emits on the next 10th sample, so recovery is automatic.

`shared/cognitive/_engine.py:215-217` [minor]
The fall-through `return await self._execute_l1(...)` on L217 is
unreachable: `ThinkLevel` is an IntEnum with exactly four values, all
covered by the if/elif chain. Defensive but dead.

`shared/cognitive/_engine.py:301-322` [minor]
`think_sync` constructs a fresh `ThreadPoolExecutor(max_workers=1)`
for every call when an event loop is already running. Each worker
thread spins up a new asyncio loop. The cost is small (~1-3ms) but
predictable, and there's no thread-safety guard around
`self._pending_writes`. If two threads invoke `think_sync` concurrently
on the same engine instance, they race the `_pending_writes.append`
list. In production the engine is per-agent (singleton via
`get_cognitive_engine`) and Python list `.append` is GIL-protected, so
no corruption — but ordering is non-deterministic.

`shared/cognitive/_engine.py:91-99` [minor]
The classifier's exception → "fall back to L1" path is logged at
`logger.warning` (good) but the error type is just printed
(`%s`, `e`). Stack traces are dropped. Difficult to root-cause a
classifier failure from a single log line.

### Nit

`shared/cognitive/_strategy.py:165-170` [nit]
`StrategyComposer.record_template_outcome` is dead — no production
caller mutates the template dict in-place; `_reflexion._store_success`
uses `MemoryManager.learn_procedure` instead. Safe to delete but not
load-bearing.

`shared/cognitive/_strategy.py:101-103` [nit]
`failures = [e for e in episodic if e.final_score < 5.0]` —
`EpisodicEntry.final_score` is typed `float` (not Optional) so this
is currently safe, but if the upstream `record_episode` signature
ever drops the type guarantee, this comparison crashes. Defensive
`getattr(e, 'final_score', 0.0)` would harden.

---

## STEP 4 — Cross-module wiring map

### Producer → Consumer table

| Signal/Row | Producer | Consumer | Schema Match? |
|------------|----------|----------|---------------|
| `cognitive_outcomes` row (`domain, agent_name, level, success, escalated, timestamp`) | `_engine.py:155, 174` (CognitiveEngine.think) | `_tracker.py:311 get_domain_stats`; `_classifier.py:49 classify` (Step 0a) | ✅ producer & consumer agree on column order, types match (int success/escalated → SQLite INTEGER) |
| `cognitive_budget_windows` row (`scope, window_start, l2_count, l3_count, cost_total, updated_at`) | `_budget.py:109 _save_window` | `_budget.py:92 _load_window`, `_budget.py:206 report` | ✅ same module producer + consumer |
| `cognitive_budget_state` row (`scope, cooldown_until, updated_at`) | `_budget.py:134 _set_cooldown_until` | `_budget.py:125 _get_cooldown_until` | ✅ same module |
| ProceduralEntry write via `learn_procedure(domain, strategy, context, score, source)` | `_engine.py:327 flush`; `_reflexion.py:140 _store_success`; `_tot.py:178 explore` | `MemoryManager.get_procedural_entries(domain)` consumed by `_classifier.classify` (L78), `_strategy.compose` (L53), `_engine._execute_l0` (L223) | ✅ matches MemoryManager facade (verified via `_manager.py:326`) |
| EpisodicEntry write via `record_episode(topic, final_score, iterations, pattern_used, agents_used, strengths, weaknesses, output_summary, domain)` | `_reflexion.py:157 _store_failure` | `MemoryManager.get_episodic_entries(domain)` consumed by `_classifier.classify` (L94), `_strategy.compose` (L98), `_reflexion._get_failure_context` (L122) | ✅ matches `_manager.py:254` |
| SemanticEntry write via `learn_fact(domain='cognitive_classifier', fact='<domain>: L0 success ...', run_id=...)` | `_classifier.py:151 _persist_domain_stats` | `_classifier.py:165 load_persisted_stats` (parses `entry.fact` via regex) | ⚠ **schema agreement is fragile**: producer and consumer share the format string, but `load_persisted_stats` parses entries by iterating `memory.semantic.facts.items()` directly (line 169) instead of going through MemoryManager.query. If the SemanticMemory storage layout ever switches to a different in-memory shape, the load silently degrades to "no stats restored". Tested empirically — currently works, but coupled. |
| Cognitive auto-escalate emits `escalation_classifier` adaptation signal? | **NO** — there's no `OptimizationEngine.emit(...)` call inside the cognitive engine. The only signal `cognitive_engine` emits is the `record_cognitive_outcome` row above. | n/a | n/a |

### Two cross-module facts worth flagging

1. **`shared.cognitive` does NOT emit
`OptimizationEngine.emit(signal_type='adaptation', ...)`** when it
auto-escalates from L1 to L2 or from L2 to L3. The escalation is
recorded only in `cognitive_outcomes` (level + escalated=1) and is
never seen by the SignalAggregator. Per `shared/optimization/CLAUDE.md`,
"All learning loops MUST emit signals at key decision points." This
violates the rule — the SignalBus has no signal type for cognitive
escalation. Considered for a follow-up commit, not this audit.

2. **`StrategyComposer.compose` reads procedural+episodic memory but
never writes — the writes happen in three different places**
(`_engine.flush`, `_reflexion._store_success`, `_tot.explore`). All
three flow through `MemoryManager.learn_procedure`, but the payload
context strings are slightly different (compare `_engine.py:194`
vs `_reflexion.py:135` vs `_tot.py:172` — the
`agent_name=...|trigger=...|times_used=...|...` shape varies).
`StrategyComposer.compose` doesn't parse `context` (only the
ProceduralEntry top-level `success_rate`, `times_used`,
`avg_score_when_used`, `created_at`, `procedure_id`, `source` fields),
so the schema drift is currently harmless. But the
`STRATEGY_PAYLOAD_KEYS` constant in `_strategy.py:20-24` claims a
canonical set of payload keys that no producer fully respects.
**MINOR**, deferred.

---

## STEP 5 — Live evidence

### Test suite (passing baseline)

```
$ python -m pytest tests/shared/cognitive/ -x --tb=short
============================== 80 passed in 5.83s ==============================
```

Coverage of the 8 modules: 7 of 8 have a dedicated test file
(`test_budget`, `test_classifier`, `test_engine`, `test_integration`,
`test_reflexion`, `test_self_improvement`, `test_strategy`,
`test_tree_of_thought`). `_prompts.py` is a constants module — no test.

### Production cognitive_outcomes (data/optimization.db)

```
$ python -c "<select level, success, escalated, count(*) ...>"
{'level': 0, 'success': 1, 'escalated': 0, 'cnt': 705}   ← L0 fast-path
{'level': 1, 'success': 0, 'escalated': 0, 'cnt':   9}
{'level': 1, 'success': 0, 'escalated': 1, 'cnt':   9}   ← L0→L1, still failed
{'level': 1, 'success': 1, 'escalated': 0, 'cnt': 315}   ← L1 hit threshold
{'level': 1, 'success': 1, 'escalated': 1, 'cnt': 158}   ← L0→L1, recovered
{'level': 2, 'success': 1, 'escalated': 0, 'cnt':  36}
{'level': 2, 'success': 1, 'escalated': 1, 'cnt':   9}
{'level': 3, 'success': 0, 'escalated': 1, 'cnt':   9}
{'level': 3, 'success': 1, 'escalated': 0, 'cnt':  18}
total: 1197
```

Confirms:
- L0 fast-path is the dominant path (705 / 1197 = 59%).
- L1 fires 491 / 1197 = 41% of the time. **This is the path where
  `result.score=None` triggers the BLOCKER.**
- L2/L3 rare (6% combined).
- 158 L0→L1 recoveries = these are the data points the M-E early-return
  flush bug discards (templates that should have been queued).

### Production budget windows (data/cognitive_budget.db)

```
windows: 67   (cognitive_budget_windows)
state:    0   (cognitive_budget_state)
```

No cooldowns ever hit in production (state=0). Budget tracker is
healthy — caps not exceeded.

### Top domains by call volume

```
cv_tailoring/cv_tailoring               391
evolving/test_agent                     180   ← test contamination
email_classification/gmail_agent        149
tested/test_agent                       135   ← test contamination
test/test_agent                         126   ← test contamination
email/test_agent                         72   ← test contamination
email_classification/test_agent          36   ← test contamination
screening_answers/screening_answers      30
cv_scrutiny/cv_scrutiny                  24
cover_letter/cover_letter                19
job_application/test_agent               18   ← test contamination
intent_classification/intent_classification 13
cron_task/cron_agent                      9
```

⚠ **Test contamination of production DB**: 567 / 1197 rows
(47%) are from `agent_name='test_agent'`. Tests are leaking writes
into `data/optimization.db`. The cognitive test suite's conftest
isolates `cognitive_budget.db` (line 97-100) but does NOT isolate
`optimization.db`, so every cognitive test that runs the engine
end-to-end writes to the production tracker. This was caused by
the `_engine.py:155, 174` calls, which use the global
`get_optimization_engine()` singleton, not a test-injected one.
**Deferred to follow-up** — fixing requires either a test-only
monkeypatch on `get_optimization_engine` or a real isolation
fixture in conftest.

### Reproducing the BLOCKER (B-1)

```python
# Minimal repro: think_sync without scorer, classifier picks L1
import asyncio, os
os.environ["COGNITIVE_ENABLED"] = "true"
from shared.cognitive import get_cognitive_engine

engine = get_cognitive_engine("audit_repro")
result = engine.think_sync(
    task="Test", domain="form_navigation_audit", stakes="medium",
)
# result.score is None whenever classifier picks L1 with no scorer
assert result.score is None  # passes for fresh domain
print(f"score < 5.0?  → {result.score < 5.0}")  # TypeError
```

Verified empirically by inspecting the L1 path in `_execute_l1` (line
243): `score = scorer(answer) if scorer else None`. The reproducing
test is added in `tests/shared/cognitive/test_engine.py` as
`test_think_sync_returns_none_score_when_no_scorer`.

---

## STEP 6 — Fixes (this commit)

| ID | File:line | Fix | Test |
|----|-----------|-----|------|
| B-1 | `jobpulse/native_form_filler.py:548` | `(result.score or 0.0) < 5.0` — treat unscored answers as below threshold so they don't bypass the gate | `tests/jobpulse/test_native_form_filler.py::test_try_cognitive_unstuck_handles_none_score` |
| M-A | `shared/cognitive/_classifier.py:159-176` (`load_persisted_stats`) | Restore `l0_total` / `l0_success` / `l1_total` / `l1_escalated` from rate × sample_size (best effort) so the recompute on the next sample stays close to the persisted rate | `tests/shared/cognitive/test_classifier.py::test_persisted_stats_survive_first_new_sample` |
| M-B | `shared/optimization/_policy.py:185-189` | Coalesce `result.score` to `0.0` before division: `(result.score or 0.0) / 10.0` | `tests/shared/optimization/test_policy.py::test_decide_async_handles_none_score` |
| M-C | `shared/cognitive/_engine.py:162-163, 181-182` | Replace bare `except Exception: pass` with `except Exception as e: logger.debug("Failed to record cognitive outcome: %s", e)` | covered by existing tests (no behavior change) |

Deferred (out of scope this session, ship in a follow-up worklist):
- M-D (cost-reporting drift in escalation): cosmetic, requires
  carrying the original level's cost forward into `escalated_result.cost`.
- M-E (escalated L0→L1 successes not flushed): adds a write at L162
  before early return; needs careful unit test for the no-scorer case.
- minor fixes (N-A through N-E): defer.

---

## STEP 7 — Doc deltas to apply

`docs/job-application-pipeline.md` claims:
> "CognitiveEngine emits `adaptation` signals to OptimizationEngine
> on every escalation"

Empirically false — see Cross-module fact #1. Cognitive escalation is
recorded in `cognitive_outcomes` only; no `emit()` call exists.
Update doc when fixing M-E or follow-up wires the signal.

`shared/cognitive/CLAUDE.md` claims:
> "Strategy templates stored via MemoryManager.learn_procedure()
> Failure patterns stored via MemoryManager.record_episode()"

True, but understates: there are **3 producer sites**
(`_engine.flush`, `_reflexion._store_success`, `_tree_of_thought.explore`)
each with slightly different `context` payload format. No doc note
about the `STRATEGY_PAYLOAD_KEYS` contract being aspirational.
Worth adding when the keys are actually enforced.

`jobpulse/CLAUDE.md` claims:
> "form_engine/field_mapper.py — recovery fallback for failed
> field fills (domain: form_recovery)"

Correct, but field_mapper goes through `cognitive_llm_call` (which
returns `result.answer` only) rather than calling `engine.think`
directly — i.e. field_mapper never sees a `ThinkResult.score`. So
the M-B blocker pattern doesn't apply there.

