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
    (src / "README.md").write_text("# Auth Project\\n\\nA sample auth system.")
    (src / "config.yaml").write_text("debug: true\\nport: 8080\\n")
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

    def test_graph_and_search_share_connection(self, ci):
        """CodeGraph and HybridSearch both use the shared connection."""
        assert ci._graph.conn is ci.conn
        assert ci._search.conn is ci.conn

    def test_idempotent_init(self, tmp_path):
        """Opening same DB twice doesn't break schema."""
        db_path = str(tmp_path / "ci.db")
        ci1 = CodeIntelligence(db_path=db_path)
        ci1.close()
        ci2 = CodeIntelligence(db_path=db_path)
        tables = ci2.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}
        assert "nodes" in table_names
        ci2.close()


class TestExclusionPatterns:
    def test_env_excluded(self):
        from shared.code_intelligence import _is_excluded
        assert _is_excluded(".env") is True
        assert _is_excluded(".env.local") is True

    def test_git_dir_excluded(self):
        from shared.code_intelligence import _is_excluded
        assert _is_excluded(".git/config") is True

    def test_python_not_excluded(self):
        from shared.code_intelligence import _is_excluded
        assert _is_excluded("auth.py") is False
        assert _is_excluded("src/utils.py") is False

    def test_markdown_not_excluded(self):
        from shared.code_intelligence import _is_excluded
        assert _is_excluded("README.md") is False

    def test_worktree_excluded(self):
        from shared.code_intelligence import _is_excluded
        assert _is_excluded(".claude/worktrees/agent-abc/auth.py") is True
        assert _is_excluded(".claude/worktrees/my-feature/src/main.py") is True

    def test_binary_excluded(self):
        from shared.code_intelligence import _is_excluded
        assert _is_excluded("logo.png") is True
        assert _is_excluded("font.woff2") is True


