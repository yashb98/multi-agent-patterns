# Phase 1B — Voice, Push, Offline Queue, and All Agents Wired

**Time**: ~3 weeks active build.
**Pre-conditions**: Phase 1A DoD complete; APK on phone; pairing/biometric/WS-echo proven.
**Goal**: Replace echo handlers with real agent dispatch for all 18 chats; ship voice end-to-end (record → Whisper → intent → agent); wire FCM with full Telegram parity through `notification_router`; persist offline queue in SQLite with reliable drain on reconnect; render multi-agent pattern threads with cancel.

This is the *content* phase — after this, the app does real work.

---

## 1. Goals

By the end of Phase 1B:

1. Every chat connects to its real agent. Sending text or voice produces a real reply from real handlers. Streaming deltas render token-by-token.
2. Voice recording works in any chat input + Hub global input. 60s cap, real-time waveform, on-device review of transcript before send.
3. FCM is configured (real Firebase project), tokens registered with backend, pushes arrive when app is backgrounded. Approval pushes carry inline action buttons that fire `/api/intents/approve` directly.
4. Offline queue lives in `expo-sqlite`; messages typed while offline are persisted, drained on reconnect, surfaced to user with per-message status.
5. Multi-agent pattern threads (Hierarchical, Peer Debate, Dynamic Swarm, Enhanced Swarm, Map-Reduce, Plan-and-Execute) render correctly: per-agent badges, color-coded, with cancel button.
6. Hub pulls real data: live agent cards reflect actual running pipelines; approval cards reflect real dry-run queue; summary tiles reflect today's actual stats.
7. Share intent from Chrome (long-press a URL → Share → NEURALIS → "Process URL") routes to `job-process-url` handler.

## 2. Success criteria (verifiable)

- [ ] Sending "spent £5 on coffee" in Budget chat creates a real budget transaction in `budget.db`.
- [ ] Sending "what's on my calendar tomorrow" in Calendar chat returns the actual next 5 events.
- [ ] Voice-recording "add task buy milk priority high" hits Tasks handler with parsed intent + payload.
- [ ] Tapping "Run Pattern" on Hub FAB → modal → choose Peer Debate, topic "Should we use Rust for the daemon?" → opens new chat thread → 4-6 agents stream their messages → final synthesis card renders → ExperienceMemory has a new row for this run.
- [ ] In an active pattern thread, tapping Cancel sends `{type:"cancel", run_id}`, server interrupts within 5s, app shows "Cancelled" footer.
- [ ] Backgrounding app → triggering a job dry-run on Mac → FCM push arrives within 3s with title "Approval needed: <Company>" and Approve/Reject buttons → tapping Approve fires `confirm_application()` and dismisses notification.
- [ ] Airplane-mode test: type 3 messages across 3 different chats, kill app, restore network, relaunch → all 3 messages drain in order, each marked "sent" once delivered.
- [ ] Daily digest from `papers/agent.py` arrives as a single grouped FCM ("3 papers ready") not 3 separate ones.
- [ ] Long-pressing a Greenhouse job URL in Chrome → Share → NEURALIS → confirms "Process this URL?" → routes to `job-process-url` handler → application enters queue.

## 3. Out of scope

- No Bridge tab toggles yet (read-only continues — Phase 1C wires toggles).
- No Profile push category settings (always-on parity for now — Phase 1C adds per-category mute).
- No on-device cached image previews of CV/CL beyond first fetch (cache lifetime + invalidation deferred to Phase 1C).
- No widgets (home-screen tiles) — Phase 1.5+.
- No streaming Whisper for partial transcripts mid-recording (full-take transcribe only this phase).
- No conflict resolution UI for offline queue failures (drop-and-toast for now).

---

## 4. Component breakdown

### 4.1 Real handler dispatch (server)

`mindgraph_app/intent_api.py` already routes intents. The WS path for chat messages now needs to:

1. Identify the channel (`channel = "agent:budget"` for Budget chat).
2. Map channel → handler:
   - `agent:<name>` → handler with that name OR LLM fallback if name maps to a "free-form chat" agent.
   - `pattern:<run_id>` → already an in-flight pattern run, this is a follow-up message.
   - `global` → run NLP classifier on the text, route to inferred intent.
3. Call handler with payload `{text, voice_transcript_id?, attachments?, context: {device, last_msgs}}`.
4. Stream response — handler is an async generator yielding `MessageDelta` chunks.

```python
# shared/dispatch/agent_dispatch.py (new)
async def dispatch_chat(channel: str, text: str, device: DeviceAuth) -> AsyncIterator[MessageChunk]:
    if channel == "global":
        intent = nlp_classifier.classify(strip_trailing_punct(text))
        async for chunk in dispatch_intent(intent, {"text": text}, device):
            yield chunk
    elif channel.startswith("agent:"):
        agent_name = channel.split(":", 1)[1]
        async for chunk in agent_chat(agent_name, text, device):
            yield chunk
    elif channel.startswith("pattern:"):
        run_id = channel.split(":", 1)[1]
        async for chunk in pattern_message(run_id, text, device):
            yield chunk
    else:
        raise ValueError(f"Unknown channel: {channel}")
```

`agent_chat(agent_name, text)` semantics:
- For deterministic agents (budget, tasks, calendar, gmail, github, papers, briefing): route the text through the agent's existing command parser; output formatted reply.
- For LLM-driven agents (cognitive, memory, fact_check, mindgraph code review): use `smart_llm_call` with the agent's system prompt + last N messages from chat history.
- For job autopilot: text either triggers `job-apply-next`, `job-process-url`, `job-stats`, or asks a question routed via NLP.

### 4.2 Pattern run dispatch

`POST /api/patterns/run` endpoint:

```python
@patterns_router.post("/api/patterns/run")
async def run_pattern(req: PatternRunRequest, device: DeviceAuth = Depends(verify_device_token)):
    run_id = str(uuid4())
    pattern_module = {
        "hierarchical": patterns.hierarchical,
        "peer_debate": patterns.peer_debate,
        ...
    }[req.pattern_name]
    # background task; stream via WS channel "pattern:{run_id}"
    asyncio.create_task(_run_pattern_stream(run_id, pattern_module, req.topic, device))
    return {"run_id": run_id, "channel": f"pattern:{run_id}"}
```

`_run_pattern_stream` wraps the pattern's existing graph `astream` and pushes each step into the connection pool's channel. Each agent step produces:
```json
{"type":"agent.step", "channel":"pattern:<id>", "agent_name":"researcher", "step_kind":"reasoning|tool_call|finalize", "content":"...", "seq":<n>}
```

When pattern reaches `convergence` or `finish`, push `{"type":"run.complete", "run_id":<id>, "summary":<final_synthesis_md>, "cost":<usd>, "iterations":<n>}`.

Cancellation:
- Connection receives `{type:"cancel", run_id}` → sets `cancellation_flags[run_id] = True`.
- Pattern's graph nodes check `state.cancel_flag` between steps; on cancel, gracefully exit.
- ExperienceMemory still records partial run with `final_status: "cancelled"`.

### 4.3 Voice flow (real)

**Mobile** (`mobile/lib/voice.ts`):

```ts
async function startRecording() {
  await Audio.requestPermissionsAsync();
  await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
  const recording = new Audio.Recording();
  await recording.prepareToRecordAsync({
    android: { extension: ".webm", outputFormat: AndroidOutputFormat.WEBM, audioEncoder: AndroidAudioEncoder.OPUS, sampleRate: 48000, numberOfChannels: 1, bitRate: 24000 },
    ios: {/* ... */ },
    web: undefined
  });
  await recording.startAsync();
  return recording;
}

async function uploadAndTranscribe(uri: string, channel: string) {
  const form = new FormData();
  form.append("audio", { uri, name: "voice.webm", type: "audio/webm" } as any);
  form.append("channel", channel);
  const res = await fetch(`${SERVER}/api/voice`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  return res.json();  // { transcript, intent_hint }
}
```

