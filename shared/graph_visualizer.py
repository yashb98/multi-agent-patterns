"""Graph Visualizer — export CodeGraph and LangGraph topology to Mermaid/DOT.

Generates:
1. Code dependency graphs from CodeGraph SQLite data
2. LangGraph pattern topology diagrams
3. Risk heatmap overlays

Output formats: Mermaid (markdown-embeddable), DOT (Graphviz)

Usage:
    from shared.graph_visualizer import export_code_graph_mermaid, export_pattern_mermaid

    # Code dependency graph
    mermaid = export_code_graph_mermaid(code_graph, focus_file="shared/agents.py")

    # Pattern topology
    mermaid = export_pattern_mermaid("hierarchical")
"""

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── PATTERN TOPOLOGY DEFINITIONS ────────────────────────────────

PATTERN_TOPOLOGIES = {
    "hierarchical": {
        "title": "Hierarchical Supervisor Pattern",
        "nodes": {
            "supervisor": "Supervisor\\n(routing)",
            "researcher": "Researcher\\n(gather facts)",
            "writer": "Writer\\n(draft article)",
            "reviewer": "Risk-Aware Reviewer\\n(score + risk)",
            "fact_checker": "Fact Checker\\n(verify claims)",
            "finish": "Finish\\n(package output)",
        },
        "edges": [
            ("START", "supervisor", ""),
            ("supervisor", "researcher", "no research"),
            ("supervisor", "writer", "need draft/revision"),
            ("supervisor", "reviewer", "draft ready"),
            ("supervisor", "fact_checker", "review passed"),
            ("supervisor", "finish", "both gates pass"),
            ("researcher", "supervisor", ""),
            ("writer", "supervisor", ""),
            ("reviewer", "supervisor", ""),
            ("fact_checker", "supervisor", ""),
            ("finish", "END", ""),
        ],
        "styles": {
            "supervisor": "fill:#f9f,stroke:#333",
            "researcher": "fill:#bbf,stroke:#333",
            "writer": "fill:#bfb,stroke:#333",
            "reviewer": "fill:#fbb,stroke:#333",
            "fact_checker": "fill:#fbf,stroke:#333",
            "finish": "fill:#ff9,stroke:#333",
        },
    },
    "peer_debate": {
        "title": "Peer Debate Pattern",
        "nodes": {
            "researcher": "Researcher",
            "writer": "Writer",
            "reviewer": "Reviewer",
            "debate_researcher": "Debate Researcher\\n(cross-critique)",
            "debate_writer": "Debate Writer\\n(revise)",
            "convergence": "Convergence Check",
            "synthesis": "Synthesis\\n(final output)",
        },
        "edges": [
            ("START", "researcher", "round 1"),
            ("researcher", "writer", ""),
            ("writer", "reviewer", ""),
            ("reviewer", "convergence", ""),
            ("convergence", "debate_researcher", "continue"),
            ("convergence", "synthesis", "finish"),
            ("debate_researcher", "debate_writer", ""),
            ("debate_writer", "reviewer", ""),
            ("synthesis", "END", ""),
        ],
        "styles": {
            "convergence": "fill:#f9f,stroke:#333",
            "synthesis": "fill:#ff9,stroke:#333",
        },
    },
    "dynamic_swarm": {
        "title": "Dynamic Swarm Pattern",
        "nodes": {
            "analyzer": "Task Analyzer\\n(decompose + prioritize)",
            "executor": "Task Executor\\n(dispatch to agents)",
            "finish": "Finish\\n(package output)",
        },
        "edges": [
            ("START", "analyzer", ""),
            ("analyzer", "executor", "tasks pending"),
            ("analyzer", "finish", "no tasks"),
            ("executor", "executor", "more tasks"),
            ("executor", "analyzer", "re-evaluate"),
            ("executor", "finish", "queue empty"),
            ("finish", "END", ""),
        ],
        "styles": {
            "analyzer": "fill:#f9f,stroke:#333",
            "executor": "fill:#bbf,stroke:#333",
            "finish": "fill:#ff9,stroke:#333",
        },
    },
    "enhanced_swarm": {
        "title": "Enhanced Adaptive Swarm Pattern",
        "nodes": {
            "task_analysis": "Task Analysis\\n(complexity + team)",
            "enhanced_researcher": "Enhanced Researcher\\n(+ experiential learning)",
            "enhanced_writer": "Enhanced Writer\\n(+ GRPO parallel)",
            "enhanced_reviewer": "Enhanced Reviewer\\n(+ risk scoring)",
            "convergence": "Convergence\\n(adaptive threshold)",
            "finish": "Finish\\n(+ learning summary)",
        },
        "edges": [
            ("START", "task_analysis", ""),
            ("task_analysis", "enhanced_researcher", ""),
            ("enhanced_researcher", "enhanced_writer", ""),
            ("enhanced_writer", "enhanced_reviewer", ""),
            ("enhanced_reviewer", "convergence", ""),
            ("convergence", "enhanced_researcher", "continue"),
            ("convergence", "finish", "converged"),
            ("finish", "END", ""),
        ],
        "styles": {
            "convergence": "fill:#f9f,stroke:#333",
            "finish": "fill:#ff9,stroke:#333",
            "enhanced_writer": "fill:#bfb,stroke:#333",
        },
    },
    "plan_and_execute": {
        "title": "Plan-and-Execute Pattern",
        "nodes": {
            "planner": "Planner\\n(decompose query)",
            "step_executor": "Step Executor\\n(run one step)",
            "evaluator": "Evaluator\\n(continue/replan/done)",
            "replanner": "Replanner\\n(adjust remaining steps)",
            "synthesizer": "Synthesizer\\n(combine outputs)",
        },
        "edges": [
            ("START", "planner", ""),
            ("planner", "step_executor", ""),
            ("step_executor", "evaluator", ""),
            ("evaluator", "step_executor", "continue"),
            ("evaluator", "replanner", "replan"),
            ("evaluator", "synthesizer", "done"),
            ("replanner", "step_executor", ""),
            ("synthesizer", "END", ""),
        ],
        "styles": {
            "planner": "fill:#f9f,stroke:#333",
            "evaluator": "fill:#fbf,stroke:#333",
            "replanner": "fill:#fbb,stroke:#333",
            "synthesizer": "fill:#ff9,stroke:#333",
        },
    },
    "map_reduce": {
        "title": "Map-Reduce Pattern",
        "nodes": {
            "splitter": "Splitter\\n(chunk input)",
            "mapper": "Mapper\\n(parallel analysis)",
            "reducer": "Reducer\\n(synthesize results)",
            "reconciler": "Reconciler\\n(resolve conflicts)",
        },
        "edges": [
            ("START", "splitter", ""),
            ("splitter", "mapper", ""),
            ("mapper", "reducer", ""),
            ("reducer", "reconciler", ""),
            ("reconciler", "END", ""),
        ],
        "styles": {
            "splitter": "fill:#bbf,stroke:#333",
            "mapper": "fill:#bfb,stroke:#333",
            "reducer": "fill:#f9f,stroke:#333",
            "reconciler": "fill:#ff9,stroke:#333",
        },
    },
}


