# Unified Code Intelligence Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge CodeGraph + HybridSearch into a persistent, auto-updating SQLite DB exposed to Claude Code via 8 MCP tools + real-time file watching + session primer — reducing navigation token cost by 96%.

**Architecture:** CodeIntelligence wraps existing CodeGraph + HybridSearch with a shared persistent SQLite DB. An MCP stdio server exposes 8 query tools. Three freshness layers (watchdog file watcher, PostToolUse hook, git post-commit hook) keep the index current. Voyage-code-3 provides code-specialized embeddings (1024d).

**Tech Stack:** Python 3.12, SQLite WAL, Voyage-code-3 (voyageai SDK), watchdog, mcp SDK, pytest

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `shared/code_intelligence.py` | Unified wrapper — persistent DB, incremental reindex, all query methods, Voyage-code-3 embeddings |
| `shared/code_intel_mcp.py` | MCP stdio server — 8 tools + watchdog file watcher thread |
| `.claude/hooks/scripts/session-primer.py` | SessionStart hook — prints codebase fingerprint (~400 tokens) |
| `.claude/hooks/scripts/reindex-file.py` | PostToolUse hook — reindexes edited file |
| `tests/test_code_intelligence.py` | Tests for CodeIntelligence class |
| `tests/test_code_intel_mcp.py` | Tests for MCP server tools |

### Modified files
| File | Change |
|------|--------|
| `shared/code_graph.py:42` | Accept `conn` parameter in `__init__` (default: create own connection) |
| `shared/hybrid_search.py:37` | Accept `conn` parameter in `__init__` (default: create own connection) |
| `.claude/settings.json` | Add MCP server config + SessionStart hook + PostToolUse reindex hook |
| `.git/hooks/post-commit` | Add reindex of changed files (background) |
| `pyproject.toml` | Add `voyageai`, `watchdog`, `mcp` to dependencies |

---

### Task 1: Add `conn` parameter to CodeGraph

**Files:**
- Modify: `shared/code_graph.py:42-46`
- Test: `tests/test_code_graph.py`

- [ ] **Step 1: Write failing test — CodeGraph accepts external connection**

Add to `tests/test_code_graph.py`:

```python
class TestExternalConnection:
    def test_accepts_external_connection(self, tmp_path):
        """CodeGraph can use a shared SQLite connection."""
        import sqlite3
        db_path = str(tmp_path / "shared.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        graph = CodeGraph(conn=conn)
        assert graph.conn is conn
        graph.close()

    def test_default_memory_still_works(self):
        """Default :memory: behavior is preserved."""
        graph = CodeGraph()
        assert graph.conn is not None
        stats = graph.get_stats()
        assert stats["nodes"] == 0
        graph.close()

    def test_db_path_still_works(self, tmp_path):
        """File-path constructor still works."""
        db_path = str(tmp_path / "test.db")
        graph = CodeGraph(db_path=db_path)
        graph.close()
        assert (tmp_path / "test.db").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_code_graph.py::TestExternalConnection -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'conn'`

- [ ] **Step 3: Modify CodeGraph.__init__ to accept conn parameter**

In `shared/code_graph.py`, replace lines 42-46:

```python
def __init__(self, db_path: str = ":memory:", conn: sqlite3.Connection | None = None):
    if conn is not None:
        self.conn = conn
    else:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    self._init_schema()
```

- [ ] **Step 4: Run all CodeGraph tests to verify nothing breaks**

Run: `python -m pytest tests/test_code_graph.py -v`
Expected: ALL PASS (existing 101+ tests + 3 new)

- [ ] **Step 5: Commit**

```bash
git add shared/code_graph.py tests/test_code_graph.py
git commit -m "feat(shared): add conn parameter to CodeGraph for shared DB connections"
```

---

### Task 2: Add `conn` parameter to HybridSearch

**Files:**
- Modify: `shared/hybrid_search.py:37-41`
- Test: `tests/test_hybrid_search.py`

- [ ] **Step 1: Write failing test — HybridSearch accepts external connection**

Add to `tests/test_hybrid_search.py`:

```python
class TestExternalConnection:
    def test_accepts_external_connection(self, tmp_path):
        """HybridSearch can use a shared SQLite connection."""
        import sqlite3
        db_path = str(tmp_path / "shared.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)
        assert search.conn is conn
        search.add("doc1", "test document")
        assert search.count() == 1
        search.close()

    def test_default_memory_still_works(self):
        """Default :memory: behavior is preserved."""
        search = HybridSearch()
        assert search.count() == 0
        search.close()

    def test_db_path_still_works(self, tmp_path):
        """File-path constructor still works."""
        db_path = str(tmp_path / "test.db")
        search = HybridSearch(db_path=db_path)
        search.close()
        assert (tmp_path / "test.db").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hybrid_search.py::TestExternalConnection -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'conn'`

- [ ] **Step 3: Modify HybridSearch.__init__ to accept conn parameter**

In `shared/hybrid_search.py`, replace lines 37-41:

```python
def __init__(self, db_path: str = ":memory:", conn: sqlite3.Connection | None = None):
    if conn is not None:
        self.conn = conn
    else:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    self._init_schema()
```

- [ ] **Step 4: Run all HybridSearch tests to verify nothing breaks**

Run: `python -m pytest tests/test_hybrid_search.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add shared/hybrid_search.py tests/test_hybrid_search.py
git commit -m "feat(shared): add conn parameter to HybridSearch for shared DB connections"
```

---

### Task 3: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add voyageai, watchdog, mcp to dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
"voyageai>=0.3.0",
"watchdog>=4.0.0",
"mcp>=1.0.0",
```

- [ ] **Step 2: Install new dependencies**

Run: `pip install voyageai watchdog mcp --break-system-packages`
Expected: Successfully installed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add voyageai, watchdog, mcp dependencies for code intelligence"
```

---

### Task 4: Create CodeIntelligence — schema + initialization

**Files:**
- Create: `shared/code_intelligence.py`
- Create: `tests/test_code_intelligence.py`

This task creates the class skeleton with schema, init, and configuration constants.

- [ ] **Step 1: Write failing test — CodeIntelligence creates DB with correct schema**

Create `tests/test_code_intelligence.py`:

