"""Tests for jobpulse.browser_cleanup — disk cleanup and restart logic."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.browser_cleanup import (
    APPS_BEFORE_RESTART,
    _CACHE_DIRS,
    _COLD_PURGE_DIRS,
    cleanup_chrome_profile_caches,
    deep_clean_chrome_profile,
    flush_browser_caches,
    reset_app_counter,
    should_restart_chrome,
)


@pytest.fixture(autouse=True)
def _reset_counter():
    reset_app_counter()
    yield
    reset_app_counter()


@pytest.fixture()
def fake_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("jobpulse.browser_cleanup.CHROME_PROFILE_DIR", tmp_path)
    for d in _CACHE_DIRS + _COLD_PURGE_DIRS:
        p = tmp_path / d
        p.mkdir(parents=True, exist_ok=True)
        (p / "blob.dat").write_bytes(b"x" * 1024)
    return tmp_path


class TestFlushBrowserCaches:
    def test_returns_results_on_success(self):
        cdp = AsyncMock()
        cdp.send = AsyncMock(return_value={})
        cdp.detach = AsyncMock()

        page = MagicMock()
        page.url = "https://example.com"
        page.context.new_cdp_session = AsyncMock(return_value=cdp)

        result = asyncio.get_event_loop().run_until_complete(
            flush_browser_caches(page)
        )
        assert result["clear_cache"] is True
        assert result["gc"] is True

    def test_handles_cdp_session_failure(self):
        page = MagicMock()
        page.context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no CDP"))

        result = asyncio.get_event_loop().run_until_complete(
            flush_browser_caches(page)
        )
        assert result == {"cdp_session": False}


class TestCleanupChromeProfileCaches:
    def test_deletes_cache_dirs(self, fake_profile):
        freed = cleanup_chrome_profile_caches()
        assert freed > 0
        for d in _CACHE_DIRS:
            assert not (fake_profile / d).exists()

    def test_cold_dirs_preserved(self, fake_profile):
        cleanup_chrome_profile_caches()
        for d in _COLD_PURGE_DIRS:
            assert (fake_profile / d).exists()

    def test_noop_on_missing_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jobpulse.browser_cleanup.CHROME_PROFILE_DIR", tmp_path)
        assert cleanup_chrome_profile_caches() == 0


class TestDeepClean:
    def test_deletes_all_dirs(self, fake_profile):
        freed = deep_clean_chrome_profile()
        assert freed > 0
        for d in _CACHE_DIRS + _COLD_PURGE_DIRS:
            assert not (fake_profile / d).exists()


class TestRestartCounter:
    def test_triggers_every_n_apps(self):
        for i in range(1, APPS_BEFORE_RESTART):
            assert should_restart_chrome() is False
        assert should_restart_chrome() is True

    def test_reset_restarts_cycle(self):
        for _ in range(APPS_BEFORE_RESTART - 1):
            should_restart_chrome()
        reset_app_counter()
        assert should_restart_chrome() is False
