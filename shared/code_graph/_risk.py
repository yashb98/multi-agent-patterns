"""Risk scoring and impact analysis for the code graph.

Computes per-function risk scores (0-1) based on security keywords, fan-in,
test coverage, and function size. Also provides BFS-based impact radius for
changed files.
"""

import sqlite3
from collections import deque

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── SECURITY KEYWORDS ────────────────────────────────────────────
# Tier 1: HIGH-CONFIDENCE — almost always security-relevant.
_HIGH_CONFIDENCE_KEYWORDS = frozenset({
    "auth", "password", "crypt", "secret", "encrypt", "credential",
    "oauth", "jwt", "privilege", "admin",
})

# Tier 2: CONTEXT-DEPENDENT — only flag when function name also contains
# a security-context word.
_CONTEXT_DEPENDENT_KEYWORDS = frozenset({
    "verify", "token", "session", "sql", "hash", "key",
    "login", "socket", "sanitize", "permission",
})

_SECURITY_CONTEXT_WORDS = frozenset({
    "auth", "user", "password", "cred", "login", "access",
    "perm", "secret", "account", "secure", "cert",
})

# Union kept for backward compatibility.
SECURITY_KEYWORDS = _HIGH_CONFIDENCE_KEYWORDS | _CONTEXT_DEPENDENT_KEYWORDS


class RiskScorer:
    """Computes risk scores and impact radius over the code graph."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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

        has_high_confidence = any(kw in name_lower for kw in _HIGH_CONFIDENCE_KEYWORDS)
        has_context_dependent = any(kw in name_lower for kw in _CONTEXT_DEPENDENT_KEYWORDS)
        has_security_context = any(ctx in name_lower for ctx in _SECURITY_CONTEXT_WORDS)
        if has_high_confidence or (has_context_dependent and has_security_context):
            score += 0.25

        callers = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls'",
            (f"%{node['name']}",),
        ).fetchone()[0]
        score += min(callers * 0.05, 0.20)

        cross_file = self.conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM edges WHERE target_qname LIKE ? AND kind='calls' AND file_path != ?",
            (f"%{node['name']}", node["file_path"]),
        ).fetchone()[0]
        if cross_file > 0:
            score += 0.10

        tested = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls' AND source_qname LIKE '%test_%'",
            (f"%{node['name']}",),
        ).fetchone()[0]
        if tested == 0:
            score += 0.30

        line_span = (node["line_end"] or 0) - (node["line_start"] or 0)
        if line_span > 50:
            score += 0.15

        return min(score, 1.0)

    def risk_report(self, top_n: int = 20) -> list[dict]:
        """Return top-N highest-risk functions across the codebase."""
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

    def impact_radius(self, changed_files: list[str], max_depth: int = 2,
                      max_results: int = 100) -> dict:
        """Compute blast radius from changed files via BFS.

        Uses hub-node dampening and pre-loaded adjacency lists.
        """
        seed_qnames = set()
        for f in changed_files:
            rows = self.conn.execute(
                "SELECT qualified_name FROM nodes WHERE file_path=?", (f,)
            ).fetchall()
            seed_qnames.update(r[0] for r in rows)

        if not seed_qnames:
            return {"impacted_files": set(), "impacted_nodes": [], "depth_map": {}}

        p95_row = self.conn.execute(
            "SELECT fan_in FROM nodes WHERE fan_in > 0 "
            "ORDER BY fan_in DESC LIMIT 1 OFFSET "
            "(SELECT MAX(1, COUNT(*)/20) FROM nodes WHERE fan_in > 0)"
        ).fetchone()
        hub_threshold = max(p95_row[0] if p95_row else 20, 10)

        hub_qnames: set[str] = set()
        for row in self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE fan_in > ?", (hub_threshold,)
        ).fetchall():
            hub_qnames.add(row[0])

        forward: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind='calls'"
        ).fetchall():
            forward.setdefault(row[0], []).append(row[1])

        backward: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT target_qname, source_qname FROM edges WHERE kind='calls'"
        ).fetchall():
            backward.setdefault(row[0], []).append(row[1])

        visited: dict[str, int] = {qn: 0 for qn in seed_qnames}
        queue = deque((qn, 0) for qn in seed_qnames)
        depth_map = {f: 0 for f in changed_files}

        while queue:
            qname, depth = queue.popleft()
            if depth >= max_depth:
                continue
            if qname in hub_qnames and qname not in seed_qnames:
                continue

            next_depth = depth + 1
            for target in forward.get(qname, []):
                if target not in visited:
                    visited[target] = next_depth
                    queue.append((target, next_depth))
            for source in backward.get(qname, []):
                if source not in visited:
                    visited[source] = next_depth
                    queue.append((source, next_depth))

        sorted_qnames = sorted(visited.keys(), key=lambda q: visited[q])
        if len(sorted_qnames) > max_results:
            sorted_qnames = sorted_qnames[:max_results]

        impacted_files: set[str] = set()
        impacted: list[dict] = []

        for i in range(0, len(sorted_qnames), 500):
            chunk = sorted_qnames[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE qualified_name IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                node = dict(row)
                node["impact_depth"] = visited.get(row["qualified_name"], max_depth)
                impacted.append(node)
                fp = row["file_path"]
                impacted_files.add(fp)
                if fp not in depth_map:
                    depth_map[fp] = visited.get(row["qualified_name"], max_depth)

        return {
            "impacted_files": impacted_files,
            "impacted_nodes": impacted,
            "depth_map": depth_map,
        }
