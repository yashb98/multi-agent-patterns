"""Graph algorithms — PageRank, community detection, fan-in/out computation.

Operates on the code graph's SQLite tables via the shared connection.
"""

import sqlite3

from shared.logging_config import get_logger

logger = get_logger(__name__)


class GraphAlgorithms:
    """Computes structural signals over the code graph."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def compute_fan_in_out(self) -> None:
        """Compute and cache fan-in/fan-out counts for all nodes."""
        self.conn.execute("UPDATE nodes SET fan_in = 0, fan_out = 0")
        self.conn.execute("""
            UPDATE nodes SET fan_in = (
                SELECT COUNT(*) FROM edges
                WHERE edges.target_qname = nodes.qualified_name
                  AND edges.kind IN ('calls', 'references')
            )
        """)
        self.conn.execute("""
            UPDATE nodes SET fan_out = (
                SELECT COUNT(*) FROM edges
                WHERE edges.source_qname = nodes.qualified_name AND edges.kind = 'calls'
            )
        """)
        self.conn.commit()

    def compute_pagerank(self, iterations: int = 15, damping: float = 0.85) -> None:
        """Compute PageRank over the call graph. Undirected edges."""
        nodes = self.conn.execute("SELECT qualified_name FROM nodes").fetchall()
        if not nodes:
            return

        qnames = [r[0] for r in nodes]
        n = len(qnames)
        rank = {q: 1.0 / n for q in qnames}

        neighbors: dict[str, list[str]] = {q: [] for q in qnames}
        edges = self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind = 'calls'"
        ).fetchall()
        for src, tgt in edges:
            if src in neighbors:
                neighbors[src].append(tgt)
            if tgt in neighbors:
                neighbors[tgt].append(src)

        degree = {q: len(neighbors[q]) for q in qnames}

        for _ in range(iterations):
            new_rank = {}
            for q in qnames:
                s = sum(rank.get(nb, 0) / max(degree.get(nb, 1), 1) for nb in neighbors[q])
                new_rank[q] = (1 - damping) / n + damping * s
            rank = new_rank

        updates = [(rank[q], q) for q in qnames]
        self.conn.executemany("UPDATE nodes SET pagerank = ? WHERE qualified_name = ?", updates)
        self.conn.commit()

    def compute_communities(self) -> None:
        """Compute Leiden communities. Falls back to file-based grouping."""
        nodes = self.conn.execute("SELECT qualified_name, file_path FROM nodes").fetchall()
        if not nodes:
            return

        qnames = [r[0] for r in nodes]
        file_paths = {r[0]: r[1] for r in nodes}

        try:
            import igraph as ig
            import leidenalg

            qname_to_idx = {q: i for i, q in enumerate(qnames)}
            g = ig.Graph(n=len(qnames), directed=False)

            edges_data = self.conn.execute(
                "SELECT source_qname, target_qname FROM edges WHERE kind = 'calls'"
            ).fetchall()
            ig_edges = []
            for src, tgt in edges_data:
                if src in qname_to_idx and tgt in qname_to_idx:
                    ig_edges.append((qname_to_idx[src], qname_to_idx[tgt]))
            if ig_edges:
                g.add_edges(ig_edges)

            partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
            updates = [(partition.membership[i], q) for i, q in enumerate(qnames)]

        except ImportError:
            logger.info("leidenalg/igraph not installed — using file-based communities")
            file_to_id: dict[str, int] = {}
            counter = 0
            updates = []
            for q in qnames:
                fp = file_paths.get(q, "unknown")
                if fp not in file_to_id:
                    file_to_id[fp] = counter
                    counter += 1
                updates.append((file_to_id[fp], q))

        self.conn.executemany("UPDATE nodes SET community_id = ? WHERE qualified_name = ?", updates)
        self.conn.commit()
