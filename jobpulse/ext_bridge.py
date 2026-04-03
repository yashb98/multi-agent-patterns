"""WebSocket server bridging Python backend and Chrome extension."""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve

from jobpulse.ext_models import (
    ExtCommand,
    FillResult,
    PageSnapshot,
)
from shared.logging_config import get_logger

logger = get_logger(__name__)


class ExtensionBridge:
    """WebSocket server that communicates with the Chrome extension."""

    def __init__(self, host: str = "localhost", port: int = 8765) -> None:
        self._host = host
        self._requested_port = port
        self.port: int = port
        self._server: Server | None = None
        self._ws: ServerConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._snapshot: PageSnapshot | None = None
        self._connected: asyncio.Event = asyncio.Event()

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await serve(
            self._handler,
            self._host,
            self._requested_port,
        )
        # Resolve actual port — prefer IPv4 socket (AF_INET, family=2)
        # When port=0, OS assigns ports independently per socket family.
        for sock in self._server.sockets:
            if sock.family == socket.AF_INET:
                self.port = sock.getsockname()[1]
                break
        else:
            # Fallback: use first available socket
            for sock in self._server.sockets:
                self.port = sock.getsockname()[1]
                break
        logger.info("Extension bridge listening on ws://%s:%d", self._host, self.port)

    async def stop(self) -> None:
        """Gracefully close connection and server."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._connected.clear()
        # Cancel all pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        logger.info("Extension bridge stopped")

    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Block until extension connects or timeout elapses."""
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def connected(self) -> bool:
        """Whether the extension is currently connected."""
        return self._ws is not None and self._connected.is_set()

    async def _handler(self, ws: ServerConnection) -> None:
        """Handle a single WebSocket connection from the extension."""
        self._ws = ws
        self._connected.set()
        logger.info("Extension connected from %s", ws.remote_address)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from extension: %s", str(raw)[:100])
                    continue

                msg_type: str = msg.get("type", "")
                msg_id: str = msg.get("id", "")

                # Ping/pong keepalive
                if msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue

                # Mutation/navigation events — update cached snapshot
                if msg_type in ("mutation", "navigation"):
                    snap_data = msg.get("payload", {}).get("snapshot")
                    if snap_data:
                        self._snapshot = PageSnapshot(**snap_data)
                    continue

                # Response to a pending command (ack or result)
                if msg_id and msg_id in self._pending:
                    # Only resolve on "result" — "ack" is informational
                    if msg_type == "result":
                        fut = self._pending.pop(msg_id)
                        if not fut.done():
                            fut.set_result(msg.get("payload", {}))
                    continue

                logger.debug("Unhandled message type=%s id=%s", msg_type, msg_id)

        except websockets.exceptions.ConnectionClosed:
            logger.info("Extension disconnected")
        finally:
            self._ws = None
            self._connected.clear()

    async def _send_command(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Send a command to the extension and wait for the result payload."""
        if not self.connected:
            raise ConnectionError("Extension not connected")

        cmd_id = str(uuid.uuid4())
        cmd = ExtCommand(id=cmd_id, action=action, payload=payload or {})  # type: ignore[arg-type]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[cmd_id] = fut

        assert self._ws is not None
        await self._ws.send(cmd.model_dump_json())

        try:
            result = await asyncio.wait_for(fut, timeout=timeout_ms / 1000)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise

    async def navigate(self, url: str, timeout_ms: int = 30000) -> PageSnapshot:
        """Navigate to URL and return a PageSnapshot."""
        result = await self._send_command(
            "navigate", {"url": url}, timeout_ms=timeout_ms
        )
        snap_data = result.get("snapshot")
        if snap_data:
            self._snapshot = PageSnapshot(**snap_data)
        if self._snapshot is None:
            raise RuntimeError("No snapshot received after navigation")
        return self._snapshot

    async def fill(self, selector: str, value: str, timeout_ms: int = 10000) -> FillResult:
        """Fill a form field and return the result."""
        result = await self._send_command(
            "fill", {"selector": selector, "value": value}, timeout_ms=timeout_ms
        )
        return FillResult(**result)

    async def click(self, selector: str, timeout_ms: int = 10000) -> bool:
        """Click an element."""
        result = await self._send_command(
            "click", {"selector": selector}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def upload(self, selector: str, file_path: Path, timeout_ms: int = 30000) -> bool:
        """Base64-encode a file and send it to the extension for DataTransfer upload."""
        import base64
        import mimetypes

        data = file_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        mime = mimetypes.guess_type(str(file_path))[0] or "application/pdf"

        result = await self._send_command(
            "upload",
            {
                "selector": selector,
                "file_base64": b64,
                "file_name": file_path.name,
                "mime_type": mime,
            },
            timeout_ms=timeout_ms,
        )
        return bool(result.get("success", False))

    async def select_option(self, selector: str, value: str, timeout_ms: int = 10000) -> bool:
        """Select a dropdown option by value."""
        result = await self._send_command(
            "select", {"selector": selector, "value": value}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def check(self, selector: str, should_check: bool, timeout_ms: int = 10000) -> bool:
        """Check or uncheck a checkbox."""
        result = await self._send_command(
            "check",
            {"selector": selector, "value": str(should_check).lower()},
            timeout_ms=timeout_ms,
        )
        return bool(result.get("success", False))

    async def screenshot(self, timeout_ms: int = 10000) -> bytes:
        """Request a screenshot from the extension (returns PNG bytes)."""
        import base64

        result = await self._send_command("screenshot", timeout_ms=timeout_ms)
        b64 = result.get("data", "")
        return base64.b64decode(b64)

    async def get_snapshot(self) -> PageSnapshot | None:
        """Return the latest cached page snapshot."""
        return self._snapshot