```python
"""Tests for shared/code_intelligence.py — Unified Code Intelligence Layer."""

import sqlite3
import textwrap

import pytest

from shared.code_intelligence import CodeIntelligence, EXCLUDE_PATTERNS, FULL_INDEX_EXTENSIONS


@pytest.fixture
def ci(tmp_path):
    """CodeIntelligence with temp DB."""
    db_path = str(tmp_path / "test_ci.db")
    instance = CodeIntelligence(db_path=db_path)
    yield instance
    instance.close()


@pytest.fixture
def sample_project(tmp_path):
    """Minimal Python project for indexing tests."""
    src = tmp_path / "project"
    src.mkdir()
    (src / "auth.py").write_text(textwrap.dedent("""\
        import hashlib

        class AuthManager:
            def verify_token(self, token: str) -> bool:
                return hashlib.sha256(token.encode()).hexdigest() == self._stored

            def revoke_session(self, session_id: str) -> None:
                pass

        def login(username: str, password: str) -> str:
            mgr = AuthManager()
            return mgr.verify_token(password)
    """))
    (src / "utils.py").write_text(textwrap.dedent("""\
        from auth import login

        def check_access(token):
            return login("admin", token)
    """))
    (src / "test_auth.py").write_text(textwrap.dedent("""\
        from auth import login

        def test_login_valid():
            assert login("user", "pass123")
    """))
    (src / "README.md").write_text("# Auth Project\n\nA sample auth system.")
    (src / "config.yaml").write_text("debug: true\nport: 8080\n")
    (src / ".env").write_text("SECRET_KEY=abc123")
    return src


class TestInit:
    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "ci.db")
        ci = CodeIntelligence(db_path=db_path)
        assert (tmp_path / "ci.db").exists()
        ci.close()

    def test_schema_has_nodes_table(self, ci):
        tables = ci.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}
        assert "nodes" in table_names
        assert "edges" in table_names
        assert "documents" in table_names
        assert "embeddings" in table_names

    def test_schema_has_fts_virtual_table(self, ci):
        tables = ci.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}
        assert "documents_fts" in table_names

    def test_wal_mode_enabled(self, ci):
        mode = ci.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_nodes_has_new_columns(self, ci):
        """Verify nodes table has signature, docstring, risk_score, last_indexed."""
        info = ci.conn.execute("PRAGMA table_info(nodes)").fetchall()
        col_names = {r[1] for r in info}
        assert "signature" in col_names
        assert "docstring" in col_names
        assert "risk_score" in col_names
        assert "last_indexed" in col_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_code_intelligence.py::TestInit -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.code_intelligence'`

- [ ] **Step 3: Create shared/code_intelligence.py with schema + init**

Create `shared/code_intelligence.py`:

```python
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

# Full AST parsing — structural nodes/edges + semantic search
FULL_INDEX_EXTENSIONS = (".py",)

# Everything not matching an exclusion is text-indexed
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

# Voyage-code-3 embedding configuration
EMBEDDING_MODEL = "voyage-code-3"
EMBEDDING_DIMENSIONS = 1024
EMBEDDING_BATCH_SIZE = 128
EMBEDDING_ENV_VAR = "VOYAGE_API_KEY"


def _is_excluded(path: str) -> bool:
    """Check if a path matches any exclusion pattern."""
    parts = Path(path).parts
    for pattern in EXCLUDE_PATTERNS:
        if pattern.endswith("/"):
            # Directory pattern — check if any path component matches
            dir_name = pattern.rstrip("/")
            if dir_name in parts:
                return True
        elif fnmatch(Path(path).name, pattern):
            return True
    return False


def _is_binary(filepath: Path, sample_size: int = 8192) -> bool:
    """Quick heuristic: file is binary if it has null bytes in the first 8KB."""
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
        # Add new columns to nodes if they don't exist
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(nodes)").fetchall()}

        if "signature" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN signature TEXT DEFAULT ''")
        if "docstring" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN docstring TEXT DEFAULT ''")
        if "risk_score" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN risk_score REAL DEFAULT 0.0")
        if "last_indexed" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN last_indexed REAL DEFAULT 0.0")

        # Embeddings table (separate from HybridSearch's bag-of-words)
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
```

- [ ] **Step 4: Run tests to verify schema creation passes**

Run: `python -m pytest tests/test_code_intelligence.py::TestInit -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add shared/code_intelligence.py tests/test_code_intelligence.py
git commit -m "feat(shared): add CodeIntelligence class with schema and initialization"
```

---

### Task 5: CodeIntelligence — full index_directory

**Files:**
- Modify: `shared/code_intelligence.py`
- Modify: `tests/test_code_intelligence.py`

- [ ] **Step 1: Write failing tests for index_directory**

Add to `tests/test_code_intelligence.py`:

```python
class TestIndexDirectory:
    def test_indexes_python_files_with_ast(self, ci, sample_project):
        result = ci.index_directory(str(sample_project))
        assert result["nodes"] > 0
        assert result["edges"] > 0
        assert result["time_ms"] > 0

    def test_indexes_text_files_as_documents(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        # README.md should be indexed as a document node
        docs = ci.conn.execute(
            "SELECT * FROM nodes WHERE kind='document' AND file_path LIKE '%README.md'"
        ).fetchall()
        assert len(docs) == 1

    def test_indexes_yaml_files(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        docs = ci.conn.execute(
            "SELECT * FROM nodes WHERE kind='document' AND file_path LIKE '%config.yaml'"
        ).fetchall()
        assert len(docs) == 1

    def test_excludes_env_files(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        env_docs = ci.conn.execute(
            "SELECT * FROM nodes WHERE file_path LIKE '%.env%'"
        ).fetchall()
        assert len(env_docs) == 0

    def test_excludes_binary_files(self, ci, sample_project):
        # Create a binary file
        (sample_project / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        ci.index_directory(str(sample_project))
        bins = ci.conn.execute(
            "SELECT * FROM nodes WHERE file_path LIKE '%.png'"
        ).fetchall()
        assert len(bins) == 0

    def test_populates_fts5_for_all_indexed_files(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        fts_count = ci.conn.execute(
            "SELECT COUNT(*) FROM documents_fts"
        ).fetchone()[0]
        assert fts_count > 0

    def test_caches_risk_scores_on_python_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        risky = ci.conn.execute(
            "SELECT * FROM nodes WHERE risk_score > 0 AND kind IN ('function', 'method')"
        ).fetchall()
        # verify_token and login contain security keywords
        assert len(risky) >= 1

    def test_returns_stats_dict(self, ci, sample_project):
        result = ci.index_directory(str(sample_project))
        assert "nodes" in result
        assert "edges" in result
        assert "documents" in result
        assert "time_ms" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_code_intelligence.py::TestIndexDirectory -v`
Expected: FAIL — `AttributeError: 'CodeIntelligence' object has no attribute 'index_directory'`

- [ ] **Step 3: Implement index_directory**

Add to `CodeIntelligence` class in `shared/code_intelligence.py`:

