"""Tests for AccountManager — ATS platform credential store."""

import pytest
from unittest.mock import patch
from jobpulse.account_manager import AccountManager


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_ENCRYPTION_KEY", "test-key-for-encryption-32bytes!")
    with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "TestPass123!"):
        yield AccountManager(db_path=str(tmp_path / "accounts.db"))


def test_no_account_initially(mgr):
    assert mgr.has_account("greenhouse.io") is False


def test_create_and_retrieve(mgr):
    email, password = mgr.create_account("greenhouse.io")
    assert email == "bishnoiyash274@gmail.com"
    assert password == "TestPass123!"
    assert mgr.has_account("greenhouse.io") is True


def test_get_credentials(mgr):
    mgr.create_account("greenhouse.io")
    email, password = mgr.get_credentials("greenhouse.io")
    assert email == "bishnoiyash274@gmail.com"
    assert password == "TestPass123!"


def test_mark_verified(mgr):
    mgr.create_account("greenhouse.io")
    mgr.mark_verified("greenhouse.io")
    info = mgr.get_account_info("greenhouse.io")
    assert info.verified is True


def test_domain_normalization_from_url(mgr):
    mgr.create_account("https://boards.greenhouse.io/acme/jobs/123")
    assert mgr.has_account("boards.greenhouse.io") is True
    assert mgr.has_account("https://boards.greenhouse.io/other") is True


def test_domain_normalization_strips_www(mgr):
    mgr.create_account("www.example.com")
    assert mgr.has_account("example.com") is True


def test_duplicate_create_returns_existing(mgr):
    e1, p1 = mgr.create_account("greenhouse.io")
    e2, p2 = mgr.create_account("greenhouse.io")
    assert e1 == e2 and p1 == p2


def test_mark_login_success(mgr):
    mgr.create_account("greenhouse.io")
    mgr.mark_login_success("greenhouse.io")
    info = mgr.get_account_info("greenhouse.io")
    assert info.last_login != ""


def test_no_password_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ATS_ENCRYPTION_KEY", "test-key-for-encryption-32bytes!")
    with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", ""):
        mgr = AccountManager(db_path=str(tmp_path / "accounts.db"))
        with pytest.raises(ValueError, match="ATS_ACCOUNT_PASSWORD"):
            mgr.create_account("example.com")
