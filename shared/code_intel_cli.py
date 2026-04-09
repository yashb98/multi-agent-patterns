"""CLI wrapper for CodeIntelligence — gives subagents instant graph queries.

Usage (from any Bash tool call — do NOT use `python -m shared.code_intel_cli`,
use the direct script path to avoid heavy shared/__init__.py imports):

    python shared/code_intel_cli.py find_symbol get_llm
    python shared/code_intel_cli.py callers_of get_llm
    python shared/code_intel_cli.py callees_of researcher_node
    python shared/code_intel_cli.py impact_analysis shared/agents.py shared/streaming.py
    python shared/code_intel_cli.py risk_report 10
    python shared/code_intel_cli.py risk_report --file shared/agents.py
    python shared/code_intel_cli.py semantic_search "rate limiting logic"
    python shared/code_intel_cli.py module_summary shared/agents.py
    python shared/code_intel_cli.py recent_changes 5
    python shared/code_intel_cli.py dead_code 20

Graph-only commands (find_symbol, callers_of, callees_of, etc.) use direct SQLite
for ~50ms latency. semantic_search loads the full CodeIntelligence stack (~4s).
"""

import json
import sqlite3
import sys
from pathlib import Path

from shared.db import get_db_conn

from shared.paths import DATA_DIR as _DATA_DIR
DB_PATH = str(_DATA_DIR / "code_intelligence.db")

# Commands that only need SQLite graph — skip full CodeIntelligence import
GRAPH_ONLY_COMMANDS = {
    "find_symbol", "callers_of", "callees_of", "impact_analysis",
    "risk_report", "module_summary", "recent_changes", "dead_code",
}


def _get_ci(graph_only: bool | None = None):
    """Get CodeIntelligence instance. graph_only=True skips embedding load."""
    from shared.code_intelligence import CodeIntelligence
    return CodeIntelligence(DB_PATH, graph_only=bool(graph_only))


def _get_conn():
    """Get a raw SQLite connection for graph-only queries (~1ms)."""
    return get_db_conn(DB_PATH, wal=False, mkdir=False)


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_find_symbol(args):
    if not args:
        print("Usage: find_symbol <name>", file=sys.stderr); sys.exit(1)
    name = args[0]
    conn = _get_conn()
    rows = conn.execute(
        "SELECT name, qualified_name, file_path, kind, line_start, line_end, "
        "risk_score, fan_in, fan_out "
        "FROM nodes WHERE name LIKE ? OR qualified_name LIKE ? "
        "ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END, fan_in DESC LIMIT 20",
        (f"%{name}%", f"%{name}%", name),
    ).fetchall()
    _print({"query": name, "matches": [dict(r) for r in rows], "total": len(rows)})
    conn.close()


def cmd_callers_of(args):
    if not args:
        print("Usage: callers_of <name> [max_results]", file=sys.stderr); sys.exit(1)
    name, max_r = args[0], int(args[1]) if len(args) > 1 else 20
    conn = _get_conn()
    rows = conn.execute(
        "SELECT source_qname, file_path, line FROM edges "
        "WHERE kind IN ('calls', 'references') AND target_qname LIKE ? "
        "LIMIT ?",
        (f"%{name}%", max_r),
    ).fetchall()
    callers = [{"name": r[0].split("::")[-1], "qualified_name": r[0],
                "file": r[1], "line": r[2]} for r in rows]
    _print({"target": name, "callers": callers, "total": len(rows)})
    conn.close()


def cmd_callees_of(args):
    if not args:
        print("Usage: callees_of <name> [max_results]", file=sys.stderr); sys.exit(1)
    name, max_r = args[0], int(args[1]) if len(args) > 1 else 20
    conn = _get_conn()
    # Resolve to qualified name
    row = conn.execute(
        "SELECT qualified_name FROM nodes WHERE name=? LIMIT 1", (name,)
    ).fetchone()
    qname = row[0] if row else name
    rows = conn.execute(
        "SELECT target_qname, file_path, line FROM edges "
        "WHERE kind='calls' AND source_qname=? LIMIT ?",
        (qname, max_r),
    ).fetchall()
    callees = [{"name": r[0].split("::")[-1], "qualified_name": r[0],
                "file": r[1], "line": r[2]} for r in rows]
    _print({"source": name, "callees": callees, "total": len(rows)})
    conn.close()


