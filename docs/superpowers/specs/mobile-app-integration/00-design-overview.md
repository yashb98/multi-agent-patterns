# NEURALIS Mobile — Design Overview

**Read this first.** Phase docs assume you understand the architecture, contracts, and migration model laid out here.

---

## 1. Product framing

NEURALIS is a single Android-native application that becomes the user's only daily-driver interface to `multi_agent_patterns`. It replaces the existing 5-bot Telegram surface (`jobpulse/multi_listener.py` + handler registry) wholesale, via a multi-week shadow-mode migration.

The user is sole-operator. Multi-user, marketplace, and community features are explicitly **out of scope** ("Product Y" deferred indefinitely). The app is private to the user's Tailnet.

### What it must do better than Telegram

1. **Render multi-agent threads** — pattern runs (Hierarchical, Peer Debate, Dynamic Swarm, Enhanced Swarm, Map-Reduce, Plan-and-Execute) stream multiple agent voices into one threaded view with per-agent badges.
2. **Approve dry-run job applications** with side-by-side preview (CV/CL, JD, screening answers, filled fields) and one-tap Approve/Reject.
3. **Voice-first input** anywhere — hold-to-record fires Whisper → NLP classifier → agent dispatch.
4. **Live state** at a glance — Hub bento grid shows what's running across all 14 subsystems.
5. **Rich media** — code blocks, charts, file cards, inline citations, action buttons that Telegram approximates poorly.

### What it must not lose vs Telegram

Every event Telegram pushes today must reach the phone via the new `notification_router`. Every NLP intent must work via text or voice. Every command/reply pattern must have a native equivalent.

---

## 2. The 14 subsystems the app fronts

| # | Subsystem | Codebase entry | Mobile chat name | Push class |
|---|---|---|---|---|
| 1 | Job autopilot | `jobpulse/applicator.py`, `ApplicationOrchestrator` | Job Bot | `approvals` (high) |
| 2 | Budget | `jobpulse/budget_agent.py` | Budget | `alerts` |
| 3 | Tasks | `jobpulse/tasks_agent.py` | Tasks | `activity` |
| 4 | Calendar | `jobpulse/calendar_agent.py` | Calendar | `alerts` |
| 5 | Gmail | `jobpulse/gmail_agent.py` | Gmail | `alerts` (priority) |
| 6 | GitHub | `jobpulse/github_agent.py` | GitHub | `digest` |
| 7 | arXiv / papers | `jobpulse/arxiv_agent.py`, `papers/` | Papers | `digest` |
| 8 | Briefing | `jobpulse/morning_briefing.py` | Briefing | `digest` |
| 9 | Pattern: Hierarchical | `patterns/hierarchical.py` | Hierarchical | `activity` |
| 10 | Pattern: Peer Debate | `patterns/peer_debate.py` | Peer Debate | `activity` |
| 11 | Pattern: Dynamic Swarm | `patterns/dynamic_swarm.py` | Dynamic Swarm | `activity` |
| 12 | Pattern: Enhanced Swarm | `patterns/enhanced_swarm.py` | Enhanced Swarm | `activity` |
| 13 | Pattern: Map-Reduce | `patterns/map_reduce.py` | Map-Reduce | `activity` |
| 14 | Pattern: Plan-and-Execute | `patterns/plan_and_execute.py` | Plan-and-Execute | `activity` |
| 15 | MindGraph CodeGraph | `mindgraph_app/codegraph_api.py` | CodeGraph | `activity` |
| 16 | Cognitive engine | `shared/cognitive/` | Think | `activity` |
| 17 | Memory layer | `shared/memory_layer/` | Memory | `activity` |
| 18 | Fact checker | `shared/fact_checker.py` | Fact Check | `activity` |

(15+ chats; 14 was a rounding from earlier brainstorming. Treat 18 as the working list.)

