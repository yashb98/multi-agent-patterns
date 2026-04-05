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
