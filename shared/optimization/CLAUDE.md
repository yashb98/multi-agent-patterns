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
    action_id = engine.before_learning_action("persona_evolution", domain="scanner",
        metrics={"avg_score": 7.5})
    # ... do the learning ...
    engine.after_learning_action(action_id, metrics={"avg_score": 8.0})

    # Log trajectory steps:
    tid = engine.start_trajectory(pipeline="job_application", domain="greenhouse",
        agent_name="form_filler", session_id="sess_001")
    engine.log_step(tid, TrajectoryStep(step_index=0, action="fill_field",
        target="salary", input_value="45000", output_value="45000",
        outcome="success", duration_ms=50, metadata={}))
    engine.complete_trajectory(tid, final_outcome="success", final_score=8.5)

    # Run optimization cycle (hourly cron):
    engine.optimize()

    # Flush pending memory writes:
    engine.flush_sync()

## Modules

| Module | Purpose |
|--------|---------|
| `_signals.py` | LearningSignal dataclass, SignalBus (SQLite + deque) |
| `_aggregator.py` | SignalAggregator, AggregatedInsight, 5 pattern-detection rules |
| `_tracker.py` | PerformanceTracker, PerformanceSnapshot, DomainStats, regression detection |
| `_policy.py` | OptimizationPolicy, OptimizationBudget, 14 action types |
| `_trajectory.py` | TrajectoryStore, Trajectory, TrajectoryStep, JSONL/CSV export |
| `_engine.py` | OptimizationEngine facade + get_optimization_engine() factory |

## Signal Types
correction | failure | success | adaptation | score_change | rollback

## Rules
- ALL learning loops MUST emit signals at key decision points
- NEVER query data/optimization.db directly — use OptimizationEngine facade
- ALWAYS wrap learning actions with before_learning_action / after_learning_action
- ALWAYS call engine.flush_sync() at end of agent runs
- Kill switch: OPTIMIZATION_ENABLED=false makes engine full no-op
- Tests MUST use tmp_path for DB — never touch data/optimization.db
- ALL LLM calls in policy go through CognitiveEngine.think() — never direct
