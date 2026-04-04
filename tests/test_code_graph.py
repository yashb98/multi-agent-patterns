"""Tests for shared/code_graph.py — AST-based code knowledge graph."""

import os
import textwrap

import pytest

from shared.code_graph import CodeGraph, SECURITY_KEYWORDS


@pytest.fixture
def sample_project(tmp_path):
    """Create a temp directory with 3 Python files for indexing."""
    # auth.py — class with methods + top-level function
    (tmp_path / "auth.py").write_text(
        textwrap.dedent("""\
        import hashlib

        class AuthManager:
            def verify_token(self, token: str) -> bool:
                return hashlib.sha256(token.encode()).hexdigest() == self._stored

            def revoke_session(self, session_id: str) -> None:
                pass

        def login(username: str, password: str) -> str:
            mgr = AuthManager()
            return mgr.verify_token(password)
        """)
    )

    # utils.py — helper functions calling auth functions
    (tmp_path / "utils.py").write_text(
        textwrap.dedent("""\
        from auth import AuthManager, login

        def check_access(token):
            mgr = AuthManager()
            return mgr.verify_token(token)

        def bootstrap():
            result = login("admin", "secret")
            return result

        async def async_helper():
            return True
        """)
    )

    # test_auth.py — test file with test_ prefixed functions
    (tmp_path / "test_auth.py").write_text(
        textwrap.dedent("""\
        from auth import login, AuthManager

        def test_login_valid():
            result = login("user", "pass")
            assert result is not None

        def test_verify_token():
            mgr = AuthManager()
            assert mgr.verify_token("abc") is False

        class TestAuthManager:
            def test_revoke(self):
                mgr = AuthManager()
                mgr.revoke_session("s1")
        """)
    )

    return tmp_path


@pytest.fixture
def graph():
    """Create an in-memory CodeGraph."""
    g = CodeGraph(":memory:")
    yield g
    g.close()


@pytest.fixture
def indexed_graph(graph, sample_project):
    """CodeGraph with the sample project already indexed."""
    graph.index_directory(str(sample_project))
    return graph


# ─── CREATION & BASIC INDEXING ────────────────────────────────────


class TestCreation:
    def test_create_memory_graph(self):
        g = CodeGraph(":memory:")
        stats = g.get_stats()
        assert stats["nodes"] == 0
        assert stats["edges"] == 0
        g.close()

    def test_create_file_backed_graph(self, tmp_path):
        db_path = str(tmp_path / "graph.db")
        g = CodeGraph(db_path)
        g.close()
        assert os.path.exists(db_path)


class TestIndexDirectory:
    def test_indexes_all_python_files(self, indexed_graph):
        stats = indexed_graph.get_stats()
        assert stats["files"] == 3
        assert stats["nodes"] > 0
        assert stats["edges"] > 0

    def test_node_counts(self, indexed_graph):
        stats = indexed_graph.get_stats()
        # Classes: AuthManager, TestAuthManager = 2
        assert stats["classes"] == 2
        # Functions + methods: verify_token, revoke_session, login,
        # check_access, bootstrap, async_helper, test_login_valid,
        # test_verify_token, test_revoke = 9
        assert stats["functions"] == 9

    def test_test_function_count(self, indexed_graph):
        stats = indexed_graph.get_stats()
        # test_login_valid, test_verify_token, test_revoke, TestAuthManager = 4
        assert stats["tests"] >= 3


# ─── AST WALKING: KINDS ──────────────────────────────────────────


class TestASTWalking:
    def test_methods_have_method_kind(self, indexed_graph):
        """Methods inside classes should have kind='method'."""
        rows = indexed_graph.conn.execute(
            "SELECT kind FROM nodes WHERE name='verify_token'"
        ).fetchall()
        kinds = [r["kind"] for r in rows]
        assert "method" in kinds

    def test_top_level_functions_have_function_kind(self, indexed_graph):
        """Top-level functions should have kind='function'."""
        row = indexed_graph.conn.execute(
            "SELECT kind FROM nodes WHERE name='login' AND file_path='auth.py'"
        ).fetchone()
        assert row is not None
        assert row["kind"] == "function"

    def test_classes_have_class_kind(self, indexed_graph):
        row = indexed_graph.conn.execute(
            "SELECT kind FROM nodes WHERE name='AuthManager'"
        ).fetchone()
        assert row is not None
        assert row["kind"] == "class"

    def test_async_function_detected(self, indexed_graph):
        """async_helper should have is_async=1."""
        row = indexed_graph.conn.execute(
            "SELECT is_async FROM nodes WHERE name='async_helper'"
        ).fetchone()
        assert row is not None
        assert row["is_async"] == 1

    def test_sync_function_not_async(self, indexed_graph):
        row = indexed_graph.conn.execute(
            "SELECT is_async FROM nodes WHERE name='login' AND file_path='auth.py'"
        ).fetchone()
        assert row is not None
        assert row["is_async"] == 0

    def test_test_prefixed_function_flagged(self, indexed_graph):
        row = indexed_graph.conn.execute(
            "SELECT is_test FROM nodes WHERE name='test_login_valid'"
        ).fetchone()
        assert row is not None
        assert row["is_test"] == 1

    def test_non_test_function_not_flagged(self, indexed_graph):
        row = indexed_graph.conn.execute(
            "SELECT is_test FROM nodes WHERE name='login' AND file_path='auth.py'"
        ).fetchone()
        assert row is not None
        assert row["is_test"] == 0


