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
