"""Unified Code Intelligence — persistent AST graph + semantic search.

Wraps existing CodeGraph (structural analysis) and HybridSearch (FTS5 + vector)
into a single persistent SQLite database with auto-reindexing and MCP query methods.

Usage:
    ci = CodeIntelligence("data/code_intelligence.db")
    ci.index_directory("/path/to/project")
    result = ci.find_symbol("login")
    ci.close()
"""

import json
import os
import sqlite3
import subprocess
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from shared.code_graph import CodeGraph
from shared.hybrid_search import HybridSearch
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────

FULL_INDEX_EXTENSIONS = (".py",)

EXCLUDE_PATTERNS = {
    ".env",
    ".env.*",
    "*.pyc",
    "__pycache__/",
    ".git/",
    "node_modules/",
    "*.db",
    "*.sqlite",
    "*.png",
    "*.jpg",
    "*.ico",
    "*.gif",
    "*.svg",
    "*.woff",
    "*.ttf",
    "*.woff2",
    "*.pdf",
    "*.lock",
    "venv/",
    ".venv/",
    ".claude/worktrees/",
}

EMBEDDING_MODEL = "voyage-code-3"
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_BATCH_SIZE = 128
EMBEDDING_ENV_VAR = "VOYAGE_API_KEY"


def _is_excluded(path: str) -> bool:
    """Check if a path matches any exclusion pattern."""
    parts = Path(path).parts
    path_str = path.replace("\\", "/")
    for pattern in EXCLUDE_PATTERNS:
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            # Multi-segment dir pattern (e.g. ".claude/worktrees") — check substring
            if "/" in dir_name:
                if dir_name + "/" in path_str or path_str.startswith(dir_name + "/"):
                    return True
            elif dir_name in parts:
                return True
        elif fnmatch(Path(path).name, pattern):
            return True
    return False


