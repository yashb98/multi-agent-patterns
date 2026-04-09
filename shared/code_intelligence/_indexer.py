"""Indexing operations — full repo index and incremental file reindex.

Extracted from CodeIntelligence class (SRP split).
All functions take `ci` (CodeIntelligence instance) as first argument.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from shared.code_intelligence import CodeIntelligence

logger = get_logger(__name__)


def index_directory(ci: CodeIntelligence, root: str) -> dict[str, Any]:
    """Full repo index across all tiers.

    Phase 1: Python AST via CodeGraph (nodes + edges).
    Phase 2: Cache risk scores for all functions/methods.
    Phase 3: Index non-Python text files as document nodes.
    Phase 4: Populate FTS5 + vector search index.

    Returns:
        dict with keys: nodes, edges, documents, time_ms
    """
    from shared.code_intelligence import FULL_INDEX_EXTENSIONS, _is_excluded, _is_binary

    t0 = time.monotonic()
    root_path = Path(root)
    ci._project_root = root

    # Phase 1 — Python AST (with exclusion filtering)
    _index_python_files(ci, root_path)

    # Phase 2 — risk scores
    _cache_risk_scores(ci)

    # Phase 3 — text files
    _index_text_files(ci, root_path)

    # Phase 4 — search index
    _populate_search_index(ci)

    # Phase 5 — graph signals (PageRank, communities, fan-in/fan-out)
    _compute_graph_signals(ci)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    nodes = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edges = ci.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    documents = ci.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    logger.info(
        "index_directory complete: %d nodes, %d edges, %d documents in %dms",
        nodes,
        edges,
        documents,
        elapsed_ms,
    )
    return {"nodes": nodes, "edges": edges, "documents": documents, "time_ms": elapsed_ms}


def _index_python_files(ci: CodeIntelligence, root_path: Path) -> None:
    """Index Python files with AST, respecting exclusion patterns."""
    from shared.code_intelligence import _is_excluded

    py_files = [
        f for f in root_path.rglob("*.py")
        if f.is_file()
        and not _is_excluded(str(f.relative_to(root_path)))
    ]

    for filepath in py_files:
        try:
            ci._graph._indexer._index_file(filepath, root_path, prefix="")
        except Exception as e:
            logger.debug("Failed to parse %s: %s", filepath.name, e)

    ci.conn.commit()

    # Resolve call edges (bare names → qualified names)
    ci._graph._indexer._resolve_call_edges()
    ci.conn.commit()

    node_count = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edge_count = ci.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    resolved = ci.conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind='calls' AND target_qname LIKE '%::%'"
    ).fetchone()[0]
    total_calls = ci.conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind='calls'"
    ).fetchone()[0]
    logger.info(
        "Indexed %d Python files → %d nodes, %d edges (%d/%d call edges resolved)",
        len(py_files), node_count, edge_count, resolved, total_calls,
    )


def _index_text_files(ci: CodeIntelligence, root_path: Path) -> None:
    """Walk all files under root_path, index non-Python text files as document nodes."""
    from shared.code_intelligence import FULL_INDEX_EXTENSIONS, _is_excluded, _is_binary

    for filepath in root_path.rglob("*"):
        if not filepath.is_file():
            continue

        rel_str = str(filepath.relative_to(root_path))

        # Skip excluded paths
        if _is_excluded(rel_str) or _is_excluded(filepath.name):
            continue

        # Skip Python files (handled by CodeGraph)
        if filepath.suffix in FULL_INDEX_EXTENSIONS:
            continue

        # Skip binary files
        if _is_binary(filepath):
            continue

        qname = f"{rel_str}::__document__"
        file_path_str = str(filepath)

        try:
            ci.conn.execute(
                """
                INSERT OR REPLACE INTO nodes
                    (kind, name, qualified_name, file_path, line_start, line_end,
                     is_test, is_async, last_indexed)
                VALUES ('document', ?, ?, ?, 0, 0, 0, 0, ?)
                """,
                (filepath.name, qname, file_path_str, time.time()),
            )
        except sqlite3.Error as exc:
            logger.debug("Failed to insert document node %s: %s", qname, exc)

    ci.conn.commit()


def _cache_risk_scores(ci: CodeIntelligence) -> None:
    """Batch-compute and cache risk scores for all Python function/method nodes.

    Optimized: 3 bulk SQL queries instead of 3 per function.
    Handles 10K+ functions in seconds instead of hours.
    """
    from shared.code_graph._risk import (
        _HIGH_CONFIDENCE_KEYWORDS,
        _CONTEXT_DEPENDENT_KEYWORDS,
        _SECURITY_CONTEXT_WORDS,
    )

    # Fetch all functions with their metadata
    functions = ci.conn.execute(
        "SELECT qualified_name, name, file_path, line_start, line_end "
        "FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()

    if not functions:
        return

    # Bulk query 1: fan-in counts (callers per target name suffix)
    fan_in = {}
    for row in ci.conn.execute(
        "SELECT target_qname, COUNT(*) as cnt FROM edges "
        "WHERE kind='calls' GROUP BY target_qname"
    ).fetchall():
        # Extract name suffix for matching
        name = row[0].split("::")[-1] if "::" in row[0] else row[0]
        fan_in[name] = fan_in.get(name, 0) + row[1]

    # Bulk query 2: cross-file caller counts
    cross_file = {}
    for row in ci.conn.execute(
        "SELECT target_qname, COUNT(DISTINCT file_path) as cnt FROM edges "
        "WHERE kind='calls' GROUP BY target_qname"
    ).fetchall():
        name = row[0].split("::")[-1] if "::" in row[0] else row[0]
        cross_file[name] = max(cross_file.get(name, 0), row[1])

    # Bulk query 3: test coverage (functions called by test_* functions)
    tested_names: set[str] = set()
    for row in ci.conn.execute(
        "SELECT DISTINCT target_qname FROM edges "
        "WHERE kind='calls' AND source_qname LIKE '%test_%'"
    ).fetchall():
        name = row[0].split("::")[-1] if "::" in row[0] else row[0]
        tested_names.add(name)

    # Score each function in-memory
    now = time.time()
    updates: list[tuple[float, float, str]] = []
    for fn in functions:
        qname = fn[0]
        name = fn[1]
        file_path = fn[2]
        line_start = fn[3] or 0
        line_end = fn[4] or 0
        name_lower = name.lower()

        score = 0.0

        # Security keywords — two-tier matching (matches CodeGraph.compute_risk_score)
        has_high = any(kw in name_lower for kw in _HIGH_CONFIDENCE_KEYWORDS)
        has_ctx_dep = any(kw in name_lower for kw in _CONTEXT_DEPENDENT_KEYWORDS)
        has_sec_ctx = any(ctx in name_lower for ctx in _SECURITY_CONTEXT_WORDS)
        if has_high or (has_ctx_dep and has_sec_ctx):
            score += 0.25

        # Fan-in
        callers = fan_in.get(name, 0)
        score += min(callers * 0.05, 0.20)

        # Cross-file callers (subtract 1 for the defining file)
        cf = cross_file.get(name, 0)
        if cf > 1:
            score += 0.10

        # Test coverage
        if name not in tested_names:
            score += 0.30

        # Function size
        if (line_end - line_start) > 50:
            score += 0.15

        updates.append((min(score, 1.0), now, qname))

    ci.conn.executemany(
        "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
        updates,
    )
    ci.conn.commit()
    logger.info("Batch risk scoring: %d functions scored", len(updates))


def _populate_search_index(ci: CodeIntelligence) -> None:
    """Add all nodes to FTS5 search index, then trigger Voyage embeddings."""
    rows = ci.conn.execute(
        "SELECT qualified_name, kind, name, file_path, signature, docstring FROM nodes"
    ).fetchall()

    for row in rows:
        qname = row[0]
        kind = row[1]
        name = row[2]
        file_path = row[3]
        signature = row[4] or ""
        docstring = row[5] or ""

        if kind == "document":
            # Read file text for document nodes
            try:
                text = Path(file_path).read_text(encoding="utf-8", errors="replace")[:5000]
            except (OSError, PermissionError):
                text = name
        else:
            parts = [name]
            if signature:
                parts.append(signature)
            if docstring:
                parts.append(docstring)
            text = " ".join(parts)

        metadata: dict[str, Any] = {"kind": kind, "file_path": file_path}

        try:
            ci._search.add(qname, text, metadata)
        except Exception as exc:
            logger.debug("Failed to add %s to search index: %s", qname, exc)

    _compute_voyage_embeddings(ci)


def _compute_voyage_embeddings(ci: CodeIntelligence) -> None:
    """Batch embed all documents via Voyage-code-3 and store as packed floats.

    Gracefully does nothing if VOYAGE_API_KEY is not set or voyageai is not installed.
    Uses smaller batch size (32) to stay under Voyage's 120K token/batch limit.
    Filters empty strings to avoid API validation errors.
    """
    from shared.code_intelligence import EMBEDDING_MODEL

    client = ci._get_voyage_client()
    if client is None:
        return

    try:
        import struct

        # Only embed documents not yet in embeddings table
        rows = ci.conn.execute(
            "SELECT d.id, d.text FROM documents d "
            "LEFT JOIN embeddings e ON d.id = e.doc_id "
            "WHERE e.doc_id IS NULL"
        ).fetchall()

        if not rows:
            return

        # Filter out empty/whitespace-only texts
        valid = [(r[0], r[1]) for r in rows if r[1] and r[1].strip()]

        if not valid:
            return

        ids = [v[0] for v in valid]
        texts = [v[1] for v in valid]

        # Use smaller batch size (32) to stay under 120K token limit
        batch_size = 32
        embedded = 0

        for batch_start in range(0, len(texts), batch_size):
            batch_ids = ids[batch_start : batch_start + batch_size]
            batch_texts = texts[batch_start : batch_start + batch_size]

            try:
                result = client.embed(
                    batch_texts,
                    model=EMBEDDING_MODEL,
                    input_type="document",
                )
                vectors = result.embeddings
            except Exception as exc:
                logger.warning("Voyage embedding batch failed: %s", exc)
                continue

            rows_to_insert = []
            for doc_id, vector in zip(batch_ids, vectors, strict=True):
                packed = struct.pack(f"{len(vector)}f", *vector)
                rows_to_insert.append((doc_id, packed))

            ci.conn.executemany(
                "INSERT OR REPLACE INTO embeddings (doc_id, vector) VALUES (?, ?)",
                rows_to_insert,
            )
            ci.conn.commit()
            embedded += len(rows_to_insert)

        logger.info("Voyage embeddings: %d/%d documents", embedded, len(valid))

    except Exception as exc:
        logger.warning("Voyage embedding step failed: %s", exc)


def _compute_graph_signals(ci: CodeIntelligence) -> None:
    """Compute graph-global signals: fan-in/fan-out, PageRank, communities."""
    try:
        ci._graph.compute_fan_in_out()
        ci._graph.compute_pagerank()
        ci._graph.compute_communities()
        logger.info("Graph signals computed (fan-in, PageRank, communities)")
    except Exception as exc:
        logger.warning("Graph signal computation failed: %s", exc)


def reindex_file(ci: CodeIntelligence, rel_path: str, root: str | None = None) -> dict[str, Any]:
    """Incrementally reindex a single file.

    Deletes stale data for the file, re-parses it (AST for Python,
    document node for text files), recomputes risk scores for changed
    functions and their callers, and updates the search index.

    Args:
        rel_path: Path relative to the project root (e.g. "auth.py").
        root: Absolute path to the project root.  Uses the directory
              of the DB file as a fallback when not supplied.

    Returns:
        dict with keys: nodes_added, edges_added, risk_updated, time_ms
    """
    from shared.code_intelligence import _is_excluded, _is_binary

    t0 = time.monotonic()

    # 1. Exclusion check — fast path
    if _is_excluded(rel_path) or _is_excluded(Path(rel_path).name):
        return {"nodes_added": 0, "edges_added": 0, "risk_updated": 0, "time_ms": 0}

    # Resolve absolute path
    if root is None:
        root = str(Path(ci.db_path).parent)
    root_path = Path(root)
    abs_path = root_path / rel_path

    # 2. Collect qualified names of existing nodes for this file
    #    (needed to find callers before we delete them)
    old_rows = ci.conn.execute(
        "SELECT qualified_name FROM nodes WHERE file_path=?",
        (rel_path,),
    ).fetchall()
    old_qnames = {r[0] for r in old_rows}

    # 3. Find callers of functions defined in this file
    caller_qnames: set[str] = set()
    if old_qnames:
        placeholders = ",".join("?" * len(old_qnames))
        caller_rows = ci.conn.execute(
            f"SELECT DISTINCT source_qname FROM edges WHERE target_qname IN ({placeholders})",
            list(old_qnames),
        ).fetchall()
        caller_qnames = {r[0] for r in caller_rows}

    # 4. Delete stale data for this file
    ci.conn.execute("DELETE FROM nodes WHERE file_path=?", (rel_path,))
    # Delete by the old qualified names we already captured
    if old_qnames:
        placeholders = ",".join("?" * len(old_qnames))
        ci.conn.execute(
            f"DELETE FROM edges WHERE source_qname IN ({placeholders})"
            f" OR target_qname IN ({placeholders})",
            list(old_qnames) * 2,
        )
    # Remove from FTS5 + embeddings
    if old_qnames:
        placeholders = ",".join("?" * len(old_qnames))
        ci.conn.execute(
            f"DELETE FROM documents WHERE id IN ({placeholders})",
            list(old_qnames),
        )
        ci.conn.execute(
            f"DELETE FROM embeddings WHERE doc_id IN ({placeholders})",
            list(old_qnames),
        )
    ci.conn.commit()

    # 5. File doesn't exist — cleanup done
    if not abs_path.exists():
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {"nodes_added": 0, "edges_added": 0, "risk_updated": 0, "time_ms": elapsed_ms}

    nodes_before = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edges_before = ci.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    # 6. Python file — use AST indexer
    if abs_path.suffix == ".py":
        try:
            ci._graph._indexer._index_file(abs_path, root_path, prefix="")
            ci.conn.commit()
            # Resolve new call edges (bare names → qualified names)
            ci._graph._indexer._resolve_call_edges()
            ci.conn.commit()
        except Exception as exc:
            logger.warning("reindex_file AST parse failed for %s: %s", rel_path, exc)

    elif not _is_binary(abs_path):
        # 7. Non-Python text file — create a document node
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")[:5000]
            qname = f"{rel_path}::__document__"
            ci.conn.execute(
                """
                INSERT OR REPLACE INTO nodes
                    (kind, name, qualified_name, file_path, line_start, line_end,
                     is_test, is_async, last_indexed)
                VALUES ('document', ?, ?, ?, 0, 0, 0, 0, ?)
                """,
                (abs_path.name, qname, rel_path, time.time()),
            )
            ci.conn.commit()
        except (OSError, PermissionError, sqlite3.Error) as exc:
            logger.warning("reindex_file document insert failed for %s: %s", rel_path, exc)

    # 8. Count new nodes/edges
    nodes_after = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edges_after = ci.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    nodes_added = max(0, nodes_after - nodes_before)
    edges_added = max(0, edges_after - edges_before)

    # 9. Recompute risk scores for this file's functions + surviving callers
    new_qname_rows = ci.conn.execute(
        "SELECT qualified_name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
        (rel_path,),
    ).fetchall()
    new_qnames = {r[0] for r in new_qname_rows}

    existing_callers: set[str] = set()
    if caller_qnames:
        placeholders = ",".join("?" * len(caller_qnames))
        existing_rows = ci.conn.execute(
            f"SELECT qualified_name FROM nodes WHERE qualified_name IN ({placeholders})",
            list(caller_qnames),
        ).fetchall()
        existing_callers = {r[0] for r in existing_rows}
    to_rescore = new_qnames | existing_callers

    now = time.time()
    risk_updates: list[tuple[float, float, str]] = []
    for qname in to_rescore:
        try:
            score = ci._graph.compute_risk_score(qname)
        except Exception as exc:
            logger.debug("Risk rescore failed for %s: %s", qname, exc)
            score = 0.0
        risk_updates.append((score, now, qname))

    if risk_updates:
        ci.conn.executemany(
            "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
            risk_updates,
        )
        ci.conn.commit()

    # 10. Update search index for new nodes
    new_node_rows = ci.conn.execute(
        "SELECT qualified_name, kind, name, file_path, signature, docstring FROM nodes "
        "WHERE file_path=?",
        (rel_path,),
    ).fetchall()

    for row in new_node_rows:
        qname = row[0]
        kind = row[1]
        name = row[2]
        file_path = row[3]
        signature = row[4] or ""
        docstring = row[5] or ""

        if kind == "document":
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")[:5000]
            except (OSError, PermissionError):
                text = name
        else:
            parts = [name]
            if signature:
                parts.append(signature)
            if docstring:
                parts.append(docstring)
            text = " ".join(parts)

        metadata: dict[str, Any] = {"kind": kind, "file_path": file_path}
        try:
            ci._search.add(qname, text, metadata)
        except Exception as exc:
            logger.debug("reindex_file search add failed for %s: %s", qname, exc)

    # 11. Recompute graph-global signals (PageRank, communities affected by edge changes)
    _compute_graph_signals(ci)

    # 12. Return stats
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {
        "nodes_added": nodes_added,
        "edges_added": edges_added,
        "risk_updated": len(risk_updates),
        "time_ms": elapsed_ms,
    }
