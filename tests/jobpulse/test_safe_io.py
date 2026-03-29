"""Tests for jobpulse/utils/safe_io.py utilities."""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure multi_agent_patterns root is on sys.path before any jobpulse imports
_ROOT = Path(__file__).parent.parent.parent  # tests/jobpulse -> tests -> multi_agent_patterns
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("JOBPULSE_TEST_MODE", "1")

# Import the module under test so patch() can resolve 'jobpulse.utils.safe_io'
import jobpulse.utils.safe_io as _safe_io_module  # noqa: E402, F401


def _make_pw_mock():
    """Build a mock playwright stack: sync_playwright() → pw → browser/page."""
    mock_pw = MagicMock()
    mock_browser = MagicMock()
    mock_page = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page

    # sync_playwright() is used as a context manager: `with sync_playwright() as pw`
    mock_sp_instance = MagicMock()
    mock_sp_instance.__enter__ = MagicMock(return_value=mock_pw)
    mock_sp_instance.__exit__ = MagicMock(return_value=False)

    mock_sp = MagicMock(return_value=mock_sp_instance)
    return mock_sp, mock_browser, mock_page


def test_managed_browser_closes_on_success():
    """Browser is closed even when body succeeds."""
    mock_sp, mock_browser, mock_page = _make_pw_mock()

    with patch("jobpulse.utils.safe_io._import_playwright", return_value=mock_sp):
        from jobpulse.utils.safe_io import managed_browser

        with managed_browser() as (browser, page):
            assert browser is mock_browser
            assert page is mock_page

    mock_browser.close.assert_called_once()


def test_managed_browser_closes_on_exception():
    """Browser is closed even when body raises."""
    mock_sp, mock_browser, mock_page = _make_pw_mock()

    with patch("jobpulse.utils.safe_io._import_playwright", return_value=mock_sp):
        from jobpulse.utils.safe_io import managed_browser

        try:
            with managed_browser() as (browser, page):
                raise RuntimeError("test crash")
        except RuntimeError:
            pass

    mock_browser.close.assert_called_once()


def test_managed_browser_close_exception_suppressed():
    """If browser.close() itself raises, the exception is suppressed."""
    mock_sp, mock_browser, mock_page = _make_pw_mock()
    mock_browser.close.side_effect = Exception("close failed")

    with patch("jobpulse.utils.safe_io._import_playwright", return_value=mock_sp):
        from jobpulse.utils.safe_io import managed_browser

        # Should not propagate the close() exception
        with managed_browser() as (browser, page):
            pass  # normal success path


def test_managed_persistent_browser_closes_on_success():
    """Persistent context is closed even when body succeeds."""
    mock_pw = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_pw.chromium.launch_persistent_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_sp_instance = MagicMock()
    mock_sp_instance.__enter__ = MagicMock(return_value=mock_pw)
    mock_sp_instance.__exit__ = MagicMock(return_value=False)
    mock_sp = MagicMock(return_value=mock_sp_instance)

    with patch("jobpulse.utils.safe_io._import_playwright", return_value=mock_sp):
        from jobpulse.utils.safe_io import managed_persistent_browser

        with managed_persistent_browser("/tmp/profile") as (context, page):
            assert context is mock_context
            assert page is mock_page

    mock_context.close.assert_called_once()


def test_managed_persistent_browser_closes_on_exception():
    """Persistent context is closed even when body raises."""
    mock_pw = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_pw.chromium.launch_persistent_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_sp_instance = MagicMock()
    mock_sp_instance.__enter__ = MagicMock(return_value=mock_pw)
    mock_sp_instance.__exit__ = MagicMock(return_value=False)
    mock_sp = MagicMock(return_value=mock_sp_instance)

    with patch("jobpulse.utils.safe_io._import_playwright", return_value=mock_sp):
        from jobpulse.utils.safe_io import managed_persistent_browser

        try:
            with managed_persistent_browser("/tmp/profile") as (context, page):
                raise RuntimeError("test crash")
        except RuntimeError:
            pass

    mock_context.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for safe_openai_call
# ---------------------------------------------------------------------------


def test_safe_openai_call_returns_content():
    """Returns content string on successful API call."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello world"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result == "Hello world"


def test_safe_openai_call_returns_none_on_none_content():
    """Returns None when API returns None content."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result is None


def test_safe_openai_call_returns_none_on_exception():
    """Returns None when API raises an exception."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("API timeout")

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result is None


def test_safe_openai_call_returns_none_on_empty_choices():
    """Returns None when API returns empty choices list."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[])

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result is None


# ---------------------------------------------------------------------------
# Tests for locked_json_file
# ---------------------------------------------------------------------------

import json
import sqlite3


def test_locked_json_file_reads_and_writes(tmp_path):
    """Reads existing JSON, allows mutation, writes back."""
    from jobpulse.utils.safe_io import locked_json_file

    json_file = tmp_path / "test.json"
    json_file.write_text('[{"id": 1}]')

    with locked_json_file(json_file) as data:
        assert data == [{"id": 1}]
        data.append({"id": 2})

    result = json.loads(json_file.read_text())
    assert result == [{"id": 1}, {"id": 2}]


def test_locked_json_file_creates_file_if_missing(tmp_path):
    """Creates file with default value if it doesn't exist."""
    from jobpulse.utils.safe_io import locked_json_file

    json_file = tmp_path / "new.json"
    assert not json_file.exists()

    with locked_json_file(json_file, default=[]) as data:
        data.append("hello")

    assert json_file.exists()
    assert json.loads(json_file.read_text()) == ["hello"]


def test_locked_json_file_no_write_on_exception(tmp_path):
    """Does NOT write back if body raises an exception."""
    from jobpulse.utils.safe_io import locked_json_file

    json_file = tmp_path / "test.json"
    json_file.write_text('[1, 2, 3]')

    try:
        with locked_json_file(json_file) as data:
            data.append(4)
            raise ValueError("abort!")
    except ValueError:
        pass

    assert json.loads(json_file.read_text()) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Tests for atomic_sqlite
# ---------------------------------------------------------------------------


def test_atomic_sqlite_commits_on_success(tmp_path):
    """Transaction commits when body succeeds."""
    from jobpulse.utils.safe_io import atomic_sqlite

    db_path = str(tmp_path / "test.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")

    with atomic_sqlite(db_path) as conn:
        conn.execute("INSERT INTO t VALUES (1)")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM t").fetchone()
        assert row == (1,)


def test_atomic_sqlite_rolls_back_on_exception(tmp_path):
    """Transaction rolls back when body raises."""
    from jobpulse.utils.safe_io import atomic_sqlite

    db_path = str(tmp_path / "test.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")

    try:
        with atomic_sqlite(db_path) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("abort!")
    except RuntimeError:
        pass

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert row == (0,)
