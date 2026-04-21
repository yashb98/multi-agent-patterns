# Continuous Learning & Optimization Engine — Design Spec

**Date:** 2026-04-21
**Pillar:** 3 of 6 (Autonomous Agent Infrastructure)
**Status:** Design approved, pending implementation plan
**Depends on:** Pillar 1 (Memory System Upgrade) — insights/strategies stored in 3-engine stack
**Depends on:** Pillar 2 (Cognitive Reasoning) — CognitiveEngine used for novel policy decisions

---

## Problem Statement

The system has 9 prompt-space learning loops that genuinely improve behavior over time:

| Loop | Module | What It Learns |
|------|--------|---------------|
| ExperienceMemory (GRPO) | `shared/experiential_learning.py` | Successful patterns from group sampling |
| PersonaEvolution | `jobpulse/persona_evolution.py` | Agent prompt improvements via A/B testing |
| CorrectionCapture | `jobpulse/correction_capture.py` | Human corrections → override rules |
| AgentRules | `jobpulse/agent_rules.py` | Auto-generated rules from corrections + rejections |
| ScanLearning | `jobpulse/scan_learning.py` | Anti-detection param adaptation (17 signals) |
| FormExperienceDB | `jobpulse/form_experience_db.py` | Per-domain form structure caching |
| NavigationLearner | `jobpulse/navigation_learner.py` | Per-domain navigation sequence replay |
| FormInteractionLog | `jobpulse/form_interaction_log.py` | Step-by-step field action recording |
| A/B Testing | `jobpulse/ab_testing.py` | Statistical prompt variant comparison |

**Five critical problems:**

1. **No measurement.** No loop knows whether its learning actually improved outcomes. PersonaEvolution evolves prompts, but nobody tracks whether those prompts reduced corrections. ScanLearning adapts params, but there's no regression alert if block rates spike after adaptation.

2. **No coordination.** When CorrectionCapture sees 3 salary field fixes AND FormExperienceDB shows Workday failures AND ScanLearning shows blocks, that's one systemic issue — but the loops treat them as three independent problems.

3. **No regression detection.** A bad persona evolution or a flawed agent rule can silently degrade the system. There's no before/after measurement for any learning action.

4. **No cross-domain transfer.** An insight learned about "Workday salary requires integer format" could help when encountering "Indeed compensation field format" — but insights are siloed by exact domain string match.

5. **No training data collection.** When Blackwell fine-tuning comes online, there will be no structured trajectories to train on. The system loses most of its operational experience.

---

## Solution: OptimizationEngine with Signal-Driven Architecture

A `shared/optimization/` module that any agent pipeline opts into via signal emission. The engine observes all learning loops, measures their impact, detects patterns across loops, and takes coordinated action.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Signal-driven bus + central engine | Mirrors P1 (MemoryManager) and P2 (CognitiveEngine) — single facade, opt-in |
| Integration burden | 1-2 lines per existing loop | Emit signal at key decision points. No rewrites. |
| Storage split | Own SQLite for events, MemoryManager for knowledge | Raw events need fast indexed queries. Distilled knowledge needs 3-engine search. |
| Policy approach | Rule-based first, CognitiveEngine fallback | Zero LLM cost for 90%+ of decisions. Same pattern as NLP classifier. |
| Trajectory format | ShareGPT-compatible JSONL | Ready for fine-tuning when Blackwell pipeline activates |
| Kill switch | `OPTIMIZATION_ENABLED=false` env var | Engine becomes full no-op. Same pattern as `COGNITIVE_ENABLED=false`. |

---

## Architecture

