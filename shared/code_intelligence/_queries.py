"""Call graph queries and structural analysis — find_symbol, callers, impact, etc.

Extracted from CodeIntelligence class (SRP split).
All functions take `ci` (CodeIntelligence instance) as first argument.
"""
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from shared.code_intelligence import CodeIntelligence

logger = get_logger(__name__)

_DEFAULT_BOUNDARY_RULES = [
    {"module": "shared", "cannot_import": ["jobpulse", "patterns", "mindgraph_app"]},
]


def find_symbol(ci: CodeIntelligence, name: str) -> dict[str, Any] | None:
    """Find a function, class, or method by name. Exact match first, LIKE fallback."""
    row = ci.conn.execute(
        "SELECT qualified_name, name, kind, file_path, line_start, line_end, "
        "risk_score, is_async FROM nodes WHERE name=? AND kind != 'document' LIMIT 1",
        (name,),
    ).fetchone()

    if row is None:
        row = ci.conn.execute(
            "SELECT qualified_name, name, kind, file_path, line_start, line_end, "
            "risk_score, is_async FROM nodes WHERE name LIKE ? AND kind != 'document' LIMIT 1",
            (f"%{name}%",),
        ).fetchone()

    if row is None:
        return None

    qname = row[0]
    callers = ci._graph.callers_of(name)
    callees = ci._graph.callees_of(qname)

    return {
        "qualified_name": qname,
        "name": row[1],
        "kind": row[2],
        "file": row[3],
        "line_start": row[4],
        "line_end": row[5],
        "risk_score": row[6],
        "is_async": bool(row[7]),
        "callers_count": len(callers),
        "callees_count": len(callees),
    }


def callers_of(ci: CodeIntelligence, name: str, max_results: int = 20) -> dict[str, Any]:
    """Find all functions that call the given name."""
    raw = ci._graph.callers_of(name)
    callers = [
        {
            "name": r["source_qname"].split(".")[-1],
            "qualified_name": r["source_qname"],
            "file": r["file_path"],
            "line": r["line"],
        }
        for r in raw[:max_results]
    ]
    return {"target": name, "callers": callers, "total": len(raw)}


def callees_of(ci: CodeIntelligence, name: str, max_results: int = 20) -> dict[str, Any]:
    """Find all functions called by the given name."""
    # Resolve qualified name from the nodes table
    row = ci.conn.execute(
        "SELECT qualified_name FROM nodes WHERE name=? AND kind != 'document' LIMIT 1",
        (name,),
    ).fetchone()
    qname = row[0] if row else name

    raw = ci._graph.callees_of(qname)
    callees = [
        {
            "name": r["target_qname"].split(".")[-1],
            "qualified_name": r["target_qname"],
            "file": r["file_path"],
            "line": r["line"],
        }
        for r in raw[:max_results]
    ]
    return {"source": name, "callees": callees, "total": len(raw)}


def impact_analysis(ci: CodeIntelligence, files: list[str], max_depth: int = 2,
                     max_results: int = 100) -> dict[str, Any]:
    """Compute blast radius from changed files."""
    radius = ci._graph.impact_radius(files, max_depth, max_results=max_results)
    impacted_files: set[str] = radius.get("impacted_files", set())
    impacted_nodes: list[dict[str, Any]] = radius.get("impacted_nodes", [])
    depth_map: dict[str, int] = radius.get("depth_map", {})

    # Enumerate functions directly changed in the specified files
    changed_functions: list[str] = []
    for f in files:
        rows = ci.conn.execute(
            "SELECT name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
            (f,),
        ).fetchall()
        changed_functions.extend(r[0] for r in rows)

    enriched: list[dict[str, Any]] = []
    for node in impacted_nodes:
        node_file = node.get("file_path", "")
        risk = node.get("risk_score", 0.0) or 0.0
        enriched.append(
            {
                "name": node.get("name", ""),
                "qualified_name": node.get("qualified_name", ""),
                "file": node_file,
                "depth": node.get("impact_depth", depth_map.get(node_file, 0)),
                "risk": risk,
            }
        )

    max_risk = max((e["risk"] for e in enriched), default=0.0)

    return {
        "changed_functions": changed_functions,
        "impacted": enriched,
        "impacted_files": list(impacted_files),
        "total_impacted": len(enriched),
        "max_risk": max_risk,
    }


