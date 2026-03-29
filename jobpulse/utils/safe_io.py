"""Shared I/O utilities — browser lifecycle, OpenAI calls, file locking, SQLite atomicity."""

from __future__ import annotations

import contextlib
from typing import Any, Generator

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. managed_browser — guaranteed browser cleanup
# ---------------------------------------------------------------------------

def _import_playwright() -> Any:
    """Lazy import to avoid hard dependency."""
    from playwright.sync_api import sync_playwright  # type: ignore[import]
    return sync_playwright


@contextlib.contextmanager
def managed_browser(
    headless: bool = True,
    **launch_args: Any,
) -> Generator[tuple[Any, Any], None, None]:
    """Context manager that guarantees browser.close() even on exception.

    Yields (browser, page) tuple.
    """
    sync_playwright = _import_playwright()
    browser = None
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless, **launch_args)
            page = browser.new_page()
            yield browser, page
        finally:
            if browser:
                with contextlib.suppress(Exception):
                    browser.close()
                logger.debug("managed_browser: browser closed")


@contextlib.contextmanager
def managed_persistent_browser(
    user_data_dir: str,
    **launch_args: Any,
) -> Generator[tuple[Any, Any], None, None]:
    """Context manager for persistent browser contexts (e.g. LinkedIn with saved cookies).

    Yields (context, page) — context IS the browser for persistent contexts.
    """
    sync_playwright = _import_playwright()
    context = None
    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(user_data_dir, **launch_args)
            page = context.new_page()
            yield context, page
        finally:
            if context:
                with contextlib.suppress(Exception):
                    context.close()
                logger.debug("managed_persistent_browser: context closed")
