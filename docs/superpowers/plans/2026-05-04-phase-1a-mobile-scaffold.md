# NEURALIS Phase 1A — Mobile Scaffold + Auth + Tab Skeletons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Boot the React Native + Expo project, render the four tabs with mockup-matching theme + fonts, complete pairing + biometric flow, and ship an installable APK to Play Store internal track via EAS. WebSocket echo round-trip proves connectivity.

**Architecture:** Expo Router (file-based) at `mobile/`, NativeWind for Tailwind classes (matches user-provided HTML mockups near 1:1), Zustand for state, expo-secure-store for token storage, expo-local-authentication for biometric, single WebSocket client with reconnect + heartbeat. Tab skeletons render mock data; one tiny backend addition (echo channel + `/api/config`) makes the round-trip tangible.

**Tech Stack:** Expo SDK 52, React Native 0.76, TypeScript, NativeWind 4 (Tailwind 3), Expo Router 4, Zustand 4, expo-secure-store, expo-local-authentication, expo-blur, react-native-reanimated, EAS Build, Maestro for E2E, Jest for unit tests.

**Branch / worktree decision:**
- The simplest path: continue on `mobile/phase-0-backend` (the existing branch) since Phase 1A depends on Phase 0's backend additions, and the branch name will make sense once it's renamed at merge time (or kept as `feat/neuralis-mobile-foundation`).
- Alternative: create a fresh worktree off the `phase-0-complete` tag at `.worktrees/mobile-phase-1a` on a new branch `mobile/phase-1a`. Cleaner if you want PR-per-phase.

This plan is agnostic — the implementer picks at execution time based on the user's preference. All paths in tasks below are written relative to the worktree root.

**Reference spec**: `docs/superpowers/specs/mobile-app-integration/02-phase-1a-scaffold-auth-skeleton.md`. This plan implements that spec; no new design decisions.

---

## File Structure

**New files** (all under `mobile/` unless noted):

```
mobile/
├── package.json                       (Task 1)
├── app.config.ts                      (Task 21)
├── babel.config.js                    (Task 1)
├── metro.config.js                    (Task 1)
├── tsconfig.json                      (Task 1)
├── eas.json                           (Task 22)
├── tailwind.config.js                 (Task 2)
├── global.css                         (Task 2)
├── nativewind-env.d.ts                (Task 2)
├── .gitignore                         (Task 1)
├── README.md                          (Task 1)
│
├── app/                               (Expo Router screens)
│   ├── _layout.tsx                    (Task 9)  root: fonts + auth gate + WS init
│   ├── pair.tsx                       (Task 10) first-launch pairing
│   ├── locked.tsx                     (Task 11) biometric gate
│   ├── (tabs)/
│   │   ├── _layout.tsx                (Task 12) tab bar + top app bar
│   │   ├── hub.tsx                    (Task 13/14) Hub tab skeleton
│   │   ├── chat/
│   │   │   ├── index.tsx              (Task 15) chat list
│   │   │   └── [agent].tsx            (Task 16) per-agent chat
│   │   ├── bridge.tsx                 (Task 17) integrations
│   │   └── profile.tsx                (Task 18) device + signout
│   └── +not-found.tsx                 (Task 9)
│
├── components/
│   ├── primitives/
│   │   ├── GlassPanel.tsx             (Task 4)
│   │   ├── NeonGlow.tsx               (Task 4)
│   │   ├── Pill.tsx                   (Task 4)
│   │   ├── Card.tsx                   (Task 4)
│   │   ├── Button.tsx                 (Task 4)
│   │   └── MessageBubble.tsx          (Task 4)
│   ├── hub/
│   │   ├── AgentCard.tsx              (Task 13)
│   │   ├── ApprovalCard.tsx           (Task 13)
│   │   ├── QuickInput.tsx             (Task 14)
│   │   ├── SummaryTile.tsx            (Task 13)
│   │   └── ActivityRow.tsx            (Task 13)
│   ├── chat/
│   │   └── AgentBadge.tsx             (Task 15)
│   └── ConnectionBadge.tsx            (Task 19)
│
├── lib/
│   ├── ws.ts                          (Task 5)  WebSocket client
│   ├── auth.ts                        (Task 8)  Keystore + biometric helpers
│   ├── api.ts                         (Task 7)  HTTP client with bearer header
│   ├── agents.ts                      (Task 15) agent metadata (18 entries)
│   ├── deep-link.ts                   (Task 21) neuralis:// scheme parser
│   ├── queue.ts                       (Task 6)  in-memory pending queue
│   └── env.ts                         (Task 1)  NEURALIS_SERVER_URL resolution
│
├── stores/
│   ├── auth.ts                        (Task 6)
│   ├── connection.ts                  (Task 6)
│   ├── chat.ts                        (Task 6)
│   ├── hub.ts                         (Task 6)
│   └── queue.ts                       (Task 6)
│
├── theme/
│   ├── fonts.ts                       (Task 3)
│   └── tokens.ts                      (Task 4)
│
└── tests/
    ├── unit/                          (Task 5, 6)
    └── e2e/                           (Task 23) Maestro flows
```

**Backend additions** (small backports into the same worktree):

```
mindgraph_app/config_api.py            (Task 0)  GET /api/config
mindgraph_app/main.py                  (Task 0)  register config_router
shared/dispatch/ws_dispatcher.py       (Task 20) ensure echo handles "global" channel
tests/integration/test_config_api.py   (Task 0)
```

**Modified files**:

```
.gitignore                             (Task 1) add mobile/node_modules etc.
CLAUDE.md                              (Task 25) add mobile/ to project structure
mindgraph_app/main.py                  (Task 0) register config_router
```

---

## Task Granularity Notes

- Each task is one PR-sized commit. Steps within a task average 2–5 minutes.
- TDD where it makes sense (Python backend, Zustand stores, lib/ utility modules). For UI components, write smoke tests via `@testing-library/react-native` where reasonable; for visual fidelity, manual verification on Expo dev client is the verification gate.
- Mobile tests use Jest (built into Expo). Backend tests continue using pytest in `tests/integration/`.
- Each commit message uses Conventional Commits: `feat(scope): …`. Scopes: `config-api`, `mobile-scaffold`, `mobile-theme`, `mobile-fonts`, `mobile-ws`, `mobile-stores`, `mobile-api`, `mobile-auth`, `mobile-router`, `mobile-pair`, `mobile-locked`, `mobile-tabs`, `mobile-hub`, `mobile-chat`, `mobile-bridge`, `mobile-profile`, `mobile-conn-badge`, `ws-echo`, `mobile-config`, `mobile-eas`, `mobile-e2e`, `mobile-build`, `mobile-docs`.
- Mobile setup commands assume `bun` (faster than `npm`). If `bun` isn't installed, fall back to `npm`.

---

## Task 0: Backend backport — `/api/config` endpoint

**Files:**
- Create: `mindgraph_app/config_api.py`
- Modify: `mindgraph_app/main.py`
- Test: `tests/integration/test_config_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_config_api.py
from __future__ import annotations
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.config_api import config_router
from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(config_router)
    app.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "C"})
    return client.post("/api/auth/pair", json={"code": init.json()["code"], "name": "C"}).json()["token"]


def test_config_returns_integrations_and_agents(client, token):
    r = client.get("/api/config", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert "integrations" in body
    assert "agents" in body
    # At least one agent registered
    assert len(body["agents"]) > 0
    first = body["agents"][0]
    assert "id" in first and "name" in first


def test_config_without_auth_401(client):
    r = client.get("/api/config")
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns/.worktrees/mobile-phase-0-backend
python -m pytest tests/integration/test_config_api.py -v
```

Expected: ImportError on `mindgraph_app.config_api`.

- [ ] **Step 3: Implement `mindgraph_app/config_api.py`**

```python
# mindgraph_app/config_api.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from mindgraph_app.auth_api import DeviceAuth, verify_device_token
from jobpulse.handler_registry import get_handler_map

config_router = APIRouter(prefix="/api", tags=["config"])

# Static integrations list — Phase 1B replaces with real status checks.
_INTEGRATIONS = [
    {"name": "notion", "status": "connected", "label": "Notion"},
    {"name": "drive", "status": "connected", "label": "Google Drive"},
    {"name": "gmail", "status": "connected", "label": "Gmail"},
    {"name": "github", "status": "connected", "label": "GitHub"},
    {"name": "telegram", "status": "connected", "label": "Telegram"},
]

# 18-agent canonical list — matches docs/superpowers/specs/mobile-app-integration/00-design-overview.md §2
_AGENTS = [
    {"id": "jobs",         "name": "Job Bot",        "icon": "work",          "channel": "agent:jobs"},
    {"id": "budget",       "name": "Budget",         "icon": "wallet",        "channel": "agent:budget"},
    {"id": "tasks",        "name": "Tasks",          "icon": "checklist",     "channel": "agent:tasks"},
    {"id": "calendar",     "name": "Calendar",       "icon": "calendar_today","channel": "agent:calendar"},
    {"id": "gmail",        "name": "Gmail",          "icon": "mail",          "channel": "agent:gmail"},
    {"id": "github",       "name": "GitHub",         "icon": "code",          "channel": "agent:github"},
    {"id": "papers",       "name": "Papers",         "icon": "article",       "channel": "agent:papers"},
    {"id": "briefing",     "name": "Briefing",       "icon": "today",         "channel": "agent:briefing"},
    {"id": "hierarchical", "name": "Hierarchical",   "icon": "account_tree",  "channel": "agent:hierarchical"},
    {"id": "peer_debate",  "name": "Peer Debate",    "icon": "forum",         "channel": "agent:peer_debate"},
    {"id": "dynamic_swarm","name": "Dynamic Swarm",  "icon": "hub",           "channel": "agent:dynamic_swarm"},
    {"id": "enhanced_swarm","name": "Enhanced Swarm","icon": "auto_awesome",  "channel": "agent:enhanced_swarm"},
    {"id": "map_reduce",   "name": "Map-Reduce",     "icon": "scatter_plot",  "channel": "agent:map_reduce"},
    {"id": "plan_execute", "name": "Plan-and-Execute","icon": "task_alt",     "channel": "agent:plan_execute"},
    {"id": "codegraph",    "name": "CodeGraph",      "icon": "graph",         "channel": "agent:codegraph"},
    {"id": "cognitive",    "name": "Think",          "icon": "psychology",    "channel": "agent:cognitive"},
    {"id": "memory",       "name": "Memory",         "icon": "memory",        "channel": "agent:memory"},
    {"id": "fact_check",   "name": "Fact Check",     "icon": "verified",      "channel": "agent:fact_check"},
]


@config_router.get("/config")
def get_config(device: DeviceAuth = Depends(verify_device_token)):
    return {
        "integrations": _INTEGRATIONS,
        "agents": _AGENTS,
        "intent_count": len(get_handler_map()),
        "device": {"name": device.name, "scope": device.scope},
    }
```

- [ ] **Step 4: Register the router in `mindgraph_app/main.py`**

Add the import:

```python
from mindgraph_app.config_api import config_router
```

Add the include_router call (alongside the other Phase 0 routers):

```python
app.include_router(config_router)
```

Update the startup logger inside `main()`:

```python
    logger.info("  Mobile config:  /api/config")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/integration/test_config_api.py tests/integration/test_main_app_wiring.py -v
```

Expected: 4 passed (2 new + 2 main wiring tests still pass).

- [ ] **Step 6: Update the wiring test to include /api/config**

Edit `tests/integration/test_main_app_wiring.py` — add `/api/config` to the expected paths set:

```python
    expected = {
        "/api/auth/pair-init",
        "/api/auth/pair",
        "/api/auth/me",
        "/api/auth/revoke",
        "/api/auth/devices",
        "/api/intents/{intent_name:path}",
        "/api/voice",
        "/api/push/register",
        "/api/config",     # ← new
        "/ws",
    }
```

- [ ] **Step 7: Run full integration suite**

```bash
python -m pytest tests/integration/ -v 2>&1 | tail -5
```