`shared/optimization/`, `shared/adversarial/`, `shared/governance/`, `shared/execution/` are **not** primary chats — they surface as Hub stat tiles, system-health cards in Bridge, or filters on existing chats. Phase 1.5+ may promote `shared/cognitive` reflection logs to a dedicated "Reflexion" chat.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Phone (Android)                              │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  RN + Expo + NativeWind                                         │  │
│  │  ┌──────────┐  ┌──────────────────┐  ┌────────────────────┐   │  │
│  │  │  Tabs    │  │  Zustand stores  │  │  expo-sqlite cache │   │  │
│  │  │ Hub Chat │  │  msgs, agents,   │  │  + pending queue   │   │  │
│  │  │ Br. Prof │  │  hub, auth, push │  │                    │   │  │
│  │  └──────────┘  └──────────────────┘  └────────────────────┘   │  │
│  │       │                  │                       │             │  │
│  │       └──────────────────┴───────────────────────┘             │  │
│  │                          │                                     │  │
│  │  ┌───────────────────────┴───────────────────────────┐         │  │
│  │  │  WebSocket client (auth, multiplex, heartbeat)    │         │  │
│  │  │  Voice recorder (expo-av) + uploader              │         │  │
│  │  │  FCM listener (expo-notifications)                │         │  │
│  │  │  Biometric gate (expo-local-authentication)       │         │  │
│  │  └────────────────────────┬──────────────────────────┘         │  │
│  └───────────────────────────┼────────────────────────────────────┘  │
└──────────────────────────────┼───────────────────────────────────────┘
                               │
                  Tailscale WireGuard mesh (private)
                               │