# ─── CALL EXTRACTION ──────────────────────────────────────────────


class TestCallExtraction:
    def test_login_calls_verify_token(self, indexed_graph):
        """login() calls AuthManager() and mgr.verify_token()."""
        edges = indexed_graph.conn.execute(
            "SELECT target_qname FROM edges WHERE kind='calls' AND source_qname LIKE '%::login'"
        ).fetchall()
        targets = [r["target_qname"] for r in edges]
        assert any("verify_token" in t for t in targets)

    def test_check_access_calls_verify_token(self, indexed_graph):
        edges = indexed_graph.conn.execute(
            "SELECT target_qname FROM edges WHERE kind='calls' AND source_qname LIKE '%::check_access'"
        ).fetchall()
        targets = [r["target_qname"] for r in edges]
        assert any("verify_token" in t for t in targets)

    def test_bootstrap_calls_login(self, indexed_graph):
        edges = indexed_graph.conn.execute(
            "SELECT target_qname FROM edges WHERE kind='calls' AND source_qname LIKE '%::bootstrap'"
        ).fetchall()
        targets = [r["target_qname"] for r in edges]
        assert any("login" in t for t in targets)


# ─── callers_of / callees_of ──────────────────────────────────────


class TestCallerCalleeQueries:
    def test_callers_of_verify_token(self, indexed_graph):
        callers = indexed_graph.callers_of("verify_token")
        # login, check_access, test_verify_token all call verify_token
        assert len(callers) >= 2
        source_names = [c["source_qname"] for c in callers]
        assert any("login" in s for s in source_names)

    def test_callees_of_bootstrap(self, indexed_graph):
        # Find the qualified name for bootstrap
        row = indexed_graph.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name='bootstrap'"
        ).fetchone()
        assert row is not None
        callees = indexed_graph.callees_of(row["qualified_name"])
        callee_names = [c["target_qname"] for c in callees]
        assert any("login" in n for n in callee_names)

    def test_callers_of_nonexistent_returns_empty(self, indexed_graph):
        callers = indexed_graph.callers_of("nonexistent_function_xyz")
        assert callers == []


# ─── IMPACT RADIUS ─────────────────────────────────────────────────


class TestImpactRadius:
    def test_impact_from_auth_file(self, indexed_graph):
        result = indexed_graph.impact_radius(["auth.py"])
        assert "auth.py" in result["impacted_files"]
        # utils.py and test_auth.py call auth functions, so they should be impacted
        assert len(result["impacted_files"]) >= 2
        assert len(result["impacted_nodes"]) > 0

    def test_impact_depth_map_has_changed_file_at_zero(self, indexed_graph):
        result = indexed_graph.impact_radius(["auth.py"])
        assert result["depth_map"]["auth.py"] == 0

    def test_impact_from_nonexistent_file(self, indexed_graph):
        result = indexed_graph.impact_radius(["nonexistent.py"])
        assert result["impacted_files"] == set()
        assert result["impacted_nodes"] == []
        assert result["depth_map"] == {}

    def test_impact_empty_list(self, indexed_graph):
        result = indexed_graph.impact_radius([])
        assert result["impacted_files"] == set()


# ─── RISK SCORING ──────────────────────────────────────────────────


