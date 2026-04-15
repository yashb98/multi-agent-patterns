"""Tests for browser tool path safety."""

import os
import pytest


def test_screenshot_rejects_path_traversal():
    """Screenshot output_path must not allow path traversal."""
    from shared.tools.browser import BrowserTool

    result = BrowserTool.execute("screenshot", {
        "url": "https://example.com",
        "output_path": "/etc/cron.d/evil.png",
    })
    assert result["status"] == "error"
    assert "outside" in result["message"].lower() or "invalid" in result["message"].lower()


def test_screenshot_rejects_relative_traversal():
    """Relative path traversal must be blocked."""
    from shared.tools.browser import BrowserTool

    result = BrowserTool.execute("screenshot", {
        "url": "https://example.com",
        "output_path": "../../etc/passwd",
    })
    assert result["status"] == "error"


def test_screenshot_allows_tmp_path():
    """Paths under /tmp/ are allowed."""
    from shared.tools.browser import BrowserTool

    result = BrowserTool.execute("screenshot", {
        "url": "https://example.com",
        "output_path": "/tmp/test_screenshot.png",
    })
    # Either success (playwright installed) or playwright import error — NOT a path error
    if result["status"] == "error":
        assert "outside" not in result["message"].lower()