def cmd_impact_analysis(args):
    if not args:
        print("Usage: impact_analysis <file1> [file2 ...]", file=sys.stderr); sys.exit(1)
    # Impact analysis needs BFS traversal — use CodeIntelligence
    _print(_get_ci(graph_only=True).impact_analysis(args))


def cmd_risk_report(args):
    conn = _get_conn()
    if args and args[0] == "--file":
        file_filter = args[1] if len(args) > 1 else None
        where = "AND file_path LIKE ?" if file_filter else ""
        params = (f"%{file_filter}%",) if file_filter else ()
        rows = conn.execute(
            f"SELECT name, file_path, risk_score FROM nodes "
            f"WHERE kind IN ('function', 'method') AND risk_score > 0 {where} "
            f"ORDER BY risk_score DESC LIMIT 15", params
        ).fetchall()
    else:
        top_n = int(args[0]) if args else 10
        rows = conn.execute(
            "SELECT name, file_path, risk_score FROM nodes "
            "WHERE kind IN ('function', 'method') AND risk_score > 0 "
            "ORDER BY risk_score DESC LIMIT ?", (top_n,)
        ).fetchall()
    _print({"functions": [{"name": r[0], "file": r[1], "risk": r[2]} for r in rows]})
    conn.close()


def cmd_semantic_search(args):
    if not args:
        print("Usage: semantic_search <query> [top_k]", file=sys.stderr); sys.exit(1)
    query = args[0]
    top_k = int(args[1]) if len(args) > 1 else 10
    # Semantic search needs embeddings — full load required
    _print(_get_ci(graph_only=False).semantic_search(query, top_k=top_k))


def cmd_module_summary(args):
    if not args:
        print("Usage: module_summary <file>", file=sys.stderr); sys.exit(1)
    file_path = args[0]
    conn = _get_conn()
    nodes = conn.execute(
        "SELECT name, kind, line_start, line_end, risk_score, fan_in, fan_out "
        "FROM nodes WHERE file_path LIKE ? ORDER BY line_start",
        (f"%{file_path}%",),
    ).fetchall()
    functions = [dict(r) for r in nodes if r["kind"] in ("function", "method")]
    classes = [dict(r) for r in nodes if r["kind"] == "class"]
    _print({"file": file_path, "functions": functions, "classes": classes,
            "total_functions": len(functions), "total_classes": len(classes)})
    conn.close()


def cmd_recent_changes(args):
    n = int(args[0]) if args else 3
    # recent_changes needs git log — use CodeIntelligence
    _print(_get_ci(graph_only=True).recent_changes(n_commits=n))


def cmd_dead_code(args):
    """Find functions with zero incoming call/reference edges — likely dead code."""
    conn = _get_conn()
    top_n = int(args[0]) if args else 50
    rows = conn.execute(
        """
        SELECT n.name, n.qualified_name, n.file_path, n.line_start, n.line_end, n.risk_score
        FROM nodes n
        WHERE n.kind IN ('function', 'method')
          AND n.is_test = 0
          AND n.qualified_name NOT IN (
              SELECT DISTINCT e.target_qname FROM edges e WHERE e.kind IN ('calls', 'references')
          )
          AND n.name NOT LIKE '\\_%' ESCAPE '\\'
          AND n.name NOT IN ('main', 'setup', 'teardown', 'configure')
        ORDER BY (n.line_end - n.line_start) DESC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()

    results = []
    total_lines = 0
    for row in rows:
        lines = (row[4] or 0) - (row[3] or 0)
        total_lines += max(lines, 0)
        results.append({
            "name": row[0],
            "qualified_name": row[1],
            "file": row[2],
            "line_start": row[3],
            "line_end": row[4],
            "lines": lines,
            "risk_score": row[5],
        })

    _print({
        "dead_functions": len(results),
        "removable_lines": total_lines,
        "functions": results,
    })
    conn.close()


COMMANDS = {
    "find_symbol": cmd_find_symbol,
    "callers_of": cmd_callers_of,
    "callees_of": cmd_callees_of,
    "impact_analysis": cmd_impact_analysis,
    "risk_report": cmd_risk_report,
    "semantic_search": cmd_semantic_search,
    "module_summary": cmd_module_summary,
    "recent_changes": cmd_recent_changes,
    "dead_code": cmd_dead_code,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m shared.code_intel_cli <command> [args...]", file=sys.stderr)
        print(f"Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
