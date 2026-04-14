"""Tests for graph visualizer pattern topologies."""


def test_plan_execute_topology_exists():
    from shared.graph_visualizer import PATTERN_TOPOLOGIES
    assert "plan_and_execute" in PATTERN_TOPOLOGIES
    topo = PATTERN_TOPOLOGIES["plan_and_execute"]
    assert "planner" in topo["nodes"]
    assert "synthesizer" in topo["nodes"]


def test_map_reduce_topology_exists():
    from shared.graph_visualizer import PATTERN_TOPOLOGIES
    assert "map_reduce" in PATTERN_TOPOLOGIES
    topo = PATTERN_TOPOLOGIES["map_reduce"]
    assert "splitter" in topo["nodes"]
    assert "reconciler" in topo["nodes"]


def test_all_six_patterns_present():
    from shared.graph_visualizer import PATTERN_TOPOLOGIES
    expected = {"hierarchical", "peer_debate", "dynamic_swarm", "enhanced_swarm", "plan_and_execute", "map_reduce"}
    assert set(PATTERN_TOPOLOGIES.keys()) == expected


def test_export_plan_execute_mermaid():
    from shared.graph_visualizer import export_pattern_mermaid
    mermaid = export_pattern_mermaid("plan_and_execute")
    assert "Plan-and-Execute" in mermaid
    assert "planner" in mermaid


def test_export_map_reduce_mermaid():
    from shared.graph_visualizer import export_pattern_mermaid
    mermaid = export_pattern_mermaid("map_reduce")
    assert "Map-Reduce" in mermaid
    assert "splitter" in mermaid
