import pytest
from pathlib import Path


@pytest.fixture
def baseline_db_path(tmp_path):
    return str(tmp_path / "eval_baselines.db")
