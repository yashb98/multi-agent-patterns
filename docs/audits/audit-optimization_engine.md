# Subsystem 10 — `optimization_engine` (line-by-line audit)

**Scope (matches audit prompt entry):**
- Entry: `OptimizationEngine.emit(...)`, `before_learning_action()`,
  `after_learning_action()`, `optimize()` (cron at hourly :15 +
  Sunday-night `learning-maintenance`), plus indirect entry via
  `get_optimization_engine()` singleton consumed across the
  codebase (44 in-tree call sites).
- Files (9 modules, ~2 650 LOC):
  - `shared/optimization/__init__.py` (50 LOC) — re-exports
  - `shared/optimization/_signals.py` (194 LOC) — `LearningSignal`, `SignalBus`
  - `shared/optimization/_aggregator.py` (388 LOC) — `SignalAggregator`, 7 detectors
  - `shared/optimization/_policy.py` (333 LOC) — `OptimizationPolicy`, budget, 14 action types
  - `shared/optimization/_tracker.py` (388 LOC, +14 post-fix) — `PerformanceTracker`, 3 tables
  - `shared/optimization/_trajectory.py` (296 LOC) — `TrajectoryStore`, JSONL/CSV export
  - `shared/optimization/_replay.py` (185 LOC) — fixture-based diff harness
  - `shared/optimization/_engine.py` (608 LOC, +14 post-fix) — facade + cycle orchestration
  - `shared/optimization/_gate_policy.py` (242 LOC) — orphan; only test imports it
- Output of the subsystem:
  - `data/optimization.db` — 8 tables: `signals`, `trajectories`,
    `trajectory_steps`, `performance_snapshots`, `learning_actions`,
    `cognitive_outcomes`, `forced_level_overrides`, `paused_loops`,
    `budget_state`. Production row counts: 32 941 / 2 642 / 4 636 /
    530 / 280 / 1 571 / 2 / 0 / 5.
  - Signals consumed by `OptimizationEngine.optimize()` (cron) which
    drives `SignalAggregator` → `OptimizationPolicy` → `_execute_one`.

---

## 1. Function inventory + wiring

### Category legend
- **A** — runtime: definitely called during `optimize()` cron / apply pipeline
- **B** — runtime-conditional: only when an env flag / consumer is wired
- **C** — runtime-unreachable from apply path; tests / CLI only
- **D** — orphan: imported nowhere; truly dead

### 1.1 `_signals.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 14 | `VALID_SIGNAL_TYPES` | A | Validates 7 types — `transfer` was added 2026-05-07 (S5 audit fix). Production producers: all 7 fire; only `score_change` is anaemic (7 rows). |
| 29 | `LearningSignal` | A | Frozen-ish dataclass; `__post_init__` raises `ValueError` on bad type/severity. |
| 57 | `SignalBus` | A | SQLite + 1000-deque. `_db_path` accessed by `_aggregator.__init__` (40 — fine). |
| 99 | `SignalBus.emit` | A | Producers: 16 distinct call sites (handler_registry, navigator, post_apply_hook, native_form_filler, …). |
| 115 | `SignalBus.query` | A | Consumed by `SignalAggregator.sweep` (120) and `OptimizationEngine.daily_report` (462). |
| 167 | `SignalBus.recent_from_db` | A | Used by `_aggregator.check_realtime` (79) when in-memory deque is empty (daemon restart fallback). |
| 173 | `SignalBus.prune` | A | Called by `weekly_maintenance` (Sun 9 PM cron). |

