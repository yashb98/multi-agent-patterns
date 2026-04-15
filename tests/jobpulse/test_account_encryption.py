"""Tests for credential encryption in account_manager."""

import os
import sqlite3
import pytest
from unittest.mock import patch


def test_password_stored_encrypted(tmp_path, monkeypatch):
    """Password in DB must not be plaintext."""
    monkeypatch.setenv("ATS_ENCRYPTION_KEY", "test-key-for-encryption-32bytes!")
    db_path = str(tmp_path / "accounts.db")

    with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "s3cret!"):
        from jobpulse.account_manager import AccountManager
        mgr = AccountManager(db_path=db_path)
        mgr.create_account("example.com")

    # Read raw DB — password should NOT be plaintext
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT password FROM accounts WHERE domain='example.com'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] != "s3cret!"  # Must be encrypted


def test_password_decrypts_correctly(tmp_path, monkeypatch):
    """Retrieved password must match what was stored."""
    monkeypatch.setenv("ATS_ENCRYPTION_KEY", "test-key-for-encryption-32bytes!")
    db_path = str(tmp_path / "accounts.db")

    with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "s3cret!"):
        from jobpulse.account_manager import AccountManager
        mgr = AccountManager(db_path=db_path)
        mgr.create_account("example.com")
        _email, password = mgr.get_credentials("example.com")

    assert password == "s3cret!"


def test_no_encryption_key_raises(tmp_path, monkeypatch):
    """Missing ATS_ENCRYPTION_KEY must raise ValueError on credential operations."""
    monkeypatch.delenv("ATS_ENCRYPTION_KEY", raising=False)
    db_path = str(tmp_path / "accounts.db")

    with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "s3cret!"):
        from jobpulse.account_manager import AccountManager
        mgr = AccountManager(db_path=db_path)
        with pytest.raises(ValueError, match="ATS_ENCRYPTION_KEY"):
            mgr.create_account("example.com")
