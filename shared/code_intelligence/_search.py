"""Search and discovery — semantic search, module summary, grep, similar functions.

Extracted from CodeIntelligence class (SRP split).
All functions take `ci` (CodeIntelligence instance) as first argument.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from shared.code_intelligence import CodeIntelligence

logger = get_logger(__name__)


def semantic_search(
    ci: CodeIntelligence,
    query: str,
    top_k: int = 10,
    context_symbol: str | None = None,
    search_context: str = "general",
) -> list[dict[str, Any]]:
    """Hybrid FTS5 + vector semantic search with graph boosting."""
    from shared.hybrid_search import compute_graph_boost_batch

    raw = ci._search.query(query, top_k=top_k * 2)  # Over-fetch for graph boost reranking

    # Collect all qnames for batch graph boost
    items_with_meta = []
    qnames = []
    for item in raw:
        metadata = item.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        qname = item.get("id", "")
        qnames.append(qname)
        items_with_meta.append((item, metadata, qname))

    # Single batch boost computation
    boosts = compute_graph_boost_batch(
        ci.conn, qnames,
        context_qname=context_symbol,
        search_context=search_context,
    )

    results: list[dict[str, Any]] = []
    for item, metadata, qname in items_with_meta:
        base_score = item.get("score", 0.0)
        boost = boosts.get(qname, 1.0)

        results.append(
            {
                "name": qname,
                "file": metadata.get("file_path", ""),
                "score": base_score * boost,
                "snippet": (item.get("text") or "")[:200],
            }
        )

    # Re-sort by boosted score and limit
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]


def module_summary(ci: CodeIntelligence, file: str) -> dict[str, Any]:
    """Summary of a file: classes, functions, risk, imports."""
    # Classes
    class_rows = ci.conn.execute(
        "SELECT name, line_start, line_end FROM nodes WHERE file_path=? AND kind='class'",
        (file,),
    ).fetchall()

    classes: list[dict[str, Any]] = []
    for crow in class_rows:
        class_name = crow[0]
        # Find methods belonging to this class
        method_rows = ci.conn.execute(
            "SELECT name FROM nodes WHERE file_path=? AND kind='method' "
            "AND qualified_name LIKE ?",
            (file, f"%.{class_name}.%"),
        ).fetchall()
        methods = [m[0] for m in method_rows]
        lines = (crow[2] or 0) - (crow[1] or 0)
        classes.append({"name": class_name, "methods": methods, "lines": lines})

    # Top-level functions
    func_rows = ci.conn.execute(
        "SELECT name, line_start, line_end, risk_score FROM nodes "
        "WHERE file_path=? AND kind='function' ORDER BY line_start",
        (file,),
    ).fetchall()
    functions = [
        {
            "name": r[0],
            "lines": (r[2] or 0) - (r[1] or 0),
            "risk": r[3],
        }
        for r in func_rows
    ]

    # Average risk across all functions + methods in this file
    risk_rows = ci.conn.execute(
        "SELECT AVG(risk_score) FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
        (file,),
    ).fetchone()
    avg_risk: float = risk_rows[0] if risk_rows and risk_rows[0] is not None else 0.0

    # imports_from: files that this file's functions are called from
    qname_rows = ci.conn.execute(
        "SELECT qualified_name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
        (file,),
    ).fetchall()
    all_qnames = [r[0] for r in qname_rows]
    imports_from: list[str] = []
    if all_qnames:
        placeholders = ",".join("?" * len(all_qnames))
        caller_rows = ci.conn.execute(
            f"SELECT DISTINCT file_path FROM edges WHERE target_qname IN ({placeholders})",
            all_qnames,
        ).fetchall()
        imports_from = [r[0] for r in caller_rows if r[0] != file]

    # imported_by: files that this file's functions call into
    imported_by: list[str] = []
    if all_qnames:
        callee_rows = ci.conn.execute(
            f"SELECT DISTINCT file_path FROM edges WHERE source_qname IN ({placeholders})",
            all_qnames,
        ).fetchall()
        imported_by = [r[0] for r in callee_rows if r[0] != file]

    return {
        "file": file,
        "classes": classes,
        "functions": functions,
        "avg_risk": avg_risk,
        "imports_from": imports_from,
        "imported_by": imported_by,
    }


def similar_functions(ci: CodeIntelligence, name: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Find functions semantically similar to the given function.

    Uses Voyage embeddings to find code with similar purpose/implementation.
    Useful for deduplication, refactoring, and understanding patterns.
    """
    import struct as _struct

    # Find the source function's embedding
    row = ci.conn.execute(
        "SELECT qualified_name, file_path FROM nodes WHERE name = ? OR qualified_name LIKE ?",
        (name, f"%::{name}"),
    ).fetchone()

    if not row:
        return []

    source_qname = row[0]
    source_file = row[1]

    # Get its Voyage embedding
    emb_row = ci.conn.execute(
        "SELECT vector FROM embeddings WHERE doc_id = ?", (source_qname,)
    ).fetchone()

    if not emb_row:
        # Fallback: use semantic search with the function name
        results = semantic_search(ci, name, top_k=top_k + 1)
        return [r for r in results if r["name"] != source_qname][:top_k]

    blob = emb_row[0]
    n_floats = len(blob) // 4
    source_vec = list(_struct.unpack(f"{n_floats}f", blob))

    # Find similar via vector search
    similar = ci._search._voyage_vector_search(source_vec, limit=top_k + 5)

    results = []
    for doc_id, rank in similar:
        if doc_id == source_qname:
            continue
        node = ci.conn.execute(
            "SELECT file_path, kind, line_start, line_end, risk_score FROM nodes WHERE qualified_name = ?",
            (doc_id,)
        ).fetchone()
        if node:
            results.append({
                "name": doc_id,
                "file": node[0],
                "kind": node[1],
                "lines": (node[3] or 0) - (node[2] or 0),
                "risk_score": round(node[4] or 0, 3),
                "similarity_rank": rank,
            })
        if len(results) >= top_k:
            break

    return results