Expected: 61 passed (59 prior + 2 new).

- [ ] **Step 8: Commit**

```bash
git add mindgraph_app/config_api.py mindgraph_app/main.py tests/integration/test_config_api.py tests/integration/test_main_app_wiring.py
git commit -m "feat(config-api): /api/config returns integrations + 18-agent inventory"
```

---

## Task 1: Mobile project scaffold

**Files:**
- Create: `mobile/` directory tree (Expo creates it)
- Create: `mobile/package.json`, `mobile/tsconfig.json`, `mobile/babel.config.js`, `mobile/metro.config.js`, `mobile/.gitignore`, `mobile/README.md`
- Create: `mobile/lib/env.ts`
- Modify: repo-root `.gitignore`

- [ ] **Step 1: Initialize the Expo project**

Run from the worktree root:

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns/.worktrees/mobile-phase-0-backend
npx create-expo-app@latest mobile --template default --no-install
cd mobile
```

Expected: `mobile/` created with the default Expo template (TypeScript). `--no-install` so we control the package install.

If `npx` complains, ensure Node 20+ is installed (`node --version`).

- [ ] **Step 2: Set the Expo SDK version**

Edit `mobile/package.json` and pin:

```json
{
  "name": "neuralis-mobile",
  "main": "expo-router/entry",
  "version": "0.1.0",
  "scripts": {
    "start": "expo start",
    "android": "expo run:android",
    "ios": "expo run:ios",
    "web": "expo start --web",
    "test": "jest"
  },
  "dependencies": {
    "expo": "~52.0.0",
    "expo-router": "~4.0.0",
    "react": "18.3.1",
    "react-native": "0.76.0",
    "react-native-safe-area-context": "4.12.0",
    "react-native-screens": "4.4.0",
    "react-native-gesture-handler": "~2.20.2",
    "react-native-reanimated": "~3.16.1",
    "expo-status-bar": "~2.0.0",
    "expo-secure-store": "~14.0.0",
    "expo-local-authentication": "~15.0.0",
    "expo-blur": "~14.0.0",
    "expo-haptics": "~14.0.0",
    "expo-font": "~13.0.0",
    "expo-splash-screen": "~0.29.0",
    "@expo-google-fonts/space-grotesk": "*",
    "@expo-google-fonts/manrope": "*",
    "nativewind": "^4.1.23",
    "tailwindcss": "^3.4.17",
    "zustand": "^5.0.2",
    "react-native-svg": "^15.8.0"
  },
  "devDependencies": {
    "@babel/core": "^7.25.0",
    "@types/react": "~18.3.12",
    "typescript": "~5.3.3",
    "jest": "^29.7.0",
    "jest-expo": "~52.0.0"
  },
  "jest": {
    "preset": "jest-expo"
  }
}
```

- [ ] **Step 3: Install dependencies**

```bash
cd mobile
bun install || npm install
```

Expected: clean install. If errors mention peer-dep mismatch, the SDK 52 + RN 0.76 combo above is verified — try `bun install --no-frozen-lockfile` or `npm install --legacy-peer-deps`.

- [ ] **Step 4: Verify TypeScript config**

`mobile/tsconfig.json` (overwrite Expo's default with this):

```json
{
  "extends": "expo/tsconfig.base",
  "compilerOptions": {
    "strict": true,
    "moduleResolution": "bundler",
    "paths": {
      "@/*": ["./*"]
    }
  },
  "include": ["**/*.ts", "**/*.tsx", ".expo/types/**/*.ts", "expo-env.d.ts"]
}
```

- [ ] **Step 5: Babel + Metro config for NativeWind**

`mobile/babel.config.js`:

```javascript
module.exports = function (api) {
  api.cache(true);
  return {
    presets: [
      ["babel-preset-expo", { jsxImportSource: "nativewind" }],
      "nativewind/babel",
    ],
  };
};
```

`mobile/metro.config.js`:

```javascript
const { getDefaultConfig } = require("expo/metro-config");
const { withNativeWind } = require("nativewind/metro");

const config = getDefaultConfig(__dirname);

module.exports = withNativeWind(config, { input: "./global.css" });
```

- [ ] **Step 6: Create `mobile/lib/env.ts` for server URL**

```typescript
// mobile/lib/env.ts
import Constants from "expo-constants";

const DEFAULT_SERVER = "http://100.x.y.z:8000"; // placeholder Tailscale IP

export const SERVER_URL: string =
  process.env.EXPO_PUBLIC_NEURALIS_SERVER_URL ??
  (Constants.expoConfig?.extra?.serverUrl as string | undefined) ??
  DEFAULT_SERVER;

export function wsUrl(path = "/ws"): string {
  return SERVER_URL.replace(/^http/, "ws") + path;
}
```

- [ ] **Step 7: Update `.gitignore` files**

In `mobile/.gitignore` (Expo creates one — append to it):

```
# Expo / RN
node_modules/
.expo/
dist/
web-build/
*.apk
*.aab
.env
.env.*

# EAS
google-services.json
GoogleService-Info.plist

