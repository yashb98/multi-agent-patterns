"""CLI wrapper for CodeIntelligence — gives subagents instant graph queries.

Usage (from any Bash tool call):
    python -m shared.code_intel_cli find_symbol get_llm
    python -m shared.code_intel_cli callers_of get_llm
    python -m shared.code_intel_cli callees_of researcher_node
    python -m shared.code_intel_cli impact_analysis shared/agents.py shared/streaming.py
    python -m shared.code_intel_cli risk_report 10
    python -m shared.code_intel_cli risk_report --file shared/agents.py
    python -m shared.code_intel_cli semantic_search "rate limiting logic"
    python -m shared.code_intel_cli module_summary shared/agents.py
    python -m shared.code_intel_cli recent_changes 5
    python -m shared.code_intel_cli dead_code 20

Each command prints JSON to stdout. Typical latency: 5-15ms.
"""

import json
import sys
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "code_intelligence.db")


def _get_ci():
    from shared.code_intelligence import CodeIntelligence
    return CodeIntelligence(DB_PATH)


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_find_symbol(args):
    if not args:
        print("Usage: find_symbol <name>", file=sys.stderr)
        sys.exit(1)
    _print(_get_ci().find_symbol(args[0]))


def cmd_callers_of(args):
    if not args:
        print("Usage: callers_of <name> [max_results]", file=sys.stderr)
        sys.exit(1)
    max_r = int(args[1]) if len(args) > 1 else 20
    _print(_get_ci().callers_of(args[0], max_results=max_r))


def cmd_callees_of(args):
    if not args:
        print("Usage: callees_of <name> [max_results]", file=sys.stderr)
        sys.exit(1)
    max_r = int(args[1]) if len(args) > 1 else 20
    _print(_get_ci().callees_of(args[0], max_results=max_r))


def cmd_impact_analysis(args):
    if not args:
        print("Usage: impact_analysis <file1> [file2 ...]", file=sys.stderr)
        sys.exit(1)
    _print(_get_ci().impact_analysis(args))


def cmd_risk_report(args):
    ci = _get_ci()
    if args and args[0] == "--file":
        _print(ci.risk_report(file=args[1] if len(args) > 1 else None))
    else:
        top_n = int(args[0]) if args else 10
        _print(ci.risk_report(top_n=top_n))


def cmd_semantic_search(args):
    if not args:
        print("Usage: semantic_search <query> [top_k]", file=sys.stderr)
        sys.exit(1)
    query = args[0]
    top_k = int(args[1]) if len(args) > 1 else 10
    _print(_get_ci().semantic_search(query, top_k=top_k))


def cmd_module_summary(args):
    if not args:
        print("Usage: module_summary <file>", file=sys.stderr)
        sys.exit(1)
    _print(_get_ci().module_summary(args[0]))


def cmd_recent_changes(args):
    n = int(args[0]) if args else 3
    _print(_get_ci().recent_changes(n_commits=n))


def cmd_dead_code(args):
    """Find functions with zero incoming call edges — likely dead code."""
    ci = _get_ci()
    top_n = int(args[0]) if args else 50
    rows = ci.conn.execute(
        """
        SELECT n.name, n.qualified_name, n.file_path, n.line_start, n.line_end, n.risk_score
        FROM nodes n
        WHERE n.kind IN ('function', 'method')
          AND n.is_test = 0
          AND n.qualified_name NOT IN (
              SELECT DISTINCT e.target_qname FROM edges e WHERE e.kind = 'calls'
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