### 1.2 `_aggregator.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 22 | `AggregatedInsight` | A | Carries `domain` only — **no `agent_name`** (foundational design choice causing B-1). |
| 31 | `SignalAggregator` | A | Constructed inside `OptimizationEngine.__init__:64`. |
| 57 | `pause_loop` / 66 `resume_loop` | A | Public; persisted via `paused_loops` table (0 production rows — never used in prod). |
| 74 | `check_realtime` | A | Driven by `optimize` cycle: 4 detectors run on the in-memory deque. |
| 86 | `check_regressions` | A | Reads `learning_actions` (280 rows in prod), compares before/after. |
| 117 | `sweep` | A | 24-hour SQLite-window pass. |
| 128 | `_detect_systemic_failures` | A | Confidence scaling D1, dedup gate via `_dedup_with_memory`. |
| 168 | `_detect_platform_change` | A | `severity == "critical"` + ≥3 failures per domain. |
| 190 | `_detect_persona_drift` | A | Linear-regression slope on `score_change` payloads — only 7 prod signals total → never fires in prod. |
| 233 | `_detect_redundant` | A | Cross-loop overlap detection. |
| 255 | `_detect_repeated_failures` | A | Triggers `investigate_domain` action. |
| 278 | `_detect_success_patterns` | A | Drives `promote_strategy`. |
| 315 | `_detect_adaptation_effectiveness` | A | Fires `reinforce_adaptation` (M-D candidate, see findings). |
| 359 | `_dedup_with_memory` | A | Bare `except Exception: pass` (m-1 deferred). |
| 379 | `_cross_domain_search` | A | Bare `except Exception: return []` (m-2 deferred). |

### 1.3 `_policy.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 17 | `OptimizationBudget` | A | 4 budget knobs; consumed in `_handle_*`. |
| 25 | `PolicyAction` | A | Carries `domain` + `target` + `evidence` + `confidence` — **no `agent_name`**. |
| 33 | `OptimizationPolicy` | A | Constructed inside `OptimizationEngine.__init__:68`. |
| 62 | `_load_budget_state` | A | `except Exception: pass` swallow (m-3 deferred). Monotonic↔wall-clock conversion at 71-75 is opaque but functionally correct (verified by trace; resets on first `_maybe_reset_window` call when monotonic_elapsed > 3600). |
| 113 | `decide` | A | 7 pattern-type routes; falls through to `alert_human` for low-confidence orphans. |
| 155 | `decide_async` | C | **Never called from production**. Only tests touch it (`test_policy.py`). All callers use `decide`, not `decide_async`. The `cognitive_decision` action it emits has zero production rows and zero consumers in `_execute_one`. (See finding W-1). |
| 198 | `_handle_systemic` | A | Emits `generate_insight` + `escalate_cognitive`. |
| 220 | `_handle_regression` | A | `rollback` + `demote_memory` + `escalate_cognitive`. Budget-gated. |
| 259 | `_handle_drift` | A | `rollback_persona` + `pause_loop`. (Persona drift detector never fires — too few `score_change` signals.) |
| 277 | `_handle_success_streak` | A | `promote_strategy` + (≥0.8 confidence) `generate_insight`. |
| 299 | `_handle_adaptation_worked` | A | `reinforce_adaptation` + `freeze_baseline`. |

### 1.4 `_tracker.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 19 | `PerformanceSnapshot` / 27 `DomainStats` | A | Returned across the engine API. |
| 41 | `PerformanceTracker` | A | Constructed inside `OptimizationEngine.__init__:60`. |
| 108 | `snapshot` | A | Producers: every `optimize` cycle + `before/after_learning_action`. |
| 133 | `_store_baseline` | A | **(M-A FIXED 2026-05-08, this commit)** Was `hasattr(self._memory, "pin")` — MemoryManager exposes `pin_memory`. Pre-fix: 30+ snapshot baselines stored unpinned, eligible for forgetting-engine eviction. |
| 153 | `before_learning_action` | A | 280 prod rows — confirms cron is firing. |
| 166 | `after_learning_action` | A | Returns `{regression, improved, before, after, action_id}`. |
| 221 | `get_recent_actions` | A | Consumed by `_aggregator.check_regressions:88`. |
| 284 | `set_forced_level` | A | Producer: `_engine._execute_one("escalate_cognitive"):312`. Schema: `(domain, agent_name=domain, level, reason)`. Production rows: 2 (`a.com|a.com|2`, `greenhouse.io|greenhouse.io|2`). |
| 303 | `record_cognitive_outcome` | A | Producer: `shared/cognitive/_engine.py:155, 174`. Schema: `(domain, agent_name=real_agent, level, success, escalated)`. Production agent_names: `cv_tailoring=391`, `gmail_agent=161`, `screening_answers=32`, `cv_scrutiny=24`, … plus `test_agent=845` (T-1 pollution). |
| 315 | `get_domain_stats` | A | **(B-1 FIXED 2026-05-08, this commit)** Pre-fix returned `forced_level=None` whenever `cognitive_outcomes WHERE domain=? AND agent_name=?` was empty. Real outcomes use real agent names; the (domain, domain) lookup performed by `_classifier.classify` always saw 0 rows → override row was never even queried. Post-fix the override is read on the same connection and exposed even when sample_size=0. |

