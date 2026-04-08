"""WebSocket relay client that connects to an already-running ExtensionBridge.

When ``ralph-test`` or another Python process needs to send commands through
the Chrome extension but the bridge server is already running (port in use),
this class connects as a WebSocket **client** and forwards commands via the
bridge's relay protocol.

The relay protocol:
    1. Client sends {"type":"relay_hello"} on connect
    2. Server replies {"type":"relay_hello_ack", "connected": true/false}
    3. Client sends {"id": "...", "type": "command", "action": "...", "payload": {...}}
    4. Server forwards to extension, relays result back as {"id": "...", "type": "result", "payload": {...}}
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import websockets

from shared.logging_config import get_logger

from jobpulse.ext_models import FillResult, PageSnapshot

logger = get_logger(__name__)


class RelayBridge:
    """WebSocket client that relays commands through the running ExtensionBridge."""

    def __init__(self, host: str = "localhost", port: int = 8765) -> None:
        self._host = host
        self.port = port
        self._ws: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._snapshot: PageSnapshot | None = None
        self._connected_to_bridge = False
        self._extension_connected = False
        self._extension_ready = asyncio.Event()
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self, timeout: float = 10.0) -> bool:
        """Connect to the running bridge server as a relay client."""
        uri = f"ws://{self._host}:{self.port}"
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(uri), timeout=timeout
            )
        except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
            logger.warning("RelayBridge: cannot connect to %s: %s", uri, exc)
            return False

        # Send relay handshake
        await self._ws.send(json.dumps({"type": "relay_hello"}))
        raw = await asyncio.wait_for(self._ws.recv(), timeout=5)
        msg = json.loads(raw)
        if msg.get("type") != "relay_hello_ack":
            logger.warning("RelayBridge: unexpected handshake response: %s", msg)
            await self._ws.close()
            return False

        self._connected_to_bridge = True
        self._extension_connected = msg.get("connected", False)
        if self._extension_connected:
            self._extension_ready.set()

        logger.info(
            "RelayBridge: connected to bridge at %s (extension=%s)",
            uri,
            "yes" if self._extension_connected else "no",
        )

        # Start background reader for responses
        self._reader_task = asyncio.create_task(self._read_loop())

        # If extension isn't connected yet, wait for it (up to 15s)
        if not self._extension_connected:
            logger.info("RelayBridge: waiting for extension to connect...")
            try:
                await asyncio.wait_for(self._extension_ready.wait(), timeout=15.0)
                logger.info("RelayBridge: extension connected!")
            except TimeoutError:
                logger.warning("RelayBridge: extension did not connect within 15s")
                return True  # still connected to bridge, commands will fail gracefully

        # Health check: verify the full command pipeline works
        # (relay → bridge → extension → content.js → back)
        if self._extension_connected:
            try:
                result = await self._send_command("get_snapshot", timeout_ms=8000)
                if isinstance(result, dict) and result.get("url"):
                    self._snapshot = PageSnapshot(**result)
                    logger.info("RelayBridge: health check OK — pipeline verified")
                else:
                    logger.info("RelayBridge: health check returned empty snapshot (no active page)")
            except Exception as exc:
                logger.warning("RelayBridge: health check FAILED — commands may not reach extension: %s", exc)

        return True

    async def _read_loop(self) -> None:
        """Read responses from the bridge and resolve pending futures."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                # Extension status update from bridge
                if msg_type == "extension_status":
                    self._extension_connected = msg.get("connected", False)
                    if self._extension_connected:
                        self._extension_ready.set()
                        logger.info("RelayBridge: extension reconnected")
                    else:
                        self._extension_ready.clear()
                        logger.info("RelayBridge: extension disconnected")
                    continue

                # Passive snapshot events forwarded from extension via bridge
                if msg_type in ("navigation", "mutation"):
                    snap_data = msg.get("payload", {}).get("snapshot")
                    if isinstance(snap_data, dict) and snap_data.get("url"):
                        self._snapshot = PageSnapshot(**snap_data)
                        logger.info("RelayBridge: snapshot received via %s event", msg_type)
                    continue

                msg_id = msg.get("id", "")
                if msg_id and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        if msg_type == "error":
                            fut.set_exception(
                                RuntimeError(msg.get("payload", {}).get("error", "unknown error"))
                            )
                        else:
                            fut.set_result(msg.get("payload", {}))
        except websockets.exceptions.ConnectionClosed:
            logger.info("RelayBridge: disconnected from bridge")
        finally:
            self._connected_to_bridge = False
            # Cancel all pending futures
            for fut in self._pending.values():
                if not fut.done():
                    fut.cancel()
            self._pending.clear()

    async def stop(self) -> None:
        """Close the relay connection."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected_to_bridge = False

    @property
    def connected(self) -> bool:
        """Whether we're connected to the bridge and the extension is connected."""
        return self._connected_to_bridge and self._extension_connected

    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Wait for the extension to be connected (polls bridge)."""
        if self.connected:
            return True
        # If bridge says extension not connected, poll
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            # Ask for snapshot — if it works, extension is alive
            try:
                await self._send_command("get_snapshot", timeout_ms=3000)
                self._extension_connected = True
                return True
            except Exception:
                await asyncio.sleep(1)
        return False

    async def _send_command(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Send a command via the relay protocol and wait for the result."""
        if not self._connected_to_bridge:
            raise ConnectionError("Not connected to bridge")
        if self._ws is None:
            raise ConnectionError("WebSocket not initialized")

        # Wait for extension if not connected (handles reload/reconnect)
        if not self._extension_connected:
            logger.info("RelayBridge: extension not ready, waiting up to 5s...")
            try:
                await asyncio.wait_for(self._extension_ready.wait(), timeout=5.0)
            except TimeoutError:
                raise ConnectionError("Extension not connected after 5s wait")

        cmd_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[cmd_id] = fut

        msg = {
            "id": cmd_id,
            "type": "command",
            "action": action,
            "payload": payload or {},
            "timeout_ms": timeout_ms,
        }
        await self._ws.send(json.dumps(msg))

        try:
            return await asyncio.wait_for(fut, timeout=timeout_ms / 1000)
        except TimeoutError:
            self._pending.pop(cmd_id, None)
            raise

    # ─── Public API (mirrors ExtensionBridge) ────────────────────

    async def navigate(self, url: str, timeout_ms: int = 30000) -> PageSnapshot:
        """Navigate to URL and return a PageSnapshot.

        Flow:
        1. Send navigate command (background.js returns immediately with snapshot=null)
        2. Page loads → content.js snapshot → background.js → bridge → relay (passive event)
        3. _read_loop receives the 'navigation' event and sets self._snapshot
        4. We just wait for _snapshot to be set — no polling needed
        5. Fallback: one get_snapshot poll if passive event didn't arrive
        """
        self._snapshot = None

        # Send navigate — short timeout since background.js responds immediately
        # (before chrome.tabs.update). Any exception here (timeout, connection drop,
        # or bridge-side timeout returning RuntimeError) is expected during MV3 restarts.
        try:
            result = await self._send_command("navigate", {"url": url}, timeout_ms=8000)
            snap_data = result.get("snapshot") if isinstance(result, dict) else None
            if snap_data:
                self._snapshot = PageSnapshot(**snap_data)
        except Exception as exc:
            logger.info("RelayBridge: navigate command: %s — waiting for snapshot", exc)

        # Wait for extension reconnect if it disconnected during navigation
        if not self._extension_connected:
            logger.info("RelayBridge: waiting for extension to reconnect...")
            try:
                await asyncio.wait_for(self._extension_ready.wait(), timeout=15.0)
            except TimeoutError:
                raise RuntimeError("Extension did not reconnect after navigation")

        # Wait for passive snapshot from navigation/mutation event.
        # The _read_loop sets self._snapshot when it receives these events.
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000) - 8
        while self._snapshot is None and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)

        # Fallback: one active get_snapshot if passive event didn't arrive
        if self._snapshot is None:
            logger.info("RelayBridge: no passive snapshot — requesting explicitly")
            try:
                result = await self._send_command("get_snapshot", timeout_ms=8000)
                if isinstance(result, dict) and result.get("url"):
                    self._snapshot = PageSnapshot(**result)
            except Exception as exc:
                logger.debug("RelayBridge: fallback get_snapshot failed: %s", exc)

        if self._snapshot is None:
            raise RuntimeError("No snapshot received after navigation")
        return self._snapshot

    async def fill(self, selector: str, value: str, timeout_ms: int = 10000) -> FillResult:
        result = await self._send_command("fill", {"selector": selector, "value": value}, timeout_ms=timeout_ms)
        return FillResult(**result)

    async def click(self, selector: str, timeout_ms: int = 10000) -> bool:
        result = await self._send_command("click", {"selector": selector}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def real_click(self, x: float, y: float, timeout_ms: int = 10000) -> bool:
        """Click at pixel coordinates via chrome.debugger (real mouse event)."""
        result = await self._send_command("real_click", {"x": x, "y": y}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def real_type(self, text: str, timeout_ms: int = 30000) -> bool:
        """Type text via chrome.debugger (real keyboard events)."""
        result = await self._send_command("real_type", {"text": text}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def upload(self, selector: str, file_path: Path, timeout_ms: int = 30000) -> bool:
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

    async def reveal_options(self, selector: str, timeout_ms: int = 10000) -> list[str]:
        """Click a combobox to reveal its options, return option texts, then close."""
        result = await self._send_command(
            "reveal_options", {"selector": selector}, timeout_ms=timeout_ms,
        )
        return result.get("options", []) if result.get("success") else []

    async def select_option(self, selector: str, value: str, timeout_ms: int = 10000) -> bool:
        result = await self._send_command("select", {"selector": selector, "value": value}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def check(self, selector: str, should_check: bool, timeout_ms: int = 10000) -> bool:
        result = await self._send_command(
            "check", {"selector": selector, "value": str(should_check).lower()}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def screenshot(self, timeout_ms: int = 10000) -> bytes:
        import base64

        result = await self._send_command("screenshot", timeout_ms=timeout_ms)
        return base64.b64decode(result.get("data", ""))

    async def element_screenshot(self, selector: str, timeout_ms: int = 15000) -> bytes:
        """Screenshot cropped to a specific element's bounding box."""
        import base64

        result = await self._send_command(
            "element_screenshot", {"selector": selector}, timeout_ms=timeout_ms
        )
        return base64.b64decode(result.get("data", ""))

    async def analyze_field_locally(
        self, question: str, input_type: str, options: list[str], timeout_ms: int = 15000
    ) -> str | None:
        result = await self._send_command(
            "analyze_field",
            {"question": question, "input_type": input_type, "options": options},
            timeout_ms=timeout_ms,
        )
        answer = result.get("answer", "")
        return answer if answer else None

    async def wait_for_apply(self, timeout_ms: int = 12000) -> dict[str, Any]:
        result = await self._send_command(
            "wait_for_apply", {"timeout_ms": timeout_ms}, timeout_ms=timeout_ms + 3000
        )
        snap_fields = {k: v for k, v in result.items() if k not in ("apply_diagnostics", "waited_ms")}
        if snap_fields:
            self._snapshot = PageSnapshot(**snap_fields)
        return result

    async def fill_radio_group(self, selector: str, value: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await self._send_command("fill_radio_group", {"selector": selector, "value": value}, timeout_ms=timeout_ms)

    async def fill_custom_select(self, selector: str, value: str, timeout_ms: int = 15000) -> dict[str, Any]:
        return await self._send_command("fill_custom_select", {"selector": selector, "value": value}, timeout_ms=timeout_ms)

    async def fill_autocomplete(self, selector: str, value: str, timeout_ms: int = 15000) -> dict[str, Any]:
        return await self._send_command("fill_autocomplete", {"selector": selector, "value": value}, timeout_ms=timeout_ms)

    async def fill_combobox(self, selector: str, value: str, timeout_ms: int = 15000) -> dict[str, Any]:
        return await self._send_command("fill_combobox", {"selector": selector, "value": value}, timeout_ms=timeout_ms)

    async def fill_contenteditable(self, selector: str, value: str, timeout_ms: int = 15000) -> dict[str, Any]:
        return await self._send_command("fill_contenteditable", {"selector": selector, "value": value}, timeout_ms=timeout_ms)

    async def fill_tag_input(self, selector: str, values: list[str], timeout_ms: int = 20000) -> dict[str, Any]:
        return await self._send_command("fill_tag_input", {"selector": selector, "values": values}, timeout_ms=timeout_ms)

    async def fill_date(self, selector: str, iso_date: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await self._send_command("fill_date", {"selector": selector, "value": iso_date}, timeout_ms=timeout_ms)

    async def scroll_to(self, selector: str, timeout_ms: int = 5000) -> bool:
        result = await self._send_command("scroll_to", {"selector": selector}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def wait_for_selector(self, selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await self._send_command("wait_for_selector", {"selector": selector, "timeout_ms": timeout_ms}, timeout_ms=timeout_ms + 3000)

    async def force_click(self, selector: str, timeout_ms: int = 10000) -> bool:
        result = await self._send_command("force_click", {"selector": selector}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def check_consent_boxes(self, root_selector: str | None = None, timeout_ms: int = 10000) -> dict[str, Any]:
        return await self._send_command("check_consent_boxes", {"root_selector": root_selector or ""}, timeout_ms=timeout_ms)

    async def scan_validation_errors(self, timeout_ms: int = 10000) -> dict[str, Any]:
        return await self._send_command("scan_validation_errors", {}, timeout_ms=timeout_ms)

    async def scan_form_groups(self, root_selector: str | None = None, timeout_ms: int = 10000) -> list[dict[str, Any]]:
        result = await self._send_command("scan_form_groups", {"root_selector": root_selector or ""}, timeout_ms=timeout_ms)
        return result.get("groups", [])

    async def rescan_after_fill(self, selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        return await self._send_command("rescan_after_fill", {"selector": selector}, timeout_ms=timeout_ms)

    async def scan_jd(self, timeout_ms: int = 8000) -> str:
        """Extract job description text from the current page."""
        result = await self._send_command("scan_jd", {}, timeout_ms=timeout_ms)
        return result.get("jd_text", "")

    async def close_tab(self, timeout_ms: int = 5000) -> bool:
        """Close the current active tab."""
        result = await self._send_command("close_tab", {}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def get_snapshot(self, force_refresh: bool = False) -> PageSnapshot | None:
        if force_refresh:
            try:
                result = await self._send_command("get_snapshot", timeout_ms=10000)
                if result:
                    self._snapshot = PageSnapshot(**result)
            except Exception:
                pass
        if self._snapshot is None:
            # Try getting cached snapshot from bridge
            try:
                msg_id = str(uuid.uuid4())
                assert self._ws is not None
                await self._ws.send(json.dumps({"id": msg_id, "type": "get_snapshot"}))
                loop = asyncio.get_running_loop()
                fut: asyncio.Future[dict[str, Any]] = loop.create_future()
                self._pending[msg_id] = fut
                result = await asyncio.wait_for(fut, timeout=5)
                if result:
                    self._snapshot = PageSnapshot(**result)
            except Exception:
                pass
        return self._snapshot

    async def save_form_progress(self, url: str, progress: dict[str, Any], timeout_ms: int = 5000) -> bool:
        result = await self._send_command("save_form_progress", {"url": url, "progress": progress}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))

    async def get_form_progress(self, url: str, timeout_ms: int = 5000) -> dict[str, Any] | None:
        result = await self._send_command("get_form_progress", {"url": url}, timeout_ms=timeout_ms)
        if result.get("success") is False:
            return None
        return result

    async def clear_form_progress(self, url: str, timeout_ms: int = 5000) -> bool:
        result = await self._send_command("clear_form_progress", {"url": url}, timeout_ms=timeout_ms)
        return bool(result.get("success", False))
