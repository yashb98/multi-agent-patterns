# Phase 0 — Backend Prerequisites

**Time**: ~1.5 weeks active build.
**Pre-conditions**: README pre-conditions checklist satisfied (Tailscale on phone + Mac, daemon `caffeinate -d`, accounts ready).
**Goal**: Backend is ready for a mobile client to connect, authenticate, stream, dispatch every intent, upload voice, and receive push registrations. Zero mobile code in this phase.

---

## 1. Goals

By the end of Phase 0:

1. Phone (using `curl` or `wscat`) can pair via QR-code flow, receive a token, hit any of the 41+ intents over HTTP, open a WebSocket, send a voice clip and get a transcript back, and register an FCM token.
2. `notification_router` is the single emit point for all notifications. Every existing call to `telegram_client.send_message` for *event-style* notifications has migrated to it. (Direct user-reply send-paths in Telegram bots stay unchanged.)
3. Every NLP intent in `jobpulse/intent_registry.py` has a corresponding HTTP route that dispatches the same handler.
4. Mac stays awake reliably; backend is reachable from phone over Tailscale from any network.

## 2. Success criteria (verifiable)

- [ ] `curl -H "Authorization: Bearer <token>" http://<mac>:8000/api/intents/budget.summary` returns the same shape as the Telegram `budget` command.
- [ ] `wscat -c ws://<mac>:8000/ws -H "Authorization: Bearer <token>"` connects, receives `auth.ok`, accepts `subscribe`/`msg`, replies with streaming deltas.
- [ ] Pairing CLI: `python -m jobpulse.runner devices pair --name=test-device` prints a 6-digit code; submitting it via `POST /api/auth/pair` returns a token; `python -m jobpulse.runner devices list` shows it as active; `revoke test-device` invalidates it.
- [ ] `POST /api/voice` accepts an Opus blob and returns `{"transcript": "...", "intent": "..."}` using the existing Whisper + NLP classifier paths.
- [ ] `notification_router.emit(NotificationEvent(...))` fans out to FCM (mocked OK), WS (if connected), and Telegram (still active during shadow).
- [ ] Mac stays reachable across a 24-hour stress test — phone hits `/health` every 5 minutes, no failures.
- [ ] Coverage test `tests/integration/test_intent_http_coverage.py` asserts every key in `handler_registry.get_handler_map()` has a route under `/api/intents/`.
- [ ] Auth integration test asserts: revoked tokens fail; expired pairing codes fail; second use of one-time pairing code fails.

## 3. Out of scope (this phase)

- No mobile UI, no Expo project initialized.
- No real FCM project setup yet (mock the FCM sink — Phase 1B sets up the real Firebase project).
- No removal of any Telegram code. Telegram fanout from `notification_router` is **active** through Phase 4.
- No public-facing routing changes (still localhost + Tailscale).

---

## 4. Component breakdown

### 4.1 `mindgraph_app/auth_api.py` (NEW)

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/api/auth/pair-init` | POST | Local-only (CLI) | CLI calls this, server returns a pairing code (UUID + 6-digit short code) with 60s TTL stored in `pairing_codes` table. |
| `/api/auth/pair` | POST | Pairing code + device name in body | Phone submits the 6-digit code shown by CLI. Server validates, generates a 256-bit token, stores `bcrypt(token)` in `device_tokens`, returns plaintext token (only time it's revealed). |
| `/api/auth/revoke` | POST | Bearer token + admin scope (CLI) | Sets `revoked_at` for named device. |
| `/api/auth/me` | GET | Bearer | Returns `{name, scope, last_seen_at}`. App calls on every cold start. |

**Pairing code storage** — separate table `pairing_codes(code TEXT PRIMARY KEY, expires_at TEXT, used_at TEXT)` in `data/device_tokens.db`.

**Token format** — opaque 256-bit URL-safe base64 string. No structure (avoid JWT — cheaper to revoke server-side).

**`verify_device_token` dependency**:

```python
async def verify_device_token(authorization: str = Header(...)) -> DeviceAuth:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization[7:]
    row = db.fetchone("""
        SELECT id, name, scope FROM device_tokens
        WHERE token_hash = ? AND revoked_at IS NULL
    """, [bcrypt_check_helper(token)])
    if row is None:
        raise HTTPException(401, "Invalid or revoked token")
    db.execute("UPDATE device_tokens SET last_seen_at = ? WHERE id = ?",
               [datetime.now(UTC).isoformat(), row.id])
    return DeviceAuth(id=row.id, name=row.name, scope=row.scope)