### 1.5 `_trajectory.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 16 / 28 | `TrajectoryStep`, `Trajectory` | A | Schemas. |
| 42 | `TrajectoryStore` | A | Constructed inside `OptimizationEngine.__init__:59`. |
| 103 | `start` | A | 2 642 prod rows. Producers: post_apply_hook, persona_evolution, application_orchestrator, scan_pipeline, weekly_optimize, dispatcher (10 distinct callers). |
| 116 | `log_step` | A | 4 636 prod rows ÷ 2 642 trajectories = 1.75 steps/trajectory — anomaly: most trajectories complete with no logged steps. (Documented in worklist; not strictly a bug — many start-then-fail paths.) |
| 130 | `complete` | A | Patches the started row with outcome+score. |
| 175 | `query` | A | Consumed by `_aggregator` (no — only by `replay.write_replay_fixture` and `weekly_maintenance` export). |
| 234 | `prune` | A | Sun 9 PM cron. |
| 255 / 281 | `export_jsonl` / `export_csv` | A | ShareGPT-format JSONL + CSV. |

### 1.6 `_replay.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 21 | `ReplayFixture` | A | Test-asset generator. |
| 65 | `render_replay_digest` | A | Deterministic digest. |
| 103-159 | `select_top_trajectories`, `build_replay_fixtures`, `write_replay_fixture`, `load_replay_fixture` | A | Used by `tests/shared/optimization/test_replay.py` and CI-friendly fixture generation. |
| 162 | `diff_replay_fixture` | A | Returns unified-diff string. |
| 182 | `assert_replay_fixture_matches` | A | Test-only assertion helper. |

### 1.7 `_engine.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 18 | `_get_auto_rule_generator` | A | Lazy import to break circular dep with `jobpulse.auto_rule_generator`. |
| 35 | `_default_db_path` | A | Lazy `DATA_DIR` resolution (avoids import-time side effect — Principle 1). |
| 40 | `OptimizationEngine` | A | Facade. |
| 84 | `set_alert_fn` | A | Wired by `jobpulse.runner:347` (Telegram callback). |
| 92 | `emit` | A | Sole signal entry point. 16 in-tree producers. |
| 112-126 | `before_learning_action` / `after_learning_action` / `snapshot` | A | Wraps `PerformanceTracker`. |
| 132-156 | `start_trajectory` / `log_step` / `complete_trajectory` | A | Wraps `TrajectoryStore`; sets/clears `RunIdFilter` trajectory_id contextvar. |
| 162-172 | `record_cognitive_outcome` / `get_domain_stats` | A | Wraps tracker. Bridge to `shared/cognitive`. |
| 178-204 | `promote_memory` / `demote_memory` / `revive_memory` / `resolve_contradiction` | A | MemoryManager bridge. |
| 210 | `optimize` | A | **The cron entry point.** Hourly :15. **(M-C FIXED — promoted snapshot-fail log to warning.)** |
| 262 | `_mine_trajectory_insights` | A | **(M-C FIXED — promoted mining-fail log to warning.)** |
| 291 / 304 | `_execute_actions` / `_execute_one` | A | 14 action types. **(M-C FIXED — promoted alert callback fail to warning.)** Eight remaining `logger.debug` swallows (memory ops, alert fail in `investigate_domain`, rule-deploy fall-through) deferred to worklist. |
| 394 | `_deploy_auto_rule` | A | Calls `AutoRuleGenerator.deploy_rule`. |
| 426 | `_parse_action_to_rule` | A | Uses regex `(\d+) corrections on '...'` against evidence string — structural parsing of a known format, NOT semantic classification. Principle 8 OK. |
| 462 / 472 / 498 | `get_report` / `daily_report` / `weekly_maintenance` | A | Cron-driven (`/runner.py:347, 357`, `/dispatcher.py:882`). |
| 525 / 529 | `pause_loop` / `resume_loop` | A | Public; private-attr access at L525 (`self._aggregator._paused_loops`) — minor style issue, deferred. |
| 537 | `health` | A | Used by `engine.health()` (runner). |
| 546-580 | `_NoOpBus`, `_NoOpTrajectory`, `_NoOpTracker` | B | Active when `OPTIMIZATION_ENABLED=false`. Each `_NoOp*` returns vacuous values. |
| 585 | `get_optimization_engine` | A | Singleton factory; constructs `MemoryManager` + `CognitiveEngine` lazily. |