def diff_impact(
    ci: CodeIntelligence, diff_text: str = "", *, ref: str | None = None,
    root: str | None = None, max_depth: int = 2, max_results: int = 100,
) -> dict[str, Any]:
    """Blast radius from a git diff or ref."""
    import re as _re

    _empty = {"changed_files": [], "changed_functions": [], "impacted": [],
               "impacted_files": [], "total_impacted": 0, "max_risk": 0.0}

    if ref and not diff_text:
        if ref.startswith("-"):
            return _empty
        _root = root or ci._project_root
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "--", ref],
                capture_output=True, text=True, timeout=5, cwd=_root,
            )
            if proc.returncode == 0:
                changed_files = [f.strip() for f in proc.stdout.strip().splitlines() if f.strip()]
            else:
                changed_files = []
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            changed_files = []
    elif diff_text:
        changed_files = list(dict.fromkeys(
            m.group(1) for m in _re.finditer(r"^(?:---|\+\+\+) [ab]/(.+)$", diff_text, _re.MULTILINE)
        ))
    else:
        return _empty

    if not changed_files:
        return _empty

    ia_result = impact_analysis(ci, changed_files, max_depth=max_depth, max_results=max_results)
    ia_result["changed_files"] = changed_files
    return ia_result


def test_coverage_map(ci: CodeIntelligence, file: str | None = None, top_n: int = 50) -> dict[str, Any]:
    """Map which functions are tested and which tests cover them."""
    file_filter = ""
    params: list[Any] = []
    if file:
        file_filter = "AND n.file_path LIKE ?"
        params.append(f"%{file}%")

    prod_functions = ci.conn.execute(
        f"""SELECT n.qualified_name, n.name, n.file_path, n.risk_score
            FROM nodes n
            WHERE n.kind IN ('function', 'method')
              AND n.is_test = 0
              AND n.file_path NOT LIKE '%test_%'
              AND n.file_path NOT LIKE '%conftest%'
              {file_filter}
            ORDER BY n.risk_score DESC LIMIT ?""",
        params + [top_n * 3],
    ).fetchall()

    test_functions = ci.conn.execute(
        "SELECT qualified_name, name, file_path FROM nodes "
        "WHERE is_test = 1 AND kind IN ('function', 'method')"
    ).fetchall()

    coverage: dict[str, list[dict[str, str]]] = {}
    for test in test_functions:
        test_qname, test_name, test_file = test[0], test[1], test[2]
        callees = ci.conn.execute(
            "SELECT target_qname FROM edges WHERE kind='calls' AND source_qname=?",
            (test_qname,),
        ).fetchall()
        for callee in callees:
            coverage.setdefault(callee[0], []).append({"test": test_name, "file": test_file})

    covered, uncovered = [], []
    for fn in prod_functions:
        qname, name, fpath, risk = fn[0], fn[1], fn[2], fn[3]
        tests_hitting = coverage.get(qname, [])
        if not tests_hitting:
            for key, val in coverage.items():
                if key.endswith(f"::{name}") or key == name:
                    tests_hitting = val
                    break
        entry = {"name": qname, "file": fpath, "risk_score": round(risk or 0, 3)}
        if tests_hitting:
            entry["tested_by"] = tests_hitting[:10]
            covered.append(entry)
        else:
            uncovered.append(entry)

    total = len(covered) + len(uncovered)
    return {
        "covered": covered[:top_n], "uncovered": uncovered[:top_n],
        "total_functions": total,
        "coverage_pct": round(len(covered) / total * 100, 1) if total else 0,
    }


