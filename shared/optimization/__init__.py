"""Continuous Learning & Optimization Engine — Pillar 3 of 6.

Signal-driven architecture: learning loops emit signals → aggregator
detects patterns → policy decides actions → tracker measures impact.

    from shared.optimization import get_optimization_engine

    engine = get_optimization_engine()
    engine.emit("correction", source_loop="correction_capture",
        domain="greenhouse", payload={...})
"""