### 1.8 `_gate_policy.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 41 / 53 | `ThresholdSuggestion` / `GatePolicy` | **D** | Module is **never imported by production code**. Only `tests/shared/optimization/test_gate_policy.py` imports it. Not registered in `shared/optimization/__init__.py`. (M-B fix: import direction violation cleaned up regardless — the module remains in tree as a parked threshold-tuning prototype.) |
| 56-241 | `__init__`, `suggest_thresholds`, `_analyze_gate3`, `_analyze_gate2`, `get_all_suggestions`, `_discover_domains`, `format_report` | D | All apply-path-unreachable. `_discover_domains` (190) uses hardcoded English-only keyword classification — Principle 8 violation but does not run in production. |

### 1.9 `__init__.py`

50 LOC, only re-exports. No code paths.

**Wiring categorisation summary:** 80 functions in category A, 5 NoOp shims in category B (`OPTIMIZATION_ENABLED=false` only), 1 unreachable-from-prod method `decide_async` (C), 8 dead-from-prod methods in `_gate_policy.py` (D). 0 functions in category E (no overrides found).

---

## 2. Cross-module wiring

### 2.1 Producer / consumer map (signals)

| Signal type | Producer (file:line) | Consumer (file:line) | Schema agreement |
|---|---|---|---|
| `correction` | `correction_capture.py:126`, `native_form_filler.py:144`, `ai_assist_logger.py:861, 883` | `_aggregator._detect_systemic_failures:131-166` reads `payload["field"]` | ✅ |
| `failure` | 6 producers across navigator, executor, scan_learning, persona_evolution | `_aggregator._detect_platform_change:171`, `_detect_repeated_failures:259` | ✅ |
| `success` | `live_review_applicator:1155`, `strategy_reflector:289`, `weekly_optimize:458`, more | `_aggregator._detect_success_patterns:282`, `_detect_adaptation_effectiveness:319` | ✅ |
| `adaptation` | `navigation/action_executor.py:383`, `platform_bypass.py:406`, `persona_evolution.py:141`, more | `_aggregator._detect_adaptation_effectiveness:319` reads `payload["param"]`, `payload["old_value"]`, `payload["new_value"]` | ✅ |
| `score_change` | `persona_evolution.py:261, 280` | `_aggregator._detect_persona_drift:193-231` reads `payload["new_score"]` | ✅ schema, ⚠️ volume (7 prod rows total — drift detector effectively dormant) |
| `rollback` | `_engine._execute_one("rollback"):330-333` self-emits | No detector — record-keeping only | OK (intentional) |
| `transfer` | `platform_transfer.record_outcome` (added S5 audit fix) | **No aggregator consumer** — 35 prod rows just stored | ⚠️ **Wiring gap W-1** (deferred, see worklist) |

