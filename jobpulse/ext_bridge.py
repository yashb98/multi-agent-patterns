"""WebSocket server bridging the Python backend and Chrome extension.

Architecture:
    Python Backend  <--WebSocket-->  Chrome Extension (MV3)
    ext_bridge.py                    background.js + content.js

The bridge is a WebSocket SERVER. The extension connects as a CLIENT.
Only one extension connection at a time — reconnections are handled
gracefully (MV3 service workers restart during navigation).

Command flow:
    1. Python calls bridge.click(selector) / bridge.fill(selector, value) / etc.
    2. Bridge sends JSON command {id, action, payload} to extension
    3. Extension executes action and sends {id, type:"result", payload} back
    4. Bridge resolves the asyncio.Future with the result

Snapshot flow (passive):
    1. Content script detects DOM mutations or page navigation
    2. Extension sends {type:"mutation"/"navigation", payload:{snapshot}} to bridge
    3. Bridge updates self._snapshot cache (used by get_snapshot())
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import websockets
from shared.logging_config import get_logger
from websockets.asyncio.server import Server, ServerConnection, serve

from jobpulse.ext_models import (
    ExtCommand,
    FillResult,
    PageSnapshot,
)

logger = get_logger(__name__)


class ExtensionBridge:
    """WebSocket server that communicates with the Chrome extension.

    Usage:
        bridge = ExtensionBridge()
        await bridge.start()
        await bridge.wait_for_connection()
        snapshot = await bridge.navigate("https://example.com")
        await bridge.fill("#email", "user@example.com")
        await bridge.click("#submit")
        await bridge.stop()
    """

    def __init__(self, host: str = "localhost", port: int = 8765) -> None:
        self._host = host
        self._requested_port = port
        self.port: int = port
        self._server: Server | None = None
        self._ws: ServerConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._snapshot: PageSnapshot | None = None
        self._connected: asyncio.Event = asyncio.Event()

    # ─── Server lifecycle ────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await serve(self._handler, self._host, self._requested_port)
        # Resolve actual port (prefer IPv4 when port=0)
        for sock in self._server.sockets:
            if sock.family == socket.AF_INET:
                self.port = sock.getsockname()[1]
                break
        else:
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
        except TimeoutError:
            return False

    @property
    def connected(self) -> bool:
        """Whether the extension is currently connected."""
        return self._ws is not None and self._connected.is_set()

    # ─── WebSocket handler ───────────────────────────────────────

    async def _handler(self, ws: ServerConnection) -> None:
        """Handle a WebSocket connection from the extension.

        Handles reconnection gracefully: if a new connection arrives while
        an old one exists (MV3 service worker restart), the old connection
        is replaced without clearing state.
        """
        # Replace previous connection if the extension reconnected
        if self._ws is not None and self._ws is not ws:
            try:
                await self._ws.close()
            except Exception:
                pass
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

                if msg_type == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue

                # Content script events — update cached snapshot
                if msg_type in ("mutation", "navigation"):
                    snap_data = msg.get("payload", {}).get("snapshot")
                    if snap_data:
                        self._snapshot = PageSnapshot(**snap_data)
                    continue

                # Response to a pending command
                if msg_id and msg_id in self._pending:
                    if msg_type == "result":
                        fut = self._pending.pop(msg_id)
                        if not fut.done():
                            fut.set_result(msg.get("payload", {}))
                    continue

                logger.debug("Unhandled message type=%s id=%s", msg_type, msg_id)

        except websockets.exceptions.ConnectionClosed:
            logger.info("Extension disconnected")
        finally:
            # Only clear state if no newer connection has replaced us
            if self._ws is ws:
                self._ws = None
                self._connected.clear()

    # ─── Command transport ───────────────────────────────────────

    async def _send_command(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Send a command and wait for the result. Raises on timeout or disconnect."""
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
            return await asyncio.wait_for(fut, timeout=timeout_ms / 1000)
        except TimeoutError:
            self._pending.pop(cmd_id, None)
            raise

    # ─── Public API ──────────────────────────────────────────────

    async def navigate(self, url: str, timeout_ms: int = 30000) -> PageSnapshot:
        """Navigate to URL and return a PageSnapshot.

        Handles MV3 service worker restarts during navigation:
        1. Sends navigate command (may timeout if service worker restarts)
        2. Waits for reconnection if needed
        3. Polls for snapshot from content script events
        4. Falls back to requesting snapshot directly
        """
        self._snapshot = None  # Clear cache — we want a fresh snapshot

        try:
            result = await self._send_command("navigate", {"url": url}, timeout_ms=timeout_ms)
            snap_data = result.get("snapshot")
            if snap_data:
                self._snapshot = PageSnapshot(**snap_data)
        except (TimeoutError, ConnectionError):
            logger.info("Navigate command lost — waiting for extension reconnect")

        # Wait for reconnection if service worker restarted
        if not self.connected:
            await self.wait_for_connection(timeout=15)

        # Poll for snapshot from content script events (navigation/mutation)
        for _ in range(10):
            if self._snapshot is not None:
                break
            await asyncio.sleep(1)

        # Last resort: request snapshot directly from content script
        # After MV3 restart, content script may need time to inject + page to render
        if self._snapshot is None and self.connected:
            for attempt in range(5):
                try:
                    result = await self._send_command("get_snapshot", timeout_ms=8000)
                    if result:
                        self._snapshot = PageSnapshot(**result)
                        break
                except (TimeoutError, ConnectionError):
                    logger.debug("get_snapshot attempt %d failed, retrying...", attempt + 1)
                    await asyncio.sleep(3)

        if self._snapshot is None:
            raise RuntimeError("No snapshot received after navigation")
        return self._snapshot

    async def fill(self, selector: str, value: str, timeout_ms: int = 10000) -> FillResult:
        """Fill a form field with human-like typing."""
        result = await self._send_command("fill", {"selector": selector, "value": value}, timeout_ms=timeout_ms)
        return FillResult(**result)

    async def click(self, selector: str, timeout_ms: int = 10000) -> bool:
        """Click an element by CSS selector."""
        result = await self._send_command("click", {"selector": selector}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def upload(self, selector: str, file_path: Path, timeout_ms: int = 30000) -> bool:
        """Upload a file to an <input type='file'> element via base64 transfer."""
        import base64
        import mimetypes

        data = file_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        mime = mimetypes.guess_type(str(file_path))[0] or "application/pdf"

        result = await self._send_command(
            "upload",
            {"selector": selector, "file_base64": b64, "file_name": file_path.name, "mime_type": mime},
            timeout_ms=timeout_ms,
        )
        return bool(result.get("success", False))

    async def select_option(self, selector: str, value: str, timeout_ms: int = 10000) -> bool:
        """Select a dropdown option by value or text match."""
        result = await self._send_command("select", {"selector": selector, "value": value}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def check(self, selector: str, should_check: bool, timeout_ms: int = 10000) -> bool:
        """Check or uncheck a checkbox."""
        result = await self._send_command(
            "check", {"selector": selector, "value": str(should_check).lower()}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def screenshot(self, timeout_ms: int = 10000) -> bytes:
        """Capture a screenshot of the visible tab (returns PNG bytes)."""
        import base64

        result = await self._send_command("screenshot", timeout_ms=timeout_ms)
        b64 = result.get("data", "")
        return base64.b64decode(b64)

    async def analyze_field_locally(
        self, question: str, input_type: str, options: list[str], timeout_ms: int = 15000
    ) -> str | None:
        """Ask Gemini Nano (via Chrome extension) to analyze a form field.

        Returns the answer string, or None if Nano is unavailable.
        This is Tier 3 of the 5-tier form intelligence system.
        """
        result = await self._send_command(
            "analyze_field",
            {"question": question, "input_type": input_type, "options": options},
            timeout_ms=timeout_ms,
        )
        answer = result.get("answer", "")
        return answer if answer else None

    async def wait_for_apply(self, timeout_ms: int = 12000) -> dict[str, Any]:
        """Wait for an apply button to appear in the DOM.

        Returns the full snapshot + apply_diagnostics + waited_ms.
        Used on LinkedIn job pages where the Easy Apply button renders late.
        """
        result = await self._send_command(
            "wait_for_apply",
            {"timeout_ms": timeout_ms},
            timeout_ms=timeout_ms + 3000,  # Extra buffer for WS round-trip
        )
        # Update cached snapshot from the result
        snap_fields = {k: v for k, v in result.items() if k not in ("apply_diagnostics", "waited_ms")}
        if snap_fields:
            self._snapshot = PageSnapshot(**snap_fields)
        return result

    # ─── v2 Form Engine API ─────────────────────────────────────

    async def fill_radio_group(
        self, selector: str, value: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Fill a radio button group by matching option labels to value."""
        return await self._send_command(
            "fill_radio_group",
            {"selector": selector, "value": value},
            timeout_ms=timeout_ms,
        )

    async def fill_custom_select(
        self, selector: str, value: str, timeout_ms: int = 15000
    ) -> dict[str, Any]:
        """Fill a custom React/Angular dropdown widget."""
        return await self._send_command(
            "fill_custom_select",
            {"selector": selector, "value": value},
            timeout_ms=timeout_ms,
        )

    async def fill_autocomplete(
        self, selector: str, value: str, timeout_ms: int = 15000
    ) -> dict[str, Any]:
        """Fill a typeahead/autocomplete field — types partial, clicks suggestion."""
        return await self._send_command(
            "fill_autocomplete",
            {"selector": selector, "value": value},
            timeout_ms=timeout_ms,
        )

    async def fill_tag_input(
        self, selector: str, values: list[str], timeout_ms: int = 20000
    ) -> dict[str, Any]:
        """Fill a tag/chip input — types each value + Enter."""
        return await self._send_command(
            "fill_tag_input",
            {"selector": selector, "values": values},
            timeout_ms=timeout_ms,
        )

    async def fill_date(
        self, selector: str, iso_date: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Fill a date field (native or text-based)."""
        return await self._send_command(
            "fill_date",
            {"selector": selector, "value": iso_date},
            timeout_ms=timeout_ms,
        )

    async def scroll_to(self, selector: str, timeout_ms: int = 5000) -> bool:
        """Scroll an element into view."""
        result = await self._send_command(
            "scroll_to", {"selector": selector}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def wait_for_selector(
        self, selector: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Wait for a selector to appear in the DOM."""
        return await self._send_command(
            "wait_for_selector",
            {"selector": selector, "timeout_ms": timeout_ms},
            timeout_ms=timeout_ms + 3000,
        )

    async def force_click(self, selector: str, timeout_ms: int = 10000) -> bool:
        """Click element even if obscured (dispatches event directly)."""
        result = await self._send_command(
            "force_click", {"selector": selector}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def check_consent_boxes(
        self, root_selector: str | None = None, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Auto-check all consent/GDPR/terms checkboxes."""
        return await self._send_command(
            "check_consent_boxes",
            {"root_selector": root_selector or ""},
            timeout_ms=timeout_ms,
        )

    async def scan_form_groups(
        self, root_selector: str | None = None, timeout_ms: int = 10000
    ) -> list[dict[str, Any]]:
        """Scan for form groups (label+input pairs) within a container."""
        result = await self._send_command(
            "scan_form_groups",
            {"root_selector": root_selector or ""},
            timeout_ms=timeout_ms,
        )
        return result.get("groups", [])

    async def rescan_after_fill(
        self, selector: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Re-scan page after filling a field for conditional fields and errors."""
        return await self._send_command(
            "rescan_after_fill",
            {"selector": selector},
            timeout_ms=timeout_ms,
        )

    async def get_snapshot(self, force_refresh: bool = False) -> PageSnapshot | None:
        """Return the latest page snapshot.

        If force_refresh=True, requests a fresh scan from the content script
        instead of returning the cached version.
        """
        if force_refresh and self.connected:
            try:
                result = await self._send_command("get_snapshot", timeout_ms=10000)
                if result:
                    self._snapshot = PageSnapshot(**result)
            except (TimeoutError, Exception):
                pass  # Fall back to cached snapshot
        return self._snapshot