```
┌──────────────────────── OptimizationEngine (facade) ───────────────────────┐
│                                                                            │
│   ┌─────────┐    ┌────────────┐    ┌─────────┐    ┌──────────────────┐    │
│   │ Signal  │───▶│ Aggregator │───▶│ Policy  │───▶│ Actions:         │    │
│   │  Bus    │    │            │    │         │    │ • rollback       │    │
│   │         │    │ detects    │    │ decides │    │ • escalate       │    │
│   │ all 9   │    │ cross-loop │    │ what to │    │ • generate rule  │    │
│   │ loops   │    │ patterns   │    │ do      │    │ • promote memory │    │
│   │ emit    │    │            │    │         │    │ • alert human    │    │
│   │ here    │    └────────────┘    └─────────┘    └──────────────────┘    │
│   └─────────┘          │                                                  │
│        │               ▼                                                  │
│        │        ┌────────────┐    ┌──────────────┐                        │
│        └───────▶│ Tracker    │    │ Trajectory   │                        │
│                 │            │    │ Store        │                        │
│                 │ before/    │    │              │                        │
│                 │ after per  │    │ structured   │                        │
│                 │ learning   │    │ action logs  │                        │
│                 │ action     │    │ (JSONL-ready)│                        │
│                 └────────────┘    └──────────────┘                        │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 6-Pillar Dependency Map

Pillar 3 is the nervous system — it carries feedback signals between all other pillars.

### P3 → P1 (Memory): "What to remember"

- Memory stores knowledge. P3 decides what's WORTH storing.
- When P3's tracker confirms a learning action improved outcomes → `memory.reinforce(memory_id)` → heat bump → promotion toward LTM.
- When a learning action caused regression → `memory.demote(memory_id)` → faster Ebbinghaus decay.
- P3 writes aggregated insights to SEMANTIC tier, effective strategies to PROCEDURAL tier, regression records to EPISODIC tier, successful optimization patterns to PATTERN tier.
- P3 provides the "usefulness" signal that modulates Memory's 6-signal Ebbinghaus decay formula. Without P3, decay is purely time-based. With P3, memories that lead to good outcomes decay slower.
- Qdrant enables cross-domain transfer: insight about "Workday salary integer format" found when querying "Indeed compensation format."
- Neo4j enables root cause tracing: regression → caused_by → learning_action → triggered_by → signals.
- AutonomousLinker auto-discovers related memories when P3 writes new insights.
- Contradiction detection fires when new insights conflict with existing ones — P3's policy resolves by comparing signal evidence.
- Memory revival: P3 can revive tombstoned memories when domain performance degrades and old knowledge becomes relevant again.
- Protection levels: P3 marks critical baselines as PINNED (never decay), hub-node insights as ELEVATED.

### P3 → P2 (Cognitive): "How to think better"

- P3 feeds domain stats into EscalationClassifier as Step 0 overrides: "email_classification L0 success 97% → always L0" or "workday_forms L1 success 30% → start at L2."
- P3 measures StrategyComposer template effectiveness: which templates correlate with good outcomes. Good templates get reinforced, bad ones decay.
- P3 tracks L2 Reflexion and L3 ToT outcomes per domain (score, attempts, cost, success rate). If L2 isn't improving scores, classifier skips it.
- If L3 ToT consistently picks the same branch strategy for a domain, P3 generates a PROCEDURAL template → future tasks use L0 instead of L3. This is how L3 costs auto-reduce over time.
- P3 monitors cognitive BudgetTracker utilization, alerts when near limits.
- P3 tracks auto-escalation frequency per domain to tune classifier thresholds.
- P3 provides the accuracy measurements that the self-improving classifier needs.
- P3's policy uses CognitiveEngine.think() for novel decisions (LLM fallback).
- P3 calls cognitive_engine.flush() after every optimize() run.

### P3 → P4 (Durable Execution, future)

- When LangGraph gets checkpointing, trajectory store provides recovery context.
- Failed/recovered executions emit signals → P3 learns which pipeline stages are fragile.

### P3 → P5 (Security, future)

- Signal bus becomes security telemetry. Anomalous learning patterns → possible poisoning.
- P3's regression detector is the first defense against memory poisoning.
- `alert_human` action is the precursor to P5's graduated response.

### P3 → P6 (Adversarial Eval, future)

- P6's null agent baselines run through P3's trajectory store.
- P6's red-team tests emit signals to the bus.
- P3's PerformanceTracker IS the scoring backbone that P6 validates.

---

## Components

### 1. LearningSignal + SignalBus (`_signals.py`)

Universal event schema for all learning loops.

```python
@dataclass
class LearningSignal:
    signal_type: str      # correction | failure | success | adaptation | score_change | rollback
    source_loop: str      # "correction_capture" | "scan_learning" | "persona_evolution" | ...
    domain: str           # "greenhouse" | "linkedin" | "email_classification" | "research"
    agent_name: str       # "form_filler" | "researcher" | "scanner" | ...
    severity: str         # info | warning | critical
    payload: dict         # signal-type-specific data
    session_id: str       # groups signals from same run
    timestamp: str        # auto-set ISO format
    signal_id: str        # auto-generated UUID
```

**SignalBus** stores signals in two layers:
- SQLite table in `data/optimization.db` (persistent, indexed by domain + source_loop + timestamp)
- In-memory deque (last 1000 signals for real-time aggregation)

**Signal types:**

| Type | Emitted When | Payload |
|------|-------------|---------|
| `correction` | Human corrects agent value | `{field, old_value, new_value, platform}` |
| `failure` | Agent action fails | `{action, error, stage, recoverable}` |
| `success` | Agent action succeeds | `{action, score, cost, duration_ms}` |
| `adaptation` | Learning loop changes behavior | `{param, old_value, new_value, reason}` |
| `score_change` | Quality score changes | `{old_score, new_score, source}` |
| `rollback` | Learning action reversed | `{action_id, reason, reverted_to}` |

**Integration examples (1-2 lines per loop):**

```python
# correction_capture.py — after recording a diff:
engine.emit("correction", source_loop="correction_capture",
    domain=platform, agent_name="form_filler",
    payload={"field": label, "old": agent_value, "new": user_value})

# scan_learning.py — after adapting params:
engine.emit("adaptation", source_loop="scan_learning",
    domain=platform, agent_name="scanner",
    payload={"param": "delay_range", "old": old_range, "new": new_range})

