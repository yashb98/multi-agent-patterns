"""Comprehensive tests for ext_bridge.py — basics, reconnection, concurrency, MV3 lifecycle.

Covers:
- Start/stop, connection detection, timeout
- Send command with ack+result flow, fill command, ping/pong
- Mutation events updating snapshot cache, command timeout
- Service worker reconnection during navigation
- Multiple concurrent commands
- Connection replaced by new WebSocket (MV3 restart)
- Navigate resilience: timeout fallback, poll recovery, direct get_snapshot
- Command after disconnect raises ConnectionError
- Graceful stop with pending commands
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from jobpulse.ext_bridge import ExtensionBridge
from jobpulse.ext_models import PageSnapshot


@pytest.fixture
def bridge():
    return ExtensionBridge(host="localhost", port=0)


# =========================================================================
# Basic bridge operations
# =========================================================================


class TestBridgeBasics:
    @pytest.mark.asyncio
    async def test_bridge_starts_and_stops(self, bridge):
        """Bridge starts a WebSocket server and stops cleanly."""
        await bridge.start()
        assert bridge.port > 0
        assert bridge._server is not None
        await bridge.stop()
        assert bridge._server is None

    @pytest.mark.asyncio
    async def test_bridge_connected_is_false_before_extension(self, bridge):
        """connected is False when no extension has connected."""
        await bridge.start()
        assert bridge.connected is False
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_wait_for_connection_timeout(self, bridge):
        """wait_for_connection returns False on timeout."""
        await bridge.start()
        result = await bridge.wait_for_connection(timeout=0.1)
        assert result is False
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_send_and_receive(self, bridge):
        """Send a command, receive ack then result via mock WebSocket client."""
        await bridge.start()
        port = bridge.port

        async def mock_extension():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "navigate"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "ack",
                    "payload": {},
                }))
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {
                        "success": True,
                        "snapshot": _snapshot_dict(url="https://example.com", title="Test"),
                    },
                }))

        ext_task = asyncio.create_task(mock_extension())
        await bridge.wait_for_connection(timeout=2.0)
        assert bridge.connected is True

        snapshot = await bridge.navigate("https://example.com", timeout_ms=5000)
        assert snapshot.url == "https://example.com"

        ext_task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_fill_command(self, bridge):
        """fill() sends a fill command and returns FillResult."""
        await bridge.start()
        port = bridge.port

        async def mock_extension():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "fill"
                assert msg["payload"]["selector"] == "#name"
                assert msg["payload"]["value"] == "Yash"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True, "value_set": "Yash"},
                }))

        ext_task = asyncio.create_task(mock_extension())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.fill("#name", "Yash")
        assert result.success is True
        assert result.value_set == "Yash"

        ext_task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_snapshot_updated_on_mutation(self, bridge):
        """Mutation events update the cached snapshot."""
        await bridge.start()
        port = bridge.port

        async def mock_extension():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.send(json.dumps({
                    "id": "",
                    "type": "mutation",
                    "payload": {
                        "snapshot": _snapshot_dict(
                            url="https://greenhouse.io/apply",
                            title="Apply",
                        ),
                    },
                }))
                await asyncio.sleep(0.2)

        ext_task = asyncio.create_task(mock_extension())
        await bridge.wait_for_connection(timeout=2.0)
        await asyncio.sleep(0.5)

        snapshot = await bridge.get_snapshot()
        assert snapshot is not None
        assert snapshot.url == "https://greenhouse.io/apply"

        ext_task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_command_timeout(self, bridge):
        """Command times out if extension never responds."""
        await bridge.start()
        port = bridge.port

        async def mock_extension():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.recv()
                await asyncio.sleep(10)

        ext_task = asyncio.create_task(mock_extension())
        await bridge.wait_for_connection(timeout=2.0)

        with pytest.raises((asyncio.TimeoutError, RuntimeError)):
            await bridge.navigate("https://example.com", timeout_ms=500)

        ext_task.cancel()
        await bridge.stop()


def _snapshot_dict(**overrides):
    base = {
        "url": "https://example.com",
        "title": "Test Page",
        "fields": [],
        "buttons": [],
        "verification_wall": None,
        "page_text_preview": "",
        "has_file_inputs": False,
        "iframe_count": 0,
        "timestamp": 1000,
    }
    base.update(overrides)
    return base


# =========================================================================
# Connection lifecycle
# =========================================================================


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_connected_after_extension_joins(self, bridge):
        await bridge.start()
        port = bridge.port

        async def client():
            async with websockets.connect(f"ws://localhost:{port}"):
                await asyncio.sleep(0.5)

        task = asyncio.create_task(client())
        assert await bridge.wait_for_connection(timeout=2.0)
        assert bridge.connected is True

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_disconnected_after_client_closes(self, bridge):
        await bridge.start()
        port = bridge.port

        async def client():
            async with websockets.connect(f"ws://localhost:{port}"):
                await asyncio.sleep(0.3)
            # connection closed here

        task = asyncio.create_task(client())
        assert await bridge.wait_for_connection(timeout=2.0)
        await asyncio.sleep(0.5)  # wait for disconnect to propagate
        assert bridge.connected is False

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_reconnection_replaces_old_connection(self, bridge):
        """New WebSocket connection replaces old one (MV3 service worker restart)."""
        await bridge.start()
        port = bridge.port

        async def client1():
            async with websockets.connect(f"ws://localhost:{port}"):
                await asyncio.sleep(0.5)

        async def client2():
            await asyncio.sleep(0.2)
            async with websockets.connect(f"ws://localhost:{port}"):
                await asyncio.sleep(1.0)  # stays alive longer than client1

        t1 = asyncio.create_task(client1())
        t2 = asyncio.create_task(client2())
        await bridge.wait_for_connection(timeout=2.0)
        await asyncio.sleep(0.7)  # client1 gone, client2 still alive
        assert bridge.connected is True

        t1.cancel()
        t2.cancel()
        await bridge.stop()


# =========================================================================
# Command handling edge cases
# =========================================================================


class TestCommandEdgeCases:
    @pytest.mark.asyncio
    async def test_command_after_disconnect_raises(self, bridge):
        """Sending command when not connected raises ConnectionError."""
        await bridge.start()
        # No extension connected
        with pytest.raises(ConnectionError):
            await bridge._send_command("fill", {"selector": "#x", "value": "y"})
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_commands(self, bridge):
        """Multiple commands sent concurrently resolve independently."""
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                for _ in range(3):
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    # Reply with the action name as value to identify responses
                    await ws.send(json.dumps({
                        "id": msg["id"],
                        "type": "result",
                        "payload": {"success": True, "action_echo": msg["action"]},
                    }))

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        # Send 3 commands concurrently
        results = await asyncio.gather(
            bridge._send_command("fill", {"selector": "#a"}, timeout_ms=3000),
            bridge._send_command("click", {"selector": "#b"}, timeout_ms=3000),
            bridge._send_command("select", {"selector": "#c"}, timeout_ms=3000),
        )
        assert len(results) == 3
        echoes = {r["action_echo"] for r in results}
        assert echoes == {"fill", "click", "select"}

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_commands(self, bridge):
        """stop() cancels all pending futures."""
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.recv()
                await asyncio.sleep(30)

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        cmd_task = asyncio.create_task(
            bridge._send_command("fill", {"selector": "#x"}, timeout_ms=30000)
        )
        await asyncio.sleep(0.3)

        await bridge.stop()
        # After stop, the future should be done (cancelled or errored)
        await asyncio.sleep(0.1)
        assert cmd_task.done()

        task.cancel()

    @pytest.mark.asyncio
    async def test_invalid_json_from_extension_ignored(self, bridge):
        """Bridge ignores non-JSON messages from extension."""
        await bridge.start()
        port = bridge.port
        pong_received = asyncio.Event()

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.send("this is not json {{{")
                await ws.send(json.dumps({"type": "ping"}))
                pong = await ws.recv()
                assert json.loads(pong)["type"] == "pong"
                pong_received.set()
                await asyncio.sleep(1)  # keep connection alive

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)
        await asyncio.wait_for(pong_received.wait(), timeout=3.0)
        assert bridge.connected is True

        task.cancel()
        await bridge.stop()


# =========================================================================
# Navigate resilience
# =========================================================================


class TestNavigateResilience:
    @pytest.mark.asyncio
    async def test_navigate_receives_snapshot_from_result(self, bridge):
        """Navigate gets snapshot directly from command result payload."""
        await bridge.start()
        port = bridge.port
        snap = _snapshot_dict(url="https://target.com", title="Target")

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True, "snapshot": snap},
                }))

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.navigate("https://target.com", timeout_ms=5000)
        assert result.url == "https://target.com"

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_navigate_falls_back_to_mutation_event(self, bridge):
        """Navigate command returns null snapshot but mutation event provides one."""
        await bridge.start()
        port = bridge.port
        snap = _snapshot_dict(url="https://target.com", title="Via Mutation")

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                # Return result with no snapshot
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True, "snapshot": None},
                }))
                # Then send a mutation event with the snapshot
                await asyncio.sleep(0.5)
                await ws.send(json.dumps({
                    "type": "mutation",
                    "payload": {"snapshot": snap},
                }))
                await asyncio.sleep(10)

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.navigate("https://target.com", timeout_ms=5000)
        assert result.title == "Via Mutation"

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_navigate_timeout_no_snapshot_raises(self, bridge):
        """Navigate with no snapshot at all raises RuntimeError."""
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                # Return result with no snapshot
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True, "snapshot": None},
                }))
                # Also make get_snapshot fail
                for _ in range(5):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2)
                        msg = json.loads(raw)
                        if msg.get("action") == "get_snapshot":
                            await ws.send(json.dumps({
                                "id": msg["id"],
                                "type": "result",
                                "payload": {},
                            }))
                    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                        break

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        with pytest.raises(RuntimeError, match="No snapshot"):
            await bridge.navigate("https://target.com", timeout_ms=2000)

        task.cancel()
        await bridge.stop()


# =========================================================================
# Snapshot cache
# =========================================================================


class TestSnapshotCache:
    @pytest.mark.asyncio
    async def test_get_snapshot_returns_cached(self, bridge):
        """get_snapshot(force_refresh=False) returns cached snapshot."""
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                await ws.send(json.dumps({
                    "type": "navigation",
                    "payload": {
                        "snapshot": _snapshot_dict(url="https://cached.com"),
                    },
                }))
                await asyncio.sleep(2)

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)
        await asyncio.sleep(0.3)

        snap = await bridge.get_snapshot()
        assert snap is not None
        assert snap.url == "https://cached.com"

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_get_snapshot_force_refresh(self, bridge):
        """get_snapshot(force_refresh=True) sends get_snapshot command."""
        await bridge.start()
        port = bridge.port
        fresh_snap = _snapshot_dict(url="https://fresh.com", title="Fresh")

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "get_snapshot"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": fresh_snap,
                }))
                await asyncio.sleep(2)

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        snap = await bridge.get_snapshot(force_refresh=True)
        assert snap is not None
        assert snap.url == "https://fresh.com"

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_get_snapshot_none_when_no_cache(self, bridge):
        """get_snapshot returns None when nothing cached and not connected."""
        await bridge.start()
        snap = await bridge.get_snapshot()
        assert snap is None
        await bridge.stop()


# =========================================================================
# Click / Fill / Upload / Select / Check
# =========================================================================


class TestBridgeCommands:
    @pytest.mark.asyncio
    async def test_click_returns_bool(self, bridge):
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "click"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True},
                }))

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.click("#btn")
        assert result is True

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_select_option(self, bridge):
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "select"
                assert msg["payload"]["value"] == "option_a"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True},
                }))

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.select_option("#dropdown", "option_a")
        assert result is True

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_check_checkbox(self, bridge):
        await bridge.start()
        port = bridge.port

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "check"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True},
                }))

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.check("#agree", True)
        assert result is True

        task.cancel()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_screenshot_returns_bytes(self, bridge):
        import base64

        await bridge.start()
        port = bridge.port
        png_data = b"\x89PNG\r\n\x1a\nfake"
        b64 = base64.b64encode(png_data).decode()

        async def mock_ext():
            async with websockets.connect(f"ws://localhost:{port}") as ws:
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "screenshot"
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "type": "result",
                    "payload": {"success": True, "data": b64},
                }))

        task = asyncio.create_task(mock_ext())
        await bridge.wait_for_connection(timeout=2.0)

        result = await bridge.screenshot()
        assert result == png_data

        task.cancel()
        await bridge.stop()