**UI** (`components/voice/MicButton.tsx`):
- Hold-to-record (gesture handler `LongPressGestureHandler`)
- Live waveform via `expo-av` `getStatusAsync` + Reanimated bars
- Release → upload → show transcript editor
- 60s hard cap; auto-stops with toast "Max 60s"

**TranscriptEditor** (`components/voice/TranscriptEditor.tsx`):
- Bottom sheet showing transcript text in editable input
- "Edit" or "Send" buttons
- Discard swipe-down

### 4.4 FCM setup

**Firebase project**: create `neuralis-mobile` in Firebase Console. Add Android app `io.yashbishnoi.neuralis`. Download `google-services.json`, add to `mobile/` (gitignored — committed via `mobile/.gitignore` exclude). EAS Secret holds the file for builds.

**Backend** (`shared/notifications/sinks/fcm.py`):
```python
class FcmSink(NotificationSink):
    name = "fcm"
    def __init__(self):
        cred = credentials.Certificate(os.environ["FCM_SERVICE_ACCOUNT_JSON"])
        firebase_admin.initialize_app(cred)
    
    def send(self, event: NotificationEvent):
        active_devices = db.fetch_active_devices_with_fcm()
        for device in active_devices:
            msg = messaging.Message(
                token=device.fcm_token,
                android=messaging.AndroidConfig(priority="high" if event.category in ["approvals","alerts"] else "normal",
                                                notification=messaging.AndroidNotification(
                                                    channel_id=event.category,
                                                    click_action="OPEN_DEEP_LINK")),
                data={"deep_link": event.deep_link, "actions": json.dumps([a.__dict__ for a in event.actions]),
                      "source": event.source, "dedup_key": event.dedup_key or ""},
                notification=messaging.Notification(title=event.title, body=event.body),
            )
            try:
                messaging.send(msg)
            except messaging.UnregisteredError:
                db.execute("UPDATE device_tokens SET fcm_token = NULL WHERE id = ?", [device.id])
```

**Mobile** (`mobile/lib/push.ts`):
- Register FCM token on app launch (after auth) → `POST /api/push/register`
- Configure notification categories with action buttons (Android channels):
  - `approvals` (high importance, sound) — actions Approve/Reject
  - `alerts` (high importance, sound) — no actions
  - `activity` (default importance) — no actions
  - `digest` (low importance, no sound) — no actions
- Background handler (`expo-notifications` + custom native code via Expo config plugin) responds to action button taps even when app is killed:
  - Action tap → `fetch(POST /api/intents/<action_id>, payload)`
  - Show success toast on next foreground

**Deep link handler**:
- App listens for `Linking.addEventListener("url", ...)`
- Parse `neuralis://chat/jobs?msg_id=123` → navigate to chat tab → that agent → scroll to that message

### 4.5 Offline queue (real, persistent)

`mobile/lib/queue.ts` backed by `expo-sqlite`:

```sql
CREATE TABLE pending_messages (
  uuid TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  text TEXT,
  voice_uri TEXT,
  created_at TEXT NOT NULL,
  attempts INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending'   -- 'pending' | 'sending' | 'failed'
);
CREATE INDEX idx_pending_created ON pending_messages(created_at);
```

```ts
queue.enqueue({channel, text, voiceUri}) → uuid       // writes row, returns uuid
queue.drain()                                          // called on WS ready event
queue.markSent(uuid)                                    // delete row
queue.markFailed(uuid, reason)                         // status='failed', user gets toast
```

Drain logic:
- For each pending message in created_at order:
  - If voice present → upload via `/api/voice` → use returned transcript as text
  - Send `{type:"msg", channel, text, client_uuid}` over WS
  - Wait for `msg.done` with same `client_uuid` (or timeout 30s)
  - On success → `markSent`
  - On failure → `markFailed` (max 3 attempts; after 3rd attempt user must manually retry from a "failed messages" view in Profile)

UI surface:
- Each chat shows pending messages with `clock` icon overlay; failed messages get red `error` icon with retry button.
- Hub badge count of pending messages when offline.