# persona_evolution.py — after evolving a prompt:
engine.emit("score_change", source_loop="persona_evolution",
    domain=agent_name, agent_name=agent_name,
    payload={"old_score": prev_score, "new_score": new_score, "generation": gen})

# patterns/*.py — at convergence/finish nodes:
engine.emit("success", source_loop="experience_memory",
    domain=state["topic"], agent_name="enhanced_swarm",
    payload={"score": state["review_score"], "iterations": state["iteration"]})
```

### 2. SignalAggregator (`_aggregator.py`)

Consumes the bus, detects cross-loop patterns, emits `AggregatedInsight`.

```python
@dataclass
class AggregatedInsight:
    pattern_type: str          # systemic_failure | regression | platform_change | persona_drift | redundant
    confidence: float          # 0.0-1.0
    contributing_signals: list[str]  # signal_ids
    domain: str
    recommended_action: str
    evidence: str              # human-readable explanation
```

**Pattern detection rules (rule-based, zero LLM cost):**

| Pattern | Trigger | Confidence | Action |
|---------|---------|------------|--------|
| Systemic field failure | 3+ corrections on same (domain, field_pattern) from different sessions | 0.8 | Generate platform insight + update rule |
| Learning regression | Tracker: metric drops >15% after learning action | 0.9 | Auto-rollback the learning action |
| Platform behavior change | 3+ new failures on stable domain within 24h | 0.7 | Alert human + escalate scan_learning cooldown |
| Persona drift | Score trend declining over 5+ runs after persona evolution | 0.8 | Rollback to previous persona, pause evolution |
| Redundant signals | Multiple loops generating rules for same root cause | 0.6 | Merge into single coordinated action |

**Two cadences:**
- **Real-time** (on every signal): checks for regressions and critical failures
- **Hourly sweep**: deeper cross-loop correlation, trend detection, batch pattern mining

**Memory integration:**
- Before generating any insight, queries MemoryManager via Qdrant hybrid search to avoid duplicates
- Cross-domain discovery: insight on platform A finds related insight on platform B via embedding similarity
- Traverses Neo4j to find all memories RELATED_TO a platform before deciding

### 3. PerformanceTracker (`_tracker.py`)

Measures before/after impact of every learning action.

```python
@dataclass
class PerformanceSnapshot:
    loop_name: str
    domain: str
    period: str              # "hour" | "day" | "week"
    timestamp: str
    metrics: dict            # loop-specific metrics
```

**Per-loop metrics:**

| Loop | Key Metrics |
|------|-------------|
| CorrectionCapture | correction_rate, fields_overridden_pct, top_corrected_fields |
| ScanLearning | block_rate, avg_scan_duration, cooldown_triggers |
| PersonaEvolution | avg_score_trend, evolution_count, rollback_count |
| FormExperience | cache_hit_rate, llm_calls_saved, new_domains_seen |
| NavigationLearner | replay_success_rate, replay_fail_rate, new_paths_learned |
| ExperienceMemory | injection_count, score_delta_with_experience, eviction_rate |
| AgentRules | rule_hit_rate, rule_effectiveness |
| A/B Testing | active_tests, winner_promotion_rate, avg_improvement_pct |
| Cognitive (L2/L3) | escalation_rate, l0_success_rate, l2_success_rate, l3_cost_per_improvement |

**Before/after measurement:**
- `before_learning_action(loop, domain)` → snapshots current metrics, returns action_id
- `after_learning_action(action_id)` → snapshots again, computes delta
- Regression = metric drops >15% AND learning action in window

**Cognitive feature tracking:**
- L0/L1/L2/L3 success rates per domain → feeds EscalationClassifier overrides
- Strategy template effectiveness: before/after when StrategyComposer uses a template
- Failure pattern effectiveness: whether EPISODIC failure patterns prevented future failures
- Auto-escalation frequency per domain → tunes classifier thresholds
- Budget utilization correlated with outcome quality

**DomainStats — returned by `get_domain_stats()` for CognitiveEngine integration:**

```python
@dataclass
class DomainStats:
    domain: str
    agent_name: str
    sample_size: int           # total tracked decisions
    l0_success_rate: float     # 0.0-1.0
    l1_success_rate: float
    l2_success_rate: float
    l3_success_rate: float
    forced_level: Optional[ThinkLevel]  # None = no override, set when stats are conclusive
    avg_correction_rate: float # from PerformanceTracker
    escalation_frequency: float  # L1→L2+ rate
    last_updated: str          # ISO timestamp