def _is_binary(filepath: Path, sample_size: int = 8192) -> bool:
    """Quick heuristic: file is binary if it has null bytes in first 8KB."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(sample_size)
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True


class CodeIntelligence:
    """Unified code intelligence — structural graph + semantic search.

    Wraps CodeGraph + HybridSearch with a shared SQLite connection.
    Single DB file at db_path with WAL mode for concurrent reads.
    """

    def __init__(self, db_path: str = "data/code_intelligence.db"):
        self.db_path = db_path

        # Ensure parent directory exists
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # Single shared connection
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")

        # Extended schema (beyond what CodeGraph/HybridSearch create)
        self._init_extended_schema()

        # Compose existing classes with shared connection
        self._graph = CodeGraph(conn=self.conn)
        self._search = HybridSearch(conn=self.conn)

        # Voyage-code-3 client (lazy init)
        self._voyage_client = None

        # Wire Voyage query embedding into HybridSearch
        self._query_embedding_cache: dict[str, list[float]] = {}
        if os.environ.get(EMBEDDING_ENV_VAR):
            self._search._query_embedding_fn = self._embed_query

    def _init_extended_schema(self):
        """Create columns/tables beyond what CodeGraph + HybridSearch provide."""
        # We need CodeGraph's schema first — create it via a temp instance
        # that uses our connection
        CodeGraph(conn=self.conn)
        HybridSearch(conn=self.conn)

        # Now add extended columns to nodes if they don't exist
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(nodes)").fetchall()}

        if "signature" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN signature TEXT DEFAULT ''")
        if "docstring" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN docstring TEXT DEFAULT ''")
        if "risk_score" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN risk_score REAL DEFAULT 0.0")
        if "last_indexed" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN last_indexed REAL DEFAULT 0.0")

        # Embeddings table (Voyage-code-3 vectors, separate from HybridSearch's bag-of-words)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                doc_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
        """)

        # Risk score index
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_risk ON nodes(risk_score DESC)")

        self.conn.commit()

    def _get_voyage_client(self):
        """Lazy-init Voyage client. Returns None if no API key."""
        if self._voyage_client is not None:
            return self._voyage_client

        api_key = os.environ.get(EMBEDDING_ENV_VAR)
        if not api_key:
            logger.info("VOYAGE_API_KEY not set — using FTS5-only search")
            return None

        try:
            import voyageai

            self._voyage_client = voyageai.Client(api_key=api_key)
            return self._voyage_client
        except ImportError:
            logger.warning("voyageai package not installed — using FTS5-only search")
            return None

    def _embed_query(self, query: str) -> list[float] | None:
        """Embed a search query via Voyage Code 3. LRU cache (max 100)."""
        if query in self._query_embedding_cache:
            return self._query_embedding_cache[query]

        client = self._get_voyage_client()
        if client is None:
            return None

        try:
            result = client.embed([query], model=EMBEDDING_MODEL, input_type="query")
            vector = result.embeddings[0]
            # LRU eviction
            if len(self._query_embedding_cache) >= 100:
                oldest_key = next(iter(self._query_embedding_cache))
                del self._query_embedding_cache[oldest_key]
            self._query_embedding_cache[query] = vector
            return vector
        except Exception as exc:
            logger.warning("Voyage query embedding failed: %s", exc)
            return None

    # ─── INDEXING ──────────────────────────────────────────────────

    def index_directory(self, root: str) -> dict[str, Any]:
        """Full repo index across all tiers.

        Phase 1: Python AST via CodeGraph (nodes + edges).
        Phase 2: Cache risk scores for all functions/methods.
        Phase 3: Index non-Python text files as document nodes.
        Phase 4: Populate FTS5 + vector search index.

        Returns:
            dict with keys: nodes, edges, documents, time_ms
        """
        t0 = time.monotonic()
        root_path = Path(root)

        # Phase 1 — Python AST (with exclusion filtering)
        self._index_python_files(root_path)

        # Phase 2 — risk scores
        self._cache_risk_scores()

        # Phase 3 — text files
        self._index_text_files(root_path)

        # Phase 4 — search index
        self._populate_search_index()

        # Phase 5 — graph signals (PageRank, communities, fan-in/fan-out)
        self._compute_graph_signals()

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        documents = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        logger.info(
            "index_directory complete: %d nodes, %d edges, %d documents in %dms",
            nodes,
            edges,
            documents,
            elapsed_ms,
        )
        return {"nodes": nodes, "edges": edges, "documents": documents, "time_ms": elapsed_ms}

    def _index_python_files(self, root_path: Path) -> None:
        """Index Python files with AST, respecting exclusion patterns."""
        py_files = [
            f for f in root_path.rglob("*.py")
            if f.is_file()
            and not _is_excluded(str(f.relative_to(root_path)))
        ]

        for filepath in py_files:
            try:
                self._graph._index_file(filepath, root_path, prefix="")
            except Exception as e:
                logger.debug("Failed to parse %s: %s", filepath.name, e)

        self.conn.commit()

        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        logger.info("Indexed %d Python files → %d nodes, %d edges", len(py_files), node_count, edge_count)

    def _index_text_files(self, root_path: Path) -> None:
        """Walk all files under root_path, index non-Python text files as document nodes."""
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
                self.conn.execute(
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

        self.conn.commit()

    def _cache_risk_scores(self) -> None:
        """Batch-compute and cache risk scores for all Python function/method nodes.

        Optimized: 3 bulk SQL queries instead of 3 per function.
        Handles 10K+ functions in seconds instead of hours.
        """
        from shared.code_graph import (
            _HIGH_CONFIDENCE_KEYWORDS,
            _CONTEXT_DEPENDENT_KEYWORDS,
            _SECURITY_CONTEXT_WORDS,
        )

        # Fetch all functions with their metadata
        functions = self.conn.execute(
            "SELECT qualified_name, name, file_path, line_start, line_end "
            "FROM nodes WHERE kind IN ('function', 'method')"
        ).fetchall()

        if not functions:
            return

        # Bulk query 1: fan-in counts (callers per target name suffix)
        fan_in = {}
        for row in self.conn.execute(
            "SELECT target_qname, COUNT(*) as cnt FROM edges "
            "WHERE kind='calls' GROUP BY target_qname"
        ).fetchall():
            # Extract name suffix for matching
            name = row[0].split("::")[-1] if "::" in row[0] else row[0]
            fan_in[name] = fan_in.get(name, 0) + row[1]

        # Bulk query 2: cross-file caller counts
        cross_file = {}
        for row in self.conn.execute(
            "SELECT target_qname, COUNT(DISTINCT file_path) as cnt FROM edges "
            "WHERE kind='calls' GROUP BY target_qname"
        ).fetchall():
            name = row[0].split("::")[-1] if "::" in row[0] else row[0]
            cross_file[name] = max(cross_file.get(name, 0), row[1])

        # Bulk query 3: test coverage (functions called by test_* functions)
        tested_names: set[str] = set()
        for row in self.conn.execute(
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

        self.conn.executemany(
            "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
            updates,
        )
        self.conn.commit()
        logger.info("Batch risk scoring: %d functions scored", len(updates))

    def _populate_search_index(self) -> None:
        """Add all nodes to FTS5 search index, then trigger Voyage embeddings."""
        rows = self.conn.execute(
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
                self._search.add(qname, text, metadata)
            except Exception as exc:
                logger.debug("Failed to add %s to search index: %s", qname, exc)

        self._compute_voyage_embeddings()

    def _compute_voyage_embeddings(self) -> None:
        """Batch embed all documents via Voyage-code-3 and store as packed floats.

        Gracefully does nothing if VOYAGE_API_KEY is not set or voyageai is not installed.
        Uses smaller batch size (32) to stay under Voyage's 120K token/batch limit.
        Filters empty strings to avoid API validation errors.
        """
        client = self._get_voyage_client()
        if client is None:
            return

        try:
            import struct

            # Only embed documents not yet in embeddings table
            rows = self.conn.execute(
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

                self.conn.executemany(
                    "INSERT OR REPLACE INTO embeddings (doc_id, vector) VALUES (?, ?)",
                    rows_to_insert,
                )
                self.conn.commit()
                embedded += len(rows_to_insert)

            logger.info("Voyage embeddings: %d/%d documents", embedded, len(valid))

        except Exception as exc:
            logger.warning("Voyage embedding step failed: %s", exc)

    def _compute_graph_signals(self) -> None:
        """Compute graph-global signals: fan-in/fan-out, PageRank, communities."""
        try:
            self._graph.compute_fan_in_out()
            self._graph.compute_pagerank()
            self._graph.compute_communities()
            logger.info("Graph signals computed (fan-in, PageRank, communities)")
        except Exception as exc:
            logger.warning("Graph signal computation failed: %s", exc)

    # ─── INCREMENTAL REINDEX ───────────────────────────────────────

    def reindex_file(self, rel_path: str, root: str | None = None) -> dict[str, Any]:
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
        t0 = time.monotonic()

        # 1. Exclusion check — fast path
        if _is_excluded(rel_path) or _is_excluded(Path(rel_path).name):
            return {"nodes_added": 0, "edges_added": 0, "risk_updated": 0, "time_ms": 0}

        # Resolve absolute path
        if root is None:
            root = str(Path(self.db_path).parent)
        root_path = Path(root)
        abs_path = root_path / rel_path

        # 2. Collect qualified names of existing nodes for this file
        #    (needed to find callers before we delete them)
        old_rows = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE file_path=?",
            (rel_path,),
        ).fetchall()
        old_qnames = {r[0] for r in old_rows}

        # 3. Find callers of functions defined in this file
        caller_qnames: set[str] = set()
        if old_qnames:
            placeholders = ",".join("?" * len(old_qnames))
            caller_rows = self.conn.execute(
                f"SELECT DISTINCT source_qname FROM edges WHERE target_qname IN ({placeholders})",
                list(old_qnames),
            ).fetchall()
            caller_qnames = {r[0] for r in caller_rows}

        # 4. Delete stale data for this file
        self.conn.execute("DELETE FROM nodes WHERE file_path=?", (rel_path,))
        # Delete by the old qualified names we already captured
        if old_qnames:
            placeholders = ",".join("?" * len(old_qnames))
            self.conn.execute(
                f"DELETE FROM edges WHERE source_qname IN ({placeholders})"
                f" OR target_qname IN ({placeholders})",
                list(old_qnames) * 2,
            )
        # Remove from FTS5 + embeddings
        if old_qnames:
            placeholders = ",".join("?" * len(old_qnames))
            self.conn.execute(
                f"DELETE FROM documents WHERE id IN ({placeholders})",
                list(old_qnames),
            )
            self.conn.execute(
                f"DELETE FROM embeddings WHERE doc_id IN ({placeholders})",
                list(old_qnames),
            )
        self.conn.commit()

        # 5. File doesn't exist — cleanup done
        if not abs_path.exists():
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return {"nodes_added": 0, "edges_added": 0, "risk_updated": 0, "time_ms": elapsed_ms}

        nodes_before = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges_before = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # 6. Python file — use AST indexer
        if abs_path.suffix == ".py":
            try:
                self._graph._index_file(abs_path, root_path, prefix="")
                self.conn.commit()
            except Exception as exc:
                logger.warning("reindex_file AST parse failed for %s: %s", rel_path, exc)

        elif not _is_binary(abs_path):
            # 7. Non-Python text file — create a document node
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")[:5000]
                qname = f"{rel_path}::__document__"
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO nodes
                        (kind, name, qualified_name, file_path, line_start, line_end,
                         is_test, is_async, last_indexed)
                    VALUES ('document', ?, ?, ?, 0, 0, 0, 0, ?)
                    """,
                    (abs_path.name, qname, rel_path, time.time()),
                )
                self.conn.commit()
            except (OSError, PermissionError, sqlite3.Error) as exc:
                logger.warning("reindex_file document insert failed for %s: %s", rel_path, exc)

        # 8. Count new nodes/edges
        nodes_after = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges_after = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        nodes_added = max(0, nodes_after - nodes_before)
        edges_added = max(0, edges_after - edges_before)

        # 9. Recompute risk scores for this file's functions + surviving callers
        new_qname_rows = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
            (rel_path,),
        ).fetchall()
        new_qnames = {r[0] for r in new_qname_rows}

        existing_callers: set[str] = set()
        if caller_qnames:
            placeholders = ",".join("?" * len(caller_qnames))
            existing_rows = self.conn.execute(
                f"SELECT qualified_name FROM nodes WHERE qualified_name IN ({placeholders})",
                list(caller_qnames),
            ).fetchall()
            existing_callers = {r[0] for r in existing_rows}
        to_rescore = new_qnames | existing_callers

        now = time.time()
        risk_updates: list[tuple[float, float, str]] = []
        for qname in to_rescore:
            try:
                score = self._graph.compute_risk_score(qname)
            except Exception as exc:
                logger.debug("Risk rescore failed for %s: %s", qname, exc)
                score = 0.0
            risk_updates.append((score, now, qname))

        if risk_updates:
            self.conn.executemany(
                "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
                risk_updates,
            )
            self.conn.commit()

        # 10. Update search index for new nodes
        new_node_rows = self.conn.execute(
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
                self._search.add(qname, text, metadata)
            except Exception as exc:
                logger.debug("reindex_file search add failed for %s: %s", qname, exc)

        # 11. Recompute graph-global signals (PageRank, communities affected by edge changes)
        self._compute_graph_signals()

        # 12. Return stats
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "nodes_added": nodes_added,
            "edges_added": edges_added,
            "risk_updated": len(risk_updates),
            "time_ms": elapsed_ms,
        }

    # ─── MCP QUERY METHODS ────────────────────────────────────────

    def find_symbol(self, name: str) -> dict[str, Any] | None:
        """Find a function, class, or method by name. Exact match first, LIKE fallback."""
        row = self.conn.execute(
            "SELECT qualified_name, name, kind, file_path, line_start, line_end, "
            "risk_score, is_async FROM nodes WHERE name=? AND kind != 'document' LIMIT 1",
            (name,),
        ).fetchone()

        if row is None:
            row = self.conn.execute(
                "SELECT qualified_name, name, kind, file_path, line_start, line_end, "
                "risk_score, is_async FROM nodes WHERE name LIKE ? AND kind != 'document' LIMIT 1",
                (f"%{name}%",),
            ).fetchone()

        if row is None:
            return None

        qname = row[0]
        callers = self._graph.callers_of(name)
        callees = self._graph.callees_of(qname)

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

    def callers_of(self, name: str, max_results: int = 20) -> dict[str, Any]:
        """Find all functions that call the given name."""
        raw = self._graph.callers_of(name)
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

    def callees_of(self, name: str, max_results: int = 20) -> dict[str, Any]:
        """Find all functions called by the given name."""
        # Resolve qualified name from the nodes table
        row = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name=? AND kind != 'document' LIMIT 1",
            (name,),
        ).fetchone()
        qname = row[0] if row else name

        raw = self._graph.callees_of(qname)
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

    def impact_analysis(self, files: list[str], max_depth: int = 2) -> dict[str, Any]:
        """Compute blast radius from changed files."""
        radius = self._graph.impact_radius(files, max_depth)
        impacted_files: set[str] = radius.get("impacted_files", set())
        impacted_nodes: list[dict[str, Any]] = radius.get("impacted_nodes", [])
        depth_map: dict[str, int] = radius.get("depth_map", {})

        # Enumerate functions directly changed in the specified files
        changed_functions: list[str] = []
        for f in files:
            rows = self.conn.execute(
                "SELECT name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
                (f,),
            ).fetchall()
            changed_functions.extend(r[0] for r in rows)

        enriched: list[dict[str, Any]] = []
        for node in impacted_nodes:
            node_file = node.get("file_path", "")
            risk_row = self.conn.execute(
                "SELECT risk_score FROM nodes WHERE qualified_name=?",
                (node.get("qualified_name", ""),),
            ).fetchone()
            risk = risk_row[0] if risk_row else 0.0
            enriched.append(
                {
                    "name": node.get("name", ""),
                    "file": node_file,
                    "depth": depth_map.get(node_file, 0),
                    "risk": risk,
                }
            )

        max_risk = max((e["risk"] for e in enriched), default=0.0)

        return {
            "changed_functions": changed_functions,
            "impacted": enriched,
            "impacted_files": list(impacted_files),
            "total_functions": len(enriched),
            "max_risk": max_risk,
        }

    def risk_report(self, top_n: int = 10, file: str | None = None) -> dict[str, Any]:
        """Top-N highest-risk functions, optionally filtered by file."""
        if file is not None:
            rows = self.conn.execute(
                "SELECT name, file_path, risk_score FROM nodes "
                "WHERE kind IN ('function', 'method') AND file_path=? "
                "ORDER BY risk_score DESC LIMIT ?",
                (file, top_n),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT name, file_path, risk_score FROM nodes "
                "WHERE kind IN ('function', 'method') "
                "ORDER BY risk_score DESC LIMIT ?",
                (top_n,),
            ).fetchall()

        functions = [{"name": r[0], "file": r[1], "risk": r[2]} for r in rows]
        return {"functions": functions}

    def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        context_symbol: str | None = None,
        search_context: str = "general",
    ) -> list[dict[str, Any]]:
        """Hybrid FTS5 + vector semantic search with graph boosting."""
        from shared.hybrid_search import compute_graph_boost

        raw = self._search.query(query, top_k=top_k * 2)  # Over-fetch for graph boost reranking
        results: list[dict[str, Any]] = []
        for item in raw:
            metadata = item.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            qname = item.get("id", "")
            base_score = item.get("score", 0.0)

            # Apply graph boost
            boost = compute_graph_boost(
                self.conn, qname,
                context_qname=context_symbol,
                search_context=search_context,
            )

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

    def module_summary(self, file: str) -> dict[str, Any]:
        """Summary of a file: classes, functions, risk, imports."""
        # Classes
        class_rows = self.conn.execute(
            "SELECT name, line_start, line_end FROM nodes WHERE file_path=? AND kind='class'",
            (file,),
        ).fetchall()

        classes: list[dict[str, Any]] = []
        for crow in class_rows:
            class_name = crow[0]
            # Find methods belonging to this class
            method_rows = self.conn.execute(
                "SELECT name FROM nodes WHERE file_path=? AND kind='method' "
                "AND qualified_name LIKE ?",
                (file, f"%.{class_name}.%"),
            ).fetchall()
            methods = [m[0] for m in method_rows]
            lines = (crow[2] or 0) - (crow[1] or 0)
            classes.append({"name": class_name, "methods": methods, "lines": lines})

        # Top-level functions
        func_rows = self.conn.execute(
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
        risk_rows = self.conn.execute(
            "SELECT AVG(risk_score) FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
            (file,),
        ).fetchone()
        avg_risk: float = risk_rows[0] if risk_rows and risk_rows[0] is not None else 0.0

        # imports_from: files that this file's functions are called from
        qname_rows = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
            (file,),
        ).fetchall()
        all_qnames = [r[0] for r in qname_rows]
        imports_from: list[str] = []
        if all_qnames:
            placeholders = ",".join("?" * len(all_qnames))
            caller_rows = self.conn.execute(
                f"SELECT DISTINCT file_path FROM edges WHERE target_qname IN ({placeholders})",
                all_qnames,
            ).fetchall()
            imports_from = [r[0] for r in caller_rows if r[0] != file]

        # imported_by: files that this file's functions call into
        imported_by: list[str] = []
        if all_qnames:
            callee_rows = self.conn.execute(
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

    def recent_changes(self, n_commits: int = 3, root: str | None = None) -> dict[str, Any]:
        """Cross-reference recent git commits with code graph."""
        if root is None:
            root = str(Path(self.db_path).parent)

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
            rows = self.conn.execute(
                "SELECT name FROM nodes WHERE file_path=? AND risk_score > 0.5 "
                "AND kind IN ('function', 'method')",
                (f,),
            ).fetchall()
            new_high_risk.extend(r[0] for r in rows)

        return {"commits": commits, "hotspots": hotspots, "new_high_risk": new_high_risk}

    # ─── SESSION PRIMER ───────────────────────────────────────────

    def get_primer(self, top_risk: int = 5, n_commits: int = 3) -> str:
        """Formatted codebase fingerprint for SessionStart hook."""
        total_nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        total_edges = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        lines: list[str] = [
            "=== Code Intelligence Primer ===",
            f"Nodes: {total_nodes}  Edges: {total_edges}",
        ]

        # Top-risk functions
        risk = self.risk_report(top_n=top_risk)
        if risk["functions"]:
            lines.append(f"\nTop-{top_risk} high-risk functions:")
            for fn in risk["functions"]:
                lines.append(f"  • {fn['name']} ({fn['file']})  risk={fn['risk']:.2f}")

        # Recent commits
        changes = self.recent_changes(n_commits=n_commits)
        if changes["commits"]:
            lines.append(f"\nRecent {n_commits} commits:")
            for commit in changes["commits"]:
                sha_short = commit["sha"][:7]
                lines.append(f"  [{sha_short}] {commit['message']}")

        # Available MCP tools
        lines.append(
            "\nMCP tools: find_symbol · callers_of · callees_of · "
            "impact_analysis · risk_report · semantic_search · "
            "module_summary · recent_changes"
        )

        return "\n".join(lines)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