┌──────────────────────────────┼───────────────────────────────────────┐
│                              │            Mac (always-on, daemon)    │
│  ┌───────────────────────────┴───────────────────────────────────┐  │
│  │              FastAPI (mindgraph_app/main.py)                   │  │
│  │  ┌──────────┐ ┌────────────┐ ┌──────────────┐ ┌────────────┐  │  │
│  │  │ /ws      │ │ /api/      │ │ /api/voice   │ │ /api/auth/ │  │  │
│  │  │ WebSock. │ │ intents/*  │ │ (Whisper)    │ │ pair, rev. │  │  │
│  │  └──────────┘ └────────────┘ └──────────────┘ └────────────┘  │  │
│  │  ┌──────────┐ ┌────────────┐ ┌──────────────┐ ┌────────────┐  │  │
│  │  │ /api/    │ │ /api/      │ │ /api/        │ │ /api/      │  │  │
│  │  │ codegrph │ │ patterns/* │ │ jobs/*       │ │ push/reg   │  │  │
│  │  └──────────┘ └────────────┘ └──────────────┘ └────────────┘  │  │
│  └───────────────────────────┬───────────────────────────────────┘  │
│                              │                                       │
│  ┌───────────────────────────┴───────────────────────────────────┐  │
│  │       notification_router (NEW, replaces multi_listener fan)  │  │
│  │   ┌────────┐  ┌────────┐  ┌──────────────┐                    │  │
│  │   │ FCM    │  │  WS    │  │  Telegram    │  (Telegram fanout │  │
│  │   │ pusher │  │ pusher │  │  pusher      │   removed Phase 4) │  │
│  │   └────────┘  └────────┘  └──────────────┘                    │  │
│  └───────────────────────────┬───────────────────────────────────┘  │
│                              │                                       │
│  ┌───────────────────────────┴───────────────────────────────────┐  │
│  │            Existing agents, patterns, DBs, daemon             │  │
│  │   jobpulse/, patterns/, mindgraph_app/, shared/, data/*.db    │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Data flow (representative)

**Voice → intent → agent → reply (full happy path)**:

1. User holds mic in chat input → `expo-av` records 48kHz Opus.
2. Release → app sends WS frame `{type: "voice.upload", channel: "global", audio: <bytes>}` *or* `POST /api/voice` (multipart).
3. Server runs Whisper (`shared/voice.py` — new), returns transcript.
4. App displays transcript with edit affordance, awaits user "send."
5. On send → app sends `{type: "msg", channel: "<agent>", text: <transcript>}`.
6. Server pipeline: `nlp_classifier.classify()` → `handler_registry.get_handler_map()[intent]` → handler executes → response streamed back via `{type: "msg.delta", channel, seq, content}` + final `{type: "msg.done", channel, seq}`.
7. Agent reply rendered in chat with rich components.
8. `notification_router.emit()` fires `activity` push only if app is backgrounded.

**Pattern run with cancel**:

1. User taps "Run" FAB → modal: pattern picker + topic input.
2. Submit → `POST /api/patterns/run` returns `run_id`.
3. App opens new chat thread `chat://patterns/<run_id>`, subscribes via WS `{type: "subscribe", channel: "pattern_run:<run_id>"}`.
4. Each agent step streams via `{type: "agent.step", channel, agent_name, step_kind, content}`.
5. User taps "Cancel" → app sends `{type: "cancel", run_id}` → server signals run loop → cleanup → final `{type: "run.cancelled"}`.
6. ExperienceMemory still records partial run for learning (per-pattern policy).

---

## 4. Backend additions (Phase 0 detail)

### 4.1 `mindgraph_app/main.py` additions

```python
# New imports
from mindgraph_app.ws_endpoint import ws_router
from mindgraph_app.intent_api import intent_router
from mindgraph_app.voice_api import voice_router
from mindgraph_app.auth_api import auth_router
from mindgraph_app.push_api import push_router

# After existing routers:
app.include_router(ws_router)        # /ws
app.include_router(intent_router)    # /api/intents/*
app.include_router(voice_router)     # /api/voice
app.include_router(auth_router)      # /api/auth/*
app.include_router(push_router)      # /api/push/*
```

All Phase 0 endpoint routers are described in detail in `01-phase-0-backend-prereqs.md`.

### 4.2 `notification_router` module (new)

**Location**: `shared/notifications/router.py` (new module).

**Single emit signature**:

```python
@dataclass
class NotificationEvent:
    category: Literal["approvals", "alerts", "activity", "digest"]
    title: str
    body: str
    deep_link: str           # e.g. "neuralis://chat/jobs?msg_id=123"
    actions: list[NotificationAction] = field(default_factory=list)  # optional inline buttons
    dedup_key: str | None = None     # for sliding-window grouping
    source: str              # subsystem name e.g. "jobs", "budget"

@dataclass
class NotificationAction:
    label: str
    action_id: str           # tapped → POST /api/intents/<action_id>
    payload: dict            # opaque, sent with action

def emit(event: NotificationEvent) -> None:
    """Fan out to FCM, WS-if-connected, Telegram (until Phase 4)."""
```

Existing call sites in `multi_listener.py`, `morning_briefing.py`, `post_apply_hook.py`, etc. migrate to `notification_router.emit()`. Telegram fanout becomes one of three sinks; FCM and WS are the other two.

### 4.3 `device_tokens` table

```sql
CREATE TABLE device_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,           -- "Yash-Pixel-9"
    token_hash TEXT NOT NULL UNIQUE,     -- bcrypt of bearer token
    fcm_token TEXT,                      -- FCM registration token (set by /api/push/register)
    created_at TEXT NOT NULL,            -- ISO 8601
    last_seen_at TEXT,
    revoked_at TEXT,                     -- NULL if active
    scope TEXT DEFAULT 'full'            -- "full" | "demo" (demo = read-only)
);
CREATE INDEX idx_device_tokens_active ON device_tokens(revoked_at) WHERE revoked_at IS NULL;
```

Lives in `data/device_tokens.db` (separate DB so it can be backed up / restored independently).

### 4.4 WebSocket protocol envelope

```typescript
type WSFrame =
  | { type: "auth", token: string }                          // first frame after connect
  | { type: "auth.ok", device_name: string, server_seq: number }
  | { type: "auth.fail", reason: string }
  | { type: "ping", t: number }                              // every 30s from client
  | { type: "pong", t: number }                              // server echoes
  | { type: "subscribe", channel: string }                   // join a chat/run
  | { type: "unsubscribe", channel: string }
  | { type: "msg", channel: string, text: string, client_uuid: string }
  | { type: "msg.delta", channel: string, seq: number, content: string }   // streaming token
  | { type: "msg.done", channel: string, seq: number, msg_id: string }
  | { type: "agent.step", channel: string, agent_name: string, step_kind: string, content: string, seq: number }
  | { type: "cancel", run_id: string }
  | { type: "run.cancelled", run_id: string }
  | { type: "voice.upload", channel: string, audio_b64: string }            // small audio inline
  | { type: "voice.transcript", channel: string, text: string }
  | { type: "resume_from", server_seq: number };             // post-reconnect

// All frames carry implicit `seq` from server; client uses last known seq for resume.
```

### 4.5 Intent HTTP wrapper

For every intent registered in `jobpulse/intent_registry.py` not currently exposed via HTTP, add a thin wrapper:

```python
# mindgraph_app/intent_api.py
@intent_router.post("/api/intents/{intent_name}")
async def dispatch_intent(
    intent_name: str,
    payload: dict,
    auth: DeviceAuth = Depends(verify_device_token),
):
    handler = handler_registry.get_handler_map().get(intent_name)
    if handler is None:
        raise HTTPException(404, f"Unknown intent: {intent_name}")
    return await handler.run_async(payload)
```

The handler interface gets a `run_async` method (existing `run` calls it synchronously where applicable). Phase 0 task includes auditing all handlers for async-readiness.

---

## 5. Mobile architecture

### 5.1 Stack details

```jsonc
// mobile/package.json (key dependencies)
{
  "expo": "^52",
  "react": "18.x",
  "react-native": "0.76.x",
  "expo-router": "^4",
  "nativewind": "^4",
  "tailwindcss": "^3",
  "expo-secure-store": "*",
  "expo-local-authentication": "*",
  "expo-av": "*",
  "expo-notifications": "*",
  "expo-share-intent": "*",
  "expo-sqlite": "*",
  "expo-haptics": "*",
  "react-native-reanimated": "*",
  "react-native-gesture-handler": "*",
  "zustand": "^4",
  "react-query": "^5",          // for HTTP intents not on WS
  "date-fns": "*",
  "expo-blur": "*"              // glassmorphism
}
```

### 5.2 Directory layout

```
mobile/
├── app/
│   ├── (tabs)/
│   │   ├── _layout.tsx           # bottom-tab nav, glassmorphic bar
│   │   ├── hub.tsx
│   │   ├── chat/
│   │   │   ├── index.tsx         # chat list
│   │   │   ├── [agent].tsx       # per-agent chat
│   │   │   └── pattern/[run_id].tsx  # multi-agent thread
│   │   ├── bridge.tsx
│   │   └── profile.tsx
│   ├── pair.tsx                   # first-launch QR pairing
│   ├── locked.tsx                 # biometric gate
│   ├── _layout.tsx                # root: theme, fonts, biometric, WS init
│   └── +not-found.tsx
├── components/
│   ├── primitives/                # Button, Card, Pill, GlassPanel, NeonGlow
│   ├── hub/                       # AgentCard, ApprovalCard, QuickInput, SummaryTile
│   ├── chat/                      # MessageBubble, AgentBadge, CodeBlock, FileCard, ChartBlock, ActionRow
│   ├── voice/                     # MicButton, WaveformPreview, TranscriptEditor
│   └── bridge/                    # IntegrationCard, AgentToggle, HealthMeter
├── lib/
│   ├── ws.ts                      # WebSocket client (reconnect, multiplex, heartbeat)
│   ├── voice.ts                   # record + upload
│   ├── push.ts                    # FCM registration + handlers
│   ├── auth.ts                    # token storage, biometric gate
│   ├── queue.ts                   # offline queue (SQLite)
│   ├── nlp.ts                     # client-side intent hint (optional, server is authoritative)
│   ├── deep-link.ts               # neuralis:// scheme parsing
│   └── api.ts                     # HTTP client with token header
├── stores/
│   ├── auth.ts                    # device, biometric, token
│   ├── connection.ts              # WS state, reconnect
│   ├── hub.ts                     # live agent state, approvals queue
│   ├── chat.ts                    # per-channel messages, scroll state
│   ├── queue.ts                   # pending msgs (mirror of SQLite)
│   └── push.ts                    # FCM token, permission state
├── theme/
│   ├── tailwind.config.js         # color tokens from mockups
│   ├── fonts.ts                   # Space Grotesk + Manrope load
│   └── tokens.ts                  # spacing, radius, shadow constants
├── tests/
│   ├── unit/                      # store logic, queue, nlp helper
│   ├── integration/               # WS mock, queue drain
│   └── e2e/                       # Maestro flows
├── eas.json                       # EAS Build profiles
├── app.config.ts                  # Expo config (icon, splash, intent filters)
├── tailwind.config.js
├── metro.config.js
├── babel.config.js
└── README.md
```

### 5.3 Theme tokens (Tailwind config)

Direct port of mockup tokens — every color hex from the user-provided HTML appears here:

```js
// mobile/tailwind.config.js
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  presets: [require("nativewind/preset")],
  theme: {
    extend: {
      colors: {
        primary: "#006c52",
        "primary-container": "#98ffd9",
        "primary-fixed": "#8ff6d0",
        "primary-fixed-dim": "#73d9b5",
        "on-primary": "#ffffff",
        "on-primary-container": "#00785c",
        secondary: "#74593f",
        "secondary-container": "#fed9b8",
        "secondary-fixed": "#ffdcbe",
        "secondary-fixed-dim": "#e3c0a0",
        tertiary: "#3d6752",
        "tertiary-container": "#c7f6db",
        background: "#f6faf8",
        surface: "#f6faf8",
        "surface-bright": "#f6faf8",
        "surface-dim": "#d7dbd9",
        "surface-container-lowest": "#ffffff",
        "surface-container-low": "#f0f4f2",
        "surface-container": "#ebefed",
        "surface-container-high": "#e5e9e7",
        "surface-container-highest": "#dfe3e1",
        "on-surface": "#181c1c",
        "on-surface-variant": "#3e4944",
        outline: "#6e7a74",
        "outline-variant": "#bdc9c2",
        error: "#ba1a1a",
        "error-container": "#ffdad6",
        "on-error": "#ffffff",
        "on-error-container": "#93000a",
      },
      borderRadius: {
        DEFAULT: "1rem",
        lg: "2rem",
        xl: "3rem",
        full: "9999px",
      },
      fontFamily: {
        headline: ["SpaceGrotesk_700Bold"],
        body: ["Manrope_500Medium"],
        label: ["SpaceGrotesk_500Medium"],
      },
    },
  },
};
```

### 5.4 State management contract

Zustand stores are **pure** — no side effects in setters. Side effects (WS sends, HTTP calls, SQLite writes) happen in `lib/*` and call store setters with the result.

```ts
// stores/chat.ts (sketch)
type ChatState = {
  channels: Record<string, ChannelState>;      // channelId -> messages, cursor, loading
  appendDelta: (channelId: string, seq: number, content: string) => void;
  finalizeMessage: (channelId: string, seq: number, msgId: string) => void;
  setHistory: (channelId: string, msgs: Message[]) => void;
};
```

WebSocket dispatcher is a single switch in `lib/ws.ts` that routes incoming frames to the appropriate store action. No store imports the WS object — strict one-way data flow.

---

## 6. Migration sequence

```
Phase 0 ─── Phase 1A ─── Phase 1B ─── Phase 1C ───┬─── Phase 2 ───┬─── Phase 3 ─── Phase 4
backend     scaffold     core flows    polish &   │   dogfood     │   demote       delete
prereqs     + auth       + agents      ship       │   2-4 weeks   │   Telegram     Telegram
                                       internal   │               │
                                                  └── shadow ──────┘
                                                  Telegram + mobile both alive
```

The cut between Phase 1C and Phase 2 is the single point where the app is **considered shippable**: installable APK on user's phone, all 18 agent chats reachable, push parity, voice working, biometric on launch.

The cut between Phase 2 and Phase 3 is the single point where the user has **chosen the mobile app over Telegram for 2+ weeks** with logged "fallback rate" data justifying it.

---

## 7. Conventions enforced across phases

- **Eight Engineering Principles** (`.claude/rules/seven-principles.md`) apply to all new code (mobile + backend additions).
- **No PII** in spec, code, tests, or repo (`.claude/rules/pii-policy.md`). Mobile fetches profile from server at runtime.
- **No regex for classification** — NLP classifier already on embedding tier; mobile just routes text → server, no client-side intent matching.
- **Dynamic over hardcoded** — agent list, push categories, intent names all fetched from server config endpoint (`/api/config`); no hardcoded per-agent strings in mobile.
- **OPRAL on every error** — backend errors emit structured context per `.claude/rules/error-handling.md`; mobile renders user-actionable messages, not stack traces.
- **Wiring verification** — every new feature ships with an integration test that asserts downstream signals fired (e.g., approval action → `confirm_application` → DB row written).

---

## 8. References

- **Mockup palette + IA** — user-supplied HTML (10 screens: Workspace, Profile, Onboarding, Inbox, Settings, Network, Bridge, Marketplace, HN Chat, GitHub Chat, Search, Budget Chat)
- **Backend agents** — `jobpulse/CLAUDE.md`, `patterns/CLAUDE.md`, `mindgraph_app/CLAUDE.md`, `shared/*/CLAUDE.md`
- **Telegram surface to replace** — `jobpulse/multi_listener.py`, `jobpulse/handler_registry.py`, `jobpulse/intent_registry.py`, `jobpulse/command_router.py`
- **Notification baseline** — `jobpulse/post_apply_hook.py`, `jobpulse/morning_briefing.py`, `shared/telegram_client.py`
- **Existing rules** — `.claude/rules/jobs.md`, `.claude/rules/jobpulse.md`, `.claude/rules/seven-principles.md`, `.claude/rules/pii-policy.md`, `.claude/rules/error-handling.md`