**Risky-write refusal**: handlers tagged `requires_realtime: True` (job-apply, pattern run, email send) refuse offline:
- Mobile knows realtime-required intents from `/api/config` response (each agent has `offline_safe: bool`).
- Send button disabled with tooltip "Needs network" when offline + realtime-required.

### 4.6 Hub real data

`GET /api/hub` returns:
```json
{
  "live_agents": [
    {"id":"jobs", "name":"Job Bot", "status":"processing", "label":"Senior PM at TechCorp", "progress":0.65, "started_at":"..."},
    {"id":"pattern_<id>", "name":"Peer Debate", "status":"iterating", "label":"Should we use Rust", "progress":0.4}
  ],
  "approvals": [
    {"id":"appr_<id>", "kind":"job_dry_run", "company":"Stripe", "role":"Senior Engineer", "preview_url":"/api/jobs/<id>/preview", "actions":["approve","reject","details"]}
  ],
  "summary": {
    "applications_today": 3,
    "papers_unread": 2,
    "budget_today": "£24.50",
    "calendar_next": [{"title":"Standup", "start":"..."}]
  },
  "activity": [{"ts":"...", "icon":"work", "text":"Applied to TechCorp"}]
}
```

Mobile `stores/hub.ts` polls every 30s when foregrounded + receives WS push events that mutate state in real-time. Pull-to-refresh in Hub forces a refetch.

Server emits `{type:"hub.update", patch:{...}}` when state changes (e.g., new approval queued, job complete) — mobile applies as a JSON-patch.

### 4.7 Approval card flow

`components/hub/ApprovalCard.tsx`:
- Company logo (fetched via favicon proxy or `<initial>` glow)
- Role + match score chip
- 3 buttons: Approve (gradient), Reject (outline), Details (text)
- Approve → `POST /api/intents/approve {id}` → triggers `confirm_application()` → card animates out
- Reject → `POST /api/intents/reject {id, reason?}` → bottom-sheet for optional reason
- Details → opens full-screen approval modal (CV preview iframe, JD text, screening Q&A list, fields filled list, dry-run screenshot if available)

Approval modal calls `GET /api/jobs/{id}/preview` returning all data to render.

### 4.8 Multi-agent thread renderer

`app/(tabs)/chat/pattern/[run_id].tsx`:
- Header: pattern name + topic + status pill ("Iterating 2/3", "Converged", "Cancelled")
- Stream: each message tagged with `agent_name`; rendered with that agent's color (researcher = primary-fixed, critic = secondary-fixed-dim, planner = tertiary, executor = primary)
- Per-agent collapsible sections (long agent monologues collapse with "Show more")
- Sticky cancel button (gradient red-tinted) at bottom while iterating
- Final synthesis appears as a special "Synthesis" card at end with cost, iterations, total time

`components/chat/AgentBadge.tsx` — pill with agent name + role; assigns deterministic color based on hash(agent_name) modulo palette.

### 4.9 Share intent from Chrome

`mobile/app.config.ts` adds Android intent filter for `ACTION_SEND` text/url:
```ts
android: {
  intentFilters: [{
    action: "VIEW",
    data: [{ scheme: "neuralis" }],
    category: ["BROWSABLE", "DEFAULT"]
  }, {
    action: "SEND",
    data: [{ mimeType: "text/plain" }],
    category: ["DEFAULT"]
  }]
}
```

`expo-share-intent` package surfaces incoming shared text. App's root layout listens; on incoming URL → bottom-sheet:
- "Process this URL with: [Job Bot ▼] [agent picker]"
- Confirm → `POST /api/intents/job-process-url {url}` → returns toast "Queued"

---

## 5. Data + IPC contracts (additions to Phase 0)

### 5.1 `/api/hub` (GET, polling endpoint)

(See §4.6.)

### 5.2 `/api/jobs/{id}/preview` (GET)