### 2.2 DB tables — write/read map

| Table | Writer | Reader | Status |
|---|---|---|---|
| `signals` | `SignalBus.emit:99` | `SignalBus.query/recent_from_db/count`, `SignalAggregator.*` | ✅ wired both ways |
| `trajectories` | `TrajectoryStore.start:103`, `complete:130` | `TrajectoryStore.query`, `_replay.write_replay_fixture` | ✅ |
| `trajectory_steps` | `TrajectoryStore.log_step:116` | `TrajectoryStore._load`, `query`, `export_jsonl/_csv` | ✅ |
| `performance_snapshots` | `_tracker.snapshot:108` (incl. `optimize` cycle:238) | `_tracker.get_snapshots:242`, `get_avg_metric`, `get_trend` | ✅ |
| `learning_actions` | `_tracker.before_learning_action:153` (insert), `after_learning_action:166` (update) | `_aggregator.check_regressions:88` (`get_recent_actions`) | ✅ |
| `cognitive_outcomes` | `_tracker.record_cognitive_outcome:303` (called by `shared/cognitive/_engine.py:155, 174` with **real** agent_name) | `_tracker.get_domain_stats:315` (called by `shared/cognitive/_classifier.py:49` with **(domain, domain)**) | ⚠️ **Schema-shape mismatch — see B-1.** Producer writes `agent_name=cv_tailoring/screening_answers/…`; consumer reads `agent_name=domain`. Pre-fix → sample_size always 0. (Mitigation post-fix: forced_level still surfaces; computed l0/l1/l2/l3 success rates remain 0.0 for this lookup shape — known soft gap, deferred to worklist.) |
| `forced_level_overrides` | `_tracker.set_forced_level:284` (called by `_engine._execute_one("escalate_cognitive"):312` with `agent_name=action.domain`) | `_tracker.get_domain_stats:315` (called by `_classifier.classify` with `(domain, domain)`) | ✅ post-fix; pre-fix dead-on-arrival because `get_domain_stats` early-returned at `sample_size=0` before reading the override. |
| `paused_loops` | `_aggregator.pause_loop:57` | `_aggregator._load_paused_loops:52`, `_filter_paused:71` | ✅ wired; 0 prod rows (the `pause_loop` policy action has never fired in production). |
| `budget_state` | `_policy._save_budget_state:79` | `_policy._load_budget_state:62` | ✅ |

### 2.3 MemoryManager API surface used by optimization

| Method called | File:line | MemoryManager exposes? |
|---|---|---|
| `learn_fact(domain, fact, run_id)` | `_engine:296`, `_tracker:139` | ✅ `_manager.py:312` |
| `learn_procedure(domain, strategy, score, source)` | `_engine:338` | ✅ `_manager.py:326` |
| `pin_memory(memory_id)` | `_engine:361`, `_tracker:144` (post-fix) | ✅ `_manager.py:518` |
| `pin(memory_id)` | (pre-fix) `_tracker:144` | ❌ **does not exist** — caused M-A. |
| `search_semantic(query, domain, limit)` | `_engine:320, 354`, `_aggregator:363, 383` | ✅ |
| `demote(id)` / `promote(id)` / `revive(id)` / `contradict(id)` | `_engine:326, 192`, `_policy:321, 328, 333` | ✅ all present |

---

## 3. Findings (line-by-line read)

### Severity legend: blocker | major | minor | nit

#### BLOCKERS

- `shared/optimization/_tracker.py:315-345` (`get_domain_stats`) **+** `shared/cognitive/_classifier.py:46-58` (`classify`) **[blocker — FIXED commit `aa6fe74`]**
  The OptimizationEngine `escalate_cognitive` policy action wrote rows
  into `forced_level_overrides` (production: 2 rows) but the override
  was never honoured end-to-end. Pre-fix: `get_domain_stats` early-
  returned `forced_level=None` when no `cognitive_outcomes` matched
  `(domain, agent_name)`. Real cognitive outcomes are stored with the
  agent's *real* name (cv_tailoring, screening_answers, gmail_agent),
  while `_classifier.classify` looks up `(domain, domain)`. The
  override was therefore stored at `(domain, domain)` but the row was
  never even queried. The classifier ALSO gated `forced_level` behind
  `sample_size >= 20`, redundantly. Both layers fixed in this session.
  Regression test: `tests/shared/cognitive/test_classifier.py::TestEscalationClassifier::test_optimization_forced_level_honored_when_sample_size_zero`.