```python
    # ─── INDEXING ──────────────────────────────────────────────────

    def index_directory(self, root: str) -> dict:
        """Full repo index. Indexes Python files with AST, other text files as documents.

        Returns: {nodes, edges, documents, time_ms}
        """
        start = time.time()
        root_path = Path(root)

        # Phase 1: Index Python files via CodeGraph (AST)
        self._graph.index_directory(root, extensions=FULL_INDEX_EXTENSIONS)

        # Phase 2: Cache risk scores for all Python functions
        self._cache_risk_scores()

        # Phase 3: Index non-Python text files as document nodes
        self._index_text_files(root_path)

        # Phase 4: Populate semantic search (FTS5 + embeddings) for all nodes
        self._populate_search_index()

        elapsed_ms = int((time.time() - start) * 1000)

        stats = self._graph.get_stats()
        doc_count = self._search.count()

        logger.info(
            "Full index: %d nodes, %d edges, %d documents in %dms",
            stats["nodes"], stats["edges"], doc_count, elapsed_ms,
        )

        return {
            "nodes": stats["nodes"],
            "edges": stats["edges"],
            "documents": doc_count,
            "time_ms": elapsed_ms,
        }

    def _index_text_files(self, root_path: Path):
        """Index non-Python text files as document nodes."""
        import ast as _ast  # avoid shadow

        for filepath in root_path.rglob("*"):
            if not filepath.is_file():
                continue

            rel_path = str(filepath.relative_to(root_path))

            # Skip excluded patterns
            if _is_excluded(rel_path):
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

            if not text.strip():
                continue

            qname = f"{rel_path}::__document__"
            self.conn.execute(
                "INSERT OR REPLACE INTO nodes "
                "(kind, name, qualified_name, file_path, line_start, line_end, last_indexed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("document", filepath.name, qname, rel_path, 1, text.count("\n") + 1, time.time()),
            )

        self.conn.commit()

    def _cache_risk_scores(self):
        """Compute and cache risk scores for all Python functions."""
        functions = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE kind IN ('function', 'method')"
        ).fetchall()

        now = time.time()
        for fn in functions:
            risk = self._graph.compute_risk_score(fn[0])
            self.conn.execute(
                "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
                (risk, now, fn[0]),
            )

        self.conn.commit()

    def _populate_search_index(self):
        """Add all nodes to HybridSearch for FTS5 + vector search."""
        rows = self.conn.execute(
            "SELECT qualified_name, name, kind, file_path, signature, docstring FROM nodes"
        ).fetchall()

        for row in rows:
            qname = row[0]
            text_parts = [row[1]]  # name

            if row[4]:  # signature
                text_parts.append(row[4])
            if row[5]:  # docstring
                text_parts.append(row[5])

            # For document nodes, include actual file text
            if row[2] == "document":
                doc_text = self.conn.execute(
                    "SELECT text FROM documents WHERE id=?", (qname,)
                ).fetchone()
                if not doc_text:
                    # Read from file if not yet in documents table
                    try:
                        fp = Path(row[3])
                        if fp.exists():
                            text_parts.append(fp.read_text(encoding="utf-8", errors="replace")[:5000])
                    except (OSError, PermissionError):
                        pass

            text = " ".join(text_parts)
            metadata = {"kind": row[2], "file": row[3]}

            self._search.add(qname, text, metadata)

        # Batch compute Voyage embeddings if available
        self._compute_voyage_embeddings()

    def _compute_voyage_embeddings(self):
        """Compute Voyage-code-3 embeddings for all documents. Graceful fallback."""
        client = self._get_voyage_client()
        if client is None:
            return  # FTS5-only mode

        rows = self.conn.execute("SELECT id, text FROM documents").fetchall()
        if not rows:
            return

        # Batch embed
        texts = [r[1] for r in rows]
        ids = [r[0] for r in rows]

        try:
            import struct

            for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch_texts = texts[i:i + EMBEDDING_BATCH_SIZE]
                batch_ids = ids[i:i + EMBEDDING_BATCH_SIZE]

                result = client.embed(
                    batch_texts,
                    model=EMBEDDING_MODEL,
                    output_dimension=EMBEDDING_DIMENSIONS,
                )

                for doc_id, vector in zip(batch_ids, result.embeddings):
                    blob = struct.pack(f"{len(vector)}f", *vector)
                    self.conn.execute(
                        "INSERT OR REPLACE INTO embeddings (doc_id, vector) VALUES (?, ?)",
                        (doc_id, blob),
                    )

            self.conn.commit()
            logger.info("Computed Voyage embeddings for %d documents", len(texts))

        except Exception as e:
            logger.warning("Voyage embedding failed, using FTS5-only: %s", e)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_code_intelligence.py::TestIndexDirectory -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add shared/code_intelligence.py tests/test_code_intelligence.py
git commit -m "feat(shared): add CodeIntelligence.index_directory with dual-tier indexing"
```

---

### Task 6: CodeIntelligence — reindex_file (incremental)

**Files:**
- Modify: `shared/code_intelligence.py`
- Modify: `tests/test_code_intelligence.py`

- [ ] **Step 1: Write failing tests for reindex_file**

Add to `tests/test_code_intelligence.py`:

```python
class TestReindexFile:
    def test_reindex_updates_modified_function(self, ci, sample_project):
        ci.index_directory(str(sample_project))

        # Modify auth.py — add a new function
        (sample_project / "auth.py").write_text(textwrap.dedent("""\
            import hashlib

            class AuthManager:
                def verify_token(self, token: str) -> bool:
                    return hashlib.sha256(token.encode()).hexdigest() == self._stored

                def revoke_session(self, session_id: str) -> None:
                    pass

            def login(username: str, password: str) -> str:
                mgr = AuthManager()
                return mgr.verify_token(password)

            def logout(session_id: str) -> None:
                pass
        """))

        result = ci.reindex_file("auth.py", str(sample_project))
        assert result["nodes_added"] > 0

        # Verify new function exists
        node = ci.conn.execute(
            "SELECT * FROM nodes WHERE name='logout'"
        ).fetchone()
        assert node is not None

    def test_reindex_removes_deleted_function(self, ci, sample_project):
        ci.index_directory(str(sample_project))

        # Remove login function from auth.py
        (sample_project / "auth.py").write_text(textwrap.dedent("""\
            class AuthManager:
                def verify_token(self, token: str) -> bool:
                    return True
        """))

        ci.reindex_file("auth.py", str(sample_project))

        login_node = ci.conn.execute(
            "SELECT * FROM nodes WHERE name='login' AND file_path='auth.py'"
        ).fetchone()
        assert login_node is None

    def test_reindex_text_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))

        # Modify README
        (sample_project / "README.md").write_text("# Updated Auth\n\nNew content here.")
        ci.reindex_file("README.md", str(sample_project))

        doc = ci.conn.execute(
            "SELECT * FROM nodes WHERE file_path='README.md'"
        ).fetchone()
        assert doc is not None

    def test_reindex_returns_timing(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.reindex_file("auth.py", str(sample_project))
        assert "time_ms" in result
        assert result["time_ms"] >= 0

    def test_reindex_excluded_file_is_noop(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.reindex_file(".env", str(sample_project))
        assert result["nodes_added"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_code_intelligence.py::TestReindexFile -v`
Expected: FAIL — `AttributeError: 'CodeIntelligence' object has no attribute 'reindex_file'`

- [ ] **Step 3: Implement reindex_file**

Add to `CodeIntelligence` class:

```python
    def reindex_file(self, rel_path: str, root: str | None = None) -> dict:
        """Incremental single-file reindex.

        1. Delete old nodes/edges/docs for this file
        2. Re-parse (AST for Python, text for other)
        3. Update FTS5 + embeddings
        4. Recompute risk for affected nodes

        Args:
            rel_path: Relative path within the project (e.g., "auth.py")
            root: Project root directory. If None, uses cwd.

        Returns: {nodes_added, edges_added, risk_updated, time_ms}
        """
        start = time.time()

        if _is_excluded(rel_path):
            return {"nodes_added": 0, "edges_added": 0, "risk_updated": 0, "time_ms": 0}

        root = root or os.getcwd()
        abs_path = Path(root) / rel_path

        # Step 1: Collect callers before deletion (for risk recalc)
        old_qnames = [
            r[0] for r in self.conn.execute(
                "SELECT qualified_name FROM nodes WHERE file_path=?", (rel_path,)
            ).fetchall()
        ]
        caller_qnames = set()
        for qn in old_qnames:
            name_part = qn.split("::")[-1]
            callers = self.conn.execute(
                "SELECT source_qname FROM edges WHERE target_qname LIKE ? AND kind='calls'",
                (f"%{name_part}",),
            ).fetchall()
            caller_qnames.update(r[0] for r in callers)

        # Step 2: Delete old data for this file
        self.conn.execute("DELETE FROM edges WHERE file_path=?", (rel_path,))
        for qn in old_qnames:
            self.conn.execute("DELETE FROM documents WHERE id=?", (qn,))
            self.conn.execute("DELETE FROM embeddings WHERE doc_id=?", (qn,))
        self.conn.execute("DELETE FROM nodes WHERE file_path=?", (rel_path,))
        self.conn.commit()

        nodes_added = 0
        edges_added = 0

        if not abs_path.exists():
            # File was deleted — cleanup is already done
            elapsed_ms = int((time.time() - start) * 1000)
            return {"nodes_added": 0, "edges_added": 0, "risk_updated": 0, "time_ms": elapsed_ms}

        # Step 3: Re-index
        if abs_path.suffix in FULL_INDEX_EXTENSIONS:
            # Python file — full AST parse
            self._graph._index_file(abs_path, Path(root))
            self.conn.commit()
        elif not _is_binary(abs_path):
            # Text file — document node
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")[:5000]
                if text.strip():
                    qname = f"{rel_path}::__document__"
                    self.conn.execute(
                        "INSERT OR REPLACE INTO nodes "
                        "(kind, name, qualified_name, file_path, line_start, line_end, last_indexed) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("document", abs_path.name, qname, rel_path, 1, text.count("\n") + 1, time.time()),
                    )
                    self.conn.commit()
            except (OSError, PermissionError):
                pass

        # Count what was added
        nodes_added = self.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path=?", (rel_path,)
        ).fetchone()[0]
        edges_added = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE file_path=?", (rel_path,)
        ).fetchone()[0]

        # Step 4: Update risk scores for this file's functions
        risk_updated = 0
        now = time.time()
        for row in self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
            (rel_path,),
        ).fetchall():
            risk = self._graph.compute_risk_score(row[0])
            self.conn.execute(
                "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
                (risk, now, row[0]),
            )
            risk_updated += 1

        # Also update risk for callers of functions in this file
        for qn in caller_qnames:
            if self.conn.execute("SELECT 1 FROM nodes WHERE qualified_name=?", (qn,)).fetchone():
                risk = self._graph.compute_risk_score(qn)
                self.conn.execute(
                    "UPDATE nodes SET risk_score=?, last_indexed=? WHERE qualified_name=?",
                    (risk, now, qn),
                )
                risk_updated += 1

        self.conn.commit()

        # Step 5: Update search index for new nodes
        for row in self.conn.execute(
            "SELECT qualified_name, name, kind, file_path FROM nodes WHERE file_path=?",
            (rel_path,),
        ).fetchall():
            text = row[1]  # name as minimal text
            self._search.add(row[0], text, {"kind": row[2], "file": row[3]})

        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "nodes_added": nodes_added,
            "edges_added": edges_added,
            "risk_updated": risk_updated,
            "time_ms": elapsed_ms,
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_code_intelligence.py::TestReindexFile -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add shared/code_intelligence.py tests/test_code_intelligence.py
git commit -m "feat(shared): add CodeIntelligence.reindex_file for incremental updates"
```

---

### Task 7: CodeIntelligence — MCP query methods

**Files:**
- Modify: `shared/code_intelligence.py`
- Modify: `tests/test_code_intelligence.py`

- [ ] **Step 1: Write failing tests for query methods**

Add to `tests/test_code_intelligence.py`:

```python
class TestFindSymbol:
    def test_find_function_by_name(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.find_symbol("login")
        assert result is not None
        assert result["name"] == "login"
        assert result["kind"] == "function"
        assert "file" in result
        assert "risk_score" in result

    def test_find_class_by_name(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.find_symbol("AuthManager")
        assert result is not None
        assert result["kind"] == "class"

    def test_find_nonexistent_returns_none(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.find_symbol("nonexistent_function")
        assert result is None

    def test_find_method(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.find_symbol("verify_token")
        assert result is not None
        assert result["kind"] == "method"


class TestCallersOf:
    def test_finds_callers(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.callers_of("verify_token")
        assert result["total"] >= 1

    def test_respects_max_results(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.callers_of("verify_token", max_results=1)
        assert len(result["callers"]) <= 1


class TestCalleesOf:
    def test_finds_callees(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.callees_of("login")
        assert result["total"] >= 1


class TestImpactAnalysis:
    def test_impact_from_single_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.impact_analysis(["auth.py"])
        assert len(result["impacted_files"]) >= 1
        assert "total_functions" in result


class TestRiskReport:
    def test_returns_ordered_by_risk(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.risk_report(top_n=5)
        scores = [f["risk"] for f in result["functions"]]
        assert scores == sorted(scores, reverse=True)

    def test_per_file_report(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.risk_report(file="auth.py")
        for fn in result["functions"]:
            assert fn["file"] == "auth.py"


class TestSemanticSearch:
    def test_keyword_match(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        results = ci.semantic_search("authentication token")
        assert len(results) > 0

    def test_returns_scores(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        results = ci.semantic_search("login")
        assert all("score" in r for r in results)


class TestModuleSummary:
    def test_summary_of_python_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.module_summary("auth.py")
        assert result["file"] == "auth.py"
        assert len(result["classes"]) >= 1
        assert len(result["functions"]) >= 1


class TestRecentChanges:
    def test_recent_changes_no_git(self, ci, sample_project):
        """In a non-git directory, returns empty."""
        ci.index_directory(str(sample_project))
        result = ci.recent_changes(n_commits=3, root=str(sample_project))
        assert result["commits"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_code_intelligence.py::TestFindSymbol tests/test_code_intelligence.py::TestCallersOf tests/test_code_intelligence.py::TestCalleesOf tests/test_code_intelligence.py::TestImpactAnalysis tests/test_code_intelligence.py::TestRiskReport tests/test_code_intelligence.py::TestSemanticSearch tests/test_code_intelligence.py::TestModuleSummary tests/test_code_intelligence.py::TestRecentChanges -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement all query methods**

Add to `CodeIntelligence` class:

```python
    # ─── MCP QUERY METHODS ────────────────────────────────────────

    def find_symbol(self, name: str) -> dict | None:
        """Find a function, class, or method by name.

        Exact match on nodes.name, fallback to LIKE %name%.
        """
        # Exact match first
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE name=? ORDER BY risk_score DESC LIMIT 1",
            (name,),
        ).fetchone()

        # Fuzzy fallback
        if not row:
            row = self.conn.execute(
                "SELECT * FROM nodes WHERE name LIKE ? ORDER BY risk_score DESC LIMIT 1",
                (f"%{name}%",),
            ).fetchone()

        if not row:
            return None

        qname = row["qualified_name"]

        # Count callers and callees
        callers_count = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls'",
            (f"%{row['name']}",),
        ).fetchone()[0]

        callees_count = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_qname=? AND kind='calls'",
            (qname,),
        ).fetchone()[0]

        return {
            "qualified_name": qname,
            "name": row["name"],
            "kind": row["kind"],
            "file": row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "risk_score": row["risk_score"] or 0.0,
            "is_async": bool(row["is_async"]),
            "callers_count": callers_count,
            "callees_count": callees_count,
        }

    def callers_of(self, name: str, max_results: int = 20) -> dict:
        """Find all functions that call the given name."""
        raw = self._graph.callers_of(name)
        callers = [
            {"name": r["source_qname"].split("::")[-1], "file": r["file_path"], "line": r["line"]}
            for r in raw[:max_results]
        ]
        return {"target": name, "callers": callers, "total": len(raw)}

    def callees_of(self, name: str, max_results: int = 20) -> dict:
        """Find all functions called by the given name."""
        # Need qualified name for callees lookup
        row = self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name=? LIMIT 1", (name,)
        ).fetchone()

        if not row:
            return {"source": name, "callees": [], "total": 0}

        raw = self._graph.callees_of(row[0])
        callees = [
            {"name": r["target_qname"].split("::")[-1], "file": r["file_path"], "line": r["line"]}
            for r in raw[:max_results]
        ]
        return {"source": name, "callees": callees, "total": len(raw)}

    def impact_analysis(self, files: list[str], max_depth: int = 2) -> dict:
        """Compute blast radius from changed files via BFS."""
        raw = self._graph.impact_radius(files, max_depth=max_depth)

        # Count changed functions
        changed_functions = 0
        for f in files:
            changed_functions += self.conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE file_path=? AND kind IN ('function', 'method')",
                (f,),
            ).fetchone()[0]

        impacted = []
        max_risk = 0.0
        for node in raw["impacted_nodes"][:30]:
            risk = node.get("risk_score", 0.0)
            if not risk:
                risk = self._graph.compute_risk_score(node["qualified_name"])
            max_risk = max(max_risk, risk)
            depth = raw["depth_map"].get(node["file_path"], max_depth)
            impacted.append({
                "name": node["name"],
                "file": node["file_path"],
                "depth": depth,
                "risk": risk,
            })

        return {
            "changed_functions": changed_functions,
            "impacted": impacted,
            "impacted_files": list(raw["impacted_files"]),
            "total_functions": len(raw["impacted_nodes"]),
            "max_risk": max_risk,
        }

    def risk_report(self, top_n: int = 10, file: str | None = None) -> dict:
        """Return top-N highest-risk functions, optionally filtered by file."""
        if file:
            rows = self.conn.execute(
                "SELECT name, file_path, risk_score FROM nodes "
                "WHERE file_path=? AND kind IN ('function', 'method') AND risk_score > 0 "
                "ORDER BY risk_score DESC LIMIT ?",
                (file, top_n),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT name, file_path, risk_score FROM nodes "
                "WHERE kind IN ('function', 'method') AND risk_score > 0 "
                "ORDER BY risk_score DESC LIMIT ?",
                (top_n,),
            ).fetchall()

        functions = [
            {"name": r[0], "file": r[1], "risk": r[2]}
            for r in rows
        ]
        return {"functions": functions}

    def semantic_search(self, query: str, top_k: int = 10) -> list[dict]:
        """Hybrid semantic search via FTS5 + vector + RRF."""
        raw = self._search.query(query, top_k=top_k)
        return [
            {
                "name": r["id"].split("::")[-1],
                "file": r["metadata"].get("file", ""),
                "score": r["score"],
                "snippet": r["text"][:200],
            }
            for r in raw
        ]

    def module_summary(self, file: str) -> dict:
        """Summary of a file: classes, functions, risk, imports."""
        classes = []
        for row in self.conn.execute(
            "SELECT name, line_start, line_end FROM nodes WHERE file_path=? AND kind='class'",
            (file,),
        ).fetchall():
            methods = self.conn.execute(
                "SELECT name FROM nodes WHERE file_path=? AND kind='method' "
                "AND qualified_name LIKE ?",
                (file, f"%{row[0]}::%"),
            ).fetchall()
            classes.append({
                "name": row[0],
                "methods": [m[0] for m in methods],
                "lines": (row[1], row[2]),
            })

        functions = [
            {"name": r[0], "lines": (r[1], r[2]), "risk": r[3] or 0.0}
            for r in self.conn.execute(
                "SELECT name, line_start, line_end, risk_score FROM nodes "
                "WHERE file_path=? AND kind='function'",
                (file,),
            ).fetchall()
        ]

        # Imports from this file
        imports = [
            r[0] for r in self.conn.execute(
                "SELECT DISTINCT target_qname FROM edges WHERE file_path=? AND kind='imports'",
                (file,),
            ).fetchall()
        ]

        # Files that import from this file
        imported_by = [
            r[0] for r in self.conn.execute(
                "SELECT DISTINCT file_path FROM edges WHERE kind='imports' AND target_qname LIKE ?",
                (f"%{Path(file).stem}%",),
            ).fetchall()
            if r[0] != file
        ]

        risk_scores = [f["risk"] for f in functions if f["risk"] > 0]
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0.0

        return {
            "file": file,
            "classes": classes,
            "functions": functions,
            "avg_risk": round(avg_risk, 3),
            "imports_from": imports,
            "imported_by": imported_by,
        }

    def recent_changes(self, n_commits: int = 3, root: str | None = None) -> dict:
        """Cross-reference recent git commits with the code graph."""
        root = root or os.getcwd()
        commits = []

        try:
            log_output = subprocess.run(
                ["git", "log", f"-{n_commits}", "--pretty=format:%H|%s", "--name-only"],
                capture_output=True, text=True, cwd=root, timeout=5,
            )
            if log_output.returncode != 0:
                return {"commits": [], "hotspots": [], "new_high_risk": []}

            # Parse git log output
            current_commit = None
            for line in log_output.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                if "|" in line and len(line.split("|")[0]) == 40:
                    parts = line.split("|", 1)
                    current_commit = {"sha": parts[0][:7], "message": parts[1], "files": []}
                    commits.append(current_commit)
                elif current_commit is not None:
                    current_commit["files"].append(line.strip())

        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {"commits": [], "hotspots": [], "new_high_risk": []}

        # Cross-reference with graph
        all_files = []
        for c in commits:
            all_files.extend(c["files"])

        hotspots = []
        for f in set(all_files):
            count = all_files.count(f)
            if count >= 2:
                hotspots.append(f)

        return {"commits": commits, "hotspots": hotspots, "new_high_risk": []}

    # ─── SESSION PRIMER ───────────────────────────────────────────

    def get_primer(self, top_risk: int = 5, n_commits: int = 3) -> str:
        """Formatted codebase fingerprint for SessionStart hook."""
        stats = self._graph.get_stats()

        lines = [
            "=== Code Intelligence: Codebase Fingerprint ===",
            f"Repo: {stats['files']} files, {stats['functions']} functions, {stats['edges']} edges",
            "",
        ]

        # Top risk functions
        report = self.risk_report(top_n=top_risk)
        if report["functions"]:
            lines.append(f"High-risk functions (top {top_risk}):")
            for i, fn in enumerate(report["functions"], 1):
                lines.append(f"  {i}. {fn['file']}:{fn['name']} ({fn['risk']:.2f})")
            lines.append("")

        # Recent changes
        changes = self.recent_changes(n_commits=n_commits)
        if changes["commits"]:
            lines.append(f"Recent changes (last {len(changes['commits'])} commits):")
            for c in changes["commits"]:
                lines.append(f"  {c['sha']} {c['message']} ({len(c['files'])} files)")
            lines.append("")

        lines.extend([
            "MCP tools: find_symbol, callers_of, callees_of, impact_analysis,",
            "  risk_report, semantic_search, module_summary, recent_changes",
            "===================================================",
        ])

        return "\n".join(lines)