def grep_search(
    ci: CodeIntelligence,
    pattern: str,
    *,
    glob: str | None = None,
    max_results: int = 50,
    context_lines: int = 0,
    fixed_string: bool = False,
    sort_by: str = "risk",
) -> dict[str, Any]:
    """Search codebase via regex/literal grep, enriched with code graph context.

    Uses Python re + pathlib for portability. Each match in a .py file is
    enriched with enclosing function, risk score, fan-in, and caller count.

    Args:
        pattern: Regex pattern (or literal if fixed_string=True).
        glob: File glob filter (e.g. "*.py", "*.md"). Defaults to all files.
        max_results: Maximum matches to return.
        context_lines: Lines of context before/after each match (0 = match only).
        fixed_string: If True, treat pattern as a literal string.
        sort_by: "risk" (high-risk matches first) or "file" (file order).

    Returns:
        Dict with matches list, total_matches count, and enrichment stats.
    """
    import re

    from shared.code_intelligence import _is_excluded

    project_root = Path(ci._project_root) if hasattr(ci, "_project_root") else Path.cwd()

    if fixed_string:
        regex = re.compile(re.escape(pattern))
    else:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {"status": "error", "message": f"Invalid regex: {e}", "matches": []}

    # Determine file pattern
    file_glob = glob or "*"

    # Collect matching files
    matches: list[dict[str, Any]] = []
    total_matches = 0
    enriched_count = 0

    # Walk project using rglob
    for fpath in sorted(project_root.rglob(file_glob)):
        if not fpath.is_file():
            continue
        rel = str(fpath.relative_to(project_root))
        # Skip excluded directories
        if _is_excluded(rel):
            continue

        try:
            text = fpath.read_text(errors="replace")
        except (OSError, PermissionError):
            continue

        lines = text.splitlines()
        for i, line in enumerate(lines):
            if regex.search(line):
                total_matches += 1
                if len(matches) >= max_results:
                    continue  # keep counting total but stop collecting

                match_entry: dict[str, Any] = {
                    "file": rel,
                    "line_number": i + 1,
                    "content": line.rstrip(),
                }

                # Add context lines
                if context_lines > 0:
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    match_entry["context"] = [
                        ln.rstrip() for ln in lines[start:end]
                    ]

                # Enrich .py file matches with code graph data
                if rel.endswith(".py"):
                    node = ci.conn.execute(
                        """SELECT qualified_name, risk_score, fan_in
                           FROM nodes
                           WHERE file_path = ? AND line_start <= ? AND line_end >= ?
                           ORDER BY (line_end - line_start) ASC
                           LIMIT 1""",
                        (rel, i + 1, i + 1),
                    ).fetchone()
                    if node:
                        callers = ci.conn.execute(
                            "SELECT COUNT(*) FROM edges WHERE target_qname = ? AND kind = 'calls'",
                            (node[0],),
                        ).fetchone()[0]
                        match_entry["enclosing_function"] = node[0]
                        match_entry["risk_score"] = round(node[1] or 0, 3)
                        match_entry["fan_in"] = node[2] or 0
                        match_entry["callers_count"] = callers
                        enriched_count += 1

                matches.append(match_entry)

    # Sort results
    if sort_by == "risk" and any(m.get("risk_score") for m in matches):
        matches.sort(key=lambda m: m.get("risk_score", 0), reverse=True)

    return {
        "matches": matches,
        "total_matches": total_matches,
        "returned": len(matches),
        "enriched": enriched_count,
        "pattern": pattern,
        "glob": glob,
    }
