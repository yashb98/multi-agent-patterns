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
        # fan-in (3 callers * 0.05 = 0.15) + cross-file (0.10) = 0.25
        # No security keyword bonus: "verify_token" matches context-dependent
        # keywords but lacks a security context word in the name (two-tier matching)
        assert score >= 0.25


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


# ─── EXTERNAL CONNECTION ───────────────────────────────────────────


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


class TestSelfMethodResolution:
    """Tests for self.method() → ClassName::method edge resolution."""

    def test_self_call_resolved_to_class_method(self, tmp_path):
        """self.bar() inside Foo::baz creates edge to Foo::bar, not 'self.bar'."""
        (tmp_path / "svc.py").write_text(textwrap.dedent("""\
            class Service:
                def connect(self):
                    pass

                def run(self):
                    self.connect()
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::run' AND kind='calls'"
        ).fetchall()
        targets = [e[0] for e in edges]
        assert any("Service::connect" in t for t in targets), f"Expected Service::connect in {targets}"
        assert not any("self.connect" in t for t in targets), f"Raw self.connect should not appear: {targets}"
        graph.close()

    def test_self_chained_call_not_resolved(self, tmp_path):
        """self.foo.bar() should NOT be resolved (chained attribute, not direct method)."""
        (tmp_path / "chain.py").write_text(textwrap.dedent("""\
            class Wrapper:
                def process(self):
                    self.client.send()
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::process' AND kind='calls'"
        ).fetchall()
        targets = [e[0] for e in edges]
        # self.client.send should NOT resolve to Wrapper::send (it's chained)
        assert not any("Wrapper::send" in t for t in targets)
        graph.close()


class TestBuiltinFiltering:
    """Tests that builtin calls are excluded from the call graph."""

    def test_builtins_excluded(self, tmp_path):
        """Common builtins like print, len, str should not create edges."""
        (tmp_path / "demo.py").write_text(textwrap.dedent("""\
            def process(data):
                print(len(data))
                result = str(data)
                items = sorted(data)
                return items
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::process' AND kind='calls'"
        ).fetchall()
        targets = [e[0] for e in edges]
        for builtin in ("print", "len", "str", "sorted"):
            assert builtin not in targets, f"Builtin {builtin} should be filtered: {targets}"
        graph.close()

    def test_non_builtins_preserved(self, tmp_path):
        """User-defined functions with builtin-like method names are preserved."""
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            def custom_process(x):
                return x * 2

            def run():
                custom_process(42)
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::run' AND kind='calls'"
        ).fetchall()
        targets = [e[0] for e in edges]
        assert any("custom_process" in t for t in targets), f"custom_process should be in edges: {targets}"
        graph.close()


class TestAssignmentTracking:
    """Tests for variable assignment tracking (x = foo(); x.bar() → edge to foo)."""

    def test_variable_call_creates_edge(self, tmp_path):
        """x = create_client(); x.send() should create edge to create_client."""
        (tmp_path / "client.py").write_text(textwrap.dedent("""\
            def create_client():
                return object()

            def send_message():
                client = create_client()
                client.send("hello")
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::send_message' AND kind='calls'"
        ).fetchall()
        targets = [e[0] for e in edges]
        assert any("create_client" in t for t in targets), f"Expected create_client in {targets}"
        graph.close()

    def test_self_assignment_not_double_tracked(self, tmp_path):
        """self.x should not trigger variable tracking (self is special-cased)."""
        (tmp_path / "obj.py").write_text(textwrap.dedent("""\
            class Mgr:
                def setup(self):
                    self.conn = get_connection()
                    self.conn.execute("SELECT 1")
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        # Should have edge to get_connection but not a duplicate from var tracking
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::setup' AND kind='calls'"
        ).fetchall()
        targets = [e[0] for e in edges]
        assert any("get_connection" in t for t in targets), f"Expected get_connection in {targets}"
        graph.close()


class TestDynamicDispatchDetection:
    """Tests for detecting function references in dynamic dispatch patterns."""

    def test_dict_value_function_reference(self, tmp_path):
        """Functions used as dict values should create 'references' edges."""
        (tmp_path / "handlers.py").write_text(textwrap.dedent("""\
            def handle_login(msg):
                return "logged in"

            def handle_logout(msg):
                return "logged out"

            def dispatch(intent, msg):
                HANDLER_MAP = {
                    "login": handle_login,
                    "logout": handle_logout,
                }
                return HANDLER_MAP[intent](msg)
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        # dispatch should have references edges to handle_login and handle_logout
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::dispatch' "
            "AND kind='references'"
        ).fetchall()
        targets = {e[0] for e in edges}
        assert any("handle_login" in t for t in targets), \
            f"Expected handle_login ref from dispatch, got {targets}"
        assert any("handle_logout" in t for t in targets), \
            f"Expected handle_logout ref from dispatch, got {targets}"
        graph.close()

    def test_thread_target_function_reference(self, tmp_path):
        """Functions passed as target= to Thread() should create 'references' edges."""
        (tmp_path / "bots.py").write_text(textwrap.dedent("""\
            import threading

            def poll_bot(name, token):
                pass

            def start_all():
                t = threading.Thread(target=poll_bot, args=("main", "tok"))
                t.start()
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::start_all' "
            "AND kind='references'"
        ).fetchall()
        targets = {e[0] for e in edges}
        assert any("poll_bot" in t for t in targets), \
            f"Expected poll_bot ref from start_all, got {targets}"
        graph.close()

    def test_add_node_function_reference(self, tmp_path):
        """Functions passed as positional args to add_node() should create 'references' edges."""
        (tmp_path / "workflow.py").write_text(textwrap.dedent("""\
            def analyzer_node(state):
                return {"result": "analyzed"}

            def writer_node(state):
                return {"result": "written"}

            def build_graph():
                graph = StateGraph()
                graph.add_node("analyzer", analyzer_node)
                graph.add_node("writer", writer_node)
                return graph
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::build_graph' "
            "AND kind='references'"
        ).fetchall()
        targets = {e[0] for e in edges}
        assert any("analyzer_node" in t for t in targets), \
            f"Expected analyzer_node ref from build_graph, got {targets}"
        assert any("writer_node" in t for t in targets), \
            f"Expected writer_node ref from build_graph, got {targets}"
        graph.close()

    def test_list_function_reference(self, tmp_path):
        """Functions in list/tuple literals should create 'references' edges."""
        (tmp_path / "pipeline.py").write_text(textwrap.dedent("""\
            def step_one(data):
                return data

            def step_two(data):
                return data

            def run_pipeline(data):
                steps = [step_one, step_two]
                for step in steps:
                    data = step(data)
                return data
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::run_pipeline' "
            "AND kind='references'"
        ).fetchall()
        targets = {e[0] for e in edges}
        assert any("step_one" in t for t in targets), \
            f"Expected step_one ref from run_pipeline, got {targets}"
        assert any("step_two" in t for t in targets), \
            f"Expected step_two ref from run_pipeline, got {targets}"
        graph.close()

    def test_callback_kwarg_function_reference(self, tmp_path):
        """Functions passed as keyword args (callbacks) should create 'references' edges."""
        (tmp_path / "runner.py").write_text(textwrap.dedent("""\
            def on_success(result):
                print(result)

            def on_failure(error):
                print(error)

            def execute(task):
                run_task(task, on_success=on_success, on_failure=on_failure)
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::execute' "
            "AND kind='references'"
        ).fetchall()
        targets = {e[0] for e in edges}
        assert any("on_success" in t for t in targets), \
            f"Expected on_success ref from execute, got {targets}"
        assert any("on_failure" in t for t in targets), \
            f"Expected on_failure ref from execute, got {targets}"
        graph.close()

    def test_dynamic_refs_resolved_to_qualified_names(self, tmp_path):
        """Dynamic references should be resolved to qualified names like call edges."""
        (tmp_path / "scan.py").write_text(textwrap.dedent("""\
            def scan_linkedin():
                pass

            def scan_indeed():
                pass

            def run_scan():
                scanners = {"linkedin": scan_linkedin, "indeed": scan_indeed}
                return scanners
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT target_qname FROM edges WHERE source_qname LIKE '%::run_scan' "
            "AND kind='references'"
        ).fetchall()
        targets = {e[0] for e in edges}
        # Should be resolved to qualified names (containing ::)
        resolved = [t for t in targets if "::" in t]
        assert len(resolved) >= 2, \
            f"Expected at least 2 resolved references, got {targets}"
        graph.close()

    def test_module_level_dict_reference(self, tmp_path):
        """Functions in module-level dicts (not inside any function) should be detected."""
        (tmp_path / "scanners.py").write_text(textwrap.dedent("""\
            def scan_linkedin():
                pass

            def scan_indeed():
                pass

            def scan_reed():
                pass

            PLATFORM_SCANNERS = {
                "linkedin": scan_linkedin,
                "indeed": scan_indeed,
                "reed": scan_reed,
            }
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        for name in ["scan_linkedin", "scan_indeed", "scan_reed"]:
            incoming = graph.conn.execute(
                "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='references'",
                (f"%::{name}",),
            ).fetchone()[0]
            assert incoming >= 1, f"{name} should have incoming reference from module-level dict"
        graph.close()

    def test_dynamically_referenced_functions_have_incoming_edges(self, tmp_path):
        """Functions referenced dynamically should have incoming edges (not appear dead)."""
        (tmp_path / "app.py").write_text(textwrap.dedent("""\
            def handler_a():
                pass

            def handler_b():
                pass

            def truly_dead():
                pass

            def main():
                handlers = {"a": handler_a, "b": handler_b}
                handlers["a"]()
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        # handler_a and handler_b should have incoming edges (references from main)
        ha_incoming = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE '%::handler_a'"
        ).fetchone()[0]
        hb_incoming = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE '%::handler_b'"
        ).fetchone()[0]
        dead_incoming = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE '%::truly_dead'"
        ).fetchone()[0]
        assert ha_incoming >= 1, "handler_a should have incoming reference edge"
        assert hb_incoming >= 1, "handler_b should have incoming reference edge"
        assert dead_incoming == 0, "truly_dead should have no incoming edges"
        graph.close()

    def test_return_value_function_reference(self, tmp_path):
        """Functions returned directly should create reference edges."""
        (tmp_path / "factory.py").write_text(textwrap.dedent("""\
            def make_handler():
                pass

            def get_handler():
                return make_handler
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_qname LIKE '%::get_handler' "
            "AND kind='references' AND target_qname LIKE '%::make_handler'"
        ).fetchone()[0]
        assert edges >= 1, "make_handler should be referenced via return"
        graph.close()

    def test_assignment_function_reference(self, tmp_path):
        """Functions assigned to variables should create reference edges."""
        (tmp_path / "alias.py").write_text(textwrap.dedent("""\
            def original_handler():
                pass

            def setup():
                fn = original_handler
                return fn
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        edges = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_qname LIKE '%::setup' "
            "AND kind='references' AND target_qname LIKE '%::original_handler'"
        ).fetchone()[0]
        assert edges >= 1, "original_handler should be referenced via assignment"
        graph.close()

    def test_default_arg_function_reference(self, tmp_path):
        """Functions used as default argument values should create reference edges."""
        (tmp_path / "defaults.py").write_text(textwrap.dedent("""\
            def fallback_handler():
                pass

            def process(callback=fallback_handler):
                callback()
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        # Check if fallback_handler has any incoming reference edge
        edges = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='references' "
            "AND target_qname LIKE '%::fallback_handler'"
        ).fetchone()[0]
        assert edges >= 1, "fallback_handler should be referenced via default arg"
        graph.close()

    def test_comprehension_function_reference(self, tmp_path):
        """Functions used inside comprehensions should create reference edges."""
        (tmp_path / "comp.py").write_text(textwrap.dedent("""\
            def transform_a(x):
                return x

            def transform_b(x):
                return x

            def run(data):
                fns = [transform_a, transform_b]
                return [fn(d) for fn, d in zip(fns, data)]
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        for name in ["transform_a", "transform_b"]:
            edges = graph.conn.execute(
                "SELECT COUNT(*) FROM edges WHERE kind='references' "
                "AND target_qname LIKE ?", (f"%::{name}",)
            ).fetchone()[0]
            assert edges >= 1, f"{name} should be referenced"
        graph.close()

    def test_main_guard_calls_detected(self, tmp_path):
        """Functions called inside if __name__ == '__main__' should have incoming edges."""
        (tmp_path / "script.py").write_text(textwrap.dedent("""\
            def run_benchmark():
                pass

            def compare_results():
                pass

            if __name__ == "__main__":
                run_benchmark()
                compare_results()
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        for name in ["run_benchmark", "compare_results"]:
            incoming = graph.conn.execute(
                "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? "
                "AND kind='calls'", (f"%::{name}",)
            ).fetchone()[0]
            assert incoming >= 1, f"{name} should have incoming call from __main__ block"
        graph.close()

    def test_decorated_route_handlers_detected(self, tmp_path):
        """Functions with @router.get/@app.post decorators should have incoming edges."""
        (tmp_path / "api.py").write_text(textwrap.dedent("""\
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/items")
            def list_items():
                return []

            @router.post("/items")
            def create_item(data):
                return data

            def helper():
                pass
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        for name in ["list_items", "create_item"]:
            incoming = graph.conn.execute(
                "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? "
                "AND kind='references'", (f"%::{name}",)
            ).fetchone()[0]
            assert incoming >= 1, f"{name} should have incoming ref from decorator"
        # helper should NOT have incoming edges
        helper_incoming = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE '%::helper' "
            "AND kind IN ('calls', 'references')"
        ).fetchone()[0]
        assert helper_incoming == 0, "helper should have no incoming edges"
        graph.close()

    def test_no_false_positive_on_variables(self, tmp_path):
        """Variable names that don't match function nodes stay unresolved.

        Unresolved references don't have '::' in target_qname, so they're
        invisible to callers_of queries and dead_code analysis.
        """
        (tmp_path / "clean.py").write_text(textwrap.dedent("""\
            import os

            def process():
                name = "hello"
                count = 42
                path = os.path.join("a", "b")
                return name, count, path
        """))
        graph = CodeGraph()
        graph.index_directory(str(tmp_path))
        # Unresolved reference edges may exist, but RESOLVED ones (with ::) should not
        resolved_refs = graph.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_qname LIKE '%::process' "
            "AND kind='references' AND target_qname LIKE '%::%'"
        ).fetchone()[0]
        assert resolved_refs == 0, f"Expected 0 resolved reference edges, got {resolved_refs}"
        graph.close()
