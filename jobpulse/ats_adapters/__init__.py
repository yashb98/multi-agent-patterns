"""ATS adapter registry — extension-only mode.

All job applications route through ExtensionAdapter which uses the
Chrome extension via WebSocket for form filling.

When an ext-bridge server is already running (port in use), a RelayBridge
client is created instead — it connects as a WS client to the running
bridge and forwards commands via the relay protocol.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Singleton extension adapter — created lazily
_ext_adapter: BaseATSAdapter | None = None


def _is_port_in_use(host: str, port: int) -> bool:
    """Check if a port is already bound (bridge already running)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _get_extension_adapter() -> BaseATSAdapter:
    """Lazily create and return the shared ExtensionAdapter.

    If the bridge server is already running (port in use), connects via
    RelayBridge (WS client). Otherwise starts a new bridge server on a
    background thread.
    """
    global _ext_adapter
    if _ext_adapter is None:
        from jobpulse.config import EXT_BRIDGE_HOST, EXT_BRIDGE_PORT
        from jobpulse.ext_adapter import ExtensionAdapter

        if _is_port_in_use(EXT_BRIDGE_HOST, EXT_BRIDGE_PORT):
            # Bridge already running — connect as relay client
            logger.info(
                "Bridge already running on ws://%s:%d — connecting as relay client",
                EXT_BRIDGE_HOST,
                EXT_BRIDGE_PORT,
            )
            from jobpulse.relay_bridge import RelayBridge

            relay = RelayBridge(host=EXT_BRIDGE_HOST, port=EXT_BRIDGE_PORT)

            # Start relay on a background thread with its own event loop
            _relay_ready = threading.Event()
            _relay_loop: asyncio.AbstractEventLoop | None = None

            async def _connect_relay() -> None:
                nonlocal _relay_loop
                _relay_loop = asyncio.get_running_loop()
                relay._loop = _relay_loop  # type: ignore[attr-defined]
                connected = await relay.connect(timeout=10)
                if connected:
                    logger.info("RelayBridge connected — extension=%s", "yes" if relay.connected else "no")
                else:
                    logger.warning("RelayBridge failed to connect to bridge")
                _relay_ready.set()
                await asyncio.Event().wait()  # Keep loop alive

            def _run_relay() -> None:
                try:
                    asyncio.run(_connect_relay())
                except Exception as exc:
                    logger.warning("Relay thread exited: %s", exc)
                    _relay_ready.set()

            t = threading.Thread(target=_run_relay, daemon=True, name="relay-bridge")
            t.start()
            _relay_ready.wait(timeout=15)

            _ext_adapter = ExtensionAdapter(relay)  # type: ignore[arg-type]
        else:
            # Start new bridge server
            from jobpulse.ext_bridge import ExtensionBridge

            bridge = ExtensionBridge(host=EXT_BRIDGE_HOST, port=EXT_BRIDGE_PORT)

            _bridge_ready = threading.Event()

            async def _run_bridge_async() -> None:
                bridge._loop = asyncio.get_running_loop()  # type: ignore[attr-defined]
                try:
                    await bridge.start()
                except OSError as exc:
                    logger.warning("Bridge start failed: %s", exc)
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
