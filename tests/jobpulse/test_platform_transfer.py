"""Tests for PlatformTransferEngine."""
from __future__ import annotations

import sqlite3

import pytest

from jobpulse.platform_transfer import PlatformTransferEngine


class TestSchema:
    def test_creates_tables(self, tmp_path):
        db = str(tmp_path / "transfer.db")
        PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "platform_similarity" in tables
        assert "transfer_outcomes" in tables
        conn.close()

    def test_idempotent_init(self, tmp_path):
        db = str(tmp_path / "transfer.db")
        PlatformTransferEngine(db_path=db)
        PlatformTransferEngine(db_path=db)  # No error on second init