def call_path(ci: CodeIntelligence, source: str, target: str, max_depth: int = 6) -> dict[str, Any]:
    """Find shortest call path from source to target via BFS."""
    from collections import deque as _deque

    src_row = ci.conn.execute(
        "SELECT qualified_name FROM nodes WHERE name=? AND kind IN ('function','method') LIMIT 1",
        (source,),
    ).fetchone()
    src_qname = src_row[0] if src_row else source

    tgt_row = ci.conn.execute(
        "SELECT qualified_name FROM nodes WHERE name=? AND kind IN ('function','method') LIMIT 1",
        (target,),
    ).fetchone()
    tgt_qname = tgt_row[0] if tgt_row else target

    forward: dict[str, list[str]] = {}
    for row in ci.conn.execute(
        "SELECT source_qname, target_qname FROM edges WHERE kind='calls'"
    ).fetchall():
        forward.setdefault(row[0], []).append(row[1])

    visited: dict[str, str | None] = {src_qname: None}
    queue = _deque([(src_qname, 0)])

    while queue:
        current, depth = queue.popleft()
        if current == tgt_qname or current.endswith(f"::{target}"):
            path = [current]
            node = current
            while visited[node] is not None:
                node = visited[node]
                path.append(node)
            path.reverse()
            return {"found": True, "source": src_qname, "target": current, "path": path, "depth": len(path) - 1}
        if depth >= max_depth:
            continue

        for neighbor in forward.get(current, []):
            if neighbor not in visited:
                visited[neighbor] = current
                queue.append((neighbor, depth + 1))

    return {"found": False, "source": src_qname, "target": tgt_qname, "path": [], "depth": 0}


def batch_find(ci: CodeIntelligence, names: list[str] | None = None, *, pattern: str | None = None, max_results: int = 50) -> dict[str, Any]:
    """Find multiple symbols at once, or match by glob pattern."""
    found: list[dict[str, Any]] = []
    not_found: list[str] = []

    if pattern:
        sql_pattern = pattern.replace("*", "%").replace("?", "_")
        rows = ci.conn.execute(
            "SELECT qualified_name, name, kind, file_path, line_start, line_end, "
            "risk_score, is_async FROM nodes "
            "WHERE name LIKE ? AND kind != 'document' ORDER BY risk_score DESC LIMIT ?",
            (sql_pattern, max_results),
        ).fetchall()
        for row in rows:
            found.append({
                "qualified_name": row[0], "name": row[1], "kind": row[2],
                "file": row[3], "line_start": row[4], "line_end": row[5],
                "risk_score": round(row[6] or 0, 3), "is_async": bool(row[7]),
            })
    elif names:
        for name in names:
            result = find_symbol(ci, name)
            if result:
                found.append(result)
            else:
                not_found.append(name)

    return {"found": found[:max_results], "not_found": not_found, "total": len(found)}


