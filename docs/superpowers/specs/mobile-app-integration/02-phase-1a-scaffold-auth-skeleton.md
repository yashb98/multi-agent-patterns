# Phase 1A — Scaffold, Auth, and Tab Skeletons

**Time**: ~2.5 weeks active build.
**Pre-conditions**: Phase 0 DoD checklist complete; backend reachable from phone via Tailscale.
**Goal**: Boot the Expo project, render the four tabs with correct theme + fonts, complete the pairing + biometric flow, and have a WebSocket client that connects, authenticates, sends/receives `ping`/`pong`, and reconnects through network changes. **No real agent flows yet** — the screens are skeletons fed by mock data and an "echo" channel on the server.

---

## 1. Goals

By the end of Phase 1A:

1. APK installable on the phone via EAS internal track.
2. App launches, biometric unlocks, paired device → token in Keystore → WS connects → user sees four tabs.
3. Hub tab shows a static layout with one mock "agent card," one mock "approval card," and a sticky quick-input that echoes through to backend and back.
4. Chat tab lists 18 chat rows (one per agent) and tapping any opens an empty chat with a working text input that round-trips through `/ws` echo channel.
5. Bridge tab and Profile tab are minimal — Bridge shows an integrations list (read-only via `/api/config`), Profile shows device name + "Sign out" (revokes token, logs back into pairing).
6. Visual fidelity matches mockups: glass panels, neon accents, typography, dark/light support deferred (light only this phase).

## 2. Success criteria (verifiable)

- [ ] `eas build -p android --profile internal` produces a `.apk` that installs on the user's Pixel.
- [ ] Cold launch on phone with no cached token → routes to `/pair` screen with QR/code input → entering valid 6-digit code from `python -m jobpulse.runner devices pair --name=Yash-Pixel-9` completes pairing → shows Hub.
- [ ] Cold launch with valid cached token → biometric prompt → on success → Hub.
- [ ] Killing the WS server while app is open → app shows "reconnecting…" badge in top app bar, reconnects within 5s when server returns, badge clears.
- [ ] Sending a message in Hub quick-input → server logs the message on the echo channel → reply appears in chat list as last message of "Echo" channel.
- [ ] Toggling airplane mode mid-session → app shows offline indicator, queues outgoing messages, drains on reconnect (full queue logic stubbed; actual offline persistence is Phase 1B).
- [ ] All theme tokens from mockup match: `tailwind.config.js` has 50+ named tokens, all referenced from at least one screen.
- [ ] Fonts loaded: Space Grotesk (700, 500) + Manrope (400, 500, 600) — typography appears correct on cold launch (no FOIT).
- [ ] Detox/Maestro smoke flow: launch → biometric mock pass → tab through all four tabs → quick-input echo round-trip — passes.

## 3. Out of scope

- No agent-specific flows (all chats are echo via server stub).
- No voice (mic button visible but disabled with tooltip "Phase 1B").
- No FCM (push permission asked but no real notifications wired).
- No offline persistence (queue is in-memory only; restart drops it — Phase 1B fixes).
- No multi-agent thread rendering (placeholder UI only).
- No share-sheet from Chrome (intent filter declared but handler is a no-op alert).

---

## 4. Component breakdown

### 4.1 Project scaffold

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns
npx create-expo-app mobile --template tabs@52
cd mobile
npx expo install nativewind tailwindcss react-native-reanimated react-native-gesture-handler
npx expo install expo-secure-store expo-local-authentication expo-av expo-notifications \
                 expo-share-intent expo-sqlite expo-haptics expo-blur expo-font \
                 @expo-google-fonts/space-grotesk @expo-google-fonts/manrope
