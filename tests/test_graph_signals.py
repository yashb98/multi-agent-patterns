"""Tests for graph signal computation — PageRank, Leiden communities, fan-in/fan-out."""

import sqlite3
import pytest

from shared.code_graph import CodeGraph


@pytest.fixture
def graph():
    g = CodeGraph(":memory:")
    # Build a small call graph:
    # A -> B -> C
    # A -> C
    # D -> B
    for name in ["A", "B", "C", "D"]:
        g.conn.execute(
            "INSERT INTO nodes (kind, name, qualified_name, file_path) VALUES (?, ?, ?, ?)",
            ("function", name, f"test.py::{name}", "test.py"),
        )
    for src, tgt in [("A", "B"), ("A", "C"), ("B", "C"), ("D", "B")]:
        g.conn.execute(
            "INSERT INTO edges (kind, source_qname, target_qname, file_path) VALUES (?, ?, ?, ?)",
            ("calls", f"test.py::{src}", f"test.py::{tgt}", "test.py"),
        )
    g.conn.commit()
    yield g


class TestFanInFanOut:
    def test_compute_fan_in_out(self, graph):
        graph.compute_fan_in_out()
        rows = {
            r[0]: (r[1], r[2])
            for r in graph.conn.execute(
                "SELECT name, fan_in, fan_out FROM nodes"
            ).fetchall()
        }
        # B is called by A and D → fan_in=2
        assert rows["B"][0] == 2
        # C is called by A and B → fan_in=2
        assert rows["C"][0] == 2
        # A calls B and C → fan_out=2
        assert rows["A"][1] == 2
        # D calls B → fan_out=1
        assert rows["D"][1] == 1


class TestPageRank:
    def test_compute_pagerank_runs(self, graph):
        graph.compute_pagerank()
        rows = graph.conn.execute(
            "SELECT name, pagerank FROM nodes ORDER BY pagerank DESC"
        ).fetchall()
        # All nodes should have pagerank > 0
        for name, pr in rows:
            assert pr > 0, f"{name} has pagerank=0"
        # B and C (most called) should have highest pagerank
        top_names = [r[0] for r in rows[:2]]
        assert "B" in top_names or "C" in top_names

    def test_pagerank_sums_to_approximately_one(self, graph):
        graph.compute_pagerank()
        total = graph.conn.execute("SELECT SUM(pagerank) FROM nodes").fetchone()[0]
        assert abs(total - 1.0) < 0.01


class TestCommunityDetection:
    def test_compute_communities_assigns_ids(self, graph):
        graph.compute_communities()
        rows = graph.conn.execute(
            "SELECT name, community_id FROM nodes"
        ).fetchall()
        for name, cid in rows:
            assert cid is not None, f"{name} has no community_id"

    def test_compute_communities_fallback_without_leidenalg(self, graph, monkeypatch):
        """Without leidenalg, falls back to file-based communities."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "leidenalg" in name or "igraph" in name:
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        graph.compute_communities()
        rows = graph.conn.execute(
            "SELECT name, community_id FROM nodes"
        ).fetchall()
        # Fallback assigns community by file_path hash
        for name, cid in rows:
            assert cid is not None
