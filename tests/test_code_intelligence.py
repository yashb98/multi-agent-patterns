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