# Native
ios/
android/
```

In repo-root `.gitignore` (add a section if not present):

```
# Mobile app
mobile/node_modules/
mobile/.expo/
mobile/dist/
mobile/*.apk
mobile/*.aab
```

- [ ] **Step 8: Add `mobile/README.md`**

```markdown
# NEURALIS mobile

React Native + Expo app fronting the multi_agent_patterns backend.

## Dev

```bash
cd mobile
bun install                                      # one-time
EXPO_PUBLIC_NEURALIS_SERVER_URL=http://<mac-magic-dns>:8000 bun expo start
```

Scan the QR code with Expo Go (development) or build a dev client via EAS.

## Build

```bash
eas build -p android --profile internal           # APK to internal track
eas submit --profile production --platform android  # later: production track
```

## Test

```bash
bun test
```

## Tech

Expo SDK 52, RN 0.76, TypeScript, NativeWind 4 (Tailwind 3), Expo Router, Zustand,
expo-secure-store, expo-local-authentication, react-native-reanimated.

## Server URL

Defaults to `http://100.x.y.z:8000` (placeholder). Override via `EXPO_PUBLIC_NEURALIS_SERVER_URL`
or `app.config.ts` extra.serverUrl. The phone reaches the Mac over Tailscale.
```

- [ ] **Step 9: Verify the project builds**

```bash
cd mobile
bun expo prebuild --platform android --no-install --clean=false || echo "skip prebuild for now"
bun expo doctor
```

Expected: `expo doctor` reports no critical issues. (Some warnings about EAS Build Cloud are expected since we haven't run `eas` yet.)

- [ ] **Step 10: Commit**

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns/.worktrees/mobile-phase-0-backend
git add mobile/ .gitignore
git commit -m "feat(mobile-scaffold): Expo SDK 52 project + NativeWind + TypeScript baseline"
```

---

## Task 2: Tailwind / NativeWind theme tokens

**Files:**
- Create: `mobile/tailwind.config.js`
- Create: `mobile/global.css`
- Create: `mobile/nativewind-env.d.ts`

- [ ] **Step 1: Write `mobile/tailwind.config.js` with the full mockup token palette**

```javascript
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  presets: [require("nativewind/preset")],
  theme: {
    extend: {
      colors: {
        // Primary palette (mint)
        primary: "#006c52",
        "on-primary": "#ffffff",
        "primary-container": "#98ffd9",
        "on-primary-container": "#00785c",
        "primary-fixed": "#8ff6d0",
        "primary-fixed-dim": "#73d9b5",
        "on-primary-fixed": "#002117",
        "on-primary-fixed-variant": "#00513d",
        "inverse-primary": "#73d9b5",

        // Secondary (peach/warm)
        secondary: "#74593f",
        "on-secondary": "#ffffff",
        "secondary-container": "#fed9b8",
        "on-secondary-container": "#795d43",
        "secondary-fixed": "#ffdcbe",
        "secondary-fixed-dim": "#e3c0a0",
        "on-secondary-fixed": "#2a1704",
        "on-secondary-fixed-variant": "#5a422a",

        // Tertiary (soft green)
        tertiary: "#3d6752",
        "on-tertiary": "#ffffff",
        "tertiary-container": "#c7f6db",
        "on-tertiary-container": "#48725d",
        "tertiary-fixed": "#bfedd3",
        "tertiary-fixed-dim": "#a3d1b7",
        "on-tertiary-fixed": "#002114",
        "on-tertiary-fixed-variant": "#244f3b",

        // Surfaces
        background: "#f6faf8",
        surface: "#f6faf8",
        "surface-bright": "#f6faf8",
        "surface-dim": "#d7dbd9",
        "surface-container-lowest": "#ffffff",
        "surface-container-low": "#f0f4f2",
        "surface-container": "#ebefed",
        "surface-container-high": "#e5e9e7",
        "surface-container-highest": "#dfe3e1",
        "surface-variant": "#dfe3e1",
        "surface-tint": "#006c52",

        // On-surface text
        "on-background": "#181c1c",
        "on-surface": "#181c1c",
        "on-surface-variant": "#3e4944",
        "inverse-on-surface": "#eef2f0",
        "inverse-surface": "#2d3130",

        // Outline
        outline: "#6e7a74",
        "outline-variant": "#bdc9c2",

        // Error
        error: "#ba1a1a",
        "on-error": "#ffffff",
        "error-container": "#ffdad6",
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
  plugins: [],
};
```

- [ ] **Step 2: Create `mobile/global.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 3: Create `mobile/nativewind-env.d.ts`**

```typescript
/// <reference types="nativewind/types" />
```

- [ ] **Step 4: Commit**

```bash
git add mobile/tailwind.config.js mobile/global.css mobile/nativewind-env.d.ts
git commit -m "feat(mobile-theme): NativeWind config with mockup token palette (mint + peach)"
```

---

## Task 3: Fonts — Space Grotesk + Manrope

**Files:**
- Create: `mobile/theme/fonts.ts`

- [ ] **Step 1: Create `mobile/theme/fonts.ts`**

```typescript
// mobile/theme/fonts.ts
import { useFonts as useExpoFonts } from "expo-font";
import {
  SpaceGrotesk_500Medium,
  SpaceGrotesk_700Bold,
} from "@expo-google-fonts/space-grotesk";
import {
  Manrope_400Regular,
  Manrope_500Medium,
  Manrope_600SemiBold,
} from "@expo-google-fonts/manrope";

export function useFonts(): boolean {
  const [loaded] = useExpoFonts({
    SpaceGrotesk_500Medium,
    SpaceGrotesk_700Bold,
    Manrope_400Regular,
    Manrope_500Medium,
    Manrope_600SemiBold,
  });
  return loaded;
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/theme/fonts.ts
git commit -m "feat(mobile-fonts): load Space Grotesk + Manrope via expo-google-fonts"
```

---

## Task 4: Theme primitives

**Files:**
- Create: `mobile/components/primitives/GlassPanel.tsx`
- Create: `mobile/components/primitives/NeonGlow.tsx`
- Create: `mobile/components/primitives/Pill.tsx`
- Create: `mobile/components/primitives/Card.tsx`
- Create: `mobile/components/primitives/Button.tsx`
- Create: `mobile/components/primitives/MessageBubble.tsx`
- Create: `mobile/theme/tokens.ts`

- [ ] **Step 1: `theme/tokens.ts` — shared dimensions/shadows**

```typescript
// mobile/theme/tokens.ts
import { ViewStyle } from "react-native";

export const ambientShadow: ViewStyle = {
  shadowColor: "#181c1c",
  shadowOffset: { width: 0, height: 20 },
  shadowOpacity: 0.04,
  shadowRadius: 40,
  elevation: 4,
};

export const neonShadow = (color = "#8ff6d0"): ViewStyle => ({
  shadowColor: color,
  shadowOffset: { width: 0, height: 0 },
  shadowOpacity: 0.5,
  shadowRadius: 15,
  elevation: 8,
});

export const insetHighlight: ViewStyle = {
  // Approximated via border — true inset shadows require a custom view
  borderTopWidth: 1,
  borderTopColor: "rgba(255,255,255,0.4)",
};
```

- [ ] **Step 2: `components/primitives/GlassPanel.tsx`**

```tsx
// mobile/components/primitives/GlassPanel.tsx
import { BlurView } from "expo-blur";
import { View, ViewProps } from "react-native";
import { ambientShadow, insetHighlight } from "@/theme/tokens";

type Props = ViewProps & { intensity?: number };

export function GlassPanel({ intensity = 24, style, children, ...rest }: Props) {
  return (
    <View
      style={[ambientShadow, { borderRadius: 16, overflow: "hidden" }, style]}
      {...rest}
    >
      <BlurView
        intensity={intensity}
        tint="light"
        style={[
          {
            backgroundColor: "rgba(255,255,255,0.7)",
            padding: 0,
            borderRadius: 16,
          },
          insetHighlight,
        ]}
      >
        {children}
      </BlurView>
    </View>
  );
}
```

- [ ] **Step 3: `components/primitives/NeonGlow.tsx`**

```tsx
// mobile/components/primitives/NeonGlow.tsx
import { View, ViewProps } from "react-native";
import { neonShadow } from "@/theme/tokens";

type Props = ViewProps & { color?: string };

export function NeonGlow({ color = "#8ff6d0", style, children, ...rest }: Props) {
  return (
    <View style={[neonShadow(color), style]} {...rest}>
      {children}
    </View>
  );
}
```

- [ ] **Step 4: `components/primitives/Pill.tsx`**

```tsx
// mobile/components/primitives/Pill.tsx
import { Text, View } from "react-native";

type Props = {
  children: React.ReactNode;
  tone?: "primary" | "secondary" | "neutral";
};

const TONES = {
  primary: { bg: "bg-primary-container/30", text: "text-on-primary-container" },
  secondary: { bg: "bg-secondary-container/30", text: "text-on-secondary-container" },
  neutral: { bg: "bg-surface-container", text: "text-on-surface-variant" },
} as const;

export function Pill({ children, tone = "neutral" }: Props) {
  const t = TONES[tone];
  return (
    <View className={`${t.bg} rounded-full px-3 py-1`}>
      <Text className={`${t.text} font-label text-xs uppercase tracking-widest`}>
        {children}
      </Text>
    </View>
  );
}
```

- [ ] **Step 5: `components/primitives/Card.tsx`**

```tsx
// mobile/components/primitives/Card.tsx
import { View, ViewProps } from "react-native";
import { ambientShadow } from "@/theme/tokens";

export function Card({ style, children, ...rest }: ViewProps) {
  return (
    <View
      style={[ambientShadow, style]}
      className="bg-surface-container-lowest rounded-xl p-5"
      {...rest}
    >
      {children}
    </View>
  );
}
```

- [ ] **Step 6: `components/primitives/Button.tsx`**

```tsx
// mobile/components/primitives/Button.tsx
import { LinearGradient } from "expo-linear-gradient";
import { Pressable, Text, ViewStyle } from "react-native";

type Props = {
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  style?: ViewStyle;
};

export function Button({ label, onPress, variant = "primary", disabled, style }: Props) {
  if (variant === "primary") {
    return (
      <Pressable onPress={disabled ? undefined : onPress} style={[{ opacity: disabled ? 0.5 : 1 }, style]}>
        <LinearGradient
          colors={["#006c52", "#73d9b5"]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ borderRadius: 9999, paddingVertical: 14, paddingHorizontal: 24 }}
        >
          <Text className="font-label text-on-primary text-center uppercase tracking-widest font-bold">
            {label}
          </Text>
        </LinearGradient>
      </Pressable>
    );
  }
  if (variant === "secondary") {
    return (
      <Pressable
        onPress={disabled ? undefined : onPress}
        className="bg-surface-container-high rounded-full py-3.5 px-6"
        style={[{ opacity: disabled ? 0.5 : 1 }, style]}
      >
        <Text className="font-label text-primary text-center uppercase tracking-widest font-bold">
          {label}
        </Text>
      </Pressable>
    );
  }
  return (
    <Pressable onPress={disabled ? undefined : onPress} style={[{ opacity: disabled ? 0.5 : 1 }, style]}>
      <Text className="font-label text-primary text-center uppercase tracking-widest">
        {label}
      </Text>
    </Pressable>
  );
}
```

Need `expo-linear-gradient`:

```bash
cd mobile
bun add expo-linear-gradient
```

- [ ] **Step 7: `components/primitives/MessageBubble.tsx`**

```tsx
// mobile/components/primitives/MessageBubble.tsx
import { Text, View } from "react-native";
import { GlassPanel } from "./GlassPanel";
import { LinearGradient } from "expo-linear-gradient";

type Props = {
  role: "user" | "agent" | "system";
  content: string;
  agentName?: string;
};

export function MessageBubble({ role, content, agentName }: Props) {
  if (role === "user") {
    return (
      <View className="self-end max-w-[85%] mb-3">
        <LinearGradient
          colors={["#006c52", "#73d9b5"]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ borderRadius: 24, borderTopRightRadius: 4, padding: 16 }}
        >
          <Text className="text-on-primary font-body text-base">{content}</Text>
        </LinearGradient>
      </View>
    );
  }
  if (role === "system") {
    return (
      <View className="self-center my-2 bg-surface-container-low rounded-full px-4 py-1.5">
        <Text className="text-on-surface-variant font-label text-xs uppercase tracking-widest">
          {content}
        </Text>
      </View>
    );
  }
  return (
    <View className="self-start max-w-[85%] mb-3">
      <GlassPanel style={{ borderTopLeftRadius: 4 }}>
        <View className="p-5">
          {agentName ? (
            <Text className="font-headline font-bold text-primary text-sm mb-1">
              {agentName}
            </Text>
          ) : null}
          <Text className="text-on-surface font-body text-base">{content}</Text>
        </View>
      </GlassPanel>
    </View>
  );
}
```

- [ ] **Step 8: Commit**

```bash
git add mobile/components/primitives/ mobile/theme/tokens.ts mobile/package.json mobile/bun.lockb 2>/dev/null
git add mobile/components/primitives/ mobile/theme/tokens.ts mobile/package.json mobile/package-lock.json 2>/dev/null
# Add whichever lockfile exists.
git commit -m "feat(mobile-theme): primitives — GlassPanel, NeonGlow, Pill, Card, Button, MessageBubble"
```

---

## Task 5: WebSocket client

**Files:**
- Create: `mobile/lib/ws.ts`
- Create: `mobile/tests/unit/ws.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// mobile/tests/unit/ws.test.ts
import { WsClient } from "@/lib/ws";

describe("WsClient", () => {
  test("backoff schedule increases exponentially capped at 30s", () => {
    const client = new WsClient({ url: "ws://localhost:9999/ws" });
    expect(client.computeBackoff(0)).toBe(1000);
    expect(client.computeBackoff(1)).toBe(2000);
    expect(client.computeBackoff(2)).toBe(4000);
    expect(client.computeBackoff(3)).toBe(8000);
    expect(client.computeBackoff(4)).toBe(16000);
    expect(client.computeBackoff(5)).toBe(30000);
    expect(client.computeBackoff(99)).toBe(30000); // capped
  });

  test("starts in disconnected state", () => {
    const client = new WsClient({ url: "ws://localhost:9999/ws" });
    expect(client.state).toBe("disconnected");
  });

  test("auth.ok transitions state to ready", () => {
    const client = new WsClient({ url: "ws://localhost:9999/ws" });
    client._onFrameForTest({ type: "auth.ok", device_name: "test", server_seq: 0 });
    expect(client.state).toBe("ready");
  });

  test("auth.fail transitions state to failed", () => {
    const client = new WsClient({ url: "ws://localhost:9999/ws" });
    client._onFrameForTest({ type: "auth.fail", reason: "bad" });
    expect(client.state).toBe("failed");
  });
});
```

- [ ] **Step 2: Verify tests fail**

```bash
cd mobile
bun test
```

Expected: ImportError on `@/lib/ws`.

- [ ] **Step 3: Implement `mobile/lib/ws.ts`**

```typescript
// mobile/lib/ws.ts
type WsFrame = Record<string, unknown> & { type: string };
type State = "disconnected" | "connecting" | "ready" | "reconnecting" | "failed";

type FrameHandler = (frame: WsFrame) => void;

const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000] as const;
const HEARTBEAT_INTERVAL_MS = 30_000;
const HEARTBEAT_TIMEOUT_MS = 60_000;

export type WsClientOptions = {
  url: string;
  getToken?: () => string | null;
  onFrame?: FrameHandler;
  onStateChange?: (s: State) => void;
};

export class WsClient {
  private socket: WebSocket | null = null;
  private _state: State = "disconnected";
  private retryCount = 0;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private lastPongAt = 0;
  private opts: WsClientOptions;
  public lastSeq = 0;

  constructor(opts: WsClientOptions) {
    this.opts = opts;
  }

  get state(): State {
    return this._state;
  }

  computeBackoff(attempt: number): number {
    return BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
  }

  connect(): void {
    if (this._state === "connecting" || this._state === "ready") return;
    this.setState(this.retryCount === 0 ? "connecting" : "reconnecting");
    try {
      this.socket = new WebSocket(this.opts.url);
    } catch (e) {
      this.scheduleReconnect();
      return;
    }
    this.socket.onopen = () => this.onOpen();
    this.socket.onmessage = (e) => this.onMessage(e);
    this.socket.onerror = () => this.onError();
    this.socket.onclose = () => this.onClose();
  }

  disconnect(): void {
    this.clearHeartbeat();
    this.socket?.close();
    this.socket = null;
    this.setState("disconnected");
  }

  send(frame: WsFrame): void {
    if (this.socket && this._state === "ready") {
      this.socket.send(JSON.stringify(frame));
    }
  }

  /** Internal — exposed for testing. */
  _onFrameForTest(frame: WsFrame): void {
    this.handleFrame(frame);
  }

  private setState(s: State): void {
    this._state = s;
    this.opts.onStateChange?.(s);
  }

  private onOpen(): void {
    const token = this.opts.getToken?.();
    if (!token) {
      this.setState("failed");
      this.socket?.close();
      return;
    }
    this.send({ type: "auth", token });
    this.lastPongAt = Date.now();
    this.startHeartbeat();
  }

  private onMessage(event: WebSocketMessageEvent): void {
    try {
      const frame = JSON.parse(event.data as string) as WsFrame;
      this.handleFrame(frame);
    } catch {
      // ignore malformed frames
    }
  }

  private handleFrame(frame: WsFrame): void {
    if (frame.type === "auth.ok") {
      this.retryCount = 0;
      this.setState("ready");
      // Resume from last seen seq if any
      if (this.lastSeq > 0) {
        this.send({ type: "resume_from", server_seq: this.lastSeq });
      }
    } else if (frame.type === "auth.fail") {
      this.setState("failed");
    } else if (frame.type === "pong") {
      this.lastPongAt = Date.now();
    }
    if (typeof frame._seq === "number") {
      this.lastSeq = frame._seq as number;
    }
    this.opts.onFrame?.(frame);
  }

  private onError(): void {
    // No-op: onclose follows
  }

  private onClose(): void {
    this.clearHeartbeat();
    if (this._state === "failed") return; // explicit failure — don't reconnect
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    const delay = this.computeBackoff(this.retryCount);
    this.retryCount += 1;
    this.setState("reconnecting");
    setTimeout(() => this.connect(), delay);
  }

  private startHeartbeat(): void {
    this.clearHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: "ping", t: Date.now() });
      if (Date.now() - this.lastPongAt > HEARTBEAT_TIMEOUT_MS) {
        this.socket?.close();
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}
```

- [ ] **Step 4: Verify tests pass**

```bash
cd mobile
bun test
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/ws.ts mobile/tests/unit/ws.test.ts
git commit -m "feat(mobile-ws): WebSocket client with auth, reconnect, heartbeat"
```

---

## Task 6: Zustand stores + offline queue

**Files:**
- Create: `mobile/stores/auth.ts`
- Create: `mobile/stores/connection.ts`
- Create: `mobile/stores/chat.ts`
- Create: `mobile/stores/hub.ts`
- Create: `mobile/stores/queue.ts`
- Create: `mobile/lib/queue.ts`
- Create: `mobile/tests/unit/stores.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// mobile/tests/unit/stores.test.ts
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";

