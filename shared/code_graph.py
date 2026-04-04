"""Lightweight Code Graph — AST-based structural analysis for generated code.

Parses Python source files into a SQLite-backed graph of functions, classes,
imports, and call relationships. Computes impact radius and risk scores so
the Reviewer agent can prioritize inspection of high-risk code.

Inspired by code-review-graph (github.com/tirth8205/code-review-graph):
- AST parsing → nodes (functions, classes) + edges (calls, imports)
- Impact radius via BFS traversal
- Risk scoring based on security keywords, fan-in, test coverage

Usage:
    graph = CodeGraph(":memory:")  # or path to SQLite file
    graph.index_directory("/path/to/generated/project")
    risk_report = graph.risk_report()
    impact = graph.impact_radius(["src/auth.py"])
"""

import ast
import sqlite3
from pathlib import Path
from collections import deque
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── SECURITY KEYWORDS ────────────────────────────────────────────
# Functions containing these keywords in their name get a risk boost.

SECURITY_KEYWORDS = frozenset({
    "auth", "password", "token", "crypt", "secret", "sql", "socket",
    "encrypt", "verify", "admin", "privilege", "session", "credential",
    "login", "permission", "sanitize", "hash", "key", "oauth", "jwt",
})


class CodeGraph:
    """SQLite-backed code knowledge graph with impact analysis."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,          -- 'function', 'class', 'method'
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                line_start INTEGER,
                line_end INTEGER,
                is_test INTEGER DEFAULT 0,
                is_async INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,          -- 'calls', 'imports', 'inherits', 'contains'
                source_qname TEXT NOT NULL,
                target_qname TEXT NOT NULL,
                file_path TEXT,
                line INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
            CREATE INDEX IF NOT EXISTS idx_nodes_qname ON nodes(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_qname);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_qname);
        """)
        self.conn.commit()

    # ─── INDEXING ──────────────────────────────────────────────────

    def index_directory(self, root: str, extensions: tuple = (".py",),
                        path_prefix: str = ""):
        """Parse all matching files under root and build the graph.

        Args:
            root: Directory to recursively scan.
            extensions: File extensions to parse.
            path_prefix: Prefix prepended to relative paths (e.g., "shared/"
                         when indexing a subdirectory but wanting paths relative
                         to the project root).
        """
        root_path = Path(root)
        files = [f for f in root_path.rglob("*") if f.suffix in extensions
                 and "__pycache__" not in str(f)
                 and ".venv" not in str(f)
                 and "node_modules" not in str(f)]

        for filepath in files:
            try:
                self._index_file(filepath, root_path, path_prefix)
            except Exception as e:
                logger.debug("Failed to parse %s: %s", filepath.name, e)

        self.conn.commit()
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        logger.info("Indexed %d files → %d nodes, %d edges", len(files), node_count, edge_count)

    def _index_file(self, filepath: Path, root: Path, prefix: str = ""):
        """Parse a single Python file into nodes and edges."""
        source = filepath.read_text(encoding="utf-8", errors="replace")
        rel_path = prefix + str(filepath.relative_to(root))

        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            return

        self._walk_ast(tree, rel_path)

    def _walk_ast(self, tree: ast.Module, file_path: str):
        """Extract functions, classes, calls, imports from an AST.

        Uses a parent-tracking visitor instead of ast.walk to correctly
        distinguish top-level functions from class methods.
        """
        # First pass: collect class bodies so we know which FunctionDefs are methods
        class_method_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    class_method_ids.add(id(item))

        for node in ast.walk(tree):
            try:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.asname or alias.name
                        self._add_edge("imports", f"{file_path}::__module__",
                                       name, file_path, node.lineno)

                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        self._add_edge("imports", f"{file_path}::__module__",
                                       f"{module}.{alias.name}", file_path, node.lineno)

                elif isinstance(node, ast.ClassDef):
                    qname = f"{file_path}::{node.name}"
                    self._add_node("class", node.name, qname, file_path,
                                   node.lineno, node.end_lineno or node.lineno,
                                   is_test=node.name.startswith("Test"))

                    for base in node.bases:
                        base_name = self._get_name(base)
                        if base_name:
                            self._add_edge("inherits", qname, base_name, file_path, node.lineno)

                    # Methods inside the class
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            method_qname = f"{qname}::{item.name}"
                            self._add_node(
                                "method", item.name, method_qname, file_path,
                                item.lineno, item.end_lineno or item.lineno,
                                is_test=item.name.startswith("test_"),
                                is_async=isinstance(item, ast.AsyncFunctionDef),
                            )
                            self._add_edge("contains", qname, method_qname, file_path, item.lineno)
                            self._extract_calls(item, method_qname, file_path)

                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Top-level functions only — methods are handled in the ClassDef branch
                    if id(node) not in class_method_ids:
                        qname = f"{file_path}::{node.name}"
                        self._add_node(
                            "function", node.name, qname, file_path,
                            node.lineno, node.end_lineno or node.lineno,
                            is_test=node.name.startswith("test_"),
                            is_async=isinstance(node, ast.AsyncFunctionDef),
                        )
                        self._extract_calls(node, qname, file_path)
            except Exception:
                # Skip nodes that fail to process — don't abort the whole file
                continue

    def _extract_calls(self, func_node, caller_qname: str, file_path: str):
        """Extract function calls from a function body."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                try:
                    callee = self._get_name(node.func)
                    if callee:
                        self._add_edge("calls", caller_qname, callee, file_path,
                                       getattr(node, "lineno", 0))
                except Exception:
                    continue

    def _get_name(self, node) -> Optional[str]:
        """Extract a name string from an AST node. Returns None for complex expressions."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._get_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        elif isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        elif isinstance(node, ast.Call):
            # e.g., foo()() — extract the inner function name
            return self._get_name(node.func)
        return None

    def _add_node(self, kind, name, qname, file_path, line_start, line_end,
                  is_test=False, is_async=False):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO nodes (kind, name, qualified_name, file_path, line_start, line_end, is_test, is_async) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (kind, name, qname, file_path, line_start, line_end, int(is_test), int(is_async)),
            )
        except sqlite3.IntegrityError:
            pass

    def _add_edge(self, kind, source, target, file_path, line):
        self.conn.execute(
            "INSERT INTO edges (kind, source_qname, target_qname, file_path, line) VALUES (?,?,?,?,?)",
            (kind, source, target, file_path, line),
        )

    # ─── QUERIES ──────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return aggregate graph statistics."""
        nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        files = self.conn.execute("SELECT COUNT(DISTINCT file_path) FROM nodes").fetchone()[0]
        functions = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE kind IN ('function','method')").fetchone()[0]
        classes = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='class'").fetchone()[0]
        tests = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE is_test=1").fetchone()[0]
        return {
            "nodes": nodes, "edges": edges, "files": files,
            "functions": functions, "classes": classes, "tests": tests,
        }

    def callers_of(self, name: str) -> list[dict]:
        """Find all functions that call the given name."""
        rows = self.conn.execute(
            "SELECT source_qname, file_path, line FROM edges WHERE kind='calls' AND target_qname LIKE ?",
            (f"%{name}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def callees_of(self, qname: str) -> list[dict]:
        """Find all functions called by the given qualified name."""
        rows = self.conn.execute(
            "SELECT target_qname, file_path, line FROM edges WHERE kind='calls' AND source_qname=?",
            (qname,),
        ).fetchall()
        return [dict(r) for r in rows]

    def functions_in_file(self, file_path: str) -> list[dict]:
        """List all functions/methods in a file."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE file_path=? AND kind IN ('function','method') ORDER BY line_start",
            (file_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── IMPACT RADIUS ─────────────────────────────────────────────

    def impact_radius(self, changed_files: list[str], max_depth: int = 2) -> dict:
        """Compute blast radius from changed files via BFS.

        Returns:
            impacted_files: set of files that could be affected
            impacted_nodes: list of node dicts in the blast radius
            depth_map: {file: min_depth} showing how far the impact reaches
        """
        # Collect all nodes in changed files
        seed_qnames = set()
        for f in changed_files:
            rows = self.conn.execute(
                "SELECT qualified_name FROM nodes WHERE file_path=?", (f,)
            ).fetchall()
            seed_qnames.update(r[0] for r in rows)

        if not seed_qnames:
            return {"impacted_files": set(), "impacted_nodes": [], "depth_map": {}}

        # BFS outward through calls and imports
        visited = set(seed_qnames)
        queue = deque((qn, 0) for qn in seed_qnames)
        depth_map = {f: 0 for f in changed_files}

        while queue:
            qname, depth = queue.popleft()
            if depth > max_depth:
                continue

            # Forward: who does this call?
            for row in self.conn.execute(
                "SELECT target_qname FROM edges WHERE source_qname=? AND kind='calls'",
                (qname,),
            ).fetchall():
                target = row[0]
                if target not in visited:
                    visited.add(target)
                    queue.append((target, depth + 1))

            # Backward: who calls this?
            name_part = qname.split("::")[-1]
            for row in self.conn.execute(
                "SELECT source_qname FROM edges WHERE target_qname LIKE ? AND kind='calls'",
                (f"%{name_part}",),
            ).fetchall():
                source = row[0]
                if source not in visited:
                    visited.add(source)
                    queue.append((source, depth + 1))

        # Resolve visited qnames to nodes
        impacted_files = set()
        impacted = []
        for qn in visited:
            row = self.conn.execute(
                "SELECT * FROM nodes WHERE qualified_name=?", (qn,)
            ).fetchone()
            if row:
                impacted.append(dict(row))
                fp = row["file_path"]
                impacted_files.add(fp)
                if fp not in depth_map:
                    depth_map[fp] = max_depth

        return {
            "impacted_files": impacted_files,
            "impacted_nodes": impacted,
            "depth_map": depth_map,
        }

    # ─── RISK SCORING ──────────────────────────────────────────────

    def compute_risk_score(self, qname: str) -> float:
        """Compute a 0-1 risk score for a function/method.

        Factors:
        - Security keyword in name: +0.25
        - High fan-in (many callers): +0.05 per caller, cap 0.20
        - No test coverage: +0.30
        - Large function (>50 lines): +0.15
        - Cross-file callers: +0.10
        """
        node = self.conn.execute(
            "SELECT * FROM nodes WHERE qualified_name=?", (qname,)
        ).fetchone()
        if not node:
            return 0.0

        score = 0.0
        name_lower = node["name"].lower()

        # Security keywords
        if any(kw in name_lower for kw in SECURITY_KEYWORDS):
            score += 0.25

        # Fan-in (callers) — use exact name suffix match to reduce false positives
        callers = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls'",
            (f"%{node['name']}",),
        ).fetchone()[0]
        score += min(callers * 0.05, 0.20)

        # Cross-file callers
        cross_file = self.conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM edges WHERE target_qname LIKE ? AND kind='calls' AND file_path != ?",
            (f"%{node['name']}", node["file_path"]),
        ).fetchone()[0]
        if cross_file > 0:
            score += 0.10

        # Test coverage
        tested = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls' AND source_qname LIKE '%test_%'",
            (f"%{node['name']}",),
        ).fetchone()[0]
        if tested == 0:
            score += 0.30

        # Function size
        line_span = (node["line_end"] or 0) - (node["line_start"] or 0)
        if line_span > 50:
            score += 0.15

        return min(score, 1.0)

    def risk_report(self, top_n: int = 20) -> list[dict]:
        """Return top-N highest-risk functions across the codebase.

        Used by the Reviewer agent to prioritize what to inspect.
        """
        functions = self.conn.execute(
            "SELECT qualified_name, name, file_path, line_start, line_end FROM nodes "
            "WHERE kind IN ('function', 'method') ORDER BY file_path, line_start"
        ).fetchall()

        scored = []
        for fn in functions:
            risk = self.compute_risk_score(fn["qualified_name"])
            if risk > 0.0:
                scored.append({
                    "qualified_name": fn["qualified_name"],
                    "name": fn["name"],
                    "file_path": fn["file_path"],
                    "line_start": fn["line_start"],
                    "line_end": fn["line_end"],
                    "risk_score": risk,
                })

        scored.sort(key=lambda x: x["risk_score"], reverse=True)
        return scored[:top_n]

    def close(self):
        self.conn.close()
