"""ATS adapter registry.

When APPLICATION_ENGINE=extension, get_adapter() returns an ExtensionAdapter
that uses the Chrome extension via WebSocket instead of Playwright.
"""

from __future__ import annotations

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)
from jobpulse.ats_adapters.generic import GenericAdapter
from jobpulse.ats_adapters.greenhouse import GreenhouseAdapter
from jobpulse.ats_adapters.indeed import IndeedAdapter
from jobpulse.ats_adapters.lever import LeverAdapter
from jobpulse.ats_adapters.linkedin import LinkedInAdapter
from jobpulse.ats_adapters.workday import WorkdayAdapter

ADAPTERS: dict[str, BaseATSAdapter] = {
    "linkedin": LinkedInAdapter(),
    "indeed": IndeedAdapter(),
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "workday": WorkdayAdapter(),
    "generic": GenericAdapter(),
}

# Singleton extension adapter — created lazily when APPLICATION_ENGINE=extension
_ext_adapter: BaseATSAdapter | None = None


def _get_extension_adapter() -> BaseATSAdapter:
    """Lazily create, start the bridge server, and return the shared ExtensionAdapter.

    Starts the WebSocket bridge on a background thread using asyncio.run() (not
    manual loop creation) so all event loop internals are properly initialized.
    Stores the loop reference on the bridge so cross-thread async calls can use
    asyncio.run_coroutine_threadsafe() to dispatch work to the bridge loop.
    """
    global _ext_adapter
    if _ext_adapter is None:
        import asyncio
        import threading

        from jobpulse.ext_adapter import ExtensionAdapter
        from jobpulse.ext_bridge import ExtensionBridge

        bridge = ExtensionBridge()

        # Start the bridge server on a background thread via asyncio.run()
        # Using asyncio.run() (not new_event_loop+run_forever) ensures the loop
        # is fully initialized — fixes websockets handler not firing on threads.
        _bridge_ready = threading.Event()

        async def _run_bridge_async() -> None:
            # Store loop reference for cross-thread dispatch
            bridge._loop = asyncio.get_running_loop()  # type: ignore[attr-defined]
            try:
                await bridge.start()
            except OSError as exc:
                logger.warning("Bridge start failed (port in use?): %s", exc)
                _bridge_ready.set()
                return
            _bridge_ready.set()
            # Block forever — keeps the event loop alive for WebSocket handling
            await asyncio.Event().wait()

        def _run_bridge() -> None:
            try:
                asyncio.run(_run_bridge_async())
            except Exception as exc:
                logger.warning("Bridge thread exited: %s", exc)
                _bridge_ready.set()

        t = threading.Thread(target=_run_bridge, daemon=True, name="ext-bridge")
        t.start()
        _bridge_ready.wait(timeout=5)

        if bridge.connected:
            logger.info("Extension already connected to bridge")
        else:
            logger.info("Bridge started on ws://%s:%d — waiting for extension...", bridge._host, bridge.port)

        _ext_adapter = ExtensionAdapter(bridge)
    return _ext_adapter


def get_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Return the adapter for the given platform, or the generic fallback.

    When APPLICATION_ENGINE=extension, always returns the ExtensionAdapter
    regardless of platform (the extension handles all platforms via state machines).
    """
    from jobpulse.config import APPLICATION_ENGINE

    if APPLICATION_ENGINE == "extension":
        return _get_extension_adapter()

    if ats_platform and ats_platform in ADAPTERS:
        return ADAPTERS[ats_platform]
    return ADAPTERS["generic"]


__all__ = [
    "ADAPTERS",
    "BaseATSAdapter",
    "GenericAdapter",
    "GreenhouseAdapter",
    "IndeedAdapter",
    "LeverAdapter",
    "LinkedInAdapter",
    "WorkdayAdapter",
    "get_adapter",
]