```json
{
  "id": "...",
  "company": "TechCorp",
  "role": "Senior Engineer",
  "jd_text": "…",
  "match_score": 8.4,
  "fields_filled": [{"label":"Email","value":"<redacted>","source":"profile.db"}],
  "screening_qa": [{"question":"Are you authorized to work?","answer":"Yes (Graduate Visa)"}],
  "cv_preview_url": "/api/files/<sha>/cv.pdf",
  "cl_preview_url": "/api/files/<sha>/cl.pdf",
  "dry_run_screenshot_url": "/api/files/<sha>/screenshot.png"
}
```

(Mind the PII policy: server returns these values *only* over an authenticated channel. Mobile renders, doesn't persist beyond session memory. Cached only via `expo-image` ephemeral cache, cleared on logout.)

### 5.3 `/api/patterns/run` (POST)

```json
{ "pattern_name": "peer_debate", "topic": "...", "params": {} }
```
Returns `{ "run_id": "...", "channel": "pattern:..." }`.

### 5.4 `/api/intents/approve` and `/reject`

Approve/reject events go through the same intent dispatcher with `requires_scope = "full"`. Server-side wraps `confirm_application()` + post-apply hooks. Action button taps from FCM hit these directly without app open.

### 5.5 `MessageChunk` envelope (server → client)

```ts
type MessageChunk =
  | { type: "msg.delta", channel: string, seq: number, content: string, role: "agent"|"system", agent_name?: string }
  | { type: "msg.done", channel: string, seq: number, msg_id: string, role, agent_name? }
  | { type: "agent.step", channel, seq, agent_name, step_kind: "thinking"|"tool_call"|"answer", content }
  | { type: "run.complete", run_id, summary_md, cost_usd, iterations, started_at, ended_at }
  | { type: "hub.update", patch: JsonPatch }
```

---

## 6. Test plan

### 6.1 Unit (Jest)

- `lib/queue.test.ts` — enqueue, drain order, retry counter, persistence across "restarts" (fresh SQLite open).
- `lib/voice.test.ts` — recording lifecycle, 60s cap, upload form construction.
- `lib/push.test.ts` — channel registration, deep link parser, action handler for "approve" intent.
- `stores/hub.test.ts` — JSON-patch application, optimistic state during approve action.

### 6.2 Backend integration

- `tests/integration/test_pattern_run.py` — start a small Hierarchical run, assert WS frames emitted, assert ExperienceMemory row created, assert cancel works.
- `tests/integration/test_voice_round_trip.py` — upload fixture audio, assert transcript + intent_hint, assert dispatched correctly.
- `tests/integration/test_fcm_grouping.py` — emit 5 paper events within 60s with same dedup_key, assert single grouped notification.
- `tests/integration/test_approval_action.py` — simulate FCM action tap to `/api/intents/approve`, assert `confirm_application` called, assert post_apply_hook fired.

### 6.3 E2E (Maestro) — extended flows

- Voice: long-press mic in Budget chat → release → review → send → assert reply contains "transaction added" → check `data/budget.db` (test-mode separate DB).
- Pattern run: tap FAB → choose Peer Debate → enter topic → assert thread opens → wait for ≥2 agent steps → assert cancel button appears → tap cancel → assert "Cancelled" footer.
- Offline drain: enable airplane mode → send 2 messages in 2 chats → kill app → restore network → reopen → assert both messages drain + replies appear.
- Share intent: launch test Chrome with stub Greenhouse URL → tap Share → tap NEURALIS → confirm → assert `/api/intents/job-process-url` called.

### 6.4 Manual

- Lock screen action: phone locked → trigger dry-run on Mac → action button on lock screen → tap Approve → unlock → confirm action took effect.
- Slow network: 3G simulation → voice upload + transcribe still completes within 10s.
- Many chats: open all 18 agent chats, switch rapidly, assert no memory leak (RN bridge + Zustand stable across 5min of fast-switching).

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| FCM deliveries silently dropped on certain Android OEMs (Xiaomi, Huawei aggressive battery savers) | Document required "auto-start" / "battery whitelist" steps in Profile > Help; show in-app banner if FCM token registered but no push received in 24h |
| Whisper latency variable (server cold start) | Show "Transcribing…" UI; consider on-device fallback later (whisper.cpp via JSI) |
| Pattern runs exceed connection pool in-process limits when many agents stream concurrently | Add per-device connection cap (5); document in protocol |
| Offline queue + voice = large local storage if user records many voice messages while offline | Cap voice files to 60s × 24kbps ≈ 180KB each; warn if queue >50MB |
| Approve action button works but rejection requires reason → no native FCM reason-input UI | Reject button without reason just dismisses; reason captured in app on next open |
| Intent that takes >10s to respond stalls WS reply | Send progress `msg.delta` updates every 2s ("still working…"); UI shows typing indicator |
| Action buttons on grouped notifications ambiguous (which paper to approve?) | Grouped pushes have no actions; user opens the chat to act |
| `expo-share-intent` may require ejecting from managed workflow on some Expo SDKs | Use `EAS Build` (custom dev client) to avoid eject; pin to Expo SDK 52+ |

---

## 8. Files touched

**New** (mobile/):
- `app/(tabs)/chat/pattern/[run_id].tsx`
- `app/share-incoming.tsx` (handles `ACTION_SEND` intent)
- `components/voice/{MicButton,WaveformPreview,TranscriptEditor}.tsx`
- `components/chat/{AgentBadge,CodeBlock,FileCard,ChartBlock,ActionRow,SystemMessage,SynthesisCard}.tsx`
- `components/hub/{ApprovalModal,RunPatternModal}.tsx`
- `lib/voice.ts`, `lib/push.ts`, `lib/queue.ts` (replaces in-memory), `lib/share-intent.ts`, `lib/agent-color.ts`
- `stores/queue.ts` (refactor to read SQLite)
- `tests/unit/{voice,queue,push,hub-store}.test.ts`
- `tests/e2e/{voice-flow,pattern-run,offline-drain,share-intent}.yaml`

**New** (backend):
- `mindgraph_app/patterns_api.py` — `/api/patterns/*`, `/api/hub`, `/api/jobs/<id>/preview`
- `shared/dispatch/agent_dispatch.py` — channel routing
- `shared/dispatch/pattern_runner.py` — run streaming wrapper
- `shared/notifications/sinks/fcm.py` — real impl
- `tests/integration/test_pattern_run.py`, `test_voice_round_trip.py`, `test_fcm_grouping.py`, `test_approval_action.py`

**Modified**:
- `jobpulse/handler_registry.py` — `offline_safe`, `requires_realtime` flags per handler
- `mindgraph_app/main.py` — register `patterns_router`
- `mindgraph_app/intent_api.py` — wire `approve` / `reject` intents
- `jobpulse/post_apply_hook.py` — emit notification with deep link to job chat
- `morning_briefing.py`, `papers/agent.py` — use grouped notifications via `dedup_key`
- `app.config.ts` — intent filters
- `eas.json` — secrets reference for `FCM_SERVICE_ACCOUNT_JSON` and `google-services.json`

---

## 9. Definition of Done (gate to Phase 1C)

- [ ] All success criteria checked.
- [ ] Every chat in `lib/agents.ts:AGENTS` produces a real reply for at least one canonical input.
- [ ] FCM push parity audit: every Telegram-emitted notification today triggers FCM. Audit log file `docs/superpowers/specs/mobile-app-integration/_audit/notifications-parity.md` enumerates each event with checkbox.
- [ ] 24-hour soak: APK installed, used naturally for a day; no crashes; no missed pushes; no stuck queue items.
- [ ] All new backend tests pass; existing test suite passes.
- [ ] `tests/e2e/` Maestro flows pass on real device.
- [ ] Memory profile: app heap stable under 200MB after 30min of mixed use.
- [ ] Battery: app drains <8%/hour foreground, <0.5%/hour background.
- [ ] Telegram still receives all events (shadow mode active).

When the above hold, the app does the *work*. Proceed to **Phase 1C** for Bridge/Profile breadth + ship polish.