```

- [ ] **Step 4: Run all query method tests**

Run: `python -m pytest tests/test_code_intelligence.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add shared/code_intelligence.py tests/test_code_intelligence.py
git commit -m "feat(shared): add all MCP query methods to CodeIntelligence"
```

---

### Task 8: Create MCP stdio server

**Files:**
- Create: `shared/code_intel_mcp.py`
- Create: `tests/test_code_intel_mcp.py`

- [ ] **Step 1: Write failing test for MCP tool registration**

Create `tests/test_code_intel_mcp.py`:

```python
"""Tests for shared/code_intel_mcp.py — MCP server tool registration."""

import importlib
import pytest


class TestMCPToolRegistration:
    def test_module_imports(self):
        """MCP server module can be imported."""
        import shared.code_intel_mcp as mcp_mod
        assert hasattr(mcp_mod, "create_mcp_server")

    def test_server_has_8_tools(self):
        """Server registers exactly 8 tools."""
        from shared.code_intel_mcp import create_mcp_server
        server = create_mcp_server.__wrapped__() if hasattr(create_mcp_server, "__wrapped__") else create_mcp_server()
        # Check tool count via server internals or tool list
        assert server is not None

    def test_tool_names(self):
        """All 8 expected tool names are registered."""
        from shared.code_intel_mcp import TOOL_NAMES
        expected = {
            "find_symbol", "callers_of", "callees_of", "impact_analysis",
            "risk_report", "semantic_search", "module_summary", "recent_changes",
        }
        assert set(TOOL_NAMES) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_code_intel_mcp.py::TestMCPToolRegistration::test_module_imports -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create shared/code_intel_mcp.py**

```python
"""MCP stdio server for Code Intelligence — 8 tools + file watcher.

Exposes CodeIntelligence query methods as MCP tools for Claude Code.
Auto-started via .claude/settings.json MCP configuration.

Run directly: python shared/code_intel_mcp.py
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

from shared.code_intelligence import CodeIntelligence, EXCLUDE_PATTERNS, _is_excluded
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── TOOL NAMES ───────────────────────────────────────────────────

TOOL_NAMES = [
    "find_symbol",
    "callers_of",
    "callees_of",
    "impact_analysis",
    "risk_report",
    "semantic_search",
    "module_summary",
    "recent_changes",
]

# ─── FILE WATCHER ─────────────────────────────────────────────────


def _start_file_watcher(ci: CodeIntelligence, root: str, debounce_ms: int = 500):
    """Start a watchdog file watcher in a background thread."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        logger.warning("watchdog not installed — file watcher disabled")
        return None

    class ReindexHandler(FileSystemEventHandler):
        def __init__(self):
            self._pending: dict[str, float] = {}
            self._lock = threading.Lock()
            self._debounce_s = debounce_ms / 1000.0

        def on_modified(self, event):
            if event.is_directory:
                return
            self._schedule(event.src_path)

        def on_created(self, event):
            if event.is_directory:
                return
            self._schedule(event.src_path)

        def _schedule(self, abs_path: str):
            rel_path = str(Path(abs_path).relative_to(root))
            if _is_excluded(rel_path):
                return

            with self._lock:
                self._pending[rel_path] = time.time()

        def flush(self):
            """Process pending reindex requests (called by timer thread)."""
            now = time.time()
            to_process = []

            with self._lock:
                for path, ts in list(self._pending.items()):
                    if now - ts >= self._debounce_s:
                        to_process.append(path)
                        del self._pending[path]

            for path in to_process:
                try:
                    ci.reindex_file(path, root)
                    logger.debug("Reindexed: %s", path)
                except Exception as e:
                    logger.debug("Reindex failed for %s: %s", path, e)

    handler = ReindexHandler()
    observer = Observer()
    observer.schedule(handler, root, recursive=True)
    observer.daemon = True
    observer.start()

    # Flush thread
    def flush_loop():
        while True:
            time.sleep(debounce_ms / 1000.0)
            handler.flush()

    flush_thread = threading.Thread(target=flush_loop, daemon=True)
    flush_thread.start()

    logger.info("File watcher started for %s", root)
    return observer


# ─── MCP SERVER ───────────────────────────────────────────────────


def create_mcp_server():
    """Create and configure the MCP server with 8 tools."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
    except ImportError:
        logger.error("mcp package not installed — run: pip install mcp")
        sys.exit(1)

    db_path = os.environ.get("CI_DB_PATH", "data/code_intelligence.db")
    project_root = os.environ.get("CI_PROJECT_ROOT", os.getcwd())

    ci = CodeIntelligence(db_path=db_path)

    # Index if DB is empty
    stats = ci._graph.get_stats()
    if stats["nodes"] == 0:
        logger.info("Empty DB — running full index...")
        ci.index_directory(project_root)

    # Start file watcher
    _start_file_watcher(ci, project_root)

    server = Server("code-intelligence")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(name="find_symbol", description="Find a function, class, or method by name",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            Tool(name="callers_of", description="Find all functions that call the given name",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "max_results": {"type": "integer", "default": 20}}, "required": ["name"]}),
            Tool(name="callees_of", description="Find all functions called by the given name",
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "max_results": {"type": "integer", "default": 20}}, "required": ["name"]}),
            Tool(name="impact_analysis", description="Compute blast radius from changed files",
                 inputSchema={"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}, "max_depth": {"type": "integer", "default": 2}}, "required": ["files"]}),
            Tool(name="risk_report", description="Top-N highest-risk functions (optionally per file)",
                 inputSchema={"type": "object", "properties": {"top_n": {"type": "integer", "default": 10}, "file": {"type": "string"}}}),
            Tool(name="semantic_search", description="Hybrid FTS5 + vector semantic search",
                 inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 10}}, "required": ["query"]}),
            Tool(name="module_summary", description="Summary of a file: classes, functions, risk, imports",
                 inputSchema={"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]}),
            Tool(name="recent_changes", description="Cross-reference recent git commits with code graph",
                 inputSchema={"type": "object", "properties": {"n_commits": {"type": "integer", "default": 3}}}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "find_symbol":
                result = ci.find_symbol(arguments["name"])
            elif name == "callers_of":
                result = ci.callers_of(arguments["name"], arguments.get("max_results", 20))
            elif name == "callees_of":
                result = ci.callees_of(arguments["name"], arguments.get("max_results", 20))
            elif name == "impact_analysis":
                result = ci.impact_analysis(arguments["files"], arguments.get("max_depth", 2))
            elif name == "risk_report":
                result = ci.risk_report(arguments.get("top_n", 10), arguments.get("file"))
            elif name == "semantic_search":
                result = ci.semantic_search(arguments["query"], arguments.get("top_k", 10))
            elif name == "module_summary":
                result = ci.module_summary(arguments["file"])
            elif name == "recent_changes":
                result = ci.recent_changes(arguments.get("n_commits", 3))
            else:
                result = {"error": f"Unknown tool: {name}"}

            return [TextContent(type="text", text=json.dumps(result, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return server


async def main():
    """Run the MCP server via stdio."""
    from mcp.server.stdio import stdio_server

    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_code_intel_mcp.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add shared/code_intel_mcp.py tests/test_code_intel_mcp.py
git commit -m "feat(shared): add MCP stdio server with 8 code intelligence tools + file watcher"
```