describe("auth store", () => {
  beforeEach(() => useAuthStore.setState({ token: null, deviceName: null, scope: "full", biometricPassed: false }));

  test("setToken stores token + device name", () => {
    useAuthStore.getState().setToken("abc", "Yash-Pixel", "full");
    expect(useAuthStore.getState().token).toBe("abc");
    expect(useAuthStore.getState().deviceName).toBe("Yash-Pixel");
  });

  test("clearToken nulls token + device name", () => {
    useAuthStore.getState().setToken("abc", "Yash-Pixel", "full");
    useAuthStore.getState().clearToken();
    expect(useAuthStore.getState().token).toBeNull();
  });
});

describe("chat store", () => {
  beforeEach(() => useChatStore.setState({ channels: {} }));

  test("appendDelta accumulates content per channel/seq", () => {
    useChatStore.getState().appendDelta("agent:budget", 1, "Hel");
    useChatStore.getState().appendDelta("agent:budget", 1, "lo");
    const ch = useChatStore.getState().channels["agent:budget"];
    expect(ch.partial[1]).toBe("Hello");
  });

  test("finalizeMessage moves partial to messages array", () => {
    useChatStore.getState().appendDelta("agent:budget", 1, "Hello");
    useChatStore.getState().finalizeMessage("agent:budget", 1, "msg-abc");
    const ch = useChatStore.getState().channels["agent:budget"];
    expect(ch.messages.length).toBe(1);
    expect(ch.messages[0].content).toBe("Hello");
    expect(ch.partial[1]).toBeUndefined();
  });
});
```

- [ ] **Step 2: Implement `stores/auth.ts`**

```typescript
// mobile/stores/auth.ts
import { create } from "zustand";

type Scope = "full" | "demo";

type AuthState = {
  token: string | null;
  deviceName: string | null;
  scope: Scope;
  biometricPassed: boolean;
  setToken: (token: string, deviceName: string, scope: Scope) => void;
  clearToken: () => void;
  markBiometric: (passed: boolean) => void;
};

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  deviceName: null,
  scope: "full",
  biometricPassed: false,
  setToken: (token, deviceName, scope) =>
    set({ token, deviceName, scope, biometricPassed: false }),
  clearToken: () => set({ token: null, deviceName: null, biometricPassed: false }),
  markBiometric: (passed) => set({ biometricPassed: passed }),
}));
```

- [ ] **Step 3: Implement `stores/connection.ts`**

```typescript
// mobile/stores/connection.ts
import { create } from "zustand";

type State = "disconnected" | "connecting" | "ready" | "reconnecting" | "failed";

type ConnectionState = {
  state: State;
  lastSeq: number;
  setState: (s: State) => void;
  setLastSeq: (n: number) => void;
};

export const useConnectionStore = create<ConnectionState>((set) => ({
  state: "disconnected",
  lastSeq: 0,
  setState: (s) => set({ state: s }),
  setLastSeq: (n) => set({ lastSeq: n }),
}));
```

- [ ] **Step 4: Implement `stores/chat.ts`**

```typescript
// mobile/stores/chat.ts
import { create } from "zustand";

export type Message = {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  agentName?: string;
  ts: number;
};

type ChannelState = {
  messages: Message[];
  partial: Record<number, string>;
};

type ChatState = {
  channels: Record<string, ChannelState>;
  appendUserMessage: (channelId: string, content: string) => void;
  appendDelta: (channelId: string, seq: number, content: string) => void;
  finalizeMessage: (channelId: string, seq: number, msgId: string, agentName?: string) => void;
};

const empty = (): ChannelState => ({ messages: [], partial: {} });

export const useChatStore = create<ChatState>((set) => ({
  channels: {},
  appendUserMessage: (channelId, content) =>
    set((s) => {
      const ch = s.channels[channelId] ?? empty();
      const msg: Message = {
        id: `local-${Date.now()}`,
        role: "user",
        content,
        ts: Date.now(),
      };
      return {
        channels: {
          ...s.channels,
          [channelId]: { ...ch, messages: [...ch.messages, msg] },
        },
      };
    }),
  appendDelta: (channelId, seq, content) =>
    set((s) => {
      const ch = s.channels[channelId] ?? empty();
      const prev = ch.partial[seq] ?? "";
      return {
        channels: {
          ...s.channels,
          [channelId]: { ...ch, partial: { ...ch.partial, [seq]: prev + content } },
        },
      };
    }),
  finalizeMessage: (channelId, seq, msgId, agentName) =>
    set((s) => {
      const ch = s.channels[channelId] ?? empty();
      const content = ch.partial[seq] ?? "";
      const { [seq]: _drop, ...rest } = ch.partial;
      return {
        channels: {
          ...s.channels,
          [channelId]: {
            messages: [
              ...ch.messages,
              { id: msgId, role: "agent", content, agentName, ts: Date.now() },
            ],
            partial: rest,
          },
        },
      };
    }),
}));
```

- [ ] **Step 5: Implement `stores/hub.ts`**

```typescript
// mobile/stores/hub.ts
import { create } from "zustand";

export type LiveAgent = {
  id: string;
  name: string;
  status: "processing" | "idle" | "error";
  label?: string;
  progress?: number;
};

export type Approval = {
  id: string;
  kind: string;
  company: string;
  role: string;
};

type HubState = {
  liveAgents: LiveAgent[];
  approvals: Approval[];
  set: (patch: Partial<HubState>) => void;
};

export const useHubStore = create<HubState>((set) => ({
  liveAgents: [],
  approvals: [],
  set: (patch) => set(patch),
}));
```

- [ ] **Step 6: Implement `lib/queue.ts` + `stores/queue.ts`**

```typescript
// mobile/lib/queue.ts
export type PendingMessage = {
  uuid: string;
  channel: string;
  text: string;
  createdAt: number;
};

// Phase 1A: in-memory only. Phase 1B replaces with expo-sqlite.
const memory: PendingMessage[] = [];