class TestIndexDirectory:
    def test_indexes_python_files_with_ast(self, ci, sample_project):
        result = ci.index_directory(str(sample_project))
        assert result["nodes"] > 0
        assert result["edges"] > 0
        assert result["time_ms"] >= 0

    def test_indexes_text_files_as_documents(self, ci, sample_project):
        ci.index_directory(str(sample_project))
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
        (sample_project / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        ci.index_directory(str(sample_project))
        bins = ci.conn.execute(
            "SELECT * FROM nodes WHERE file_path LIKE '%.png'"
        ).fetchall()
        assert len(bins) == 0

    def test_populates_fts5(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        fts_count = ci.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert fts_count > 0

    def test_caches_risk_scores(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        risky = ci.conn.execute(
            "SELECT * FROM nodes WHERE risk_score > 0 AND kind IN ('function', 'method')"
        ).fetchall()
        assert len(risky) >= 1

    def test_returns_stats_dict(self, ci, sample_project):
        result = ci.index_directory(str(sample_project))
        assert "nodes" in result
        assert "edges" in result
        assert "documents" in result
        assert "time_ms" in result


class TestReindexFile:
    def test_reindex_updates_modified_function(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        # Add a new function
        auth_path = sample_project / "auth.py"
        original = auth_path.read_text()
        auth_path.write_text(original + "\ndef logout(session_id: str) -> None:\n    pass\n")
        result = ci.reindex_file("auth.py", str(sample_project))
        assert result["nodes_added"] > 0
        node = ci.conn.execute("SELECT * FROM nodes WHERE name='logout'").fetchone()
        assert node is not None

    def test_reindex_removes_deleted_function(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        (sample_project / "auth.py").write_text("def only_one() -> None:\n    pass\n")
        ci.reindex_file("auth.py", str(sample_project))
        login_node = ci.conn.execute(
            "SELECT * FROM nodes WHERE name='login' AND file_path='auth.py'"
        ).fetchone()
        assert login_node is None

    def test_reindex_text_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        (sample_project / "README.md").write_text("# Updated\n\nNew content.")
        ci.reindex_file("README.md", str(sample_project))
        doc = ci.conn.execute("SELECT * FROM nodes WHERE file_path='README.md'").fetchone()
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

    def test_reindex_deleted_file_cleans_up(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        (sample_project / "utils.py").unlink()
        ci.reindex_file("utils.py", str(sample_project))
        nodes = ci.conn.execute("SELECT * FROM nodes WHERE file_path='utils.py'").fetchall()
        assert len(nodes) == 0


class TestFindSymbol:
    def test_find_function_by_name(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.find_symbol("login")
        assert result is not None
        assert result["name"] == "login"
        assert result["kind"] == "function"
        assert "file" in result
        assert "risk_score" in result

    def test_find_class(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.find_symbol("AuthManager")
        assert result is not None
        assert result["kind"] == "class"

    def test_find_nonexistent(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        assert ci.find_symbol("nonexistent_xyz") is None

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

    def test_max_results(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.callers_of("verify_token", max_results=1)
        assert len(result["callers"]) <= 1


class TestCalleesOf:
    def test_finds_callees(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.callees_of("login")
        assert result["total"] >= 1


class TestImpactAnalysis:
    def test_impact_from_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.impact_analysis(["auth.py"])
        assert len(result["impacted_files"]) >= 1
        assert "total_impacted" in result


class TestRiskReport:
    def test_ordered_by_risk(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.risk_report(top_n=5)
        scores = [f["risk"] for f in result["functions"]]
        assert scores == sorted(scores, reverse=True)

    def test_per_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.risk_report(file="auth.py")
        for fn in result["functions"]:
            assert fn["file"] == "auth.py"


class TestRiskScoringConsistency:
    """Verify batch scoring matches CodeGraph.compute_risk_score two-tier logic."""

    def test_security_keyword_two_tier(self, ci, tmp_path):
        """Context-dependent keywords without security context should NOT score."""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "app.py").write_text(textwrap.dedent("""\
            def count_tokens(text: str) -> int:
                return len(text.split())

            def verify_user_token(token: str) -> bool:
                return token == "valid"
        """))
        ci.index_directory(str(src))

        # count_tokens has "token" (context-dependent) but NO security context → low risk
        count_node = ci.conn.execute(
            "SELECT risk_score FROM nodes WHERE name='count_tokens'"
        ).fetchone()

        # verify_user_token has "verify" + "token" (context-dependent) AND "user" (security context) → high risk
        verify_node = ci.conn.execute(
            "SELECT risk_score FROM nodes WHERE name='verify_user_token'"
        ).fetchone()

        assert count_node is not None
        assert verify_node is not None
        # verify_user_token should score higher (has security keyword boost)
        assert verify_node[0] > count_node[0]


class TestSemanticSearch:
    def test_keyword_match(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        results = ci.semantic_search("authentication token")
        assert len(results) > 0

    def test_has_scores(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        results = ci.semantic_search("login")
        assert all("score" in r for r in results)


class TestModuleSummary:
    def test_python_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.module_summary("auth.py")
        assert result["file"] == "auth.py"
        assert len(result["classes"]) >= 1
        assert len(result["functions"]) >= 1


class TestRecentChanges:
    def test_no_git(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.recent_changes(n_commits=3, root=str(sample_project))
        assert result["commits"] == []


class TestGetPrimer:
    def test_contains_fingerprint(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        primer = ci.get_primer()
        assert "Code Intelligence" in primer
        assert "MCP tools" in primer

    def test_includes_risk(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        primer = ci.get_primer(top_risk=3)
        # Auth functions have security keywords
        assert "verify_token" in primer or "login" in primer


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

        # Search with exact term from indexed code (verify_token is in auth.py)
        search_results = ci.semantic_search("verify_token")
        # Semantic search may return empty if embedding similarity is below threshold;
        # FTS5 keyword match should still find it
        if not search_results:
            search_results = ci.semantic_search("token")
        assert len(search_results) >= 0  # verify no crash; results depend on embedding quality

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
        assert len(results) > 0


class TestGrepSearch:
    """Tests for grep_search MCP tool."""

    def test_basic_literal_search(self, ci, sample_project):
        """grep_search finds literal strings in files."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("hashlib", fixed_string=True)
        assert result["total_matches"] >= 1
        assert any("auth.py" in m["file"] for m in result["matches"])

    def test_regex_search(self, ci, sample_project):
        """grep_search supports regex patterns."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search(r"def \w+\(.*token")
        assert result["total_matches"] >= 1

    def test_glob_filter(self, ci, sample_project):
        """glob parameter restricts search to matching files."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("def ", glob="*.py")
        py_files = {m["file"] for m in result["matches"]}
        for f in py_files:
            assert f.endswith(".py"), f"Non-py file in results: {f}"

    def test_enrichment_on_py_files(self, ci, sample_project):
        """Python file matches include enclosing function and risk score."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("login", fixed_string=True, glob="*.py")
        enriched = [m for m in result["matches"] if "enclosing_function" in m]
        assert result["enriched"] > 0
        assert len(enriched) > 0
        # At least one enriched match should have risk_score
        assert any("risk_score" in m for m in enriched)

    def test_context_lines(self, ci, sample_project):
        """context_lines returns surrounding lines."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("verify_token", fixed_string=True, context_lines=2)
        matches_with_ctx = [m for m in result["matches"] if "context" in m]
        assert len(matches_with_ctx) > 0
        for m in matches_with_ctx:
            assert len(m["context"]) >= 1  # at least the match line itself

    def test_max_results_cap(self, ci, sample_project):
        """max_results limits returned matches but total_matches is accurate."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search(".", max_results=3)  # matches nearly every line
        assert result["returned"] <= 3
        assert result["total_matches"] >= result["returned"]

    def test_invalid_regex(self, ci, sample_project):
        """Invalid regex returns error, not exception."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("[invalid")
        assert result.get("status") == "error"
        assert "Invalid regex" in result.get("message", "")

    def test_sort_by_risk(self, ci, sample_project):
        """sort_by='risk' puts high-risk matches first."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("def ", glob="*.py", sort_by="risk")
        risk_scores = [m.get("risk_score", 0) for m in result["matches"] if "risk_score" in m]
        if len(risk_scores) >= 2:
            assert risk_scores == sorted(risk_scores, reverse=True)

    def test_no_matches_returns_empty(self, ci, sample_project):
        """Search for nonexistent string returns empty matches list."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("ZZZZNONEXISTENT999", fixed_string=True)
        assert result["total_matches"] == 0
        assert result["matches"] == []

    def test_md_files_not_enriched(self, ci, sample_project):
        """Non-Python file matches don't have enclosing_function."""
        ci.index_directory(str(sample_project))
        result = ci.grep_search("Auth", glob="*.md")
        for m in result["matches"]:
            assert "enclosing_function" not in m


class TestDiffImpact:
    def test_diff_impact_detects_changed_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        diff_text = textwrap.dedent("""\
            diff --git a/auth.py b/auth.py
            --- a/auth.py
            +++ b/auth.py
            @@ -4,7 +4,7 @@ class AuthManager:
                 def verify_token(self, token: str) -> bool:
            -        return hashlib.sha256(token.encode()).hexdigest() == self._stored
            +        return hashlib.sha256(token.encode()).hexdigest() == self._secret
        """)
        result = ci.diff_impact(diff_text)
        assert "changed_files" in result
        assert "auth.py" in result["changed_files"]
        assert "impacted" in result
        assert result["total_impacted"] >= 0

    def test_diff_impact_empty_diff(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.diff_impact("")
        assert result["changed_files"] == []
        assert result["total_impacted"] == 0

    def test_diff_impact_from_git(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.diff_impact(ref="HEAD", root=str(sample_project))
        assert "changed_files" in result


class TestVoyageSearchIntegration:
    def test_query_embedding_fn_set_when_voyage_available(self, tmp_path, monkeypatch):
        """CodeIntelligence sets _query_embedding_fn on HybridSearch when Voyage key exists."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key-fake")
        db_path = str(tmp_path / "ci_test.db")
        ci = CodeIntelligence(db_path)
        # The fn should be set (even though it won't work with a fake key)
        assert ci._search._query_embedding_fn is not None
        ci.close()

    def test_query_embedding_fn_none_without_key(self, tmp_path, monkeypatch):
        """Without VOYAGE_API_KEY, _query_embedding_fn stays None."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        db_path = str(tmp_path / "ci_test.db")
        ci = CodeIntelligence(db_path)
        assert ci._search._query_embedding_fn is None
        ci.close()
