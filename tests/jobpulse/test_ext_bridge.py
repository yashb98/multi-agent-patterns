"""Tests for the WebSocket bridge between Python and Chrome extension."""

import asyncio
import json
import uuid

import pytest

from jobpulse.ext_bridge import ExtensionBridge
from jobpulse.ext_models import PageSnapshot, FillResult


@pytest.fixture
def bridge():
    return ExtensionBridge(host="localhost", port=0)  # port=0 picks random free port


@pytest.mark.asyncio
async def test_bridge_starts_and_stops(bridge):
    """Bridge starts a WebSocket server and stops cleanly."""
    await bridge.start()
    assert bridge.port > 0
    assert bridge._server is not None
    await bridge.stop()
    assert bridge._server is None


@pytest.mark.asyncio
async def test_bridge_connected_is_false_before_extension(bridge):
    """connected is False when no extension has connected."""
    await bridge.start()
    assert bridge.connected is False
    await bridge.stop()


@pytest.mark.asyncio
async def test_bridge_wait_for_connection_timeout(bridge):
    """wait_for_connection returns False on timeout."""
    await bridge.start()
    result = await bridge.wait_for_connection(timeout=0.1)
    assert result is False
    await bridge.stop()


@pytest.mark.asyncio
async def test_bridge_send_and_receive(bridge):
    """Send a command, receive a response via mock WebSocket client."""
    import websockets

    await bridge.start()
    port = bridge.port

    async def mock_extension():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            # Should receive command from bridge
            raw = await ws.recv()
            msg = json.loads(raw)
            assert msg["action"] == "navigate"
            # Send ack
            await ws.send(json.dumps({
                "id": msg["id"],
                "type": "ack",
                "payload": {},
            }))
            # Send result with snapshot
            await ws.send(json.dumps({
                "id": msg["id"],
                "type": "result",
                "payload": {
                    "success": True,
                    "snapshot": {
                        "url": "https://example.com",
                        "title": "Test",
                        "fields": [],
                        "buttons": [],
                        "verification_wall": None,
                        "page_text_preview": "",
                        "has_file_inputs": False,
                        "iframe_count": 0,
                        "timestamp": 1000,
                    },
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
async def test_bridge_fill_command(bridge):
    """fill() sends a fill command and returns FillResult."""
    import websockets

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
async def test_bridge_handles_ping_pong(bridge):
    """Bridge responds to ping with pong."""
    import websockets

    await bridge.start()
    port = bridge.port
    got_pong = asyncio.Event()

    async def mock_extension():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            await ws.send(json.dumps({"type": "ping"}))
            raw = await ws.recv()
            msg = json.loads(raw)
            assert msg["type"] == "pong"
            got_pong.set()

    ext_task = asyncio.create_task(mock_extension())
    await asyncio.wait_for(got_pong.wait(), timeout=3.0)

    ext_task.cancel()
    await bridge.stop()


@pytest.mark.asyncio
async def test_bridge_snapshot_updated_on_mutation(bridge):
    """Mutation events update the cached snapshot."""
    import websockets

    await bridge.start()
    port = bridge.port

    async def mock_extension():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            # Send a mutation event (no command expected)
            await ws.send(json.dumps({
                "id": "",
                "type": "mutation",
                "payload": {
                    "snapshot": {
                        "url": "https://greenhouse.io/apply",
                        "title": "Apply",
                        "fields": [{"selector": "#email", "input_type": "email", "label": "Email"}],
                        "buttons": [],
                        "verification_wall": None,
                        "page_text_preview": "",
                        "has_file_inputs": False,
                        "iframe_count": 0,
                        "timestamp": 2000,
                    },
                },
            }))
            await asyncio.sleep(0.2)

    ext_task = asyncio.create_task(mock_extension())
    await bridge.wait_for_connection(timeout=2.0)
    await asyncio.sleep(0.5)  # Let mutation process

    snapshot = await bridge.get_snapshot()
    assert snapshot is not None
    assert snapshot.url == "https://greenhouse.io/apply"
    assert len(snapshot.fields) == 1

    ext_task.cancel()
    await bridge.stop()


@pytest.mark.asyncio
async def test_bridge_command_timeout(bridge):
    """Command times out if extension never responds."""
    import websockets

    await bridge.start()
    port = bridge.port

    async def mock_extension():
        async with websockets.connect(f"ws://localhost:{port}") as ws:
            # Receive command but never respond
            await ws.recv()
            await asyncio.sleep(10)

    ext_task = asyncio.create_task(mock_extension())
    await bridge.wait_for_connection(timeout=2.0)

    with pytest.raises(asyncio.TimeoutError):
        await bridge.navigate("https://example.com", timeout_ms=500)

    ext_task.cancel()
    await bridge.stop()