```

**Memory integration:**
- After 30+ snapshots for a domain, writes baseline to SEMANTIC tier as PINNED memory
- Trend data written to SEMANTIC tier for CognitiveEngine's StrategyComposer to find

### 4. OptimizationPolicy (`_policy.py`)

Decides actions based on insights + metrics. Rule-based for 90%+, CognitiveEngine fallback.

**Action types:**

| Action | Target | When |
|--------|--------|------|
| `rollback_persona` | PersonaEvolution | Score trend declining after evolution |
| `disable_rule` | AgentRules | Rule causing more failures than it prevents |
| `generate_insight` | MemoryManager SEMANTIC tier | 3+ corrections on same field pattern |
| `generate_strategy` | MemoryManager PROCEDURAL tier | Effective optimization pattern confirmed |
| `escalate_cognitive` | CognitiveEngine | Agent outputs degrading → force L2 on next run |
| `override_classifier` | EscalationClassifier | Domain stats prove L0 sufficient or L2 required |
| `promote_memory` | MemoryManager (all 3 engines) | Learning action confirmed helpful → boost heat |
| `demote_memory` | MemoryManager (all 3 engines) | Learning action caused regression → reduce heat, add CAUSED_REGRESSION edge |
| `revive_memory` | MemoryManager | Domain degrading + tombstoned memory exists → revive with 2x stability |
| `pin_memory` | MemoryManager | Baseline established → mark PINNED, never decay |
| `resolve_contradiction` | MemoryManager | New insight contradicts existing → compare evidence, stronger wins |
| `alert_human` | Telegram AlertManager | Confidence < 0.7 or severity=critical |
| `pause_loop` | Any learning loop | Loop causing net-negative outcomes |
| `resume_loop` | Any learning loop | After human review or regression cleared |

**CognitiveEngine integration for novel decisions:**

When rule-based policy doesn't match (confidence < 0.6):

```python
result = await cognitive_engine.think(
    task=f"Decide optimization action for: {insight.evidence}",
    domain="optimization",
    stakes="medium",  # triggers L2 Reflexion for self-correcting policy decisions
)
```

This means P3's policy benefits from L2 Reflexion (try/critique/retry) and L3 ToT (explore multiple action strategies) when facing novel situations. The cognitive engine's strategy templates accumulate over time, so novel situations become L0 memory recalls.

**Budget guardrails:**

```python
@dataclass
class OptimizationBudget:
    max_rollbacks_per_hour: int = 3
    max_rule_generations_per_hour: int = 10
    max_llm_policy_calls_per_hour: int = 5
    cooldown_after_rollback_minutes: int = 30
```

When budget exhausted, all actions degrade to `alert_human`. The engine never stops observing — it only stops acting.

### 5. TrajectoryStore (`_trajectory.py`)

Structured action logging across ALL agent pipelines.

```python
@dataclass
class TrajectoryStep:
    step_index: int
    action: str           # "fill_field" | "click" | "select" | "upload" | "llm_call" | "classify" | ...
    target: str           # field label, button text, model name
    input_value: str      # what was attempted
    output_value: str     # what actually happened
    outcome: str          # "success" | "failure" | "corrected" | "skipped"
    duration_ms: float
    metadata: dict        # action-specific (selector, confidence, model, tokens, cost)

@dataclass
class Trajectory:
    trajectory_id: str
    pipeline: str         # "job_application" | "research" | "email_classification" | ...
    domain: str           # "greenhouse" | "physics" | "gmail" | ...
    agent_name: str
    session_id: str
    steps: list[TrajectoryStep]
    final_outcome: str    # "success" | "failure" | "partial"
    final_score: float
    total_duration_ms: float
    total_cost: float
    timestamp: str
```

**Storage:** SQLite in `data/optimization.db` (shared with signals/snapshots).
**Export:** JSONL in ShareGPT format for fine-tuning. CSV for dashboards.
**Pruning:** >90 days deleted in weekly maintenance. Configurable.
**Linking:** Every trajectory step can link to the LearningSignals it triggered.

### 6. OptimizationEngine (`_engine.py`)

Single entry point facade.

```python
from shared.optimization import OptimizationEngine, get_optimization_engine

engine = get_optimization_engine()

# Emit signals (any learning loop):
engine.emit("correction", source_loop="correction_capture",
    domain="greenhouse", payload={...})

# Before/after learning actions:
action_id = engine.before_learning_action("persona_evolution", domain="scanner")
# ... learning happens ...
engine.after_learning_action(action_id)

# Run optimization cycle (hourly cron):
engine.optimize()

# Log trajectory steps:
tid = engine.start_trajectory(pipeline="job_application", domain="greenhouse")
engine.log_step(tid, action="fill_field", target="salary", input_value="45,000",
    output_value="45000", outcome="corrected", duration_ms=150)
engine.complete_trajectory(tid, final_outcome="success", final_score=8.5)

# Get stats for CognitiveEngine classifier (returns DomainStats):
stats = engine.get_domain_stats(domain="workday", agent_name="form_filler")
# stats.forced_level, stats.l0_success_rate, stats.sample_size

# Reports:
report = engine.get_report(domain="greenhouse", period="week")

