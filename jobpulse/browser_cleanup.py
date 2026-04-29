"""Browser memory and cache cleanup between applications.

Chrome's memory model is grow-only — closed tabs free some memory but
Chrome retains allocated pools.  After 10-15 applications across heavy
ATS sites (LinkedIn, Greenhouse, Workday), Chrome alone can sit at
6-10 GB RSS.  The dedicated Playwright profile (~/.chrome-playwright-profile)
also balloons on disk: Chrome auto-downloads a 4 GB on-device AI model
(OptGuideOnDeviceModel) plus HTTP/code/GPU caches per ATS site.

Three levels of cleanup:
  1. flush_browser_caches(page) — CDP commands to clear caches, Service
     Workers, and force GC.  ~200 ms, called after every application.
  2. cleanup_chrome_profile_caches() — delete expendable dirs from the
     profile on disk.  Safe while Chrome runs.  Called after every app
     during the anti-detection delay.
  3. restart_chrome_if_needed() — full kill + disk purge + relaunch every
     N applications.  ~5 s, the only way to reclaim pooled renderer RAM.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

CHROME_PROFILE_DIR = Path(os.path.expanduser("~/.chrome-playwright-profile"))
APPS_BEFORE_RESTART = 8

# Dirs safe to delete while Chrome is running (recreated on demand).
_CACHE_DIRS = [
    "Default/Service Worker/CacheStorage",
    "Default/Code Cache",
    "Default/Cache",
    "Default/GPUCache",
    "GraphiteDawnCache",
    "BrowserMetrics",
]

# Dirs safe to delete only when Chrome is NOT running (large, inert blobs).
_COLD_PURGE_DIRS = [
    "OptGuideOnDeviceModel",
    "optimization_guide_model_store",
    "WasmTtsEngine",
    "OnDeviceHeadSuggestModel",
    "extensions_crx_cache",
    "component_crx_cache",
]


async def flush_browser_caches(page) -> dict[str, bool]:
    """Send CDP commands to reclaim memory on the running Chrome instance.

    Call right before closing a Playwright page (CDP session still alive).
    """
    results: dict[str, bool] = {}

    try:
        cdp = await page.context.new_cdp_session(page)
    except Exception as exc:
        logger.debug("flush_browser_caches: CDP session failed: %s", exc)
        return {"cdp_session": False}

    try:
        await cdp.send("Network.clearBrowserCache")
        results["clear_cache"] = True
    except Exception as exc:
        logger.debug("flush: Network.clearBrowserCache: %s", exc)
        results["clear_cache"] = False

    try:
        await cdp.send("HeapProfiler.collectGarbage")
        results["gc"] = True
    except Exception as exc:
        logger.debug("flush: HeapProfiler.collectGarbage: %s", exc)
        results["gc"] = False

    try:
        await cdp.send("ServiceWorker.enable")
        results["sw_enabled"] = True
    except Exception:
        results["sw_enabled"] = False

    try:
        await cdp.send("Storage.clearDataForOrigin", {
            "origin": page.url,
            "storageTypes": "service_workers,cache_storage",
        })
        results["clear_sw_storage"] = True
    except Exception as exc:
        logger.debug("flush: Storage.clearDataForOrigin: %s", exc)
        results["clear_sw_storage"] = False

    try:
        await cdp.detach()
    except Exception:
        pass

    logger.info("flush_browser_caches: %s", results)
    return results


def _purge_dirs(dir_list: list[str]) -> int:
    """Remove directories from CHROME_PROFILE_DIR, return bytes freed."""
    freed = 0
    for rel in dir_list:
        target = CHROME_PROFILE_DIR / rel
        if not target.exists():
            continue
        try:
            size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            shutil.rmtree(target)
            freed += size
        except Exception as exc:
            logger.debug("_purge_dirs: %s: %s", rel, exc)
    return freed


def cleanup_chrome_profile_caches() -> int:
    """Delete expendable cache directories from the Chrome profile on disk.

    Safe to call while Chrome is running — Chrome recreates these dirs
    on demand.  Returns bytes freed.
    """
    freed = _purge_dirs(_CACHE_DIRS)
    if freed:
        logger.info("cleanup_chrome_profile_caches: freed %d MB", freed >> 20)
    return freed


def deep_clean_chrome_profile() -> int:
    """Full purge including Chrome's on-device AI models (~4 GB).

    Only call when Chrome is NOT running (before restart).
    """
    freed = _purge_dirs(_CACHE_DIRS + _COLD_PURGE_DIRS)
    if freed:
        logger.info("deep_clean_chrome_profile: freed %d MB", freed >> 20)
    return freed


_app_counter: int = 0


def should_restart_chrome() -> bool:
    """Return True every APPS_BEFORE_RESTART applications."""
    global _app_counter  # noqa: PLW0603
    _app_counter += 1
    return _app_counter % APPS_BEFORE_RESTART == 0


def reset_app_counter() -> None:
    """Reset counter (e.g. at start of a scan window)."""
    global _app_counter  # noqa: PLW0603
    _app_counter = 0


def restart_chrome() -> None:
    """Kill Chrome, deep-clean profile (AI models + caches), relaunch.

    Delegates to PlaywrightDriver's existing restart logic so the CDP
    port and profile dir stay consistent.
    """
    from jobpulse.playwright_driver import CDP_URL, _restart_cdp_chrome

    deep_clean_chrome_profile()
    _restart_cdp_chrome(CDP_URL)
    logger.info("restart_chrome: Chrome restarted and profile cleaned")