bun add zustand date-fns
bun add -D @types/react @types/node detox eas-cli
```

`mobile/.gitignore` adds `node_modules/`, `.expo/`, `dist/`, `*.apk`. Repo-root `.gitignore` already excludes `node_modules` from prior config.

### 4.2 Theme + fonts

- `mobile/tailwind.config.js` — full token palette from `00-design-overview.md` §5.3.
- `mobile/theme/fonts.ts` — `useFonts` hook loading Space Grotesk + Manrope; `_layout.tsx` blocks render until loaded.
- `mobile/components/primitives/GlassPanel.tsx` — wraps `BlurView` + bg-white/70 + inset white border + shadow.
- `mobile/components/primitives/NeonGlow.tsx` — `boxShadow: 0 0 15px primary-fixed` with optional pulse animation via Reanimated.
- `mobile/components/primitives/Pill.tsx`, `Card.tsx`, `Button.tsx` — match mockup `rounded-full`, `rounded-xl`, `gradient-living` button gradient (`primary` → `primary-fixed-dim`).

### 4.3 Expo Router structure

```
app/
├── _layout.tsx                    # root: load fonts, biometric gate, WS init, theme provider
├── locked.tsx                     # biometric prompt; on success → tabs
├── pair.tsx                       # pairing screen (no token in Keystore)
├── (tabs)/
│   ├── _layout.tsx                # 4-tab bottom bar with NEURALIS top app bar
│   ├── hub.tsx
│   ├── chat/
│   │   ├── index.tsx
│   │   └── [agent].tsx            # placeholder: shows agent name + empty message list + input
│   ├── bridge.tsx
│   └── profile.tsx
└── +not-found.tsx
```

**Routing rules**:

- `app/_layout.tsx` decides initial route:
  - No token in Keystore → `Redirect` to `/pair`
  - Token present, biometric not yet passed this session → `Redirect` to `/locked`
  - Both pass → `(tabs)/hub`

### 4.4 Pairing screen (`app/pair.tsx`)

UI:
- NEURALIS wordmark at top
- Heading "Add this device" (Space Grotesk 32pt)
- Body "On your Mac, run: `python -m jobpulse.runner devices pair --name=<this-device-name>`"
- 6-digit code input (segmented, large)
- Optional QR scan button — deferred to Phase 1C polish (camera setup); for 1A, code-only
- "Connect" button (gradient)
- Error toast on bad/expired code

Flow:
1. User enters code.
2. App calls `POST /api/auth/pair` with `{code, name: getDeviceName()}` — `name` defaults to `Device.modelName` from `expo-device`, user-editable.
3. On 200, response `{token}` stored via `expo-secure-store.setItemAsync("auth_token", token, {requireAuthentication: true})`.
4. Navigate to `/locked` (biometric gate).

`getDeviceName()` uses `expo-device`'s `modelName` + first 4 chars of `installationId` to disambiguate multiple Pixels.

### 4.5 Biometric gate (`app/locked.tsx`)

UI:
- Centered NEURALIS wordmark
- "Unlock NEURALIS" body
- Biometric icon (`fingerprint` from Material Symbols)
- "Use device PIN" fallback link
- Auto-prompts on mount

Flow:
- `LocalAuthentication.authenticateAsync({promptMessage, fallbackLabel: "Use PIN"})`
- On success → token retrieved from Keystore (this also requires biometric due to `requireAuthentication: true` set during write) → ready
- On failure → retry button
- 3 failed attempts → force re-pair (clear Keystore) — protect against shoulder-surfing

### 4.6 Tab layout (`app/(tabs)/_layout.tsx`)

Top app bar (sticky):
- NEURALIS wordmark left
- Search icon right (no-op in 1A — Phase 1C wires global search)
- Glassmorphic background: `bg-emerald-50/70 backdrop-blur-xl shadow-[inset_0_1px_0_0_rgba(255,255,255,0.4)]`
- Connection badge: small dot under wordmark — primary-fixed pulse when connected, error red when disconnected, peach when reconnecting

Bottom tab bar (the mockup's floating pill):
- Centered, w=90%, `rounded-full`, `bg-white/60 backdrop-blur-2xl`, `shadow-2xl shadow-emerald-500/10`
- Items: Hub (compass), Chat (forum), Bridge (storefront — re-iconed as `hub` icon for "Bridge"), Profile (person)
- Active item: `bg-emerald-400/20` ring + neon glow

### 4.7 Hub tab (`app/(tabs)/hub.tsx`) — skeleton

Sections rendered top to bottom:
1. **Greeting** — "Good morning, Yash" using Space Grotesk; date pill ("Mon, May 4")
2. **Live agents** — horizontal scroll of agent cards (mock data: 1 card "Job Bot — Processing 65%")
3. **Pending approvals** — vertical stack of cards (mock: 0 cards in 1A)
4. **Today summary** — bento 2×2 (apps, papers, budget, calendar — all mock zeros for now)
5. **Recent activity** — vertical list (mock 3 entries)
6. **Quick-input** (sticky at bottom, above tab bar) — text input + mic button (disabled) + send button

Quick-input behavior:
- Focused → top half scrolls under header
- Send button hits `lib/ws.ts:sendMessage("global", text)`
- `global` channel server-side routes to a debug echo handler that returns `text + " [echoed]"`
- Reply renders as a system toast at bottom of Hub: "Echo: <text> [echoed]"

### 4.8 Chat tab (`app/(tabs)/chat/index.tsx` and `[agent].tsx`)

`index.tsx`:
- Title "Chats"
- Vertical list of 18 rows from `lib/agents.ts:AGENTS` — name, icon, "no messages yet" subtitle (1A), unread badge (always 0 in 1A)
- Tap → `/chat/<agent_id>`

`[agent].tsx`:
- Header: agent icon + name + "Synced" pill (when WS connected)
- Empty message list: "Start a conversation"
- Sticky input bar at bottom (text + mic disabled + send)
- Send → `sendMessage(agentId, text)` — backend echo handler returns `"[<agent>] You said: <text>"`
- Render messages with `MessageBubble` primitive (user-right gradient, agent-left glass-panel)

### 4.9 Bridge tab (`app/(tabs)/bridge.tsx`)

Read-only in 1A:
- Section "Integrations" — list cards from `GET /api/config` (Notion, Drive, Gmail, GitHub, Telegram, Tailscale-self status)
- Section "System" — single card: "Daemon — running" with last-seen timestamp from `/api/health`
- Phase 1B will add toggles + agent enable/disable

`/api/config` returns:
```json
{
  "integrations": [
    {"name": "notion", "status": "connected", "label": "Notion"},
    {"name": "drive", "status": "connected", "label": "Google Drive"},
    {"name": "gmail", "status": "connected", "label": "Gmail"},
    {"name": "github", "status": "connected", "label": "GitHub"},
    {"name": "telegram", "status": "connected", "label": "Telegram"}
  ],
  "agents": [/* one entry per agent with name, icon, default_chat_channel */]
}
```

(Add this endpoint as part of Phase 0 if not already there — backport.)

### 4.10 Profile tab (`app/(tabs)/profile.tsx`)

1A scope:
- Profile header card — user-defined display name (default `device.name`); avatar = mint glow + initial. **No real avatar/PII** — see PII policy.
- Stats bento — "Connected agents: <count>", "Uptime: <last_seen>"
- Buttons:
  - "Re-pair this device" (revokes token via `/api/auth/revoke` + clears Keystore + → `/pair`)
  - "Sign out" (clears Keystore only; token stays valid until re-paired)

### 4.11 WebSocket client (`mobile/lib/ws.ts`)

Single module. State machine:

```
disconnected ── connect() ──> connecting ── auth.ok ──> ready
                                  │
                                  └── auth.fail / close ──> failed (no auto retry; UI shows re-pair)
