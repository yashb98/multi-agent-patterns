"""Shared fixtures for the multi-agent test suite.

SAFETY: Sets JOBPULSE_TEST_MODE=1 so storage modules can guard against
accidentally writing to production databases during test runs.
"""

import sys
import os
from pathlib import Path

# Add multi_agent_patterns root to sys.path so jobpulse/shared are importable
# regardless of which directory pytest is invoked from.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
import sqlite3
from unittest.mock import patch, MagicMock

# Global safety flag — set before ANY imports touch storage modules
os.environ["JOBPULSE_TEST_MODE"] = "1"


def pytest_configure(config):
    config.addinivalue_line("markers", "live: requires live API access (skipped by default, run with -m live)")
    config.addinivalue_line("markers", "slow: marks tests as slow-running")


@pytest.fixture(autouse=True)
def isolate_optimization_db(monkeypatch, tmp_path):
    """Redirect every `get_optimization_engine()` write to a per-test tmp DB.

    The S6 T-1 / S10 T-10.1 audit found that `data/optimization.db` had
    47-54% of its `cognitive_outcomes` rows under `agent_name='test_agent'`
    — tests were leaking into production via the singleton. Cognitive
    consumers (`shared/cognitive/_engine.py:155, 174`) call
    `get_optimization_engine().record_cognitive_outcome(...)`, and the
    cached singleton points at the default DB path.

    This fixture:
      1. Sets `OPTIMIZATION_DB` so `_default_db_path()` returns tmp_path.
      2. Resets `_shared_engine = None` BEFORE the test, so the next
         lazy build picks up the env var.
      3. Resets again AFTER, so tests that run after this one don't
         inherit the tmp engine (or accidentally rebuild against
         production).
    """
    from shared.optimization import _engine as _opt_engine

    monkeypatch.setenv("OPTIMIZATION_DB", str(tmp_path / "optimization.db"))
    monkeypatch.setattr(_opt_engine, "_shared_engine", None)
    yield
    _opt_engine._shared_engine = None


def pytest_collection_modifyitems(config, items):
    if not config.getoption("-m", default=""):
        skip_live = pytest.mark.skip(reason="live tests require -m live")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)


@pytest.fixture
def mock_openai():
    """Mock OpenAI client for LLM calls."""
    with patch("openai.OpenAI") as mock:
        client = MagicMock()
        mock.return_value = client
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "UNKNOWN"
        client.chat.completions.create.return_value = response
        yield client


@pytest.fixture
def in_memory_db():
    """In-memory SQLite database for tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def mock_telegram():
    """Mock telegram send_message."""
    with patch("jobpulse.telegram_agent.send_message") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def mock_event_logger():
    """Mock event_logger.log_event to prevent DB writes during tests."""
    with patch("jobpulse.event_logger.log_event") as mock:
        yield mock


@pytest.fixture
def mock_process_trail():
    """Mock ProcessTrail so tests don't write to mindgraph.db."""
    with patch("jobpulse.process_logger.ProcessTrail") as mock:
        trail = MagicMock()
        mock.return_value = trail
        # Make the context manager step() work
        step_ctx = MagicMock()
        step_ctx.__enter__ = MagicMock(return_value={})
        step_ctx.__exit__ = MagicMock(return_value=False)
        trail.step.return_value = step_ctx
        yield trail