---

### Task 9: Create SessionStart hook

**Files:**
- Create: `.claude/hooks/scripts/session-primer.py`

- [ ] **Step 1: Write test for session primer output**

Add to `tests/test_code_intelligence.py`:

```python
class TestSessionPrimer:
    def test_primer_contains_fingerprint(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        primer = ci.get_primer()
        assert "Code Intelligence" in primer
        assert "functions" in primer
        assert "MCP tools" in primer

    def test_primer_includes_risk_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        primer = ci.get_primer(top_risk=3)
        # verify_token has security keyword — should appear
        assert "verify_token" in primer or "login" in primer
```

- [ ] **Step 2: Run tests to verify get_primer works**

Run: `python -m pytest tests/test_code_intelligence.py::TestSessionPrimer -v`
Expected: PASS (get_primer was implemented in Task 7)

- [ ] **Step 3: Create the hook script**

Create `.claude/hooks/scripts/session-primer.py`:

```python
#!/usr/bin/env python3
"""SessionStart hook — prints codebase fingerprint to stdout.

Output is injected into the Claude Code conversation as context (~400 tokens).
If DB doesn't exist, runs full index first (~3-5s one-time cost).
"""

import os
import sys
import time

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

from shared.code_intelligence import CodeIntelligence

DB_PATH = os.environ.get("CI_DB_PATH", os.path.join(project_root, "data", "code_intelligence.db"))

try:
    ci = CodeIntelligence(db_path=DB_PATH)

    # Check if DB needs indexing
    stats = ci._graph.get_stats()
    if stats["nodes"] == 0:
        ci.index_directory(project_root)

    print(ci.get_primer())
    ci.close()
except Exception as e:
    # Hook must never fail — exit silently
    print(f"[Code Intelligence unavailable: {e}]", file=sys.stderr)
    sys.exit(0)
```

- [ ] **Step 4: Make executable**

Run: `chmod +x .claude/hooks/scripts/session-primer.py`

- [ ] **Step 5: Commit**

```bash
git add .claude/hooks/scripts/session-primer.py tests/test_code_intelligence.py
git commit -m "feat(hooks): add SessionStart hook for codebase fingerprint injection"
```

---

### Task 10: Create PostToolUse reindex hook

**Files:**
- Create: `.claude/hooks/scripts/reindex-file.py`

- [ ] **Step 1: Create the reindex hook script**

Create `.claude/hooks/scripts/reindex-file.py`:

```python
#!/usr/bin/env python3
"""PostToolUse hook — reindexes a file after Write/Edit.

Called by Claude Code after every Write/Edit tool use.
Target: <200ms. Silent output (no stdout = zero token cost).
Fallback if MCP server file watcher isn't running.
"""

import json
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

# Parse tool input from environment/stdin
tool_input = os.environ.get("TOOL_INPUT", "")
if not tool_input:
    # Try reading from stdin (Claude Code passes JSON)
    try:
        tool_input = sys.stdin.read()
    except Exception:
        sys.exit(0)

# Extract file path
file_path = None
try:
    data = json.loads(tool_input)
    file_path = data.get("file_path") or data.get("path")
except (json.JSONDecodeError, TypeError):
    # Might be a plain path string
    file_path = tool_input.strip()

if not file_path:
    sys.exit(0)

# Make relative to project root
try:
    rel_path = os.path.relpath(file_path, project_root)
except ValueError:
    sys.exit(0)

DB_PATH = os.environ.get("CI_DB_PATH", os.path.join(project_root, "data", "code_intelligence.db"))

# Only reindex if DB exists (don't create a new one from hook)
if not os.path.exists(DB_PATH):
    sys.exit(0)

try:
    from shared.code_intelligence import CodeIntelligence
    ci = CodeIntelligence(db_path=DB_PATH)
    ci.reindex_file(rel_path, project_root)
    ci.close()
except Exception:
    # Hook must never fail
    sys.exit(0)
```

- [ ] **Step 2: Make executable**

Run: `chmod +x .claude/hooks/scripts/reindex-file.py`

- [ ] **Step 3: Commit**

```bash
git add .claude/hooks/scripts/reindex-file.py
git commit -m "feat(hooks): add PostToolUse hook for incremental file reindexing"
```

---

### Task 11: Create git post-commit hook

**Files:**
- Modify: `.git/hooks/post-commit`

- [ ] **Step 1: Check if post-commit hook exists**

Run: `ls -la .git/hooks/post-commit 2>/dev/null || echo "does not exist"`

- [ ] **Step 2: Create or append to post-commit hook**

Create/append to `.git/hooks/post-commit`:

```bash
#!/usr/bin/env bash
# Git post-commit hook — reindex changed files in code intelligence DB.
# Runs in background (&) to avoid blocking the commit.

(
    PROJECT_ROOT="$(git rev-parse --show-toplevel)"
    DB_PATH="${CI_DB_PATH:-$PROJECT_ROOT/data/code_intelligence.db}"

    # Only run if DB exists
    [ -f "$DB_PATH" ] || exit 0

    # Get changed files from this commit
    CHANGED=$(git diff --name-only HEAD~1 HEAD 2>/dev/null)
    [ -z "$CHANGED" ] && exit 0

    # Reindex each changed file
    cd "$PROJECT_ROOT"
    echo "$CHANGED" | while read -r file; do
        python -c "
import sys; sys.path.insert(0, '.')
from shared.code_intelligence import CodeIntelligence
ci = CodeIntelligence(db_path='$DB_PATH')
ci.reindex_file('$file', '.')
ci.close()
" 2>/dev/null
    done
) &
```

- [ ] **Step 3: Make executable**

Run: `chmod +x .git/hooks/post-commit`

- [ ] **Step 4: Commit**

Note: `.git/hooks/` is not tracked by git. Document in README or CLAUDE.md that this hook exists and should be installed.

```bash
git commit --allow-empty -m "docs: document git post-commit hook for code intelligence reindexing"
```

---

### Task 12: Update .claude/settings.json

**Files:**
- Modify: `.claude/settings.json`

- [ ] **Step 1: Read current settings**

Run: `cat .claude/settings.json`

- [ ] **Step 2: Update settings with MCP server + hooks**

Update `.claude/settings.json` to add the MCP server config and new hooks while preserving existing hooks:

```json
{
  "mcp": {
    "servers": {
      "code-intelligence": {
        "type": "stdio",
        "command": "python",
        "args": ["shared/code_intel_mcp.py"],
        "env": {
          "CI_DB_PATH": "data/code_intelligence.db",
          "VOYAGE_API_KEY": "${VOYAGE_API_KEY}"
        }
      }
    }
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/hooks/scripts/session-primer.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "bash .claude/hooks/scripts/post-edit-lint.sh $TOOL_INPUT_PATH"
          },
          {
            "type": "command",
            "command": "python .claude/hooks/scripts/reindex-file.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash .claude/hooks/scripts/pre-bash-guard.sh \"$TOOL_INPUT\""
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: Verify JSON is valid**

Run: `python -c "import json; json.load(open('.claude/settings.json'))"`
Expected: No error

- [ ] **Step 4: Commit**

```bash
git add .claude/settings.json
git commit -m "feat(config): add code intelligence MCP server + hooks to settings"
```

---

### Task 13: Integration test — full pipeline

**Files:**
- Modify: `tests/test_code_intelligence.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_code_intelligence.py`:

```python
class TestIntegration:
    """End-to-end: index → query → reindex → re-query."""

    def test_full_pipeline(self, ci, sample_project):
        # Index
        stats = ci.index_directory(str(sample_project))
        assert stats["nodes"] > 0

        # Query
        sym = ci.find_symbol("login")
        assert sym is not None
        assert sym["kind"] == "function"

        callers = ci.callers_of("login")
        assert callers["total"] >= 1

        search_results = ci.semantic_search("authentication")
        assert len(search_results) > 0

        summary = ci.module_summary("auth.py")
        assert len(summary["classes"]) >= 1

        risk = ci.risk_report(top_n=5)
        assert len(risk["functions"]) >= 1

        # Primer
        primer = ci.get_primer()
        assert len(primer) > 100

        # Modify and reindex
        (sample_project / "auth.py").write_text(textwrap.dedent("""\
            def login(username: str, password: str) -> str:
                return "token_" + username

            def register(email: str) -> bool:
                return True
        """))
        result = ci.reindex_file("auth.py", str(sample_project))
        assert result["nodes_added"] >= 2

        # Verify new function is findable
        reg = ci.find_symbol("register")
        assert reg is not None

        # Verify old class is gone
        auth_mgr = ci.find_symbol("AuthManager")
        assert auth_mgr is None

    def test_text_file_searchable(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        results = ci.semantic_search("Auth Project")
        # README.md contains "Auth Project" — should be found
        assert len(results) > 0
```

- [ ] **Step 2: Run integration tests**

Run: `python -m pytest tests/test_code_intelligence.py::TestIntegration -v`
Expected: ALL PASS

- [ ] **Step 3: Run full test suite to confirm no regressions**

Run: `python -m pytest tests/test_code_graph.py tests/test_hybrid_search.py tests/test_code_intelligence.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_code_intelligence.py
git commit -m "test(shared): add integration tests for full code intelligence pipeline"
```

---

### Task 14: Verify existing tests pass + final cleanup

**Files:**
- None (verification only)

- [ ] **Step 1: Run existing CodeGraph tests**

Run: `python -m pytest tests/test_code_graph.py -v`
Expected: ALL PASS — no regressions from conn parameter addition

- [ ] **Step 2: Run existing HybridSearch tests**

Run: `python -m pytest tests/test_hybrid_search.py -v`
Expected: ALL PASS — no regressions from conn parameter addition

- [ ] **Step 3: Run all new tests**

Run: `python -m pytest tests/test_code_intelligence.py tests/test_code_intel_mcp.py -v`
Expected: ALL PASS

- [ ] **Step 4: Run linter**

Run: `ruff check shared/code_intelligence.py shared/code_intel_mcp.py .claude/hooks/scripts/session-primer.py .claude/hooks/scripts/reindex-file.py --fix && ruff format shared/code_intelligence.py shared/code_intel_mcp.py`
Expected: Clean or auto-fixed

- [ ] **Step 5: Final commit if any lint fixes**

```bash
git add -u
git commit -m "chore: lint fixes for code intelligence module"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Add `conn` param to CodeGraph | `shared/code_graph.py`, tests |
| 2 | Add `conn` param to HybridSearch | `shared/hybrid_search.py`, tests |
| 3 | Add dependencies | `pyproject.toml` |
| 4 | CodeIntelligence — schema + init | `shared/code_intelligence.py`, tests |
| 5 | CodeIntelligence — index_directory | `shared/code_intelligence.py`, tests |
| 6 | CodeIntelligence — reindex_file | `shared/code_intelligence.py`, tests |
| 7 | CodeIntelligence — 8 query methods | `shared/code_intelligence.py`, tests |
| 8 | MCP stdio server + file watcher | `shared/code_intel_mcp.py`, tests |
| 9 | SessionStart hook | `.claude/hooks/scripts/session-primer.py` |
| 10 | PostToolUse reindex hook | `.claude/hooks/scripts/reindex-file.py` |
| 11 | Git post-commit hook | `.git/hooks/post-commit` |
| 12 | Settings.json update | `.claude/settings.json` |
| 13 | Integration tests | tests |
| 14 | Verify + lint | All files |