#### MAJORS

- `shared/optimization/_tracker.py:144` **[major — FIXED commit `aa6fe74`]**
  `_store_baseline` after 30 snapshots called `hasattr(self._memory, "pin")` and `self._memory.pin(memory_id)`. `MemoryManager` exposes `pin_memory`, not `pin`, so the branch was always False. Net effect: every 30+ snapshot baseline learned a `learn_fact` row but the row was never pinned and was eligible for forgetting-engine eviction. Switched to `pin_memory`. Regression test: `tests/shared/optimization/test_tracker.py::TestPerformanceTracker::test_baseline_pin_uses_pin_memory_not_pin` (uses a `_PinOnlyMemory` stub that exposes only `pin_memory` so the test fails loud if the call regresses to `pin`).

- `shared/optimization/_gate_policy.py:19` **[major — FIXED commit `619ee4c`]**
  `from jobpulse.config import DATA_DIR` violates Principle 1 (shared/ MUST NOT import from jobpulse/). Re-routed through `shared.paths.DATA_DIR`. Module is otherwise unreachable from production (category D), but the import-direction rule applies regardless. Regression test: `tests/shared/optimization/test_gate_policy.py::TestGatePolicy::test_module_does_not_import_from_jobpulse`.

- `shared/optimization/_engine.py:240, 277, 308` **[major — FIXED commit `619ee4c`]**
  Three OPRAL-critical failure paths emitted at `logger.debug`, silently dropping signal:
  - `optimize:_execute_one("alert_human")` — alert callback fail (the only user-visible signal for the cycle; debug-level meant Telegram alerts could disappear with no log).
  - `optimize` cycle — `performance_snapshots` write fail (regression-detection data loss for the cron tick).
  - `_mine_trajectory_insights` — silent failure drops every auto-rule the cycle would have generated.
  All three promoted to `logger.warning(..., extra={"error_type": ...})`.

#### MINORS (deferred to worklist)

- `_engine.py:329, 346, 364, 371` `logger.debug` swallows on memory `demote`/`promote`/`pin`/alert-callback in `investigate_domain` action paths.
- `_engine.py:381, 397, 406` `logger.debug` swallows on `AutoRuleGenerator unavailable` / rule-deploy fall-through / outer auto-rule deploy.
- `_aggregator.py:359, 379` bare `except Exception: pass` / `return []` on memory dedup + cross-domain search.
- `_policy.py:62-77` `_load_budget_state` bare `except: pass`.
- `_policy.py:71-75` Monotonic↔wall-clock conversion is opaque but functionally correct (verified: `_maybe_reset_window` cleans up on first call when monotonic_elapsed > 3600). Refactor to `if (time.time() - saved_window) < 3600` for clarity.
- `_tracker.py:362` `correction_rate` bare `except Exception: pass` — silent zero on signal-bus failure.
- `_engine.py:525` `self._aggregator._paused_loops` private-attr access in `health()`.
- `_engine.py:567-572` `_NoOpTracker` returns mostly-empty `DomainStats` on the disabled path — fine, but the `agent_name=domain` import shape inside `get_domain_stats` could be hardened.

#### NITS

- `_engine.py:18-28` `_get_auto_rule_generator` references `logger` before module-level `logger = get_logger(__name__)` at line 30. Functionally fine (lazy evaluation when called), but reads weird.
- `_signals.py:103` `INSERT OR IGNORE` silently drops UUID-collision dupes (vanishingly unlikely; no action).

#### DEAD CODE

