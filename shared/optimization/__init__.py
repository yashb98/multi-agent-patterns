"""Continuous Learning & Optimization Engine — Pillar 3 of 6.

Signal-driven architecture: learning loops emit signals → aggregator
detects patterns → policy decides actions → tracker measures impact.

    from shared.optimization import get_optimization_engine

    engine = get_optimization_engine()
    engine.emit("correction", source_loop="correction_capture",
        domain="greenhouse", payload={...})
"""

from shared.optimization._signals import (  # noqa: F401
    LearningSignal,
    SignalBus,
    VALID_SIGNAL_TYPES,
)
from shared.optimization._trajectory import (  # noqa: F401
    TrajectoryStore,
    Trajectory,
    TrajectoryStep,
)
from shared.optimization._tracker import (  # noqa: F401
    PerformanceTracker,
    PerformanceSnapshot,
    DomainStats,
)
from shared.optimization._aggregator import (  # noqa: F401
    SignalAggregator,
    AggregatedInsight,
)
from shared.optimization._policy import (  # noqa: F401
    OptimizationPolicy,
    OptimizationBudget,
    PolicyAction,
)
