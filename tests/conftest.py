"""Shared fixtures for the multi-agent test suite.

SAFETY: Sets JOBPULSE_TEST_MODE=1 so storage modules can guard against
accidentally writing to production databases during test runs.
"""

import pytest
import sqlite3
import os
from unittest.mock import patch, MagicMock

# Global safety flag — set before ANY imports touch storage modules
os.environ["JOBPULSE_TEST_MODE"] = "1"


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