def export_pattern_mermaid(pattern_name: str) -> str:
    """Export a pattern topology as Mermaid flowchart.

    Args:
        pattern_name: One of "hierarchical", "peer_debate", "dynamic_swarm", "enhanced_swarm", "plan_and_execute", "map_reduce"

    Returns:
        Mermaid markdown string
    """
    topo = PATTERN_TOPOLOGIES.get(pattern_name)
    if not topo:
        return f"%% Unknown pattern: {pattern_name}"

    lines = [f"---", f"title: {topo['title']}", f"---", f"flowchart TD"]

    # Define nodes
    for node_id, label in topo["nodes"].items():
        lines.append(f"    {node_id}[\"{label}\"]")

    # Define edges
    for src, dst, label in topo["edges"]:
        if label:
            lines.append(f"    {src} -->|{label}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")

    # Apply styles
    for node_id, style in topo.get("styles", {}).items():
        lines.append(f"    style {node_id} {style}")

    return "\n".join(lines)


def export_all_patterns_mermaid() -> str:
    """Export all 6 pattern topologies as a single Mermaid document."""
    sections = []
    for name in PATTERN_TOPOLOGIES:
        sections.append(f"## {PATTERN_TOPOLOGIES[name]['title']}\n")
        sections.append(f"```mermaid\n{export_pattern_mermaid(name)}\n```\n")
    return "\n".join(sections)


# ─── CODE GRAPH VISUALIZATION ────────────────────────────────────