# Flush pending memory writes:
engine.flush()
```

**Kill switch:** `OPTIMIZATION_ENABLED=false` → all methods return immediately, zero overhead.

---

## Storage Architecture

### Layer 1: P3's Own Operational SQLite (`data/optimization.db`)

Raw, high-throughput event data. P3 owns and queries directly.

| Table | Purpose |
|-------|---------|
| `signals` | Time-series events, indexed by (domain, source_loop, timestamp) |
| `trajectories` | Session-level records with pipeline, domain, outcome, score |
| `trajectory_steps` | Per-step actions within a trajectory |
| `performance_snapshots` | Time-series metrics per loop per domain |
| `learning_actions` | Audit trail with before/after metrics |

### Layer 2: Knowledge via MemoryManager → 3-Engine Stack

Distilled knowledge goes through MemoryManager to the appropriate engine(s).

| P3 Knowledge | Memory Tier | SQLite Value | Qdrant Value | Neo4j Value |
|---|---|---|---|---|
| Aggregated insight | SEMANTIC | Source of truth | Embedding-searchable across domains | APPLIES_TO, SIMILAR_TO, CONTRADICTS edges |
| Effective strategy | PROCEDURAL | Source of truth | StrategyComposer finds via embedding | Links to domain, agent, success metrics |
| Regression record | EPISODIC | Source of truth | Reflexion finds past failures | CAUSED_REGRESSION, TRIGGERED_BY edges |
| Performance baseline | SEMANTIC (PINNED) | Source of truth | Searchable for similar platforms | Links to Platform node, trend history |
| Optimization pattern | PATTERN | Source of truth | Embedding captures pattern description | Connects signals → insight → action |

### Memory Lifecycle Integration

```python
# When tracker confirms improvement:
engine.promote(memory_id)
    → MemoryManager.promote(memory_id)
        → SQLite: bump access_count, update lifecycle stage
        → Qdrant: boost score weight
        → Neo4j: increase edge weights

# When tracker detects regression:
engine.demote(memory_id)
    → MemoryManager.demote(memory_id)
        → SQLite: decrease decay_score
        → Qdrant: reduce score weight
        → Neo4j: add CAUSED_REGRESSION edge, weaken connections

# When insight contradicts existing:
engine.resolve_contradiction(new_id, old_id)
    → MemoryManager.contradict(old_id)  # if new is stronger
    → Neo4j: add SUPERSEDES edge

# When domain degrades + tombstoned memory exists:
engine.revive(memory_id)
    → MemoryManager.revive(memory_id)
        → 2x stability boost (spaced repetition)
