"""OptimizationEngine — single entry point facade for Pillar 3."""

import os
from datetime import datetime, timezone
from typing import Optional

from shared.logging_config import get_logger
from shared.optimization._signals import LearningSignal, SignalBus
from shared.optimization._trajectory import TrajectoryStore, Trajectory, TrajectoryStep
from shared.optimization._tracker import PerformanceTracker, DomainStats
from shared.optimization._aggregator import SignalAggregator
from shared.optimization._policy import OptimizationPolicy, OptimizationBudget

logger = get_logger(__name__)

_DEFAULT_DB_PATH = None  # set lazily to avoid import-time DATA_DIR side effects


def _default_db_path() -> str:
    from shared.paths import DATA_DIR
    return str(DATA_DIR / "optimization.db")


class OptimizationEngine:
    """Single entry point for the continuous learning & optimization system."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        memory_manager=None,
        cognitive_engine=None,
        budget: Optional[OptimizationBudget] = None,
    ):
        self._enabled = os.getenv("OPTIMIZATION_ENABLED", "true").lower() in ("true", "1", "yes")
        self._db_path = db_path or _default_db_path()
        self._memory = memory_manager
        self._cognitive = cognitive_engine

        if self._enabled:
            self._bus = SignalBus(db_path=self._db_path)
            self._trajectory = TrajectoryStore(db_path=self._db_path)
            self._tracker = PerformanceTracker(
                db_path=self._db_path, memory_manager=memory_manager,
            )
            self._aggregator = SignalAggregator(
                signal_bus=self._bus, tracker=self._tracker,
                memory_manager=memory_manager,
            )
            self._policy = OptimizationPolicy(
                memory_manager=memory_manager,
                cognitive_engine=cognitive_engine,
                budget=budget,
            )
        else:
            self._bus = _NoOpBus()
            self._trajectory = _NoOpTrajectory()
            self._tracker = _NoOpTracker()
            self._aggregator = None
            self._policy = None
            logger.info("OptimizationEngine disabled via OPTIMIZATION_ENABLED=false")

    def emit(self, signal_type: str, source_loop: str, domain: str,
             agent_name: str = "", payload: dict = None,
             session_id: str = "", severity: str = "info"):
        if not self._enabled:
            return
        signal = LearningSignal(
            signal_type=signal_type,
            source_loop=source_loop,
            domain=domain,
            agent_name=agent_name,
            severity=severity,
            payload=payload or {},
            session_id=session_id,
        )
        self._bus.emit(signal)

    def before_learning_action(self, loop_name: str, domain: str,
                               metrics: dict) -> str:
        if not self._enabled:
            return ""
        return self._tracker.before_learning_action(loop_name, domain, metrics)

    def after_learning_action(self, action_id: str, metrics: dict) -> dict:
        if not self._enabled:
            return {}
        return self._tracker.after_learning_action(action_id, metrics)

    def start_trajectory(self, pipeline: str, domain: str,
                         agent_name: str, session_id: str) -> str:
        if not self._enabled:
            return ""
        return self._trajectory.start(pipeline, domain, agent_name, session_id)

    def log_step(self, trajectory_id: str, step: TrajectoryStep):
        if not self._enabled or not trajectory_id:
            return
        self._trajectory.log_step(trajectory_id, step)

    def complete_trajectory(self, trajectory_id: str, final_outcome: str,
                            final_score: float, total_duration_ms: float = 0.0,
                            total_cost: float = 0.0) -> Optional[Trajectory]:
        if not self._enabled or not trajectory_id:
            return None
        return self._trajectory.complete(
            trajectory_id, final_outcome, final_score,
            total_duration_ms, total_cost,
        )

    def record_cognitive_outcome(self, domain: str, agent_name: str,
                                 level: int, success: bool,
                                 escalated: bool = False):
        if not self._enabled:
            return
        self._tracker.record_cognitive_outcome(
            domain, agent_name, level, success, escalated,
        )

    def get_domain_stats(self, domain: str, agent_name: str) -> DomainStats:
        return self._tracker.get_domain_stats(domain, agent_name)

    def optimize(self) -> dict:
        if not self._enabled:
            return {"insights": [], "actions": []}
        insights = self._aggregator.check_realtime()
        insights.extend(self._aggregator.check_regressions())
        all_actions = []
        for insight in insights:
            actions = self._policy.decide(insight)
            all_actions.extend(actions)
        return {
            "insights": [
                {"type": i.pattern_type, "domain": i.domain,
                 "confidence": i.confidence, "evidence": i.evidence}
                for i in insights
            ],
            "actions": [
                {"type": a.action_type, "target": a.target, "domain": a.domain}
                for a in all_actions
            ],
        }

    def get_report(self, domain: str = "", period: str = "week") -> dict:
        signal_count = self._bus.count(domain=domain)
        return {
            "domain": domain or "all",
            "period": period,
            "signal_count": signal_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def daily_report(self) -> dict:
        return {
            "type": "daily",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_count": self._bus.count(),
        }

    def weekly_maintenance(self, export_dir: str = "",
                           max_age_days: int = 90) -> dict:
        if not self._enabled:
            return {"pruned": False}
        self._bus.prune(max_age_days=max_age_days)
        self._trajectory.prune(max_age_days=max_age_days)
        if export_dir:
            import os as _os
            _os.makedirs(export_dir, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d")
            self._trajectory.export_jsonl(
                _os.path.join(export_dir, f"trajectories_{ts}.jsonl"),
            )
        return {"pruned": True, "max_age_days": max_age_days}

    async def flush(self):
        if self._cognitive:
            await self._cognitive.flush()

    def flush_sync(self):
        if self._cognitive:
            self._cognitive.flush_sync()

    def pause_loop(self, loop_name: str):
        if self._aggregator:
            self._aggregator.pause_loop(loop_name)

    def resume_loop(self, loop_name: str):
        if self._aggregator:
            self._aggregator.resume_loop(loop_name)


class _NoOpBus:
    def emit(self, signal): pass
    def query(self, **kwargs): return []
    def recent(self): return []
    def count(self, **kwargs): return 0
    def prune(self, **kwargs): pass


class _NoOpTrajectory:
    def start(self, *a, **kw): return ""
    def log_step(self, *a, **kw): pass
    def complete(self, *a, **kw): return None
    def prune(self, **kw): pass
    def export_jsonl(self, *a, **kw): pass


class _NoOpTracker:
    def before_learning_action(self, *a, **kw): return ""
    def after_learning_action(self, *a, **kw): return {}
    def snapshot(self, *a, **kw): return None
    def record_cognitive_outcome(self, *a, **kw): pass
    def get_domain_stats(self, domain, agent_name):
        from shared.optimization._tracker import DomainStats
        return DomainStats(
            domain=domain, agent_name=agent_name, sample_size=0,
            l0_success_rate=0.0, l1_success_rate=0.0,
            l2_success_rate=0.0, l3_success_rate=0.0,
            forced_level=None, avg_correction_rate=0.0,
            escalation_frequency=0.0, last_updated="",
        )


_shared_engine: Optional[OptimizationEngine] = None


def get_optimization_engine() -> OptimizationEngine:
    """Factory that creates or returns the shared OptimizationEngine."""
    global _shared_engine
    if _shared_engine is None:
        memory = None
        cognitive = None
        try:
            from shared.memory_layer import get_shared_memory_manager
            memory = get_shared_memory_manager()
        except Exception as e:
            logger.debug("MemoryManager not available: %s", e)
        try:
            from shared.cognitive import get_cognitive_engine
            cognitive = get_cognitive_engine(agent_name="optimization_engine")
        except Exception as e:
            logger.debug("CognitiveEngine not available: %s", e)
        _shared_engine = OptimizationEngine(
            memory_manager=memory,
            cognitive_engine=cognitive,
        )
    return _shared_engine