```

### 4.2 CLI integration in `jobpulse/runner.py`

New subcommand: `devices`.

```bash
python -m jobpulse.runner devices list
python -m jobpulse.runner devices pair --name=Yash-Pixel-9
python -m jobpulse.runner devices revoke --name=Yash-Pixel-9
python -m jobpulse.runner devices rotate --name=Yash-Pixel-9   # revoke + new pair
```

`pair` shows:
```
Pairing code for Yash-Pixel-9: 482917
Expires in 60s.
On the phone: open NEURALIS → tap "Add this device" → enter 482917.
```

### 4.3 `mindgraph_app/ws_endpoint.py` (NEW)

Single WebSocket route at `/ws`.

**Connection lifecycle**:

```python
@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    auth_frame = await websocket.receive_json()
    if auth_frame.get("type") != "auth":
        await websocket.close(code=4001, reason="auth required first")
        return
    try:
        device = await verify_token_str(auth_frame["token"])
    except AuthError as e:
        await websocket.send_json({"type": "auth.fail", "reason": str(e)})
        await websocket.close(code=4003)
        return
    server_seq = await event_log.last_seq_for_device(device.id)
    await websocket.send_json({"type": "auth.ok", "device_name": device.name, "server_seq": server_seq})
    
    connection = WSConnection(websocket, device)
    connection_pool.register(connection)
    try:
        await connection.run()    # main loop: receive frames, dispatch
    finally:
        connection_pool.unregister(connection)
```

**Per-connection responsibilities** (`WSConnection.run`):

- Receive frames in a loop
- Heartbeat: track last `pong`; if >60s without pong, close with 4008
- Dispatch per `type`:
  - `subscribe`/`unsubscribe` — update channel set on connection
  - `msg` — route to `intent_dispatcher.handle_message(channel, text, device)`, stream replies via `send_msg_delta` / `send_msg_done`
  - `cancel` — set cancel flag in `pattern_runs[run_id]`
  - `voice.upload` — pass to Whisper, return `voice.transcript`
  - `resume_from` — replay events from `event_log` since `server_seq`
- Write all server-originated frames to `event_log` (keyed by device + monotonic seq) so reconnects can resume

**Connection pool**:

```python
# in-process for now (single uvicorn worker — assert in startup)
connection_pool: dict[int, list[WSConnection]] = {}  # device_id -> connections
```

If multiple uvicorn workers ever — switch to Redis pub/sub. Out of scope for Phase 0.

**Event log** — append-only SQLite table `data/ws_events.db`:
```sql
CREATE TABLE ws_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_device_seq ON ws_events(device_id, seq);
```

Retention: 24 hours of events. Janitor cron deletes older rows nightly (added to `scripts/install_cron.py`).

### 4.4 `mindgraph_app/intent_api.py` (NEW)

```python
@intent_router.post("/api/intents/{intent_name}")
async def dispatch_intent(intent_name: str, body: dict, device: DeviceAuth = Depends(verify_device_token)):
    handler = handler_registry.get_handler_map().get(intent_name)
    if handler is None:
        raise HTTPException(404, {"errorCategory": "validation", "message": f"Unknown intent: {intent_name}"})
    if handler.requires_scope and device.scope != "full":
        raise HTTPException(403, {"errorCategory": "permission", "message": "Demo scope cannot run this intent"})
    try:
        result = await handler.run_async(body, device=device)
        return {"status": "ok", "result": result}
    except DispatchError as e:
        raise HTTPException(e.http_status, e.to_dict())
```

**Handler interface change** — `BaseHandler` gains:

```python
class BaseHandler:
    requires_scope: Literal["full", "demo"] = "full"
    
    async def run_async(self, payload: dict, device: DeviceAuth | None = None) -> dict:
        # default: wrap sync run() in run_in_executor
        return await asyncio.to_thread(self.run, payload)
```

Handlers that already are async-friendly override directly. Handlers that call into Playwright must stay sync-wrapped (Playwright is sync in our codebase).

### 4.5 `mindgraph_app/voice_api.py` (NEW)

```python
@voice_router.post("/api/voice")
async def upload_voice(
    audio: UploadFile,
    channel: str | None = Form(None),
    device: DeviceAuth = Depends(verify_device_token),
):
    if audio.size > 10 * 1024 * 1024:                  # 10 MB cap → ~60s Opus
        raise HTTPException(413, "Audio too large")
    if audio.content_type not in {"audio/webm", "audio/ogg", "audio/opus"}:
        raise HTTPException(415, "Unsupported audio format")
    transcript = await whisper_service.transcribe(audio.file)
    intent_hint = nlp_classifier.classify(strip_trailing_punct(transcript))
    return {"transcript": transcript, "intent_hint": intent_hint}
```

`shared/voice/whisper_service.py` (NEW) wraps existing Whisper integration that lives in the Telegram voice path. Extract that logic to a reusable async service.

### 4.6 `mindgraph_app/push_api.py` (NEW)

```python
@push_router.post("/api/push/register")
async def register_fcm_token(payload: FcmRegisterPayload, device: DeviceAuth = Depends(verify_device_token)):
    db.execute("UPDATE device_tokens SET fcm_token = ? WHERE id = ?", [payload.fcm_token, device.id])
    return {"status": "ok"}

