"""Unified Code Intelligence — persistent AST graph + semantic search.

Wraps existing CodeGraph (structural analysis) and HybridSearch (FTS5 + vector)
into a single persistent SQLite database with auto-reindexing and MCP query methods.

Split into focused modules:
- _indexer.py — full repo index, incremental file reindex
- _queries.py — call graph queries, impact analysis, refactoring suggestions
- _search.py — semantic search, module summary, grep, similar functions
- _analytics.py — recent changes, dead code, complexity, dependency cycles

CodeIntelligence is the public facade — same API as before the split.
"""

import hashlib
import os
import sqlite3
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from shared.code_graph import CodeGraph
from shared.hybrid_search import HybridSearch
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────

from shared.paths import DATA_DIR as _DATA_DIR

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


# Import sub-module functions
from shared.code_intelligence._indexer import (
    index_directory as _index_directory,
    reindex_file as _reindex_file,
)
from shared.code_intelligence._queries import (
    find_symbol as _find_symbol,
    callers_of as _callers_of,
    callees_of as _callees_of,
    impact_analysis as _impact_analysis,
    diff_impact as _diff_impact,
    test_coverage_map as _test_coverage_map,
    call_path as _call_path,
    batch_find as _batch_find,
    boundary_check as _boundary_check,
    suggest_extract as _suggest_extract,
    rename_preview as _rename_preview,
    risk_report as _risk_report,
)
from shared.code_intelligence._search import (
    semantic_search as _semantic_search,
    module_summary as _module_summary,
    similar_functions as _similar_functions,
    grep_search as _grep_search,
)
from shared.code_intelligence._analytics import (
    recent_changes as _recent_changes,
    get_primer as _get_primer,
    dead_code_report as _dead_code_report,
    complexity_hotspots as _complexity_hotspots,
    dependency_cycles as _dependency_cycles,
)


class CodeIntelligence:
    """Unified code intelligence — structural graph + semantic search.

    Wraps CodeGraph + HybridSearch with a shared SQLite connection.
    Single DB file at db_path with WAL mode for concurrent reads.
    """

    def __init__(self, db_path: str = str(_DATA_DIR / "code_intelligence.db"),
                 graph_only: bool = False):
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

        # graph_only mode: skip embedding load for fast structural queries
        if not graph_only:
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

    # ─── DELEGATED METHODS ────────────────────────────────────────

    # Indexing
    def index_directory(self, root: str) -> dict[str, Any]:
        return _index_directory(self, root)

    def reindex_file(self, rel_path: str, root: str | None = None) -> dict[str, Any]:
        return _reindex_file(self, rel_path, root)

    # Call graph queries
    def find_symbol(self, name: str) -> dict[str, Any] | None:
        return _find_symbol(self, name)

    def callers_of(self, name: str, max_results: int = 20) -> dict[str, Any]:
        return _callers_of(self, name, max_results)

    def callees_of(self, name: str, max_results: int = 20) -> dict[str, Any]:
        return _callees_of(self, name, max_results)

    def impact_analysis(self, files: list[str], max_depth: int = 2,
                         max_results: int = 100) -> dict[str, Any]:
        return _impact_analysis(self, files, max_depth, max_results)

    def diff_impact(self, diff_text: str = "", *, ref: str | None = None,
                    root: str | None = None, max_depth: int = 2,
                    max_results: int = 100) -> dict[str, Any]:
        return _diff_impact(self, diff_text, ref=ref, root=root,
                           max_depth=max_depth, max_results=max_results)

    def test_coverage_map(self, file: str | None = None, top_n: int = 50) -> dict[str, Any]:
        return _test_coverage_map(self, file, top_n)

    def call_path(self, source: str, target: str, max_depth: int = 6) -> dict[str, Any]:
        return _call_path(self, source, target, max_depth)

    def batch_find(self, names: list[str] | None = None, *, pattern: str | None = None,
                   max_results: int = 50) -> dict[str, Any]:
        return _batch_find(self, names, pattern=pattern, max_results=max_results)

    def boundary_check(self, rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return _boundary_check(self, rules)

    def suggest_extract(self, file: str | None = None, min_lines: int = 50,
                        top_n: int = 20) -> dict[str, Any]:
        return _suggest_extract(self, file, min_lines, top_n)

    def rename_preview(self, symbol: str, new_name: str) -> dict[str, Any]:
        return _rename_preview(self, symbol, new_name)

    def risk_report(self, top_n: int = 10, file: str | None = None) -> dict[str, Any]:
        return _risk_report(self, top_n, file)

    # Search & discovery
    def semantic_search(self, query: str, top_k: int = 10,
                        context_symbol: str | None = None,
                        search_context: str = "general") -> list[dict[str, Any]]:
        return _semantic_search(self, query, top_k, context_symbol, search_context)

    def module_summary(self, file: str) -> dict[str, Any]:
        return _module_summary(self, file)

    def similar_functions(self, name: str, top_k: int = 5) -> list[dict[str, Any]]:
        return _similar_functions(self, name, top_k)

    def grep_search(self, pattern: str, *, glob: str | None = None,
                    max_results: int = 50, context_lines: int = 0,
                    fixed_string: bool = False, sort_by: str = "risk") -> dict[str, Any]:
        return _grep_search(self, pattern, glob=glob, max_results=max_results,
                           context_lines=context_lines, fixed_string=fixed_string,
                           sort_by=sort_by)

    # Analytics & reporting
    def recent_changes(self, n_commits: int = 3, root: str | None = None) -> dict[str, Any]:
        return _recent_changes(self, n_commits, root)

    def get_primer(self, top_risk: int = 5, n_commits: int = 3) -> str:
        return _get_primer(self, top_risk, n_commits)

    def dead_code_report(self, top_n: int = 20, file: str | None = None) -> dict[str, Any]:
        return _dead_code_report(self, top_n, file)

    def complexity_hotspots(self, top_n: int = 15) -> list[dict[str, Any]]:
        return _complexity_hotspots(self, top_n)

    def dependency_cycles(self, max_depth: int = 4) -> list[dict[str, Any]]:
        return _dependency_cycles(self, max_depth)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
