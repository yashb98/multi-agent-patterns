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
    ".env", ".env.*",
    "*.pyc", "__pycache__/",
    ".git/",
    "node_modules/",
    "*.db", "*.sqlite",
    "*.png", "*.jpg", "*.ico", "*.gif", "*.svg",
    "*.woff", "*.ttf", "*.woff2",
    "*.pdf",
    "*.lock",
    "venv/", ".venv/",
    ".claude/worktrees/",
}

EMBEDDING_MODEL = "voyage-code-3"
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_BATCH_SIZE = 128
EMBEDDING_ENV_VAR = "VOYAGE_API_KEY"


def _is_excluded(path: str) -> bool:
    """Check if a path matches any exclusion pattern."""
    parts = Path(path).parts
    for pattern in EXCLUDE_PATTERNS:
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            if dir_name in parts:
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

    def _init_extended_schema(self):
        """Create columns/tables beyond what CodeGraph + HybridSearch provide."""
        # We need CodeGraph's schema first — create it via a temp instance
        # that uses our connection
        temp_graph = CodeGraph(conn=self.conn)
        temp_search = HybridSearch(conn=self.conn)

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
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_risk ON nodes(risk_score DESC)"
        )

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

    def close(self):
        """Close the database connection."""
        self.conn.close()

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

        # Phase 1 — Python AST
        self._graph.index_directory(str(root_path), extensions=FULL_INDEX_EXTENSIONS)

        # Phase 2 — risk scores
        self._cache_risk_scores()

        # Phase 3 — text files
        self._index_text_files(root_path)

        # Phase 4 — search index
        self._populate_search_index()

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        documents = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        logger.info(
            "index_directory complete: %d nodes, %d edges, %d documents in %dms",
            nodes, edges, documents, elapsed_ms,
        )
        return {"nodes": nodes, "edges": edges, "documents": documents, "time_ms": elapsed_ms}

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

            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")[:5000]
            except (OSError, PermissionError):
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
        """Compute and cache risk scores for all Python function/method nodes."""
        rows = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE kind IN ('function', 'method')"
        ).fetchall()

        now = time.time()
        updates: list[tuple[float, float, str]] = []
        for row in rows:
            qname = row[0]
            try:
                score = self._graph.compute_risk_score(qname)
            except Exception as exc:
                logger.debug("Risk score failed for %s: %s", qname, exc)
                score = 0.0
            updates.append((score, now, qname))

        self.conn.executemany(
            "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
            updates,
        )
        self.conn.commit()

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
        """
        client = self._get_voyage_client()
        if client is None:
            return

        try:
            import struct

            rows = self.conn.execute(
                "SELECT id, text FROM documents"
            ).fetchall()

            if not rows:
                return

            ids = [r[0] for r in rows]
            texts = [r[1] for r in rows]

            for batch_start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch_ids = ids[batch_start: batch_start + EMBEDDING_BATCH_SIZE]
                batch_texts = texts[batch_start: batch_start + EMBEDDING_BATCH_SIZE]

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
                for doc_id, vector in zip(batch_ids, vectors):
                    packed = struct.pack(f"{len(vector)}f", *vector)
                    rows_to_insert.append((doc_id, packed))

                self.conn.executemany(
                    "INSERT OR REPLACE INTO embeddings (doc_id, vector) VALUES (?, ?)",
                    rows_to_insert,
                )
                self.conn.commit()

        except Exception as exc:
            logger.warning("Voyage embedding step failed: %s", exc)