@push_router.post("/api/push/test")
async def push_test(device: DeviceAuth = Depends(verify_device_token)):
    notification_router.emit(NotificationEvent(
        category="activity",
        title="NEURALIS",
        body=f"Test push for {device.name}",
        deep_link="neuralis://hub",
        source="test",
    ))
    return {"status": "queued"}
```

### 4.7 `shared/notifications/router.py` (NEW)

```python
class NotificationRouter:
    def __init__(self, sinks: list[NotificationSink]):
        self.sinks = sinks
        self._dedup_window: dict[str, list[NotificationEvent]] = {}  # dedup_key -> recent events

    def emit(self, event: NotificationEvent) -> None:
        if event.dedup_key:
            grouped = self._maybe_group(event)
            if grouped is event:
                pass  # send as-is
            elif grouped is None:
                return  # absorbed into pending group
            else:
                event = grouped  # this is the flush of a group
        for sink in self.sinks:
            try:
                sink.send(event)
            except Exception as e:
                log.error("notification.sink.error",
                          extra={"sink": sink.name, "error": str(e), "event_source": event.source})
```

**Sinks** — `FcmSink`, `WsSink` (looks up active connections in `connection_pool` for any device with `fcm_token`-or-not), `TelegramSink` (wraps existing `telegram_client`).

**Grouping** — `dedup_key` like `"papers.daily-digest"` triggers a 60-second window. Within the window, additional events with the same key replace the body (`"3 papers" → "5 papers"`). Window flushes via background task or next event with different key.

**Migration**: every existing call site that today sends a Telegram message for an *event-driven* reason (job applied, paper digest, budget alert, daemon error, recruiter email) migrates to `notification_router.emit()`. Direct user-message-reply paths (e.g., `multi_listener` echoing back to a user's command) stay on `telegram_client` for now and migrate only in Phase 3.

**Call-site audit** — Phase 0 task includes grep for every `telegram_client.send_message` and classifying:

| Call site | Migrate to `notification_router`? |
|---|---|
| `morning_briefing.py` | Yes |
| `post_apply_hook.py` | Yes |
| `gmail_agent.py` (priority emails) | Yes |
| `papers/agent.py` (daily digest) | Yes |
| `health watchdog cron` | Yes |
| `multi_listener.py` (echo replies to commands) | No (Phase 3) |
| `telegram bot direct command output` | No (Phase 3) |

### 4.8 `mindgraph_app/main.py` patch

Add five `include_router` calls and import the routers. Update startup logger lines.

```python
from mindgraph_app.ws_endpoint import ws_router
from mindgraph_app.intent_api import intent_router
from mindgraph_app.voice_api import voice_router
from mindgraph_app.auth_api import auth_router
from mindgraph_app.push_api import push_router

app.include_router(ws_router)
app.include_router(intent_router)
app.include_router(voice_router)
app.include_router(auth_router)
app.include_router(push_router)
```

### 4.9 Daemon plist update

`com.jobpulse.brain.json` (or wherever the launchd plist lives) — add `KeepAlive: true`, `RunAtLoad: true`, and prepend `caffeinate -d -i -s` to the program command.

```xml
<key>ProgramArguments</key>
<array>
    <string>/usr/bin/caffeinate</string>
    <string>-d</string><string>-i</string><string>-s</string>
    <string>/path/to/.venv/bin/python</string>
    <string>-m</string><string>jobpulse.runner</string>
    <string>multi-bot</string>
</array>
```

---

## 5. Data contracts

### 5.1 Pairing codes table

```sql
CREATE TABLE pairing_codes (
    code TEXT PRIMARY KEY,                  -- 6-digit short code
    expires_at TEXT NOT NULL,               -- ISO 8601, 60s from creation
    used_at TEXT,                           -- single-use; non-null = consumed
    intended_name TEXT NOT NULL             -- device name CLI passed at pair-init
);
```

### 5.2 Device tokens table (already in Overview)

See §4.3 of `00-design-overview.md`.

### 5.3 WS event log (already in §4.3 above)

### 5.4 Intent dispatch envelope

```typescript
type IntentRequest = {
  // intent_name in path
  payload: Record<string, unknown>;       // intent-specific
  client_uuid?: string;                    // for idempotency
};

type IntentResponse = 
  | { status: "ok", result: unknown }
  | { status: "error", errorCategory: "transient" | "validation" | "permission" | "business",
      message: string, isRetryable: boolean, attemptedAction: string };
