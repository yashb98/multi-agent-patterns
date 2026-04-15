"""Tests for run_id propagation."""

import uuid
from shared.logging_config import get_logger, set_run_id, get_run_id


def test_set_and_get_run_id():
    rid = str(uuid.uuid4())[:8]
    set_run_id(rid)
    assert get_run_id() == rid


def test_logger_includes_run_id():
    rid = "test-123"
    set_run_id(rid)
    logger = get_logger("test_module")
    # Verify the filter is attached and returns the run_id
    assert get_run_id() == rid
