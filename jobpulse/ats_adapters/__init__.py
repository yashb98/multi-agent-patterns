"""ATS adapter registry — extension-only mode.

All job applications route through ExtensionAdapter which uses the
Chrome extension via WebSocket for form filling.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Singleton extension adapter — created lazily
_ext_adapter: BaseATSAdapter | None = None


def _get_extension_adapter() -> BaseATSAdapter:
    """Lazily create, start the bridge server, and return the shared ExtensionAdapter.

    Starts the WebSocket bridge on a background thread using asyncio.run() so
    all event loop internals are properly initialized. Stores the loop reference
    on the bridge so cross-thread async calls can use asyncio.run_coroutine_threadsafe().
    """
    global _ext_adapter
    if _ext_adapter is None:
        from jobpulse.ext_adapter import ExtensionAdapter
        from jobpulse.ext_bridge import ExtensionBridge

        bridge = ExtensionBridge()

        _bridge_ready = threading.Event()

        async def _run_bridge_async() -> None:
            bridge._loop = asyncio.get_running_loop()  # type: ignore[attr-defined]
            try:
                await bridge.start()
            except OSError as exc:
                logger.warning("Bridge start failed (port in use?): %s", exc)
                _bridge_ready.set()
                return
            _bridge_ready.set()
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
            loop = getattr(bridge, "_loop", None)
            if loop is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    bridge.wait_for_connection(timeout=15), loop
                )
                try:
                    connected = fut.result(timeout=20)
                    if connected:
                        logger.info("Extension connected to bridge")
                    else:
                        logger.warning("Extension did not connect within 15s — commands may fail")
                except Exception as exc:
                    logger.warning("Error waiting for extension connection: %s", exc)

        _ext_adapter = ExtensionAdapter(bridge)
    return _ext_adapter


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the ExtensionAdapter (sole adapter).

    The ats_platform parameter is retained for interface compatibility
    but is not used for routing — all platforms go through the extension.
    """
    return _get_extension_adapter()


__all__ = ["BaseATSAdapter", "get_adapter"]