def boundary_check(ci: CodeIntelligence, rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Check architectural boundary rules — detect forbidden imports."""
    if rules is None:
        rules = _DEFAULT_BOUNDARY_RULES

    violations: list[dict[str, Any]] = []
    for rule in rules:
        source_module = rule["module"]
        forbidden = rule["cannot_import"]

        import_edges = ci.conn.execute(
            "SELECT source_qname, target_qname, file_path, line "
            "FROM edges WHERE kind='imports' AND file_path LIKE ?",
            (f"{source_module}/%",),
        ).fetchall()

        call_edges = ci.conn.execute(
            "SELECT e.source_qname, e.target_qname, e.file_path, e.line "
            "FROM edges e JOIN nodes n ON n.qualified_name = e.target_qname "
            "WHERE e.kind='calls' AND e.file_path LIKE ? AND n.file_path IS NOT NULL",
            (f"{source_module}/%",),
        ).fetchall()

        for edge in list(import_edges) + list(call_edges):
            target_str = str(edge[1])
            for forbidden_mod in forbidden:
                if (target_str.startswith(f"{forbidden_mod}.")
                        or target_str.startswith(f"{forbidden_mod}/")
                        or f"/{forbidden_mod}/" in target_str):
                    violations.append({
                        "source_module": source_module, "source_file": edge[2],
                        "source_function": edge[0], "target": target_str,
                        "forbidden_module": forbidden_mod, "line": edge[3],
                    })

    seen = set()
    unique = []
    for v in violations:
        key = (v["source_file"], v["target"], v["line"])
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return {"violations": unique, "rules_checked": len(rules), "clean": len(unique) == 0}


def suggest_extract(ci: CodeIntelligence, file: str | None = None, min_lines: int = 50, top_n: int = 20) -> dict[str, Any]:
    """Suggest functions that could benefit from extraction/refactoring."""
    file_filter = ""
    params: list[Any] = []
    if file:
        file_filter = "AND file_path LIKE ?"
        params.append(f"%{file}%")

    suggestions: list[dict[str, Any]] = []

    # Large functions
    large = ci.conn.execute(
        f"""SELECT qualified_name, file_path, line_start, line_end, risk_score, fan_in
            FROM nodes WHERE kind IN ('function', 'method')
            AND (line_end - line_start) > ? {file_filter}
            ORDER BY (line_end - line_start) DESC LIMIT ?""",
        [min_lines] + params + [top_n],
    ).fetchall()

    for row in large:
        size = (row[3] or 0) - (row[2] or 0)
        callees = ci.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls' AND source_qname=?", (row[0],),
        ).fetchone()[0]
        suggestions.append({
            "name": row[0], "file": row[1], "lines": size,
            "risk_score": round(row[4] or 0, 3), "fan_in": row[5] or 0,
            "callees_count": callees, "reason": "large_function",
            "suggestion": f"Function is {size} lines. Consider extracting logical blocks into helpers.",
        })

    # Functions with too many callees
    busy = ci.conn.execute(
        f"""SELECT n.qualified_name, n.file_path, n.line_start, n.line_end,
                   n.risk_score, n.fan_in, COUNT(e.id) as callee_count
            FROM nodes n
            JOIN edges e ON e.source_qname = n.qualified_name AND e.kind = 'calls'
            WHERE n.kind IN ('function', 'method') AND (n.line_end - n.line_start) > 20
            {file_filter.replace('file_path', 'n.file_path')}
            GROUP BY n.qualified_name HAVING callee_count > 8
            ORDER BY callee_count DESC LIMIT ?""",
        params + [top_n],
    ).fetchall()

    seen = {s["name"] for s in suggestions}
    for row in busy:
        if row[0] in seen:
            continue
        suggestions.append({
            "name": row[0], "file": row[1],
            "lines": (row[3] or 0) - (row[2] or 0),
            "risk_score": round(row[4] or 0, 3), "fan_in": row[5] or 0,
            "callees_count": row[6], "reason": "too_many_callees",
            "suggestion": f"Function calls {row[6]} other functions. Consider splitting responsibilities.",
        })

    return {"suggestions": suggestions[:top_n], "total": len(suggestions)}


def rename_preview(ci: CodeIntelligence, symbol: str, new_name: str) -> dict[str, Any]:
    """Preview all locations that would change if a symbol is renamed. Read-only."""
    locations: list[dict[str, Any]] = []

    defs = ci.conn.execute(
        "SELECT qualified_name, file_path, line_start, line_end, kind "
        "FROM nodes WHERE name=? AND kind != 'document'",
        (symbol,),
    ).fetchall()
    for d in defs:
        locations.append({
            "kind": "definition", "qualified_name": d[0],
            "file": d[1], "line": d[2], "symbol_kind": d[4],
        })

    callers = ci.conn.execute(
        "SELECT source_qname, file_path, line FROM edges "
        "WHERE kind='calls' AND (target_qname LIKE ? OR target_qname=?)",
        (f"%::{symbol}", symbol),
    ).fetchall()
    for c in callers:
        locations.append({"kind": "caller", "qualified_name": c[0], "file": c[1], "line": c[2]})

    imports = ci.conn.execute(
        "SELECT source_qname, file_path, line FROM edges "
        "WHERE kind='imports' AND target_qname LIKE ?",
        (f"%.{symbol}",),
    ).fetchall()
    for imp in imports:
        locations.append({"kind": "import", "qualified_name": imp[0], "file": imp[1], "line": imp[2]})

    files_affected = list({loc["file"] for loc in locations if loc.get("file")})
    return {
        "symbol": symbol, "new_name": new_name,
        "locations": locations, "total_locations": len(locations),
        "files_affected": files_affected,
    }


def risk_report(ci: CodeIntelligence, top_n: int = 10, file: str | None = None) -> dict[str, Any]:
    """Top-N highest-risk functions, optionally filtered by file."""
    if file is not None:
        rows = ci.conn.execute(
            "SELECT name, file_path, risk_score FROM nodes "
            "WHERE kind IN ('function', 'method') AND file_path=? "
            "ORDER BY risk_score DESC LIMIT ?",
            (file, top_n),
        ).fetchall()
    else:
        rows = ci.conn.execute(
            "SELECT name, file_path, risk_score FROM nodes "
            "WHERE kind IN ('function', 'method') "
            "ORDER BY risk_score DESC LIMIT ?",
            (top_n,),
        ).fetchall()

    functions = [{"name": r[0], "file": r[1], "risk": r[2]} for r in rows]
    return {"functions": functions}