export const queue = {
  enqueue(channel: string, text: string): string {
    const uuid = `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    memory.push({ uuid, channel, text, createdAt: Date.now() });
    return uuid;
  },
  drain(): PendingMessage[] {
    const items = memory.slice();
    memory.length = 0;
    return items;
  },
  size(): number {
    return memory.length;
  },
};
```

```typescript
// mobile/stores/queue.ts
import { create } from "zustand";

type QueueState = {
  pending: number;
  setPending: (n: number) => void;
};

export const useQueueStore = create<QueueState>((set) => ({
  pending: 0,
  setPending: (n) => set({ pending: n }),
}));
```

- [ ] **Step 7: Verify store tests pass**

```bash
cd mobile
bun test
```

Expected: 6 passed (4 ws + 2 store).

- [ ] **Step 8: Commit**

```bash
git add mobile/stores/ mobile/lib/queue.ts mobile/tests/unit/stores.test.ts
git commit -m "feat(mobile-stores): Zustand stores (auth, connection, chat, hub, queue) + in-memory queue"
```

---

## Task 7: HTTP API client

**Files:**
- Create: `mobile/lib/api.ts`

- [ ] **Step 1: Implement `lib/api.ts`**

```typescript
// mobile/lib/api.ts
import { SERVER_URL } from "@/lib/env";
import { useAuthStore } from "@/stores/auth";

class ApiError extends Error {
  constructor(public status: number, public body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeader(): Record<string, string> {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${SERVER_URL}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init?.headers ?? {}),
    },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new ApiError(res.status, body, body?.detail?.message ?? `HTTP ${res.status}`);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  pairInit(name: string) {
    return request<{ code: string; ttl_seconds: number; name: string }>("/api/auth/pair-init", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  pair(code: string, name: string) {
    return request<{ token: string; device_name: string; scope: "full" | "demo" }>("/api/auth/pair", {
      method: "POST",
      body: JSON.stringify({ code, name }),
    });
  },
  me() {
    return request<{ name: string; scope: "full" | "demo" }>("/api/auth/me");
  },
  config() {
    return request<{
      integrations: Array<{ name: string; status: string; label: string }>;
      agents: Array<{ id: string; name: string; icon: string; channel: string }>;
      intent_count: number;
      device: { name: string; scope: string };
    }>("/api/config");
  },
};

export { ApiError };
```

- [ ] **Step 2: Commit**

```bash
git add mobile/lib/api.ts
git commit -m "feat(mobile-api): HTTP client with bearer header + typed pair/me/config helpers"
```

---

## Task 8: Auth (Keystore + biometric helpers)

**Files:**
- Create: `mobile/lib/auth.ts`

- [ ] **Step 1: Implement `lib/auth.ts`**

```typescript
// mobile/lib/auth.ts
import * as SecureStore from "expo-secure-store";
import * as LocalAuthentication from "expo-local-authentication";

const TOKEN_KEY = "neuralis_auth_token";
const DEVICE_NAME_KEY = "neuralis_device_name";

export async function storeCredentials(token: string, deviceName: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token, {
    requireAuthentication: true,
    authenticationPrompt: "Unlock NEURALIS",
  });
  await SecureStore.setItemAsync(DEVICE_NAME_KEY, deviceName);
}

export async function loadCredentials(): Promise<{ token: string; deviceName: string } | null> {
  try {
    const token = await SecureStore.getItemAsync(TOKEN_KEY, {
      requireAuthentication: true,
      authenticationPrompt: "Unlock NEURALIS",
    });
    const deviceName = await SecureStore.getItemAsync(DEVICE_NAME_KEY);
    if (!token || !deviceName) return null;
    return { token, deviceName };
  } catch {
    return null;
  }
}

export async function clearCredentials(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
  await SecureStore.deleteItemAsync(DEVICE_NAME_KEY);
}

export async function authenticateBiometric(): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const isEnrolled = await LocalAuthentication.isEnrolledAsync();
  if (!hasHardware || !isEnrolled) {
    // Device has no biometric — skip the gate (still backed by Keystore + token)
    return true;
  }
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: "Unlock NEURALIS",
    fallbackLabel: "Use device PIN",
  });
  return result.success;
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/lib/auth.ts
git commit -m "feat(mobile-auth): Keystore wrapper + biometric helper"
```

---

## Task 9: Expo Router root layout + auth gate

**Files:**
- Create: `mobile/app/_layout.tsx`
- Create: `mobile/app/+not-found.tsx`

- [ ] **Step 1: Implement `app/_layout.tsx`**

```tsx
// mobile/app/_layout.tsx
import "@/global.css";
import { useEffect } from "react";
import { Stack, Redirect, SplashScreen } from "expo-router";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { useFonts } from "@/theme/fonts";
import { useAuthStore } from "@/stores/auth";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const fontsLoaded = useFonts();
  const token = useAuthStore((s) => s.token);
  const biometricPassed = useAuthStore((s) => s.biometricPassed);

  useEffect(() => {
    if (fontsLoaded) SplashScreen.hideAsync();
  }, [fontsLoaded]);

  if (!fontsLoaded) return null;

  // Routing decision tree:
  // - No token → /pair
  // - Token but biometric not passed this session → /locked
  // - Both → tabs
  if (!token) return <Redirect href="/pair" />;
  if (!biometricPassed) return <Redirect href="/locked" />;

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="pair" />
        <Stack.Screen name="locked" />
      </Stack>
    </GestureHandlerRootView>
  );
}
```

- [ ] **Step 2: `app/+not-found.tsx`**

```tsx
// mobile/app/+not-found.tsx
import { View, Text } from "react-native";
import { Link } from "expo-router";

export default function NotFound() {
  return (
    <View className="flex-1 items-center justify-center bg-background">
      <Text className="font-headline text-2xl text-on-surface mb-4">Not found</Text>
      <Link href="/" className="text-primary font-label uppercase tracking-widest">
        Go home
      </Link>
    </View>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add mobile/app/_layout.tsx "mobile/app/+not-found.tsx"
git commit -m "feat(mobile-router): root layout with fonts + 3-state auth gate"
```

---

## Task 10: Pairing screen

**Files:**
- Create: `mobile/app/pair.tsx`

- [ ] **Step 1: Implement `app/pair.tsx`**

```tsx
// mobile/app/pair.tsx
import { useState } from "react";
import { View, Text, TextInput, ScrollView, Alert } from "react-native";
import { useRouter } from "expo-router";
import * as Device from "expo-device";
import { Button } from "@/components/primitives/Button";
import { GlassPanel } from "@/components/primitives/GlassPanel";
import { api, ApiError } from "@/lib/api";
import { storeCredentials } from "@/lib/auth";
import { useAuthStore } from "@/stores/auth";
import { SERVER_URL } from "@/lib/env";

export default function PairScreen() {
  const router = useRouter();
  const setToken = useAuthStore((s) => s.setToken);
  const [code, setCode] = useState("");
  const defaultName = `${Device.modelName ?? "Device"}-${(Device.modelId ?? Math.random().toString()).slice(0, 4)}`;
  const [name, setName] = useState(defaultName);
  const [submitting, setSubmitting] = useState(false);

  const onConnect = async () => {
    if (!/^\d{6}$/.test(code)) {
      Alert.alert("Invalid code", "The pairing code must be 6 digits.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.pair(code, name);
      await storeCredentials(res.token, res.device_name);
      setToken(res.token, res.device_name, res.scope);
      router.replace("/locked");
    } catch (e) {
      const msg = e instanceof ApiError ? (e.body as any)?.detail?.message ?? e.message : String(e);
      Alert.alert("Pairing failed", msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={{ flexGrow: 1 }} className="bg-background">
      <View className="flex-1 justify-center px-8 py-16 gap-y-8">
        <Text className="font-headline text-primary text-sm uppercase tracking-widest text-center">
          NEURALIS
        </Text>
        <Text className="font-headline text-on-surface text-3xl font-bold text-center">
          Add this device
        </Text>
        <Text className="text-on-surface-variant font-body text-base leading-relaxed text-center">
          On your Mac, run:
          {"\n\n"}
          <Text className="font-mono text-primary">
            python -m jobpulse.runner devices pair --name={name}
          </Text>
          {"\n\n"}
          Enter the 6-digit code below within 60 seconds.
        </Text>

        <GlassPanel>
          <View className="p-6 gap-y-4">
            <View>
              <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-2">
                Device name
              </Text>
              <TextInput
                value={name}
                onChangeText={setName}
                className="bg-surface-container-low rounded-full px-4 py-3 font-body text-on-surface"
                placeholder="device name"
              />
            </View>
            <View>
              <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-2">
                Pairing code
              </Text>
              <TextInput
                value={code}
                onChangeText={setCode}
                keyboardType="number-pad"
                maxLength={6}
                className="bg-surface-container-low rounded-full px-4 py-4 font-headline text-on-surface text-2xl text-center tracking-widest"
                placeholder="000000"
              />
            </View>
            <Button label={submitting ? "Connecting…" : "Connect"} onPress={onConnect} disabled={submitting} />
          </View>
        </GlassPanel>

        <Text className="text-outline text-xs font-body text-center">
          Server: {SERVER_URL}
        </Text>
      </View>
    </ScrollView>
  );
}
```

Need `expo-device`:

```bash
cd mobile
bun add expo-device
```

- [ ] **Step 2: Commit**

```bash
git add mobile/app/pair.tsx mobile/package.json
git commit -m "feat(mobile-pair): pairing screen with 6-digit code input"
```

---

## Task 11: Biometric gate screen

**Files:**
- Create: `mobile/app/locked.tsx`

- [ ] **Step 1: Implement `app/locked.tsx`**

```tsx
// mobile/app/locked.tsx
import { useEffect, useState } from "react";
import { View, Text } from "react-native";
import { useRouter } from "expo-router";
import { Button } from "@/components/primitives/Button";
import { authenticateBiometric, clearCredentials } from "@/lib/auth";
import { useAuthStore } from "@/stores/auth";

export default function LockedScreen() {
  const router = useRouter();
  const markBiometric = useAuthStore((s) => s.markBiometric);
  const clearToken = useAuthStore((s) => s.clearToken);
  const [failures, setFailures] = useState(0);

  const tryUnlock = async () => {
    const ok = await authenticateBiometric();
    if (ok) {
      markBiometric(true);
      router.replace("/(tabs)/hub");
    } else {
      const next = failures + 1;
      setFailures(next);
      if (next >= 3) {
        // Force re-pair after 3 failures
        await clearCredentials();
        clearToken();
        router.replace("/pair");
      }
    }
  };

  useEffect(() => {
    tryUnlock();
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <View className="flex-1 bg-background items-center justify-center px-8 gap-y-6">
      <Text className="font-headline text-primary text-sm uppercase tracking-widest">
        NEURALIS
      </Text>
      <Text className="font-headline text-on-surface text-2xl font-bold">
        Unlock NEURALIS
      </Text>
      <Text className="text-on-surface-variant font-body text-base text-center">
        Use fingerprint or face to continue.{"\n"}
        {failures > 0 ? `Attempt ${failures} of 3.` : ""}
      </Text>
      <Button label="Try again" onPress={tryUnlock} />
    </View>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/app/locked.tsx
git commit -m "feat(mobile-locked): biometric gate with 3-failure re-pair fallback"
```

---

## Task 12: Tab layout

**Files:**
- Create: `mobile/app/(tabs)/_layout.tsx`

- [ ] **Step 1: Implement `app/(tabs)/_layout.tsx`**

```tsx
// mobile/app/(tabs)/_layout.tsx
import { Tabs } from "expo-router";
import { View, Text } from "react-native";
import { BlurView } from "expo-blur";
import { ConnectionBadge } from "@/components/ConnectionBadge";

function TopBar() {
  return (
    <BlurView
      intensity={32}
      tint="light"
      className="flex-row items-center justify-between px-6 pt-12 pb-3"
      style={{ backgroundColor: "rgba(246,250,248,0.7)" }}
    >
      <Text className="font-headline text-primary text-2xl font-bold tracking-widest">
        NEURALIS
      </Text>
      <ConnectionBadge />
    </BlurView>
  );
}

function TabBarIcon({ icon, focused }: { icon: string; focused: boolean }) {
  // Material Symbols not bundled; use simple emoji/letter placeholders.
  // Phase 1B replaces with actual Material Symbols rendering.
  const map: Record<string, string> = { hub: "◎", chat: "✎", bridge: "⬡", profile: "◉" };
  return (
    <View
      className={`items-center justify-center rounded-full px-5 py-2 ${
        focused ? "bg-primary-container/30" : ""
      }`}
    >
      <Text className={`text-xl ${focused ? "text-primary" : "text-outline"}`}>
        {map[icon]}
      </Text>
    </View>
  );
}

export default function TabsLayout() {
  return (
    <View style={{ flex: 1 }}>
      <TopBar />
      <Tabs
        screenOptions={{
          headerShown: false,
          tabBarShowLabel: true,
          tabBarStyle: {
            position: "absolute",
            bottom: 24,
            left: 16,
            right: 16,
            borderRadius: 9999,
            backgroundColor: "rgba(255,255,255,0.6)",
            elevation: 12,
            height: 72,
            paddingTop: 8,
            paddingBottom: 8,
            borderTopWidth: 0,
          },
          tabBarLabelStyle: {
            fontFamily: "SpaceGrotesk_500Medium",
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: 1.5,
            marginTop: 2,
          },
          tabBarActiveTintColor: "#006c52",
          tabBarInactiveTintColor: "#6e7a74",
        }}
      >
        <Tabs.Screen
          name="hub"
          options={{
            title: "Hub",
            tabBarIcon: ({ focused }) => <TabBarIcon icon="hub" focused={focused} />,
          }}
        />
        <Tabs.Screen
          name="chat"
          options={{
            title: "Chat",
            tabBarIcon: ({ focused }) => <TabBarIcon icon="chat" focused={focused} />,
          }}
        />
        <Tabs.Screen
          name="bridge"
          options={{
            title: "Bridge",
            tabBarIcon: ({ focused }) => <TabBarIcon icon="bridge" focused={focused} />,
          }}
        />
        <Tabs.Screen
          name="profile"
          options={{
            title: "Profile",
            tabBarIcon: ({ focused }) => <TabBarIcon icon="profile" focused={focused} />,
          }}
        />
      </Tabs>
    </View>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/app/\(tabs\)/_layout.tsx
git commit -m "feat(mobile-tabs): glassmorphic tab bar + top app bar layout"
```

---

## Task 13: Hub tab — bento layout (mock data)

**Files:**
- Create: `mobile/app/(tabs)/hub.tsx`
- Create: `mobile/components/hub/AgentCard.tsx`
- Create: `mobile/components/hub/ApprovalCard.tsx`
- Create: `mobile/components/hub/SummaryTile.tsx`
- Create: `mobile/components/hub/ActivityRow.tsx`

- [ ] **Step 1: `components/hub/AgentCard.tsx`**

```tsx
// mobile/components/hub/AgentCard.tsx
import { View, Text } from "react-native";
import { GlassPanel } from "@/components/primitives/GlassPanel";
import { Pill } from "@/components/primitives/Pill";

type Props = {
  name: string;
  status: string;
  label?: string;
  progress?: number; // 0..1
};

export function AgentCard({ name, status, label, progress }: Props) {
  return (
    <GlassPanel>
      <View className="p-5 gap-y-3 w-72">
        <View className="flex-row items-center justify-between">
          <Text className="font-headline font-bold text-on-surface text-lg">{name}</Text>
          <Pill tone="primary">{status}</Pill>
        </View>
        {label ? <Text className="font-body text-on-surface-variant text-sm">{label}</Text> : null}
        {typeof progress === "number" ? (
          <View className="h-1.5 bg-surface-variant rounded-full overflow-hidden">
            <View
              className="h-full bg-primary"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </View>
        ) : null}
      </View>
    </GlassPanel>
  );
}
```

- [ ] **Step 2: `components/hub/ApprovalCard.tsx`**

```tsx
// mobile/components/hub/ApprovalCard.tsx
import { View, Text } from "react-native";
import { GlassPanel } from "@/components/primitives/GlassPanel";
import { Button } from "@/components/primitives/Button";

type Props = {
  company: string;
  role: string;
  onApprove: () => void;
  onReject: () => void;
};

export function ApprovalCard({ company, role, onApprove, onReject }: Props) {
  return (
    <GlassPanel>
      <View className="p-5 gap-y-3">
        <Text className="font-headline font-bold text-on-surface text-lg">{company}</Text>
        <Text className="font-body text-on-surface-variant">{role}</Text>
        <View className="flex-row gap-x-3 pt-2">
          <View className="flex-1">
            <Button label="Approve" onPress={onApprove} />
          </View>
          <View className="flex-1">
            <Button label="Reject" onPress={onReject} variant="secondary" />
          </View>
        </View>
      </View>
    </GlassPanel>
  );
}
```

- [ ] **Step 3: `components/hub/SummaryTile.tsx`**

```tsx
// mobile/components/hub/SummaryTile.tsx
import { View, Text } from "react-native";
import { GlassPanel } from "@/components/primitives/GlassPanel";

type Props = { label: string; value: string };

export function SummaryTile({ label, value }: Props) {
  return (
    <GlassPanel style={{ flex: 1 }}>
      <View className="p-5 gap-y-2">
        <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest">
          {label}
        </Text>
        <Text className="font-headline text-on-surface text-3xl font-bold">{value}</Text>
      </View>
    </GlassPanel>
  );
}
```

- [ ] **Step 4: `components/hub/ActivityRow.tsx`**

```tsx
// mobile/components/hub/ActivityRow.tsx
import { View, Text } from "react-native";

type Props = { ts: string; text: string };

export function ActivityRow({ ts, text }: Props) {
  return (
    <View className="flex-row items-start gap-x-3 py-3 border-b border-outline-variant/30">
      <Text className="font-label text-outline text-xs">{ts}</Text>
      <Text className="font-body text-on-surface flex-1">{text}</Text>
    </View>
  );
}
```

- [ ] **Step 5: `app/(tabs)/hub.tsx` (with mock data — Task 14 wires the quick-input)**

```tsx
// mobile/app/(tabs)/hub.tsx
import { ScrollView, View, Text } from "react-native";
import { AgentCard } from "@/components/hub/AgentCard";
import { ApprovalCard } from "@/components/hub/ApprovalCard";
import { SummaryTile } from "@/components/hub/SummaryTile";
import { ActivityRow } from "@/components/hub/ActivityRow";
import { QuickInput } from "@/components/hub/QuickInput";

export default function HubScreen() {
  const greeting = "Good morning";
  const today = new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

  return (
    <View style={{ flex: 1 }}>
      <ScrollView className="bg-background" contentContainerStyle={{ paddingBottom: 200 }}>
        <View className="px-6 pt-6 gap-y-6">
          <View>
            <Text className="font-headline text-on-surface text-3xl font-bold">{greeting}</Text>
            <Text className="font-label text-on-surface-variant text-sm uppercase tracking-widest mt-1">
              {today}
            </Text>
          </View>

          {/* Live agents (horizontal) */}
          <View>
            <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-3">
              Live agents
            </Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} className="gap-x-3">
              <View className="mr-3">
                <AgentCard name="Job Bot" status="Processing" label="Mock — Phase 1B wires real data" progress={0.65} />
              </View>
            </ScrollView>
          </View>

          {/* Pending approvals */}
          <View>
            <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-3">
              Pending approvals
            </Text>
            <Text className="font-body text-outline text-sm italic">
              No pending approvals (mock — Phase 1B wires real data)
            </Text>
          </View>

          {/* Today summary */}
          <View>
            <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-3">
              Today
            </Text>
            <View className="flex-row gap-x-3">
              <SummaryTile label="Apps" value="0" />
              <SummaryTile label="Papers" value="0" />
            </View>
            <View className="flex-row gap-x-3 mt-3">
              <SummaryTile label="Budget" value="£0" />
              <SummaryTile label="Calendar" value="—" />
            </View>
          </View>

          {/* Recent activity */}
          <View>
            <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-3">
              Recent activity
            </Text>
            <ActivityRow ts="—" text="Connected. Phase 1A skeleton — Phase 1B wires real activity." />
          </View>
        </View>
      </ScrollView>
      <QuickInput />
    </View>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add mobile/app/\(tabs\)/hub.tsx mobile/components/hub/AgentCard.tsx mobile/components/hub/ApprovalCard.tsx mobile/components/hub/SummaryTile.tsx mobile/components/hub/ActivityRow.tsx
git commit -m "feat(mobile-hub): bento layout with mock agent cards, summary tiles, activity"
```

---

## Task 14: Quick-input bar with WebSocket echo round-trip

**Files:**
- Create: `mobile/components/hub/QuickInput.tsx`
- Modify: `mobile/app/_layout.tsx` (initialize WS connection on mount)

- [ ] **Step 1: Initialize the WS client at root layout**

Edit `mobile/app/_layout.tsx` — add WS init after the auth gate logic:

```tsx
// Add to the imports:
import { useEffect, useRef } from "react";
import { WsClient } from "@/lib/ws";
import { wsUrl } from "@/lib/env";
import { useAuthStore } from "@/stores/auth";
import { useConnectionStore } from "@/stores/connection";
import { useChatStore } from "@/stores/chat";

// ... inside RootLayout function, before `if (!fontsLoaded) return null;`:
const setConnState = useConnectionStore((s) => s.setState);
const appendDelta = useChatStore((s) => s.appendDelta);
const finalizeMessage = useChatStore((s) => s.finalizeMessage);
const wsRef = useRef<WsClient | null>(null);

useEffect(() => {
  if (!token || !biometricPassed) return;
  const client = new WsClient({
    url: wsUrl(),
    getToken: () => useAuthStore.getState().token,
    onStateChange: setConnState,
    onFrame: (frame) => {
      if (frame.type === "msg.delta") {
        appendDelta(frame.channel as string, frame.seq as number, frame.content as string);
      } else if (frame.type === "msg.done") {
        finalizeMessage(frame.channel as string, frame.seq as number, frame.msg_id as string);
      }
    },
  });
  client.connect();
  wsRef.current = client;
  return () => {
    client.disconnect();
    wsRef.current = null;
  };
}, [token, biometricPassed, setConnState, appendDelta, finalizeMessage]);
```

Expose `wsRef` via a simple module-level singleton so `QuickInput.tsx` can call `send()`:

Create `mobile/lib/ws-instance.ts`:

```typescript
// mobile/lib/ws-instance.ts
import { WsClient } from "./ws";

let instance: WsClient | null = null;

export function setWs(client: WsClient | null): void {
  instance = client;
}

export function getWs(): WsClient | null {
  return instance;
}
```

Update `_layout.tsx` to set the singleton:

```tsx
import { setWs } from "@/lib/ws-instance";

// Inside the useEffect, after `wsRef.current = client;`:
setWs(client);
return () => {
  client.disconnect();
  wsRef.current = null;
  setWs(null);
};
```

- [ ] **Step 2: Implement `components/hub/QuickInput.tsx`**

```tsx
// mobile/components/hub/QuickInput.tsx
import { useState } from "react";
import { View, TextInput, Pressable, Text, KeyboardAvoidingView, Platform } from "react-native";
import { GlassPanel } from "@/components/primitives/GlassPanel";
import { getWs } from "@/lib/ws-instance";
import { useChatStore } from "@/stores/chat";

export function QuickInput() {
  const [text, setText] = useState("");
  const appendUserMessage = useChatStore((s) => s.appendUserMessage);

  const send = () => {
    if (!text.trim()) return;
    const ws = getWs();
    if (!ws) return;
    appendUserMessage("global", text);
    ws.send({
      type: "msg",
      channel: "global",
      text,
      client_uuid: `quick-${Date.now()}`,
    });
    setText("");
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      className="absolute left-4 right-4 bottom-32"
    >
      <GlassPanel>
        <View className="flex-row items-center gap-x-2 p-2">
          <Pressable className="w-12 h-12 items-center justify-center">
            <Text className="text-on-surface-variant text-2xl">+</Text>
          </Pressable>
          <TextInput
            value={text}
            onChangeText={setText}
            placeholder="Ask anything…"
            className="flex-1 font-body text-on-surface"
            multiline
            onSubmitEditing={send}
          />
          <Pressable className="w-12 h-12 items-center justify-center" disabled>
            <Text className="text-outline text-xl">🎤</Text>
          </Pressable>
          <Pressable
            onPress={send}
            disabled={!text.trim()}
            className={`w-12 h-12 rounded-full items-center justify-center ${
              text.trim() ? "bg-primary" : "bg-surface-variant"
            }`}
          >
            <Text className={text.trim() ? "text-on-primary" : "text-outline"}>↑</Text>
          </Pressable>
        </View>
      </GlassPanel>
    </KeyboardAvoidingView>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add mobile/components/hub/QuickInput.tsx mobile/lib/ws-instance.ts mobile/app/_layout.tsx
git commit -m "feat(mobile-hub): QuickInput bar with WebSocket round-trip + WS singleton"
```

---

## Task 15: Chat list

**Files:**
- Create: `mobile/lib/agents.ts`
- Create: `mobile/app/(tabs)/chat/index.tsx`
- Create: `mobile/components/chat/AgentBadge.tsx`

- [ ] **Step 1: Create `lib/agents.ts` with the canonical 18-agent list**

```typescript
// mobile/lib/agents.ts
export type Agent = {
  id: string;
  name: string;
  icon: string;
  channel: string;
};

export const AGENTS: Agent[] = [
  { id: "jobs",          name: "Job Bot",          icon: "work",          channel: "agent:jobs" },
  { id: "budget",        name: "Budget",           icon: "wallet",        channel: "agent:budget" },
  { id: "tasks",         name: "Tasks",            icon: "checklist",     channel: "agent:tasks" },
  { id: "calendar",      name: "Calendar",         icon: "calendar",      channel: "agent:calendar" },
  { id: "gmail",         name: "Gmail",            icon: "mail",          channel: "agent:gmail" },
  { id: "github",        name: "GitHub",           icon: "code",          channel: "agent:github" },
  { id: "papers",        name: "Papers",           icon: "article",       channel: "agent:papers" },
  { id: "briefing",      name: "Briefing",         icon: "today",         channel: "agent:briefing" },
  { id: "hierarchical",  name: "Hierarchical",     icon: "tree",          channel: "agent:hierarchical" },
  { id: "peer_debate",   name: "Peer Debate",      icon: "forum",         channel: "agent:peer_debate" },
  { id: "dynamic_swarm", name: "Dynamic Swarm",    icon: "hub",           channel: "agent:dynamic_swarm" },
  { id: "enhanced_swarm",name: "Enhanced Swarm",   icon: "auto",          channel: "agent:enhanced_swarm" },
  { id: "map_reduce",    name: "Map-Reduce",       icon: "scatter",       channel: "agent:map_reduce" },
  { id: "plan_execute",  name: "Plan-and-Execute", icon: "task",          channel: "agent:plan_execute" },
  { id: "codegraph",     name: "CodeGraph",        icon: "graph",         channel: "agent:codegraph" },
  { id: "cognitive",     name: "Think",            icon: "psychology",    channel: "agent:cognitive" },
  { id: "memory",        name: "Memory",           icon: "memory",        channel: "agent:memory" },
  { id: "fact_check",    name: "Fact Check",       icon: "verified",      channel: "agent:fact_check" },
];
```

- [ ] **Step 2: `components/chat/AgentBadge.tsx`**

```tsx
// mobile/components/chat/AgentBadge.tsx
import { View, Text } from "react-native";

type Props = { name: string };

const PALETTE = ["#006c52", "#73d9b5", "#3d6752", "#8ff6d0", "#74593f", "#e3c0a0", "#fed9b8", "#48725d"];

function colorFor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

export function AgentBadge({ name }: Props) {
  return (
    <View
      className="rounded-full px-3 py-1"
      style={{ backgroundColor: `${colorFor(name)}33` }}
    >
      <Text className="font-label text-xs uppercase tracking-widest" style={{ color: colorFor(name) }}>
        {name}
      </Text>
    </View>
  );
}
```

- [ ] **Step 3: `app/(tabs)/chat/index.tsx`**

```tsx
// mobile/app/(tabs)/chat/index.tsx
import { ScrollView, View, Text, Pressable } from "react-native";
import { useRouter } from "expo-router";
import { AGENTS } from "@/lib/agents";
import { useChatStore } from "@/stores/chat";

export default function ChatList() {
  const router = useRouter();
  const channels = useChatStore((s) => s.channels);

  return (
    <ScrollView className="bg-background" contentContainerStyle={{ paddingBottom: 200, paddingTop: 24 }}>
      <View className="px-6 mb-4">
        <Text className="font-headline text-on-surface text-3xl font-bold">Chats</Text>
      </View>
      <View className="px-4">
        {AGENTS.map((agent) => {
          const ch = channels[agent.channel];
          const lastMsg = ch?.messages.at(-1)?.content ?? "No messages yet";
          return (
            <Pressable
              key={agent.id}
              onPress={() => router.push(`/(tabs)/chat/${agent.id}` as any)}
              className="px-4 py-3 mb-1 rounded-xl bg-surface-container-lowest"
            >
              <Text className="font-headline font-bold text-on-surface text-base">{agent.name}</Text>
              <Text className="font-body text-on-surface-variant text-sm mt-1" numberOfLines={1}>
                {lastMsg}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </ScrollView>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/agents.ts mobile/app/\(tabs\)/chat/index.tsx mobile/components/chat/AgentBadge.tsx
git commit -m "feat(mobile-chat): chat list with 18 agent rows + last-message preview"
```

---

## Task 16: Per-agent chat (echo round-trip)

**Files:**
- Create: `mobile/app/(tabs)/chat/[agent].tsx`

- [ ] **Step 1: Implement `app/(tabs)/chat/[agent].tsx`**

```tsx
// mobile/app/(tabs)/chat/[agent].tsx
import { useState } from "react";
import { ScrollView, View, Text, TextInput, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AGENTS } from "@/lib/agents";
import { useChatStore } from "@/stores/chat";
import { getWs } from "@/lib/ws-instance";
import { MessageBubble } from "@/components/primitives/MessageBubble";

export default function AgentChat() {
  const { agent: agentId } = useLocalSearchParams<{ agent: string }>();
  const agent = AGENTS.find((a) => a.id === agentId);
  const channelId = agent?.channel ?? `agent:${agentId}`;
  const messages = useChatStore((s) => s.channels[channelId]?.messages ?? []);
  const partial = useChatStore((s) => s.channels[channelId]?.partial ?? {});
  const appendUserMessage = useChatStore((s) => s.appendUserMessage);
  const [text, setText] = useState("");

  const send = () => {
    if (!text.trim()) return;
    const ws = getWs();
    if (!ws) return;
    appendUserMessage(channelId, text);
    ws.send({
      type: "msg",
      channel: channelId,
      text,
      client_uuid: `chat-${Date.now()}`,
    });
    setText("");
  };

  return (
    <View style={{ flex: 1 }} className="bg-background">
      <View className="px-6 pt-6 pb-3 border-b border-outline-variant/20">
        <Text className="font-headline text-on-surface text-2xl font-bold">{agent?.name ?? agentId}</Text>
      </View>
      <ScrollView className="flex-1 px-4" contentContainerStyle={{ paddingBottom: 160, paddingTop: 16 }}>
        {messages.length === 0 && Object.keys(partial).length === 0 ? (
          <View className="items-center pt-20">
            <Text className="font-body text-outline text-base">No messages yet. Say hi.</Text>
          </View>
        ) : (
          messages.map((m) => (
            <MessageBubble key={m.id} role={m.role} content={m.content} agentName={m.agentName} />
          ))
        )}
        {Object.entries(partial).map(([seq, content]) => (
          <MessageBubble key={`p-${seq}`} role="agent" content={content + "▌"} />
        ))}
      </ScrollView>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        className="absolute left-4 right-4 bottom-32"
      >
        <View className="flex-row items-center gap-x-2 bg-surface-container-lowest rounded-full p-2">
          <TextInput
            value={text}
            onChangeText={setText}
            placeholder={`Message ${agent?.name ?? "agent"}…`}
            className="flex-1 px-4 py-3 font-body text-on-surface"
            onSubmitEditing={send}
            multiline
          />
          <Pressable
            onPress={send}
            disabled={!text.trim()}
            className={`w-12 h-12 rounded-full items-center justify-center ${
              text.trim() ? "bg-primary" : "bg-surface-variant"
            }`}
          >
            <Text className={text.trim() ? "text-on-primary text-xl" : "text-outline text-xl"}>↑</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/app/\(tabs\)/chat/\[agent\].tsx
git commit -m "feat(mobile-chat): per-agent chat with echo round-trip + streaming render"
```

---

## Task 17: Bridge tab

**Files:**
- Create: `mobile/app/(tabs)/bridge.tsx`

- [ ] **Step 1: Implement `app/(tabs)/bridge.tsx`**

```tsx
// mobile/app/(tabs)/bridge.tsx
import { useEffect, useState } from "react";
import { ScrollView, View, Text } from "react-native";
import { Card } from "@/components/primitives/Card";
import { Pill } from "@/components/primitives/Pill";
import { api, ApiError } from "@/lib/api";

type Integration = { name: string; status: string; label: string };

export default function BridgeScreen() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.config()
      .then((cfg) => setIntegrations(cfg.integrations))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  return (
    <ScrollView className="bg-background" contentContainerStyle={{ paddingBottom: 200, paddingTop: 24 }}>
      <View className="px-6 gap-y-6">
        <Text className="font-headline text-on-surface text-3xl font-bold">Bridge</Text>

        <View>
          <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest mb-3">
            Integrations
          </Text>
          {error ? (
            <Card>
              <Text className="font-body text-error">{error}</Text>
            </Card>
          ) : integrations.length === 0 ? (
            <Text className="font-body text-outline">Loading…</Text>
          ) : (
            integrations.map((i) => (
              <View key={i.name} className="mb-3">
                <Card>
                  <View className="flex-row items-center justify-between">
                    <Text className="font-headline font-bold text-on-surface text-base">{i.label}</Text>
                    <Pill tone={i.status === "connected" ? "primary" : "neutral"}>{i.status}</Pill>
                  </View>
                </Card>
              </View>
            ))
          )}
        </View>

        <Text className="font-body text-outline text-sm italic">
          Toggles + system health arrive in Phase 1C.
        </Text>
      </View>
    </ScrollView>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/app/\(tabs\)/bridge.tsx
git commit -m "feat(mobile-bridge): integrations list from /api/config (read-only)"
```

---

## Task 18: Profile tab

**Files:**
- Create: `mobile/app/(tabs)/profile.tsx`

- [ ] **Step 1: Implement `app/(tabs)/profile.tsx`**

```tsx
// mobile/app/(tabs)/profile.tsx
import { ScrollView, View, Text } from "react-native";
import { useRouter } from "expo-router";
import { Card } from "@/components/primitives/Card";
import { Button } from "@/components/primitives/Button";
import { useAuthStore } from "@/stores/auth";
import { clearCredentials } from "@/lib/auth";
import { api } from "@/lib/api";

export default function ProfileScreen() {
  const router = useRouter();
  const deviceName = useAuthStore((s) => s.deviceName);
  const scope = useAuthStore((s) => s.scope);
  const clearToken = useAuthStore((s) => s.clearToken);

  const onSignOut = async () => {
    await clearCredentials();
    clearToken();
    router.replace("/pair");
  };

  const onRePair = async () => {
    if (deviceName) {
      try {
        await api.post("/api/auth/revoke", { name: deviceName });
      } catch {
        /* ignore — will re-pair anyway */
      }
    }
    await clearCredentials();
    clearToken();
    router.replace("/pair");
  };

  return (
    <ScrollView className="bg-background" contentContainerStyle={{ paddingBottom: 200, paddingTop: 24 }}>
      <View className="px-6 gap-y-6">
        <Text className="font-headline text-on-surface text-3xl font-bold">Profile</Text>

        <Card>
          <View className="gap-y-2">
            <Text className="font-label text-on-surface-variant text-xs uppercase tracking-widest">Device</Text>
            <Text className="font-headline font-bold text-on-surface text-xl">{deviceName ?? "—"}</Text>
            <Text className="font-body text-on-surface-variant text-sm">Scope: {scope}</Text>
          </View>
        </Card>

        <View className="gap-y-3">
          <Button label="Re-pair this device" onPress={onRePair} variant="secondary" />
          <Button label="Sign out" onPress={onSignOut} variant="ghost" />
        </View>

        <Text className="font-body text-outline text-sm italic">
          Push categories, biometric prefs, paired devices, export — Phase 1C.
        </Text>
      </View>
    </ScrollView>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/app/\(tabs\)/profile.tsx
git commit -m "feat(mobile-profile): identity card + sign out + re-pair (Phase 1A skeleton)"
```

---

## Task 19: Connection badge (top bar)

**Files:**
- Create: `mobile/components/ConnectionBadge.tsx`

- [ ] **Step 1: Implement `components/ConnectionBadge.tsx`**

```tsx
// mobile/components/ConnectionBadge.tsx
import { View, Text } from "react-native";
import { useConnectionStore } from "@/stores/connection";

const COLORS: Record<string, { dot: string; label: string }> = {
  ready:        { dot: "bg-primary-fixed", label: "Connected" },
  connecting:   { dot: "bg-secondary-fixed-dim", label: "Connecting…" },
  reconnecting: { dot: "bg-secondary-fixed-dim", label: "Reconnecting…" },
  failed:       { dot: "bg-error", label: "Failed" },
  disconnected: { dot: "bg-outline-variant", label: "Disconnected" },
};

export function ConnectionBadge() {
  const state = useConnectionStore((s) => s.state);
  const c = COLORS[state] ?? COLORS.disconnected;
  return (
    <View className="flex-row items-center gap-x-2">
      <View className={`w-2 h-2 rounded-full ${c.dot}`} />
      <Text className="font-label text-outline text-xs uppercase tracking-widest">{c.label}</Text>
    </View>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add mobile/components/ConnectionBadge.tsx
git commit -m "feat(mobile-conn-badge): top-bar connection state indicator"
```

---

## Task 20: Backend echo channel handler (smoke target)

**Files:**
- Modify: `shared/dispatch/ws_dispatcher.py`

- [ ] **Step 1: Verify the dispatcher already echoes `msg` frames**

```bash
grep -A 10 "if t == \"msg\":" shared/dispatch/ws_dispatcher.py
```

Expected: the existing implementation (from Task 14 of Phase 0) already echoes `msg` frames as `[echo] <text>` deltas. No change needed.

- [ ] **Step 2: Confirm with a quick smoke test**

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns/.worktrees/mobile-phase-0-backend
python -m pytest tests/integration/test_ws_endpoint.py -v
```

Expected: 5 passed. The echo behavior covers the mobile app's QuickInput round-trip during Phase 1A — no backend change needed for this task.

- [ ] **Step 3: No commit (this task is a verification gate; the work was already done in Phase 0)**

Add a note in `tasks_complete.md` or proceed directly to Task 21. If a small enhancement IS needed (e.g., better echo formatting), make it surgically; otherwise this task is a no-op.

---

## Task 21: `app.config.ts` — name, scheme, deep links

**Files:**
- Create: `mobile/app.config.ts`
- Create: `mobile/lib/deep-link.ts`

- [ ] **Step 1: Implement `mobile/app.config.ts`**

```typescript
// mobile/app.config.ts
import type { ExpoConfig } from "@expo/config";

const config: ExpoConfig = {
  name: "NEURALIS",
  slug: "neuralis",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  userInterfaceStyle: "light",
  scheme: "neuralis",
  android: {
    package: "io.yashbishnoi.neuralis",
    adaptiveIcon: {
      foregroundImage: "./assets/adaptive-icon.png",
      backgroundColor: "#f6faf8",
    },
    permissions: ["USE_BIOMETRIC", "USE_FINGERPRINT", "INTERNET"],
    intentFilters: [
      {
        action: "VIEW",
        autoVerify: false,
        data: [{ scheme: "neuralis" }],
        category: ["BROWSABLE", "DEFAULT"],
      },
    ],
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    "expo-local-authentication",
    "expo-font",
  ],
  extra: {
    serverUrl: process.env.EXPO_PUBLIC_NEURALIS_SERVER_URL ?? "http://100.x.y.z:8000",
    eas: {
      projectId: "REPLACE_WITH_EAS_PROJECT_ID_AFTER_FIRST_BUILD",
    },
  },
  experiments: {
    typedRoutes: true,
  },
};

export default config;
```

The `eas.projectId` will be filled in automatically when you run `eas init` for the first time.

- [ ] **Step 2: `mobile/lib/deep-link.ts`**

```typescript
// mobile/lib/deep-link.ts
import { Linking } from "react-native";

export type DeepLinkTarget =
  | { kind: "hub" }
  | { kind: "chat"; agent: string; msgId?: string }
  | { kind: "approval"; id: string }
  | { kind: "settings"; section?: string }
  | { kind: "unknown" };

export function parse(url: string): DeepLinkTarget {
  if (!url.startsWith("neuralis://")) return { kind: "unknown" };
  const path = url.slice("neuralis://".length);
  const [pathname, qs = ""] = path.split("?");
  const segments = pathname.split("/").filter(Boolean);
  const params = new URLSearchParams(qs);

  if (segments.length === 0 || segments[0] === "hub") return { kind: "hub" };
  if (segments[0] === "chat" && segments[1])
    return { kind: "chat", agent: segments[1], msgId: params.get("msg_id") ?? undefined };
  if (segments[0] === "approval" && segments[1]) return { kind: "approval", id: segments[1] };
  if (segments[0] === "settings")
    return { kind: "settings", section: segments[1] };
  return { kind: "unknown" };
}

export function listen(handler: (t: DeepLinkTarget) => void): () => void {
  const sub = Linking.addEventListener("url", ({ url }) => handler(parse(url)));
  return () => sub.remove();
}
```

- [ ] **Step 3: Commit**

```bash
git add mobile/app.config.ts mobile/lib/deep-link.ts
git commit -m "feat(mobile-config): app.config.ts (scheme, intent filters) + deep link parser"
```

---

## Task 22: EAS Build profiles

**Files:**
- Create: `mobile/eas.json`

- [ ] **Step 1: Implement `mobile/eas.json`**

```json
{
  "cli": {
    "version": ">= 12.0.0"
  },
  "build": {
    "internal": {
      "android": {
        "buildType": "apk",
        "distribution": "internal",
        "env": {
          "EXPO_PUBLIC_NEURALIS_SERVER_URL": "http://100.x.y.z:8000"
        }
      }
    },
    "preview": {
      "extends": "internal",
      "android": {
        "buildType": "apk"
      }
    },
    "production": {
      "android": {
        "buildType": "app-bundle"
      }
    }
  },
  "submit": {
    "production": {
      "android": {
        "track": "internal"
      }
    }
  }
}
```

Replace `100.x.y.z` with the actual Tailscale IP of the Mac (find via `tailscale ip -4` on the Mac).

- [ ] **Step 2: Initialize EAS (interactive — manual step)**

The user runs this once to link the project:

```bash
cd mobile
bunx eas-cli login
bunx eas-cli init
```

After init, `app.config.ts` will be updated automatically with the `eas.projectId`.

- [ ] **Step 3: Commit**

```bash
git add mobile/eas.json
git commit -m "feat(mobile-eas): EAS Build profiles (internal APK + production AAB)"
```

---

## Task 23: Maestro E2E test scaffolding

**Files:**
- Create: `mobile/tests/e2e/smoke.yaml`
- Create: `mobile/tests/e2e/README.md`

- [ ] **Step 1: Install Maestro CLI (one-time, manual)**

```bash
curl -Ls "https://get.maestro.mobile.dev" | bash
maestro --version
```

- [ ] **Step 2: Create `mobile/tests/e2e/smoke.yaml`**

```yaml
appId: io.yashbishnoi.neuralis
---
- launchApp
- assertVisible: "NEURALIS"
- assertVisible: "Add this device"
# Manual: pair from Mac CLI, enter code, confirm Hub appears
- assertVisible:
    text: "Hub"
    optional: true
```

- [ ] **Step 3: `mobile/tests/e2e/README.md`**

```markdown
# Maestro E2E tests

Run on a connected Android device/emulator with the NEURALIS APK installed.

```bash
maestro test mobile/tests/e2e/smoke.yaml
```

The smoke test verifies cold launch + pairing screen visibility. Pair flow is
manual (requires the Mac CLI to generate the code). Phase 1B/1C add automated
flows for voice, push, share intent, etc.
```

- [ ] **Step 4: Commit**

```bash
git add mobile/tests/e2e/
git commit -m "test(mobile-e2e): Maestro smoke test scaffold"
```

---

## Task 24: First EAS internal build + manual install

**Files:**
- (no code changes; documentation + manual run)

- [ ] **Step 1: Verify Mac is reachable from phone over Tailscale**

On the Mac:

```bash
tailscale ip -4
# note the 100.x.y.z address
```

On the phone (with Tailscale app installed and connected):

```bash
# In any browser:
http://<that 100.x.y.z>:8000/health
```

Expected: returns the daemon health JSON. If it fails: ensure both devices are on the same Tailnet, MagicDNS is enabled, and `caffeinate` is keeping the Mac awake.

- [ ] **Step 2: Update `EXPO_PUBLIC_NEURALIS_SERVER_URL`**

Either set it as an environment variable for the EAS build, or hardcode it in `mobile/eas.json` profile env (replace placeholder `100.x.y.z` with the real IP).

- [ ] **Step 3: Run the EAS build**

```bash
cd mobile
bunx eas-cli build -p android --profile internal --non-interactive
```

Expected: ~10-15 min build. Returns a download URL for the APK when done.

- [ ] **Step 4: Install the APK on the phone**

Either:
- Open the EAS build URL on the phone → download → install (allow "install unknown apps" for Chrome/Files)
- OR `adb install <apk-path>` if connected via USB

- [ ] **Step 5: Cold-launch smoke test**

Open NEURALIS on the phone. Expected:
1. Splash → fonts load → pair screen
2. On Mac: `python -m jobpulse.runner devices pair --name=Yash-Pixel`
3. Enter the 6-digit code on the phone
4. Biometric prompt → succeed → Hub
5. Connection badge shows "Connected"
6. Type a message in the QuickInput → "[echo] <message>" appears in the global channel
7. Tap Chat tab → see 18 agents → tap one → echo round-trip works

If any step fails, capture the error and fix in a follow-up commit. Common gotchas:
- DNS doesn't resolve `<mac>` — use the literal `100.x.y.z` IP instead
- WS won't connect — verify FastAPI is running and `caffeinate` is keeping Mac awake
- Biometric never prompts — phone has no enrolled fingerprint/face; the code falls through to allow access (per `lib/auth.ts`)

- [ ] **Step 6: Commit a build-success note (optional)**

```bash
echo "Phase 1A first internal-track build: $(date -u +%Y-%m-%dT%H:%M:%SZ) — APK installed on Yash-Pixel" >> mobile/BUILD_LOG.md
git add mobile/BUILD_LOG.md
git commit -m "chore(mobile-build): first EAS internal build successful"
```

---

## Task 25: Docs + Phase 1A complete tag

**Files:**
- Modify: `CLAUDE.md` — add `mobile/` section
- Modify: top-level `README.md` — quick-start link to mobile

- [ ] **Step 1: Add a `mobile/` section to `CLAUDE.md`**

Append a section after the existing "NEURALIS Mobile Backend (Phase 0)" section:

```markdown
## NEURALIS Mobile App (Phase 1A)

Android-native app at `mobile/` (React Native + Expo SDK 52 + NativeWind 4 + TypeScript).

### Dev

```bash
cd mobile
EXPO_PUBLIC_NEURALIS_SERVER_URL=http://<tailscale-ip>:8000 bun expo start
```

### Build

```bash
cd mobile
bunx eas-cli build -p android --profile internal
```

### Tabs

- **Hub** — bento layout (live agents, approvals, summary, activity) + sticky QuickInput. Phase 1A is a skeleton with mock data.
- **Chat** — 18 agent rows; per-agent echo round-trip via WebSocket.
- **Bridge** — integrations list from `/api/config`. Toggles + health in Phase 1C.
- **Profile** — device info, sign out, re-pair. Push prefs + biometric idle threshold in Phase 1C.

### What's not in Phase 1A (deferred)

- Voice (`expo-av`) — Phase 1B
- FCM push — Phase 1B (real Firebase project)
- Offline persistence (`expo-sqlite`) — Phase 1B
- Multi-agent pattern threads — Phase 1B
- Share-sheet from Chrome — Phase 1B
- Bridge toggles + system health — Phase 1C
- Profile push categories + biometric idle threshold — Phase 1C
- Animations + haptics polish — Phase 1C
- A11y pass — Phase 1C
- Play Store internal/production track release — Phase 1C
```

- [ ] **Step 2: Top-level `README.md` quick-start update**

Add a one-liner under the existing "Three Integrated Systems" section:

```markdown
### 4. Mobile (mobile/) — Phase 1A

NEURALIS Android app. React Native + Expo. Connects to FastAPI over Tailscale.
See `docs/superpowers/specs/mobile-app-integration/` for the full design + phase docs.
```

- [ ] **Step 3: Run all integration tests one last time**

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns/.worktrees/mobile-phase-0-backend
python -m pytest tests/integration/ -v 2>&1 | tail -5
```

Expected: 61 passed (Phase 0's 59 + Task 0's 2 new from this plan).

```bash
cd mobile
bun test
```

Expected: 6 passed (4 ws + 2 stores).

- [ ] **Step 4: Commit**

```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns/.worktrees/mobile-phase-0-backend
git add CLAUDE.md README.md
git commit -m "docs(mobile): document Phase 1A app in CLAUDE.md + README"
```

- [ ] **Step 5: Tag the milestone**

```bash
git tag -a phase-1a-complete -m "NEURALIS Phase 1A mobile scaffold complete

- Expo SDK 52 + RN 0.76 + NativeWind 4 + TypeScript
- 4 tabs (Hub / Chat / Bridge / Profile) with mockup-matching theme
- Pairing → biometric → tab UI
- WebSocket echo round-trip in Hub QuickInput + per-agent chats
- 18 agents from /api/config
- EAS Build to internal track + APK installed on user's Pixel
- Tag: phase-1a-complete"
```

---

## Definition of Done

The plan is complete when:

- [ ] All 26 tasks committed (Tasks 0–25, with Task 20 as a verification gate / no-op).
- [ ] `python -m pytest tests/integration/ -v` reports ≥ 61 passed.
- [ ] `cd mobile && bun test` reports ≥ 6 passed.
- [ ] `bunx eas-cli build -p android --profile internal` produces an installable APK.
- [ ] APK installed on the user's Pixel.
- [ ] Cold-launch smoke test passes: pair → biometric → Hub → echo round-trip → all 4 tabs reachable.
- [ ] `phase-1a-complete` git tag created locally.

When the above hold, the app exists in your hand. Proceed to **Phase 1B** (write a fresh plan: voice + push + offline + 18 agents wired + multi-agent pattern threads).

---

## Self-Review Notes

This plan implements the spec at `docs/superpowers/specs/mobile-app-integration/02-phase-1a-scaffold-auth-skeleton.md`. Spec-coverage check:

- [x] §3 success criteria — covered by Tasks 1–22 + Task 24 manual smoke
- [x] §4.1 project scaffold — Task 1
- [x] §4.2 theme + fonts — Tasks 2 & 3
- [x] §4.3 Expo Router structure — Task 9
- [x] §4.4 pairing screen — Task 10
- [x] §4.5 biometric gate — Task 11
- [x] §4.6 tab layout — Task 12
- [x] §4.7 Hub tab — Tasks 13 & 14
- [x] §4.8 Chat tab — Tasks 15 & 16
- [x] §4.9 Bridge tab — Task 17 (consumes `/api/config` from Task 0)
- [x] §4.10 Profile tab — Task 18
- [x] §4.11 WebSocket client — Task 5
- [x] §4.12 stores — Task 6
- [x] §4.13 EAS Build profiles — Task 22
- [x] Connection badge — Task 19 (was implicit in §4.6)
- [x] Tests — Tasks 5, 6 unit + Task 23 E2E scaffold
- [x] Backend `/api/config` backport — Task 0
- [x] Manual smoke + APK install — Task 24
- [x] Docs + tag — Task 25

No placeholders. Type/method names are consistent (`WsClient`, `useAuthStore`, `useChatStore`, `useConnectionStore`, `useHubStore`, `getWs`/`setWs`, `api.pair`/`api.config`, `storeCredentials`/`loadCredentials`/`clearCredentials`/`authenticateBiometric`).

Out-of-scope items deferred (correctly) to later phases: voice (Phase 1B), FCM push (1B), offline SQLite queue (1B), multi-agent pattern threads (1B), Bridge toggles + health (1C), Profile push categories (1C), animations + haptics (1C), a11y (1C), Play Store production track (1C).