- `_policy.py:155` `decide_async` — only test calls it; production uses `decide`. The `cognitive_decision` action it emits has zero rows in production and zero handlers in `_execute_one`. The whole async branch (LLM-fallback policy) is dead. Documented as W-2 in worklist; not deleted (out of scope for this audit).
- `_gate_policy.py` (whole module) — only test imports it. Documented above; M-B import fix shipped, deletion deferred (parked threshold-tuning prototype).

#### WIRING GAPS

- **W-1 `transfer` signal — no aggregator consumer.** Producer fires (35 prod rows). `SignalAggregator` has 7 detectors covering 6 signal types; none key on `transfer`. The signals are stored but never trigger an insight. Could be intentional (cross-domain transfer outcomes are recorded, not acted on), but the lack of any documentation makes it a wiring smell. Deferred to worklist.
- **W-2 `decide_async` / `cognitive_decision` action** — emit-without-consume in `_execute_one`. Either delete or wire.
- **W-3 `cognitive_outcomes` schema-shape mismatch (soft).** Producer writes real agent_name; consumer (classifier) queries `(domain, domain)`. The forced_level override is now surfaced (B-1 fix), but the computed l0/l1/l2/l3 success rates derived from `cognitive_outcomes` remain 0.0 for the `(domain, domain)` shape. The L0 fast-path at `_classifier.py:57` (`opt_stats.l0_success_rate >= 0.95 and opt_stats.sample_size >= 20`) therefore still never fires. Fix is bigger: either change the classifier to look up by domain only (aggregating across agents), or thread the agent_name through `EscalationClassifier`. Deferred — needs design discussion.

#### TEST-SUITE FINDINGS

- **T-1 production DB pollution (carryover from S6 audit).** `data/optimization.db` `cognitive_outcomes` has **845 rows** with `agent_name='test_agent'` out of 1 571 total (54%). Plus `agent_3=13`, `agent_4=13`, `cron_agent=13`. The S6 audit identified the test conftest at `tests/shared/cognitive/conftest.py:96-100` isolates `cognitive_budget.db` via env override but does NOT isolate `data/optimization.db`. Tests leak via the `get_optimization_engine()` singleton inside `record_cognitive_outcome`. Fix sketch: monkeypatch `shared.optimization.get_optimization_engine` to return a tmp-DB instance for the cognitive test scope. Not shipped in this audit (advisor flagged that this needs to be checked against `test_wiring_e2e` and similar tests that intentionally exercise the singleton). Deferred to worklist.

- **MockMemoryManager hides the M-A bug.** `tests/shared/optimization/conftest.py:69, 72` exposes BOTH `pin` and `pin_memory` on the mock. The S10 audit M-A test deliberately uses a `_PinOnlyMemory` stub (only `pin_memory`) to bypass this defensive over-mocking and force a regression-loud assertion. Cleanup of the conftest mock surface is deferred to the worklist (cleaning it up risks breaking the existing 192-test suite that relies on the loose mock).

---

## 4. Live evidence

### 4.1 Production DB inspection (2026-05-08)

```
$ sqlite3 data/optimization.db ".tables"
budget_state            learning_actions        signals
cognitive_outcomes      paused_loops            trajectories
forced_level_overrides  performance_snapshots   trajectory_steps

$ for t in signals trajectories trajectory_steps performance_snapshots \
           learning_actions cognitive_outcomes forced_level_overrides \
           paused_loops budget_state; do
    echo -n "$t: "; sqlite3 data/optimization.db "SELECT COUNT(*) FROM $t"
  done

signals: 32941
trajectories: 2642
trajectory_steps: 4636
performance_snapshots: 530
learning_actions: 280
cognitive_outcomes: 1571
forced_level_overrides: 2
paused_loops: 0
budget_state: 5

$ sqlite3 data/optimization.db "SELECT signal_type, COUNT(*) FROM signals GROUP BY signal_type"
adaptation|10928
correction|2259
failure|4363
score_change|7
success|15349
transfer|35

$ sqlite3 data/optimization.db "SELECT * FROM forced_level_overrides"
a.com|a.com|2|11 corrections on a.com/salary across 11 sessions|2026-05-03T19:05:29.204931+00:00
greenhouse.io|greenhouse.io|2|9 corrections on greenhouse.io/Salary across 9 sessions|2026-05-03T19:05:29.206040+00:00

$ sqlite3 data/optimization.db "SELECT agent_name, COUNT(*) FROM cognitive_outcomes GROUP BY agent_name ORDER BY COUNT(*) DESC LIMIT 10"
test_agent|845
cv_tailoring|391
gmail_agent|161
screening_answers|32
cv_scrutiny|24
cover_letter|22
intent_classification|15
cron_agent|13
agent_4|13
agent_3|13
```