ready ── close (clean / unclean) ──> reconnecting ── connect() ──> connecting...
ready ── ping timeout ──> reconnecting
```

API:
```ts
wsClient.connect()
wsClient.disconnect()
wsClient.subscribe(channel: string): unsubscribe-fn
wsClient.sendMessage(channel: string, text: string, clientUuid?: string): Promise<void>
wsClient.onFrame(frame: WSFrame): void           // exposed for tests
wsClient.state: "disconnected" | "connecting" | "ready" | "reconnecting" | "failed"
```

Backoff: 1s → 2s → 4s → 8s → 16s → 30s (cap). Resets on successful connect.

Heartbeat: send `{type:"ping", t: Date.now()}` every 30s. If no `pong` for 60s, close + reconnect.

Resume: on reconnect, send `{type:"resume_from", server_seq: lastKnownSeq}` after `auth`. Server replays missed events.

### 4.12 Stores (Zustand)

```ts
// stores/auth.ts
{ token: string | null, deviceName: string | null, scope: "full"|"demo", biometricPassed: boolean,
  setToken, clearToken, markBiometric }

// stores/connection.ts
{ state, lastSeq, set, setLastSeq }

// stores/chat.ts
{ channels: Record<string, ChannelState>, appendMessage, appendDelta, finalizeMessage, setHistory }

// stores/hub.ts
{ liveAgents, approvals, summary, activity, set }

// stores/queue.ts (placeholder; in-memory in 1A)
{ pending: PendingMsg[], enqueue, drain }
```

### 4.13 EAS Build profiles

```json
// mobile/eas.json
{
  "build": {
    "internal": {
      "android": { "buildType": "apk", "distribution": "internal" }
    },
    "preview": {
      "android": { "buildType": "apk", "distribution": "internal" }
    },
    "production": {
      "android": { "buildType": "app-bundle" }
    }
  },
  "submit": {
    "production": {
      "android": { "track": "internal" }
    }
  }
}
```

`app.config.ts`:
- `name: "NEURALIS"`, `slug: "neuralis"`, `version: "0.1.0"`
- `android.package: "io.yashbishnoi.neuralis"`
- `scheme: "neuralis"` (deep links)
- `extra.serverUrl: process.env.NEURALIS_SERVER_URL ?? "http://<mac-magic-dns>:8000"` (configurable per build)
- Intent filters for `neuralis://...`

---

## 5. Data + IPC contracts

All backend contracts are inherited from Phase 0. New in Phase 1A:

- `GET /api/config` — returns integrations + agents list. Static + cheap.
- WebSocket "echo" handler at server side — `intent_dispatcher` recognizes channel `global` or any unrecognized agent name and replies with `[echoed]` suffix. Used solely for 1A bring-up; remains as developer/health probe in later phases.

