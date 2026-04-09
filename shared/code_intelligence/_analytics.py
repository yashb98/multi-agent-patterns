"""Analytics and reporting — recent changes, dead code, complexity, cycles, primer.

Extracted from CodeIntelligence class (SRP split).
All functions take `ci` (CodeIntelligence instance) as first argument.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from shared.code_intelligence import CodeIntelligence

logger = get_logger(__name__)


def recent_changes(ci: CodeIntelligence, n_commits: int = 3, root: str | None = None) -> dict[str, Any]:
    """Cross-reference recent git commits with code graph."""
    if root is None:
        root = str(Path(ci.db_path).parent)

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--max-count={n_commits}",
                "--name-only",
                "--pretty=format:%H %s",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=root,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {"commits": [], "hotspots": [], "new_high_risk": []}

    if result.returncode != 0:
        return {"commits": [], "hotspots": [], "new_high_risk": []}

    # Parse output: alternating header lines and file lists separated by blank lines
    commits: list[dict[str, Any]] = []
    current_commit: dict[str, Any] | None = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            if current_commit is not None:
                commits.append(current_commit)
                current_commit = None
            continue
        if current_commit is None:
            # Header line: "<sha> <message>"
            parts = line.split(" ", 1)
            sha = parts[0]
            message = parts[1] if len(parts) > 1 else ""
            current_commit = {"sha": sha, "message": message, "files": []}
        else:
            current_commit["files"].append(line)

    if current_commit is not None:
        commits.append(current_commit)

    # Hotspots: files that appear in multiple commits
    from collections import Counter

    file_counts: Counter[str] = Counter()
    for commit in commits:
        for f in commit["files"]:
            file_counts[f] += 1
    hotspots = [f for f, count in file_counts.most_common(5) if count > 1]

    # New high-risk: functions in changed files with risk > 0.5
    changed_files = list(file_counts.keys())
    new_high_risk: list[str] = []
    for f in changed_files:
        rows = ci.conn.execute(
            "SELECT name FROM nodes WHERE file_path=? AND risk_score > 0.5 "
            "AND kind IN ('function', 'method')",
            (f,),
        ).fetchall()
        new_high_risk.extend(r[0] for r in rows)

    return {"commits": commits, "hotspots": hotspots, "new_high_risk": new_high_risk}


def get_primer(ci: CodeIntelligence, top_risk: int = 5, n_commits: int = 3) -> str:
    """Formatted codebase fingerprint for SessionStart hook."""
    from shared.code_intelligence._queries import risk_report

    total_nodes = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    total_edges = ci.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    lines: list[str] = [
        "=== Code Intelligence Primer ===",
        f"Nodes: {total_nodes}  Edges: {total_edges}",
    ]

    # Top-risk functions
    risk = risk_report(ci, top_n=top_risk)
    if risk["functions"]:
        lines.append(f"\nTop-{top_risk} high-risk functions:")
        for fn in risk["functions"]:
            lines.append(f"  • {fn['name']} ({fn['file']})  risk={fn['risk']:.2f}")

    # Recent commits
    changes = recent_changes(ci, n_commits=n_commits)
    if changes["commits"]:
        lines.append(f"\nRecent {n_commits} commits:")
        for commit in changes["commits"]:
            sha_short = commit["sha"][:7]
            lines.append(f"  [{sha_short}] {commit['message']}")

    # Available MCP tools
    lines.append(
        "\nMCP tools: find_symbol · callers_of · callees_of · "
        "impact_analysis · diff_impact · risk_report · semantic_search · "
        "module_summary · recent_changes · dead_code_report · "
        "complexity_hotspots · dependency_cycles · similar_functions · "
        "grep_search · test_coverage_map · call_path · batch_find · "
        "boundary_check · suggest_extract · rename_preview"
    )

    return "\n".join(lines)


def dead_code_report(ci: CodeIntelligence, top_n: int = 20, file: str | None = None) -> dict[str, Any]:
    """Find functions with zero callers — potential dead code.

    Cross-checks both resolved and unresolved edges to reduce false positives.
    Entry points (main, dispatch, CLI handlers) may appear as false positives.
    """
    where_clause = """
        WHERE n.fan_in = 0
          AND n.is_test = 0
          AND n.kind IN ('function', 'method')
          AND n.qualified_name NOT LIKE '%__init__%'
          AND n.qualified_name NOT LIKE '%__main__%'
          AND n.qualified_name NOT LIKE '%test_%'
          AND n.file_path NOT LIKE '%test_%'
          AND n.file_path NOT LIKE '%conftest%'
    """
    if file:
        where_clause += f" AND n.file_path LIKE '%{file}%'"

    candidates = ci.conn.execute(f"""
        SELECT n.qualified_name, n.file_path, n.kind, n.line_start, n.line_end,
               n.fan_in, n.pagerank, n.risk_score
        FROM nodes n
        {where_clause}
        ORDER BY (n.line_end - n.line_start) DESC
        LIMIT ?
    """, (top_n * 2,)).fetchall()

    # Cross-check against unresolved edges
    confirmed = []
    for row in candidates:
        qname = row[0]
        func_name = qname.split("::")[-1]
        called = ci.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? OR target_qname = ?",
            (f"%::{func_name}", func_name),
        ).fetchone()[0]
        if called == 0:
            lines = (row[4] or 0) - (row[3] or 0)
            confirmed.append({
                "name": qname,
                "file": row[1],
                "kind": row[2],
                "lines": lines,
                "pagerank": row[6] or 0.0,
                "risk_score": row[7] or 0.0,
            })
        if len(confirmed) >= top_n:
            break

    # Summary stats
    total_dead = ci.conn.execute(f"""
        SELECT COUNT(*) FROM nodes n {where_clause}
    """).fetchone()[0]
    total_dead_lines = ci.conn.execute(f"""
        SELECT COALESCE(SUM(n.line_end - n.line_start), 0) FROM nodes n {where_clause}
    """).fetchone()[0]
    total_functions = ci.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchone()[0]

    return {
        "total_candidates": total_dead,
        "total_functions": total_functions,
        "dead_code_pct": round(total_dead / total_functions * 100, 1) if total_functions else 0,
        "removable_lines": total_dead_lines,
        "confirmed_dead": confirmed,
    }


def complexity_hotspots(ci: CodeIntelligence, top_n: int = 15) -> list[dict[str, Any]]:
    """Find functions that are high-risk AND high fan-in — complexity hotspots.

    These are the most dangerous functions: lots of callers + high risk score.
    Changes here have maximum blast radius.
    """
    rows = ci.conn.execute("""
        SELECT qualified_name, file_path, fan_in, pagerank, risk_score,
               (line_end - line_start) as size,
               community_id
        FROM nodes
        WHERE kind IN ('function', 'method')
          AND fan_in > 0
          AND risk_score > 0
        ORDER BY (risk_score * fan_in) DESC
        LIMIT ?
    """, (top_n,)).fetchall()

    results = []
    for row in rows:
        # Count callers for context
        callers = ci.conn.execute(
            "SELECT COUNT(DISTINCT source_qname) FROM edges WHERE target_qname = ?",
            (row[0],)
        ).fetchone()[0]

        results.append({
            "name": row[0],
            "file": row[1],
            "fan_in": row[2],
            "callers": callers,
            "pagerank": round(row[3] or 0, 6),
            "risk_score": round(row[4] or 0, 3),
            "lines": row[5] or 0,
            "danger_score": round((row[4] or 0) * (row[2] or 0), 3),
            "community_id": row[6],
        })

    return results


def dependency_cycles(ci: CodeIntelligence, max_depth: int = 4) -> list[dict[str, Any]]:
    """Detect circular dependencies between modules (file-level cycles).

    Finds A→B→C→A cycles that make the codebase hard to refactor.
    """
    # Build file-level adjacency from edges
    file_edges = ci.conn.execute("""
        SELECT DISTINCT
            (SELECT file_path FROM nodes WHERE qualified_name = e.source_qname) as src_file,
            (SELECT file_path FROM nodes WHERE qualified_name = e.target_qname) as tgt_file
        FROM edges e
        WHERE e.kind = 'calls'
    """).fetchall()

    adjacency: dict[str, set[str]] = {}
    for row in file_edges:
        src, tgt = row[0], row[1]
        if src and tgt and src != tgt:
            adjacency.setdefault(src, set()).add(tgt)

    # DFS cycle detection
    cycles: list[list[str]] = []
    visited: set[str] = set()

    def dfs(node: str, path: list[str], seen: set[str]):
        if len(path) > max_depth:
            return
        for neighbor in adjacency.get(node, []):
            if neighbor == path[0] and len(path) >= 2:
                cycle = path + [neighbor]
                # Normalize: start from alphabetically smallest
                min_idx = cycle.index(min(cycle[:-1]))
                normalized = cycle[min_idx:] + cycle[1:min_idx + 1]
                if normalized not in cycles:
                    cycles.append(normalized)
            elif neighbor not in seen and neighbor not in visited:
                dfs(neighbor, path + [neighbor], seen | {neighbor})

    for node in adjacency:
        if node not in visited:
            dfs(node, [node], {node})
        visited.add(node)
        if len(cycles) >= 20:
            break

    results = []
    for cycle in cycles[:20]:
        results.append({
            "cycle": cycle,
            "length": len(cycle) - 1,
            "files": [f.split("/")[-1] for f in cycle],
        })

    return results