Notes from the inspection:
1. The 2 `forced_level_overrides` rows confirm `escalate_cognitive` HAS fired in production (writing pre-fix), proving the writer half of B-1 was active.
2. The agent_name values in `cognitive_outcomes` (`cv_tailoring`, `screening_answers`, …) confirm the schema-shape mismatch with `forced_level_overrides` (`a.com`, `greenhouse.io`).
3. `paused_loops=0` confirms the `pause_loop` policy action has never fired in production (no persona drift detected → expected, given only 7 `score_change` signals).
4. `transfer=35` confirms the producer added in S5 audit fires; cross-checked W-1 (no consumer).
5. `test_agent=845` confirms T-1 (54% test pollution).

### 4.2 Test runs

After fixes:

```
$ python -m pytest tests/shared/optimization/ tests/shared/cognitive/ -q --timeout=60
192 passed, 12 warnings in 75.44s

$ python -m pytest tests/jobpulse/test_learning_loops.py \
    tests/jobpulse/test_adaptation_chains_real.py \
    tests/jobpulse/test_post_apply_hook.py \
    tests/jobpulse/test_post_apply_integration.py \
    tests/jobpulse/test_wiring_e2e.py \
    tests/jobpulse/test_scan_learning_wiring.py --timeout=120
49 passed, 24 warnings in 14.90s
```

3 new regression tests added:
- `tests/shared/cognitive/test_classifier.py::TestEscalationClassifier::test_optimization_forced_level_honored_when_sample_size_zero` (B-1)
- `tests/shared/optimization/test_tracker.py::TestPerformanceTracker::test_baseline_pin_uses_pin_memory_not_pin` (M-A)
- `tests/shared/optimization/test_gate_policy.py::TestGatePolicy::test_module_does_not_import_from_jobpulse` (M-B)

---

## 5. Fixes shipped this session

| ID | Severity | Commit | Files |
|---|---|---|---|
| B-1 | blocker | `aa6fe74` | `shared/optimization/_tracker.py`, `shared/cognitive/_classifier.py`, 1 new test |
| M-A | major | `aa6fe74` | `shared/optimization/_tracker.py`, 1 new test |
| M-B | major | `619ee4c` | `shared/optimization/_gate_policy.py`, 1 new test |
| M-C | major | `619ee4c` | `shared/optimization/_engine.py` (3 log promotions) |

Test count delta: **+3 new regression tests**.

## 6. Deferred to follow-up worklist

See `docs/audits/audit-followup-worklist.md` § Subsystem 10 for the full list:

- 8 minors (log-level promotions, bare `except: pass`, monotonic↔wall-clock cleanup)
- 2 nits
- 2 dead-code modules / methods (`_policy.decide_async`, whole `_gate_policy.py`)
- 3 wiring gaps: W-1 (`transfer` signal no consumer), W-2 (`cognitive_decision` action no handler), W-3 (`cognitive_outcomes` schema-shape mismatch — soft)
- T-1 (test pollution into production DB; conftest fix)
- MockMemoryManager defensive over-mocking (hides bugs of M-A shape)

None of these share a function with a shipped fix; per the audit prompt, they were intentionally not bundled to keep this session's scope tight.