```

---

## Integration Plan

### Existing Loop Instrumentation

**Tier 1: Signal emitters (1-2 lines each):**

| Loop | File | Signal Points | Lines Changed |
|------|------|---------------|---------------|
| CorrectionCapture | `jobpulse/correction_capture.py` | `emit("correction")` after diff | ~3 |
| AgentRules | `jobpulse/agent_rules.py` | `emit("adaptation")` on rule create, `emit("rollback")` on disable | ~5 |
| PersonaEvolution | `jobpulse/persona_evolution.py` | `emit("score_change")` with old/new score | ~3 |
| ScanLearning | `jobpulse/scan_learning.py` | `emit("adaptation")` on param change, `emit("failure")` on block | ~4 |
| A/B Testing | `jobpulse/ab_testing.py` | `emit("score_change")` on result, `emit("adaptation")` on promotion | ~4 |
| ExperienceMemory | `shared/experiential_learning.py` | `emit("success")` on store, `emit("score_change")` on injection | ~3 |

**Tier 2: Signal emitters + trajectory logging:**

| Loop | File | Trajectory Points | Lines Changed |
|------|------|-------------------|---------------|
| FormExperienceDB | `jobpulse/form_experience_db.py` | Log field fills as TrajectorySteps | ~10 |
| NavigationLearner | `jobpulse/navigation_learner.py` | Log navigation actions as steps | ~8 |
| FormInteractionLog | `jobpulse/form_interaction_log.py` | Adapter to emit to TrajectoryStore | ~15 |

**Tier 3: Pipeline-level trajectory logging:**

| Pipeline | File | What Gets Logged |
|----------|------|-----------------|
| Job application | `jobpulse/applicator.py` | Full session: page detection → fills → screening → submission |
| Pre-screen | `jobpulse/scan_pipeline.py` | Gate results: G0→G1→G2→G3→G4 with pass/fail |
| Research patterns | `patterns/*.py` | Agent calls, scores per iteration, convergence path |
| Email classification | `jobpulse/email_preclassifier.py` | Tier used, result, confidence |

### Cognitive Engine Integration

**EscalationClassifier Step 0 override:**

```python
# In shared/cognitive/_classifier.py — classify() method:
domain_stats = optimization_engine.get_domain_stats(domain, agent_name)
if domain_stats.forced_level is not None:
    return domain_stats.forced_level
if domain_stats.l0_success_rate > 0.95 and domain_stats.sample_size >= 20:
    return ThinkLevel.L0
```

**Pattern finish nodes emit signals:**

```python
# In patterns/hierarchical.py, peer_debate.py, etc. — finish_node():
engine.emit("success", source_loop="experience_memory",
    domain=state["topic"], agent_name=pattern_name,
    payload={"score": state["review_score"], "iterations": state["iteration"],
             "cost": cost_summary["total_cost"]})
```

### Cron Integration

```python
# In jobpulse/runner.py:
schedule.every().hour.do(optimization_engine.optimize)          # Aggregation + regression check
schedule.every().day.at("04:00").do(optimization_engine.daily_report)  # After 3am profile sync
schedule.every().sunday.at("08:00").do(optimization_engine.weekly_maintenance)  # Prune + export
```

### Telegram Commands

| Command | Action |
|---------|--------|
| `learning status` | Per-loop performance summary, active regressions |
| `learning report` | Weekly optimization report with trends |
| `learning pause <loop>` | Manually pause a learning loop |
| `learning resume <loop>` | Resume a paused loop |
| `learning rollback <action_id>` | Manually rollback a specific learning action |

---

## End-to-End Example

Salary field correction on Workday, showing all 3 pillars working together:

```
1. USER corrects salary field: "45,000" → "45000"

2. CorrectionCapture records diff, emits:
   LearningSignal("correction", domain="workday", field="salary",
                   old="45,000", new="45000")

3. SignalBus persists to optimization.db, notifies Aggregator

4. Aggregator: 3rd correction on (workday, salary) in 7 days → confidence 0.85

5. Aggregator queries Memory (Qdrant hybrid search):
   "salary format integer" → finds Indeed insight: "compensation rejects currency symbols"
   → cross-platform match, confidence boosted to 0.92

6. Policy: systemic_field_failure + confidence > 0.8
   → Actions: [generate_insight, generate_strategy]

7. Insight written via MemoryManager:
   SEMANTIC: "Workday salary field requires integer — no commas"
   → SQLite (source of truth)
   → Qdrant (embedded for cross-platform search)
   → Neo4j (APPLIES_TO→Workday, SIMILAR_TO→Indeed_insight)
   → AutonomousLinker discovers 2 more related memories

8. Strategy written via MemoryManager:
   PROCEDURAL: "Override salary with int(value.replace(',',''))"
   → Available to CognitiveEngine's StrategyComposer

9. Tracker snapshots: workday correction_rate before=18%, action_id=xyz

10. Next Workday application:
    CognitiveEngine.think(task="fill salary", domain="workday")
    → EscalationClassifier Step 0: checks P3 domain stats
    → StrategyComposer finds PROCEDURAL template from step 8
    → L0 Memory Recall: uses template directly, zero LLM cost

11. Tracker snapshots: workday correction_rate after=4%
    → Improvement confirmed
    → memory.reinforce(insight_id) + reinforce(strategy_id)
    → Heat bump → promotion toward LTM
    → Ebbinghaus decay slowed (high quality signal from P3)
```

---

## File Layout

```
shared/optimization/
    __init__.py              — Public API exports
    _signals.py              — LearningSignal, SignalBus (SQLite + deque)
    _aggregator.py           — SignalAggregator, AggregatedInsight, pattern rules
    _tracker.py              — PerformanceTracker, PerformanceSnapshot, regression detection
    _policy.py               — OptimizationPolicy, Action types, budget guardrails
    _trajectory.py           — TrajectoryStore, Trajectory, TrajectoryStep, JSONL export
    _engine.py               — OptimizationEngine facade + get_optimization_engine()
    CLAUDE.md                — Module docs for Claude Code sessions
```

Estimated ~1200-1500 LOC total. Each file focused and under 300 lines.

---

## Documentation Updates

All agent-facing docs updated so Claude Code sessions and subagents know about the optimization engine.

| File | Update |
|------|--------|
| `CLAUDE.md` (root) | Add `shared/optimization/CLAUDE.md` to Module Context. Add `OptimizationEngine` to Quick Reference. |
| `shared/CLAUDE.md` | Add Optimization Engine section: usage, rules, signal emission pattern |
| `shared/optimization/CLAUDE.md` | New file — full module docs: components, usage, signal types, rules |
| `patterns/CLAUDE.md` | Add: patterns emit signals at finish nodes + log trajectories |
| `jobpulse/CLAUDE.md` | Add: all 9 loops emit signals, trajectory logging in apply flow |
| `.claude/rules/shared.md` | Add: always emit signals for learning actions, never query optimization.db directly from outside shared/optimization/ |
| `.claude/rules/seven-principles.md` | Add Principle 6 checkpoint: "Learning actions tracked via OptimizationEngine.before/after_learning_action()" |
| `.claude/rules/jobpulse.md` | Add: "All learning loops MUST emit LearningSignals" |
| `.claude/rules/patterns.md` | Add: "Pattern finish nodes MUST emit signals + log trajectories" |
| `AGENTS.md` | Add optimization engine instructions for subagents |

### shared/optimization/CLAUDE.md Content

```markdown
# Optimization Engine (shared/optimization/)

Continuous learning & optimization — Pillar 3 of 6.

Signal-driven architecture: learning loops emit signals → aggregator detects patterns →
policy decides actions → tracker measures impact → trajectories log everything.

## Usage

    from shared.optimization import get_optimization_engine
    engine = get_optimization_engine()

    # Emit signal from any learning loop:
    engine.emit("correction", source_loop="correction_capture",
        domain="greenhouse", payload={"field": "salary", "old": "45,000", "new": "45000"})

    # Wrap learning actions with before/after:
    action_id = engine.before_learning_action("persona_evolution", domain="scanner")
    # ... do the learning ...
    engine.after_learning_action(action_id)

    # Log trajectory steps:
    tid = engine.start_trajectory(pipeline="job_application", domain="greenhouse")
    engine.log_step(tid, action="fill_field", target="salary", ...)
    engine.complete_trajectory(tid, final_outcome="success")

    # Flush pending memory writes:
    engine.flush()

## Signal Types
correction | failure | success | adaptation | score_change | rollback

## Rules
- ALL learning loops MUST emit signals at key decision points
- NEVER query data/optimization.db directly — use OptimizationEngine facade
- ALWAYS wrap learning actions with before_learning_action / after_learning_action
- ALWAYS call engine.flush() at end of agent runs
- Kill switch: OPTIMIZATION_ENABLED=false makes engine no-op
```

---

## Test Specifications

### Unit Tests (75 tests)

**`_signals.py` — SignalBus (12 tests)**

- `test_emit_persists_to_sqlite` — Signal written to optimization.db with all fields
- `test_emit_adds_to_memory_deque` — In-memory window receives signal
- `test_signal_auto_generates_id_and_timestamp` — Auto-populated when omitted
- `test_query_by_domain_and_time_window` — Correct subset returned
- `test_query_by_source_loop` — Filter by originating loop
- `test_query_by_session_id` — Group signals from same session
- `test_deque_overflow_drops_oldest` — Oldest evicted at capacity
- `test_sqlite_persists_across_restart` — New instance loads from DB
- `test_emit_with_invalid_signal_type_raises` — Only allowed types
- `test_bulk_emit_performance` — 1000 signals < 500ms
- `test_prune_old_signals` — >90 day signals deleted
- `test_signal_payload_round_trips_json` — Complex dicts survive storage

**`_aggregator.py` — SignalAggregator (15 tests)**

- `test_systemic_failure_detection` — 3 corrections → insight confidence ≥ 0.8
- `test_below_threshold_no_insight` — 2 corrections → no insight
- `test_regression_detection` — Metric drops >15% after action → regression
- `test_regression_requires_learning_action_in_window` — Drop without action → not flagged
- `test_platform_behavior_change` — 3 failures on stable domain → platform_change
- `test_persona_drift_detection` — Declining trend over 5+ runs → drift
- `test_redundant_signal_detection` — Multiple loops same root cause → redundant
- `test_dedup_with_memory_search` — Existing insight found via Qdrant → skip
- `test_cross_domain_discovery_via_qdrant` — Platform A insight found for platform B
- `test_confidence_boosted_by_cross_platform_match` — Similar insight → confidence up
- `test_hourly_sweep_finds_slow_patterns` — Accumulative patterns caught
- `test_contributing_signals_tracked` — Correct signal_ids in insight
- `test_real_time_vs_sweep_cadence` — Critical = immediate, non-critical = sweep
- `test_aggregator_respects_paused_loops` — Paused loops excluded
- `test_neo4j_traversal_for_context` — Graph traversal before deciding

**`_tracker.py` — PerformanceTracker (14 tests)**

- `test_snapshot_creation` — Records current metrics correctly
- `test_before_after_tagging` — Linked by action_id
- `test_regression_detected_on_decline` — 15% drop → flagged
- `test_no_regression_on_normal_variance` — 10% drop → no flag
- `test_improvement_detected` — 10%+ improvement → confirmed
- `test_per_loop_metrics_correct` — All 9 loops report specific metrics
- `test_period_aggregation` — Hourly → daily → weekly rollup
- `test_baseline_stored_to_memory_as_pinned` — 30+ snapshots → PINNED SEMANTIC
- `test_trend_calculation` — 5+ snapshots → direction computed
- `test_cognitive_level_tracking` — L0/L1/L2/L3 rates per domain
- `test_strategy_template_effectiveness` — Before/after with template
- `test_failure_pattern_effectiveness` — Did EPISODIC patterns prevent failures?
- `test_escalation_frequency_tracking` — L1→L2 counts per domain
- `test_budget_utilization_monitoring` — Cognitive budget vs quality correlation

**`_policy.py` — OptimizationPolicy (13 tests)**

- `test_systemic_failure_generates_insight_and_rule` — Correct actions
- `test_regression_triggers_rollback` — rollback + memory demote
- `test_persona_drift_triggers_rollback_and_pause` — rollback + pause_loop
- `test_platform_change_alerts_human` — alert via Telegram
- `test_cognitive_escalation_on_degradation` — escalate_cognitive action
- `test_budget_guardrails_enforced` — Max 3 rollbacks/hour
- `test_cooldown_after_rollback` — 30-minute cooldown
- `test_llm_fallback_for_novel_situations` — Low confidence → CognitiveEngine.think()
- `test_cognitive_think_uses_reflexion` — Novel decision → L2
- `test_memory_promote_on_improvement` — Confirmed → promote
- `test_memory_demote_on_regression` — Confirmed → demote + CAUSED_REGRESSION edge
- `test_pinned_memories_never_auto_demoted` — PINNED → human review only
- `test_contradiction_resolution` — Stronger insight wins, weaker tombstoned

**`_trajectory.py` — TrajectoryStore (11 tests)**

- `test_create_trajectory_and_add_steps` — Full lifecycle
- `test_step_ordering_preserved` — Correct order
- `test_trajectory_links_to_session_id` — Session grouping
- `test_jsonl_export_sharegpt_format` — Valid training format
- `test_csv_export_for_analytics` — Flat export
- `test_pruning_removes_old_trajectories` — >90 day deleted
- `test_query_by_pipeline_and_domain` — Filter correctly
- `test_query_by_outcome` — success/failure/partial filter
- `test_trajectory_step_metadata_round_trips` — Complex metadata survives
- `test_cost_and_duration_aggregation` — Totals computed from steps
- `test_signal_linkage` — Steps linked to signals

**`_engine.py` — OptimizationEngine (10 tests)**

- `test_singleton_shared_instance` — Same instance returned
- `test_emit_delegates_to_signal_bus` — Flow-through
- `test_optimize_runs_aggregation_and_policy` — Full chain
- `test_before_after_learning_action_flow` — Complete cycle
- `test_get_report_returns_formatted_summary` — Human-readable
- `test_get_domain_stats_for_cognitive` — EscalationClassifier-compatible
- `test_flush_calls_cognitive_flush` — Pending writes committed
- `test_daily_report_includes_trends` — Per-loop trends
- `test_weekly_maintenance_prunes_and_exports` — Prune + JSONL
- `test_disabled_via_env_var` — OPTIMIZATION_ENABLED=false → no-op

### Integration Tests (8 tests)

- `test_correction_to_insight_to_cognitive_reuse` — 3 corrections → insight → L0 Memory Recall
- `test_regression_detection_and_auto_rollback` — Persona evolution → decline → rollback → demote
- `test_cross_domain_transfer_via_qdrant` — Workday insight found for Indeed query
- `test_cognitive_classifier_override` — P3 domain stats → EscalationClassifier changes level
- `test_l3_cost_reduction_over_time` — L3 same branch 3x → PROCEDURAL template → L0 next time
- `test_memory_lifecycle_driven_by_tracker` — Good → reinforce → promote. Bad → demote → decay.
- `test_full_trajectory_to_training_export` — Session → steps → signals → JSONL
- `test_contradiction_resolution_with_neo4j` — New vs old → compare evidence → SUPERSEDES edge

### Database Isolation

All tests use `tmp_path` for `optimization.db`. MemoryManager mocked or pointed to temp storage.

```python
@pytest.fixture
def optimization_engine(tmp_path):
    db_path = str(tmp_path / "optimization.db")
    mock_memory = MockMemoryManager(tmp_path / "memory")
    mock_cognitive = MockCognitiveEngine()
    return OptimizationEngine(
        db_path=db_path,
        memory_manager=mock_memory,
        cognitive_engine=mock_cognitive,
    )
```

---

## Success Criteria

Measurable outcomes after 2 weeks of production use:

| Criterion | Target | How Measured |
|-----------|--------|-------------|
| Regression detection rate | Catch 90%+ within 1 hour | Regressions caught vs manual discoveries |
| Cross-loop pattern detection | Detect systemic issues from 2+ loops within 24h | Multi-signal insights vs manual discoveries |
| Duplicate insight prevention | <5% duplicates | Insights that Qdrant search would have caught |
| Cognitive cost reduction | L3 cost drops 30%+ over 4 weeks | Weekly L3 spend via BudgetTracker |
| Memory quality signal | Promoted memories 2x higher access rate | PerformanceTracker data |
| Correction rate decline | Per-domain rate drops 20%+ over 4 weeks | Tracker trend data |
| Signal bus coverage | All 9 loops emit (100%) | Distinct source_loop values |
| Trajectory coverage | 95%+ application sessions logged | Trajectory count vs application count |
| Zero production impact | No latency or failure rate increase | Pre/post metrics |
| Kill switch works | OPTIMIZATION_ENABLED=false = full no-op | Tests + manual verification |

## Cost Budget

| Component | Cost |
|-----------|------|
| Signal bus + aggregator (rule-based) | $0/month |
| Qdrant queries (Voyage embeddings) | ~$0.30/month |
| Policy LLM fallback (via CognitiveEngine) | ~$1.50/month |
| Tracker + trajectory storage | ~50MB/month SQLite |
| Memory writes through MemoryManager | Negligible |
| **Total** | **~$2/month** |