```

### 5.5 NotificationEvent

(See `00-design-overview.md` §4.2)

---

## 6. Test plan

### 6.1 Unit tests

- `tests/integration/test_auth_api.py` — pairing happy path, expired code, used code, revoked token, scope check.
- `tests/integration/test_intent_http_coverage.py` — assert every key in `handler_registry.get_handler_map()` resolves under `/api/intents/<key>`.
- `tests/integration/test_ws_endpoint.py` — connect, auth, ping/pong, subscribe, msg, cancel, resume_from.
- `tests/integration/test_voice_api.py` — accepts WebM, rejects oversized, rejects wrong content-type, returns shape.
- `tests/integration/test_notification_router.py` — fanout to all sinks, dedup grouping, sink failure isolation.

### 6.2 Wiring tests

- Start uvicorn against `:memory:` SQLite + mocked Whisper + mocked FCM. Run a full pairing → token → intent → WS → notification → push flow. Asserts no DB drift in production.

### 6.3 Manual smoke

- `wscat` from laptop on different WiFi (via Tailscale) — connects, sends `msg`, receives streamed reply.
- `curl POST /api/voice` with a 5s WebM clip (sample fixture) — returns transcript.
- 24-hour Mac uptime test: launchd-managed daemon survives lid close, network changes (home → café), wake from sleep events.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Some intents need request context (sender_id, chat_id) Telegram provides | Add optional `device` arg to `run_async`; intents that needed `sender_id` use `device.name` as substitute |
| Whisper module is sync and slow → blocks WS | Run in `asyncio.to_thread`; concurrent uploads share thread pool; cap concurrent at 4 |
| Multiple uvicorn workers break in-process `connection_pool` | Assert single worker in startup; document in deployment notes; switch to Redis if scaled |
| `bcrypt` token check is slow on every request | Cache `(token, device_id)` in TTL dict (60s) inside `verify_device_token` — cache invalidated on revoke |
| Adding `run_async` to handlers breaks ones not yet async-ready | Default impl wraps `run` in `to_thread`; no handler is forced to change |
| `event_log` grows unboundedly | Nightly cron deletes rows older than 24 hours |
| Mac IP changes when switching networks | Tailscale MagicDNS resolves a stable hostname; mobile uses hostname not IP |

---

## 8. Files touched

**New**:
- `mindgraph_app/auth_api.py`
- `mindgraph_app/ws_endpoint.py`
- `mindgraph_app/intent_api.py`
- `mindgraph_app/voice_api.py`
- `mindgraph_app/push_api.py`
- `shared/notifications/__init__.py`
- `shared/notifications/router.py`
- `shared/notifications/sinks/fcm.py` (mock impl in this phase)
- `shared/notifications/sinks/ws.py`
- `shared/notifications/sinks/telegram.py` (wraps existing client)
- `shared/voice/whisper_service.py` (extracted)
- `data/device_tokens.db` (created at first run)
- `data/ws_events.db` (created at first run)
- `tests/integration/test_auth_api.py`
- `tests/integration/test_intent_http_coverage.py`
- `tests/integration/test_ws_endpoint.py`
- `tests/integration/test_voice_api.py`
- `tests/integration/test_notification_router.py`

**Modified**:
- `mindgraph_app/main.py` — register new routers
- `jobpulse/runner.py` — `devices` subcommand
- `jobpulse/handler_registry.py` — `BaseHandler.run_async`, `requires_scope`
- All `jobpulse/handlers/*.py` — verify async readiness; mark `requires_scope = "full"` where appropriate
- `morning_briefing.py`, `post_apply_hook.py`, `gmail_agent.py`, `papers/agent.py`, etc. — migrate event sends to `notification_router.emit()`
- `com.jobpulse.brain.json` (launchd plist) — `caffeinate` wrapper
- `scripts/install_cron.py` — add `ws_events` janitor
- `CLAUDE.md` — document new endpoints in Quick Reference

---

## 9. Definition of Done (gate to Phase 1A)

All success criteria checked. Additionally:

- [ ] `python -m pytest tests/integration/ -v` passes 100%.
- [ ] No regressions in existing test suite (`python -m pytest tests/ -v`).
- [ ] `mindgraph_app/main.py` startup log lists all new endpoints.
- [ ] `python -m jobpulse.runner devices list` returns at least one paired test device.
- [ ] `wscat` smoke test completed from a non-Mac machine on the Tailnet.
- [ ] `notification_router.emit(test_event)` triggers Telegram notification (validates fanout still works).
- [ ] `notification_router.emit(test_event)` does NOT trigger FCM in this phase (mock sink); WS sink delivers if a test client is connected.
- [ ] Mac 24-hour uptime stress test logged with zero unreachable windows.
- [ ] `tests/integration/test_intent_http_coverage.py` lists count of intents covered ≥ count of intents in `handler_registry.get_handler_map()`.

When the above hold, the backend is ready for the mobile client. Proceed to **Phase 1A**.