class TestRiskScoring:
    def test_security_keyword_boosts_risk(self, indexed_graph):
        """verify_token contains 'verify' and 'token' — both security keywords."""
        row = indexed_graph.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name='verify_token' AND kind='method'"
        ).fetchone()
        assert row is not None
        score = indexed_graph.compute_risk_score(row["qualified_name"])
        assert score >= 0.25  # At least the security keyword bonus

    def test_login_has_security_risk(self, indexed_graph):
        row = indexed_graph.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name='login' AND file_path='auth.py'"
        ).fetchone()
        score = indexed_graph.compute_risk_score(row["qualified_name"])
        assert score >= 0.25  # 'login' is a security keyword

    def test_non_security_function_lower_risk(self, indexed_graph):
        """bootstrap has no security keywords in its name."""
        row = indexed_graph.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name='bootstrap'"
        ).fetchone()
        score = indexed_graph.compute_risk_score(row["qualified_name"])
        # bootstrap is NOT a security keyword, so no 0.25 bonus
        # But may still have some risk from fan-in or no-test
        assert score < 0.60

    def test_nonexistent_qname_returns_zero(self, indexed_graph):
        score = indexed_graph.compute_risk_score("fake::nonexistent")
        assert score == 0.0

    def test_untested_function_gets_no_coverage_penalty(self, indexed_graph):
        """async_helper has no test calling it — should get +0.30 for no test coverage."""
        row = indexed_graph.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name='async_helper'"
        ).fetchone()
        score = indexed_graph.compute_risk_score(row["qualified_name"])
        assert score >= 0.30

    def test_fan_in_increases_risk(self, indexed_graph):
        """verify_token is called from multiple files — should get fan-in + cross-file bonus."""
        row = indexed_graph.conn.execute(
            "SELECT qualified_name FROM nodes WHERE name='verify_token' AND kind='method'"
        ).fetchone()
        score = indexed_graph.compute_risk_score(row["qualified_name"])
        # security keyword (0.25) + fan-in + cross-file + possibly no direct test
        assert score >= 0.35


class TestRiskReport:
    def test_risk_report_returns_sorted_list(self, indexed_graph):
        report = indexed_graph.risk_report()
        assert isinstance(report, list)
        if len(report) >= 2:
            assert report[0]["risk_score"] >= report[1]["risk_score"]

    def test_risk_report_contains_expected_fields(self, indexed_graph):
        report = indexed_graph.risk_report()
        assert len(report) > 0
        entry = report[0]
        assert "qualified_name" in entry
        assert "name" in entry
        assert "file_path" in entry
        assert "risk_score" in entry
        assert entry["risk_score"] > 0.0

    def test_risk_report_top_n(self, indexed_graph):
        report = indexed_graph.risk_report(top_n=2)
        assert len(report) <= 2


# ─── get_stats ─────────────────────────────────────────────────────


class TestGetStats:
    def test_empty_graph_stats(self, graph):
        stats = graph.get_stats()
        assert stats == {
            "nodes": 0, "edges": 0, "files": 0,
            "functions": 0, "classes": 0, "tests": 0,
        }

    def test_indexed_stats_all_keys(self, indexed_graph):
        stats = indexed_graph.get_stats()
        for key in ("nodes", "edges", "files", "functions", "classes", "tests"):
            assert key in stats
            assert isinstance(stats[key], int)


# ─── PATH PREFIX ───────────────────────────────────────────────────


class TestPathPrefix:
    def test_path_prefix_prepended(self, graph, sample_project):
        graph.index_directory(str(sample_project), path_prefix="src/")
        row = graph.conn.execute(
            "SELECT file_path FROM nodes WHERE name='login'"
        ).fetchone()
        assert row is not None
        assert row["file_path"].startswith("src/")

    def test_functions_in_file_with_prefix(self, graph, sample_project):
        graph.index_directory(str(sample_project), path_prefix="project/")
        funcs = graph.functions_in_file("project/auth.py")
        names = [f["name"] for f in funcs]
        assert "login" in names
        assert "verify_token" in names


# ─── SYNTAX ERROR HANDLING ─────────────────────────────────────────


class TestSyntaxErrorHandling:
    def test_file_with_syntax_error_does_not_crash(self, graph, tmp_path):
        """A file with invalid Python should be skipped gracefully."""
        (tmp_path / "bad.py").write_text("def broken(\n")
        (tmp_path / "good.py").write_text("def working():\n    return 42\n")
        graph.index_directory(str(tmp_path))
        stats = graph.get_stats()
        # good.py should be indexed, bad.py skipped
        assert stats["functions"] >= 1

    def test_binary_file_ignored(self, graph, tmp_path):
        """Non-Python files should not be picked up."""
        (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
        (tmp_path / "ok.py").write_text("def hello(): pass\n")
        graph.index_directory(str(tmp_path))
        stats = graph.get_stats()
        assert stats["functions"] == 1


# ─── FUNCTIONS IN FILE ─────────────────────────────────────────────


class TestFunctionsInFile:
    def test_lists_functions_in_auth(self, indexed_graph):
        funcs = indexed_graph.functions_in_file("auth.py")
        names = [f["name"] for f in funcs]
        assert "verify_token" in names
        assert "login" in names
        assert "revoke_session" in names

    def test_empty_for_nonexistent_file(self, indexed_graph):
        funcs = indexed_graph.functions_in_file("nonexistent.py")
        assert funcs == []
