"""Unified Code Intelligence — persistent AST graph + semantic search.

Wraps existing CodeGraph (structural analysis) and HybridSearch (FTS5 + vector)
into a single persistent SQLite database with auto-reindexing and MCP query methods.

Usage:
    ci = CodeIntelligence("data/code_intelligence.db")
    ci.index_directory("/path/to/project")
    result = ci.find_symbol("login")
    ci.close()
"""

import hashlib
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
    ".worktrees/",
    ".coverage",
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

        # Project root for grep_search (set by index_directory or env)
        self._project_root = os.environ.get("CI_PROJECT_ROOT", str(Path.cwd()))

        # Voyage-code-3 client (lazy init)
        self._voyage_client = None

        # Wire Voyage query embedding into HybridSearch (with disk cache)
        self._query_embedding_cache: dict[str, list[float]] = {}
        self._init_query_cache_table()
        if os.environ.get(EMBEDDING_ENV_VAR):
            self._search._query_embedding_fn = self._embed_query

        # Pre-load Voyage embeddings into numpy matrix for fast search
        self._search.load_embeddings_to_memory()

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

    def _init_query_cache_table(self):
        """Create disk cache table for Voyage query embeddings."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS query_embedding_cache (
                query_hash TEXT PRIMARY KEY,
                query_text TEXT NOT NULL,
                vector BLOB NOT NULL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)
        self.conn.commit()

    def _embed_query(self, query: str) -> list[float] | None:
        """Embed a search query via Voyage Code 3.

        3-tier cache: in-memory dict → SQLite disk cache → Voyage API.
        """
        import struct as _struct

        # Tier 1: in-memory cache
        if query in self._query_embedding_cache:
            return self._query_embedding_cache[query]

        # Tier 2: disk cache
        query_hash = hashlib.md5(query.encode()).hexdigest()
        try:
            row = self.conn.execute(
                "SELECT vector FROM query_embedding_cache WHERE query_hash = ?",
                (query_hash,),
            ).fetchone()
            if row:
                blob = row[0]
                n_floats = len(blob) // 4
                vector = list(_struct.unpack(f"{n_floats}f", blob))
                self._query_embedding_cache[query] = vector
                return vector
        except Exception:
            pass  # Table may not exist yet

        # Tier 3: Voyage API
        client = self._get_voyage_client()
        if client is None:
            return None

        try:
            result = client.embed([query], model=EMBEDDING_MODEL, input_type="query")
            vector = result.embeddings[0]

            # Save to in-memory cache (LRU eviction)
            if len(self._query_embedding_cache) >= 100:
                oldest_key = next(iter(self._query_embedding_cache))
                del self._query_embedding_cache[oldest_key]
            self._query_embedding_cache[query] = vector

            # Save to disk cache
            try:
                blob = _struct.pack(f"{len(vector)}f", *vector)
                self.conn.execute(
                    "INSERT OR REPLACE INTO query_embedding_cache (query_hash, query_text, vector) VALUES (?,?,?)",
                    (query_hash, query, blob),
                )
                self.conn.commit()
            except Exception:
                pass  # Non-critical

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
        self._project_root = root

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

        # Resolve call edges (bare names → qualified names)
        self._graph._resolve_call_edges()
        self.conn.commit()

        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        resolved = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls' AND target_qname LIKE '%::%'"
        ).fetchone()[0]
        total_calls = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls'"
        ).fetchone()[0]
        logger.info(
            "Indexed %d Python files → %d nodes, %d edges (%d/%d call edges resolved)",
            len(py_files), node_count, edge_count, resolved, total_calls,
        )

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
                # Resolve new call edges (bare names → qualified names)
                self._graph._resolve_call_edges()
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

    def impact_analysis(self, files: list[str], max_depth: int = 2,
                         max_results: int = 100) -> dict[str, Any]:
        """Compute blast radius from changed files."""
        radius = self._graph.impact_radius(files, max_depth, max_results=max_results)
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
        self, diff_text: str = "", *, ref: str | None = None,
        root: str | None = None, max_depth: int = 2, max_results: int = 100,
    ) -> dict[str, Any]:
        """Blast radius from a git diff or ref."""
        import re as _re

        _empty = {"changed_files": [], "changed_functions": [], "impacted": [],
                   "impacted_files": [], "total_impacted": 0, "max_risk": 0.0}

        if ref and not diff_text:
            if ref.startswith("-"):
                return _empty
            _root = root or self._project_root
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

        ia_result = self.impact_analysis(changed_files, max_depth=max_depth, max_results=max_results)
        ia_result["changed_files"] = changed_files
        return ia_result

    def test_coverage_map(self, file: str | None = None, top_n: int = 50) -> dict[str, Any]:
        """Map which functions are tested and which tests cover them."""
        file_filter = ""
        params: list[Any] = []
        if file:
            file_filter = "AND n.file_path LIKE ?"
            params.append(f"%{file}%")

        prod_functions = self.conn.execute(
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

        test_functions = self.conn.execute(
            "SELECT qualified_name, name, file_path FROM nodes "
            "WHERE is_test = 1 AND kind IN ('function', 'method')"
        ).fetchall()

        coverage: dict[str, list[dict[str, str]]] = {}
        for test in test_functions:
            test_qname, test_name, test_file = test[0], test[1], test[2]
            callees = self.conn.execute(
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

    def call_path(self, source: str, target: str, max_depth: int = 6) -> dict[str, Any]:
        """Find shortest call path from source to target via BFS."""
        from collections import deque as _deque

        src_row = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name=? AND kind IN ('function','method') LIMIT 1",
            (source,),
        ).fetchone()
        src_qname = src_row[0] if src_row else source

        tgt_row = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name=? AND kind IN ('function','method') LIMIT 1",
            (target,),
        ).fetchone()
        tgt_qname = tgt_row[0] if tgt_row else target

        forward: dict[str, list[str]] = {}
        for row in self.conn.execute(
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
        from shared.hybrid_search import compute_graph_boost_batch

        raw = self._search.query(query, top_k=top_k * 2)  # Over-fetch for graph boost reranking

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
            self.conn, qnames,
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
            "module_summary · recent_changes · dead_code_report · "
            "complexity_hotspots · dependency_cycles · similar_functions · "
            "grep_search"
        )

        return "\n".join(lines)

    # ─── NEW TOOLS ─────────────────────────────────────────────────

    def dead_code_report(self, top_n: int = 20, file: str | None = None) -> dict[str, Any]:
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

        candidates = self.conn.execute(f"""
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
            called = self.conn.execute(
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
        total_dead = self.conn.execute(f"""
            SELECT COUNT(*) FROM nodes n {where_clause}
        """).fetchone()[0]
        total_dead_lines = self.conn.execute(f"""
            SELECT COALESCE(SUM(n.line_end - n.line_start), 0) FROM nodes n {where_clause}
        """).fetchone()[0]
        total_functions = self.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind IN ('function', 'method')"
        ).fetchone()[0]

        return {
            "total_candidates": total_dead,
            "total_functions": total_functions,
            "dead_code_pct": round(total_dead / total_functions * 100, 1) if total_functions else 0,
            "removable_lines": total_dead_lines,
            "confirmed_dead": confirmed,
        }

    def complexity_hotspots(self, top_n: int = 15) -> list[dict[str, Any]]:
        """Find functions that are high-risk AND high fan-in — complexity hotspots.

        These are the most dangerous functions: lots of callers + high risk score.
        Changes here have maximum blast radius.
        """
        rows = self.conn.execute("""
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
            callers = self.conn.execute(
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

    def dependency_cycles(self, max_depth: int = 4) -> list[dict[str, Any]]:
        """Detect circular dependencies between modules (file-level cycles).

        Finds A→B→C→A cycles that make the codebase hard to refactor.
        """
        # Build file-level adjacency from edges
        file_edges = self.conn.execute("""
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

    def similar_functions(self, name: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Find functions semantically similar to the given function.

        Uses Voyage embeddings to find code with similar purpose/implementation.
        Useful for deduplication, refactoring, and understanding patterns.
        """
        import struct as _struct

        # Find the source function's embedding
        row = self.conn.execute(
            "SELECT qualified_name, file_path FROM nodes WHERE name = ? OR qualified_name LIKE ?",
            (name, f"%::{name}"),
        ).fetchone()

        if not row:
            return []

        source_qname = row[0]
        source_file = row[1]

        # Get its Voyage embedding
        emb_row = self.conn.execute(
            "SELECT vector FROM embeddings WHERE doc_id = ?", (source_qname,)
        ).fetchone()

        if not emb_row:
            # Fallback: use semantic search with the function name
            results = self.semantic_search(name, top_k=top_k + 1)
            return [r for r in results if r["name"] != source_qname][:top_k]

        blob = emb_row[0]
        n_floats = len(blob) // 4
        source_vec = list(_struct.unpack(f"{n_floats}f", blob))

        # Find similar via vector search
        similar = self._search._voyage_vector_search(source_vec, limit=top_k + 5)

        results = []
        for doc_id, rank in similar:
            if doc_id == source_qname:
                continue
            node = self.conn.execute(
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

    # ─── GREP SEARCH ─────────────────────────────────────────────────

    def grep_search(
        self,
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

        project_root = Path(self._project_root) if hasattr(self, "_project_root") else Path.cwd()

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
                        node = self.conn.execute(
                            """SELECT qualified_name, risk_score, fan_in
                               FROM nodes
                               WHERE file_path = ? AND line_start <= ? AND line_end >= ?
                               ORDER BY (line_end - line_start) ASC
                               LIMIT 1""",
                            (rel, i + 1, i + 1),
                        ).fetchone()
                        if node:
                            callers = self.conn.execute(
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

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
