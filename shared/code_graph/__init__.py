"""Lightweight Code Graph — AST-based structural analysis for generated code.

Parses Python source files into a SQLite-backed graph of functions, classes,
imports, and call relationships. Computes impact radius and risk scores so
the Reviewer agent can prioritize inspection of high-risk code.

Split into focused modules:
- _indexer.py — AST parsing, node/edge creation, call-edge resolution
- _risk.py — risk scoring, impact radius (BFS)
- _algorithms.py — PageRank, community detection, fan-in/out

CodeGraph is the public facade — same API as before the split.
"""

import sqlite3
from typing import Optional

from shared.code_graph._indexer import ASTIndexer
from shared.code_graph._risk import RiskScorer, SECURITY_KEYWORDS  # noqa: F401
from shared.code_graph._algorithms import GraphAlgorithms
from shared.logging_config import get_logger

logger = get_logger(__name__)


class CodeGraph:
    """SQLite-backed code knowledge graph with impact analysis.

    Composes ASTIndexer, RiskScorer, and GraphAlgorithms — delegates to each
    while keeping a single public API and shared SQLite connection.
    """

    def __init__(self, db_path: str = ":memory:", conn: Optional[sqlite3.Connection] = None):
        if conn is not None:
            self.conn = conn
        else:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

        # Compose focused collaborators
        self._indexer = ASTIndexer(self.conn)
        self._risk = RiskScorer(self.conn)
        self._algorithms = GraphAlgorithms(self.conn)

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
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
                kind TEXT NOT NULL,
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

        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "pagerank" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN pagerank REAL DEFAULT 0.0")
        if "community_id" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN community_id INTEGER")
        if "fan_in" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fan_in INTEGER DEFAULT 0")
        if "fan_out" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fan_out INTEGER DEFAULT 0")

        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_community ON nodes(community_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_pagerank ON nodes(pagerank DESC)")
        self.conn.commit()

    # ─── INDEXING (delegated to ASTIndexer) ─────────────────────────

    def index_directory(self, root: str, extensions: tuple = (".py",),
                        path_prefix: str = ""):
        self._indexer.index_directory(root, extensions, path_prefix)

    # ─── QUERIES ────────────────────────────────────────────────────

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
        """Find all functions that call or reference the given name."""
        rows = self.conn.execute(
            "SELECT source_qname, file_path, line FROM edges "
            "WHERE kind IN ('calls', 'references') AND target_qname LIKE ?",
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

    # ─── RISK & IMPACT (delegated to RiskScorer) ───────────────────

    def compute_risk_score(self, qname: str) -> float:
        return self._risk.compute_risk_score(qname)

    def risk_report(self, top_n: int = 20) -> list[dict]:
        return self._risk.risk_report(top_n)

    def impact_radius(self, changed_files: list[str], max_depth: int = 2,
                      max_results: int = 100) -> dict:
        return self._risk.impact_radius(changed_files, max_depth, max_results)

    # ─── GRAPH ALGORITHMS (delegated to GraphAlgorithms) ───────────

    def compute_fan_in_out(self) -> None:
        self._algorithms.compute_fan_in_out()

    def compute_pagerank(self, iterations: int = 15, damping: float = 0.85) -> None:
        self._algorithms.compute_pagerank(iterations, damping)

    def compute_communities(self) -> None:
        self._algorithms.compute_communities()

    # ─── LIFECYCLE ──────────────────────────────────────────────────

    def close(self):
        self.conn.close()
