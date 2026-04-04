"""FastAPI routes for CodeGraph — AST-based code analysis, risk scoring, visualization."""

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from shared.code_graph import CodeGraph
from shared.graph_visualizer import (
    export_pattern_mermaid,
    export_all_patterns_mermaid,
    export_code_graph_mermaid,
    export_code_graph_dot,
    PATTERN_TOPOLOGIES,
)
from shared.logging_config import get_logger

logger = get_logger(__name__)

codegraph_router = APIRouter(prefix="/api/codegraph")

# ─── PERSISTENT GRAPH INSTANCE ──────────────────────────────
# Re-index on first request or when explicitly triggered.

_graph: Optional[CodeGraph] = None
_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _get_graph(force_reindex: bool = False) -> CodeGraph:
    """Get or create the CodeGraph instance, indexing the project."""
    global _graph
    if _graph is None or force_reindex:
        _graph = CodeGraph(":memory:")
        # Index shared/ and patterns/ (the core Python code)
        for subdir in ["shared", "patterns", "jobpulse", "mindgraph_app"]:
            dirpath = os.path.join(_PROJECT_ROOT, subdir)
            if os.path.isdir(dirpath):
                _graph.index_directory(dirpath, path_prefix=subdir)
        stats = _graph.get_stats()
        logger.info(
            "CodeGraph indexed: %d nodes, %d edges, %d files",
            stats["nodes"], stats["edges"], stats["files"],
        )
    return _graph


# ─── GRAPH DATA ENDPOINTS ──────────────────────────────────

@codegraph_router.get("/graph")
def get_codegraph(focus_file: str = None, max_nodes: int = 100):
    """Get the code dependency graph as nodes + edges for 3D visualization.

    Returns data in the same {nodes, edges} format the frontend expects,
    with risk scores and kind info on each node.
    """
    graph = _get_graph()

    if focus_file:
        nodes = graph.functions_in_file(focus_file)
        # Add callers/callees for context
        for node in list(nodes):
            callers = graph.callers_of(node["name"])
            for c in callers[:5]:
                row = graph.conn.execute(
                    "SELECT * FROM nodes WHERE qualified_name=?",
                    (c["source_qname"],)
                ).fetchone()
                if row and dict(row) not in nodes:
                    nodes.append(dict(row))
    else:
        rows = graph.conn.execute(
            "SELECT * FROM nodes ORDER BY file_path, line_start LIMIT ?",
            (max_nodes,)
        ).fetchall()
        nodes = [dict(r) for r in rows]

    # Deduplicate
    seen = set()
    unique = []
    for n in nodes:
        if n["qualified_name"] not in seen:
            seen.add(n["qualified_name"])
            unique.append(n)
    nodes = unique[:max_nodes]

    # Build frontend-compatible format with risk scores
    out_nodes = []
    for n in nodes:
        qn = n["qualified_name"]
        risk = 0.0
        if n["kind"] in ("function", "method"):
            risk = graph.compute_risk_score(qn)

        out_nodes.append({
            "id": qn,
            "name": n["name"],
            "entity_type": n["kind"].upper(),  # FUNCTION, CLASS, METHOD
            "file_path": n["file_path"],
            "line_start": n["line_start"],
            "line_end": n["line_end"],
            "is_test": bool(n["is_test"]),
            "is_async": bool(n["is_async"]),
            "risk_score": round(risk, 3),
            "description": f"{n['file_path']}:{n['line_start']}-{n['line_end']}",
            "mention_count": 1,  # compat with old frontend
            "importance": risk,  # compat: maps to node size
        })

    # Build edges between these nodes
    qname_set = {n["qualified_name"] for n in nodes}
    out_edges = []
    for n in nodes:
        qn = n["qualified_name"]
        edges = graph.conn.execute(
            "SELECT target_qname, kind FROM edges WHERE source_qname=?", (qn,)
        ).fetchall()
        for e in edges:
            target = e["target_qname"]
            edge_kind = e["kind"]
            # Match target to known nodes
            for other_qn in qname_set:
                if (other_qn.endswith(f"::{target}")
                        or other_qn.endswith(f"::{target.split('.')[-1]}")
                        or other_qn == target):
                    out_edges.append({
                        "from_id": qn,
                        "to_id": other_qn,
                        "type": edge_kind.upper(),
                        "context": edge_kind,
                    })
                    break

    return {"nodes": out_nodes, "edges": out_edges}


@codegraph_router.get("/stats")
def get_codegraph_stats():
    """Aggregate stats: nodes, edges, files, risk distribution."""
    graph = _get_graph()
    stats = graph.get_stats()

    # Risk distribution
    risk_report = graph.risk_report(top_n=200)
    high = sum(1 for r in risk_report if r["risk_score"] >= 0.7)
    medium = sum(1 for r in risk_report if 0.4 <= r["risk_score"] < 0.7)
    low = sum(1 for r in risk_report if r["risk_score"] < 0.4)

    return {
        **stats,
        "risk_distribution": {"high": high, "medium": medium, "low": low},
    }


@codegraph_router.get("/risk-report")
def get_risk_report(top_n: int = 20):
    """Top-N highest-risk functions for review prioritization."""
    graph = _get_graph()
    return {"functions": graph.risk_report(top_n=top_n)}


class ImpactQuery(BaseModel):
    changed_files: list[str]
    max_depth: int = 2


@codegraph_router.post("/impact")
def get_impact_radius(body: ImpactQuery):
    """BFS blast radius analysis for changed files."""
    graph = _get_graph()
    impact = graph.impact_radius(body.changed_files, body.max_depth)
    return impact


@codegraph_router.post("/reindex")
def reindex():
    """Force re-index the entire project."""
    _get_graph(force_reindex=True)
    graph = _get_graph()
    return {"status": "reindexed", "stats": graph.get_stats()}


# ─── VISUALIZATION ENDPOINTS ──────────────────────────────

@codegraph_router.get("/mermaid")
def get_mermaid(focus_file: str = None, max_nodes: int = 50, show_risk: bool = True):
    """Export code graph as Mermaid flowchart."""
    graph = _get_graph()
    mermaid = export_code_graph_mermaid(graph, focus_file, max_nodes, show_risk)
    return {"format": "mermaid", "content": mermaid}


@codegraph_router.get("/dot")
def get_dot(focus_file: str = None, max_nodes: int = 50):
    """Export code graph as Graphviz DOT."""
    graph = _get_graph()
    dot = export_code_graph_dot(graph, focus_file, max_nodes)
    return {"format": "dot", "content": dot}


@codegraph_router.get("/patterns")
def get_patterns():
    """List available LangGraph pattern topologies."""
    return {
        "patterns": [
            {"name": k, "title": v["title"]}
            for k, v in PATTERN_TOPOLOGIES.items()
        ]
    }


@codegraph_router.get("/patterns/{pattern_name}")
def get_pattern_mermaid(pattern_name: str):
    """Export a specific pattern topology as Mermaid."""
    if pattern_name not in PATTERN_TOPOLOGIES:
        raise HTTPException(404, f"Unknown pattern: {pattern_name}")
    return {"pattern": pattern_name, "mermaid": export_pattern_mermaid(pattern_name)}


@codegraph_router.get("/patterns/all/mermaid")
def get_all_patterns_mermaid():
    """Export all pattern topologies as a single Mermaid document."""
    return {"content": export_all_patterns_mermaid()}