def export_code_graph_mermaid(
    graph,
    focus_file: str = "",
    max_nodes: int = 50,
    show_risk: bool = True,
) -> str:
    """Export a CodeGraph as Mermaid flowchart.

    Args:
        graph: CodeGraph instance (already indexed)
        focus_file: If set, only show nodes connected to this file
        max_nodes: Maximum nodes to display
        show_risk: Overlay risk scores as node colors

    Returns:
        Mermaid markdown string
    """
    lines = ["flowchart LR"]

    # Get nodes
    if focus_file:
        nodes = graph.functions_in_file(focus_file)
        # Also get callers/callees
        for node in list(nodes):
            callers = graph.callers_of(node["name"])
            for c in callers[:3]:  # Limit per node
                caller_node = graph.conn.execute(
                    "SELECT * FROM nodes WHERE qualified_name=?",
                    (c["source_qname"],)
                ).fetchone()
                if caller_node:
                    nodes.append(dict(caller_node))
    else:
        # Get top nodes by risk or all
        if show_risk:
            risk_report = graph.risk_report(top_n=max_nodes)
            qnames = [r["qualified_name"] for r in risk_report]
            nodes = []
            for qn in qnames:
                row = graph.conn.execute(
                    "SELECT * FROM nodes WHERE qualified_name=?", (qn,)
                ).fetchone()
                if row:
                    nodes.append(dict(row))
        else:
            rows = graph.conn.execute(
                "SELECT * FROM nodes ORDER BY file_path, line_start LIMIT ?",
                (max_nodes,)
            ).fetchall()
            nodes = [dict(r) for r in rows]

    if not nodes:
        return "flowchart LR\n    empty[No nodes found]"

    # Deduplicate nodes
    seen = set()
    unique_nodes = []
    for n in nodes:
        if n["qualified_name"] not in seen:
            seen.add(n["qualified_name"])
            unique_nodes.append(n)
    nodes = unique_nodes[:max_nodes]

    # Build node definitions with risk coloring
    risk_cache = {}
    for node in nodes:
        qn = node["qualified_name"]
        safe_id = qn.replace("::", "__").replace(".", "_").replace("/", "_")
        name = node["name"]
        kind = node["kind"]

        if show_risk and kind in ("function", "method"):
            risk = graph.compute_risk_score(qn)
            risk_cache[qn] = risk
            if risk >= 0.7:
                color = "#ff6b6b"  # Red — high risk
            elif risk >= 0.4:
                color = "#ffd93d"  # Yellow — medium risk
            else:
                color = "#6bcb77"  # Green — low risk
            lines.append(f'    {safe_id}["{name}\\n({kind}, risk={risk:.2f})"]')
            lines.append(f"    style {safe_id} fill:{color},stroke:#333")
        else:
            shape = "([" if kind == "class" else "["
            end_shape = "])" if kind == "class" else "]"
            lines.append(f'    {safe_id}{shape}"{name}\\n({kind})"{end_shape}')

    # Build edges between these nodes
    qname_set = {n["qualified_name"] for n in nodes}
    for node in nodes:
        qn = node["qualified_name"]
        safe_src = qn.replace("::", "__").replace(".", "_").replace("/", "_")

        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname=? AND kind='calls'",
            (qn,)
        ).fetchall()

        for edge in edges:
            target = edge[0]
            # Try to match target to a known node
            for other_qn in qname_set:
                if other_qn.endswith(f"::{target}") or other_qn.endswith(f"::{target.split('.')[-1]}"):
                    safe_dst = other_qn.replace("::", "__").replace(".", "_").replace("/", "_")
                    lines.append(f"    {safe_src} --> {safe_dst}")
                    break

    return "\n".join(lines)


def export_code_graph_dot(graph, focus_file: str = "", max_nodes: int = 50) -> str:
    """Export a CodeGraph as DOT format for Graphviz.

    Returns DOT string suitable for `dot -Tpng`.
    """
    lines = ['digraph CodeGraph {', '    rankdir=LR;', '    node [shape=box, style=filled];']

    if focus_file:
        nodes = graph.functions_in_file(focus_file)
    else:
        rows = graph.conn.execute(
            "SELECT * FROM nodes ORDER BY file_path LIMIT ?", (max_nodes,)
        ).fetchall()
        nodes = [dict(r) for r in rows]

    for node in nodes[:max_nodes]:
        qn = node["qualified_name"]
        safe_id = qn.replace("::", "__").replace(".", "_").replace("/", "_").replace("-", "_")
        risk = graph.compute_risk_score(qn)

        if risk >= 0.7:
            color = "red"
        elif risk >= 0.4:
            color = "yellow"
        else:
            color = "lightgreen"

        lines.append(f'    {safe_id} [label="{node["name"]}\\n{node["kind"]}" fillcolor={color}];')

    # Edges
    qname_set = {n["qualified_name"] for n in nodes[:max_nodes]}
    for node in nodes[:max_nodes]:
        qn = node["qualified_name"]
        safe_src = qn.replace("::", "__").replace(".", "_").replace("/", "_").replace("-", "_")
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname=? AND kind='calls'",
            (qn,)
        ).fetchall()
        for edge in edges:
            for other_qn in qname_set:
                if other_qn.endswith(f"::{edge[0]}") or other_qn.endswith(f"::{edge[0].split('.')[-1]}"):
                    safe_dst = other_qn.replace("::", "__").replace(".", "_").replace("/", "_").replace("-", "_")
                    lines.append(f'    {safe_src} -> {safe_dst};')
                    break

    lines.append("}")
    return "\n".join(lines)


def save_visualization(content: str, filepath: str):
    """Save visualization content to file."""
    from pathlib import Path
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    Path(filepath).write_text(content, encoding="utf-8")
    logger.info("Saved visualization to %s", filepath)