---

## 6. Test plan

### 6.1 Unit (Jest)

- `lib/ws.test.ts` — backoff schedule, reconnect after close, heartbeat timeout, resume frame on reconnect.
- `stores/chat.test.ts` — `appendDelta` accumulates correctly across out-of-order seqs (with reorder buffer up to 32).
- `stores/auth.test.ts` — token clear on revoke; biometric required after idle.

### 6.2 Integration

- Start FastAPI in test mode with mock device token. RN test renderer mounts `_layout` → asserts initial route based on Keystore state.
- WebSocket smoke: connect to test server, send `msg`, assert echo arrives within 1s.

### 6.3 E2E (Maestro)

```yaml
appId: io.yashbishnoi.neuralis
---
- launchApp
- assertVisible: NEURALIS
- inputText: "482917"  # mock paired code in test build
- tapOn: "Connect"
- assertVisible: "Hub"
- tapOn: "chat-tab"
- assertVisible: "Job Bot"
- tapOn: "Job Bot"
- inputText: "hello"
- tapOn: "send-button"
- assertVisible: "[echoed]"
```

### 6.4 Manual

- Cold launch on real Pixel from EAS-built APK.
- Toggle airplane mode for 30s → reconnect indicator → reconnect succeeds → message round-trip OK.
- Pair from a friend's Tailnet-joined Android phone with `--scope demo` token (verifies multi-device pairing).

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `expo-secure-store` `requireAuthentication` interaction with biometric is buggy on some devices | Fall back to storing token without biometric requirement if first store throws; rely on biometric gate at app level |
| Font loading flicker on cold start | Show splash with NEURALIS wordmark until `useFonts` resolves; cache fonts after first load |
| Keystore loss on app uninstall ⇒ re-pair every time | Acceptable; document in onboarding |
| WebSocket connect over Tailscale CGNAT may require DERP relay; perceptible delay | Show "connecting…" UI for up to 8s; failure UX after that |
| EAS build over Wi-Fi takes 15+ min on first build | Acceptable; second+ builds are 4-6 min with EAS cache |
| Mockup uses `bg-emerald-*` Tailwind classes that aren't in our token palette | Replace with `bg-primary-container/30` etc.; do an explicit class-by-class translation pass |

---

## 8. Files touched

**New** (mobile/):
- All scaffold per §4.1
- `app/_layout.tsx`, `app/locked.tsx`, `app/pair.tsx`
- `app/(tabs)/_layout.tsx`, `hub.tsx`, `chat/index.tsx`, `chat/[agent].tsx`, `bridge.tsx`, `profile.tsx`
- `components/primitives/{GlassPanel, NeonGlow, Pill, Card, Button, MessageBubble}.tsx`
- `components/hub/{AgentCard, ApprovalCard, QuickInput, SummaryTile, ActivityRow}.tsx`
- `components/chat/{AgentBadge}.tsx` (more in 1B)
- `lib/ws.ts`, `lib/auth.ts`, `lib/api.ts`, `lib/agents.ts`, `lib/deep-link.ts`, `lib/queue.ts` (in-memory)
- `stores/{auth, connection, chat, hub, queue}.ts`
- `theme/{fonts.ts, tokens.ts}`
- `tailwind.config.js`, `babel.config.js`, `metro.config.js`, `app.config.ts`, `eas.json`
- `tests/unit/*.test.ts`, `tests/integration/*.test.ts`, `tests/e2e/*.yaml`
- `mobile/README.md`

**New** (backend additions for 1A):
- `mindgraph_app/config_api.py` — `/api/config` endpoint
- Echo handler registration in `intent_dispatcher`

**Modified**:
- Repo-root `.gitignore` — `mobile/node_modules/`, `mobile/.expo/`, `mobile/dist/`, `mobile/*.apk`
- `mindgraph_app/main.py` — register `config_router`
- `CLAUDE.md` — add `mobile/` to project structure section

---

## 9. Definition of Done (gate to Phase 1B)

- [ ] All success criteria checked.
- [ ] Real device install: APK runs on user's primary phone, full pairing → biometric → Hub flow works.
- [ ] Theme tokens verified against mockup screenshots side-by-side; no off-token colors in any component.
- [ ] `bun test` passes; no lint errors.
- [ ] Backend integration tests for `/api/config` and echo channel pass.
- [ ] No console warnings in production-mode launch.
- [ ] Repo-root `python -m pytest tests/ -v` still 100% green (no backend regressions).
- [ ] `mobile/README.md` documents: how to dev (`bun expo start`), how to build (`eas build`), how to set `NEURALIS_SERVER_URL`.

When the above hold, the app shell is real and ready for actual flows. Proceed to **Phase 1B**.
