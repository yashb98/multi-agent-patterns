# Phase 1C — Bridge, Profile, Polish, and Ship Internal

**Time**: ~1.5 weeks active build.
**Pre-conditions**: Phase 1B DoD complete; all 18 chats real; voice + push + offline + multi-agent threads working.
**Goal**: Round out the IA — Bridge tab functional with toggles, Profile tab feature-complete (paired devices, push categories, biometric prefs, export), accessibility pass, animation polish, error-state coverage, search, deep links robust, and an installable Play Store internal-track release.

This is the *finish* phase — after this, the app is shippable and Phase 2 (dogfooding) begins.

---

## 1. Goals

By the end of Phase 1C:

1. Bridge tab: integrations show real status from `/api/config`; agents can be enabled/disabled (turning off Budget hides the chat row + suppresses notifications); system health card reflects daemon, cron, rate limits, last error.
2. Profile tab: paired-devices manager (list, revoke), push categories with per-category mute toggles, biometric preferences (always vs idle threshold), export button (downloads a `.zip` of user data via `/api/export`), about/version info.
3. Global search: top-bar search icon opens a search overlay; queries `/api/search` and returns mixed-type results (messages, files, agents) per the mockup's Global Search screen.
4. Comprehensive error/empty/loading states for every screen; every API failure renders a structured user-facing message (per error-handling rules), never a stack trace.
5. Animations + haptics polished: smooth gradient transitions, neon pulses on active agents, micro-haptics on key actions.
6. Accessibility: VoiceOver/TalkBack labels on every interactive element; minimum 44×44pt tap targets; color-blind-safe (no info conveyed by color alone).
7. APK shipped to Play Store internal track; user has it installed on phone.

## 2. Success criteria (verifiable)

- [ ] In Bridge, toggling Budget agent off → the Budget chat row in Chat tab disappears + budget notifications stop arriving via FCM.
- [ ] In Profile > Paired Devices, revoking a test device immediately invalidates its token (test device gets `auth.fail` on next WS message and is force-logged-out).
- [ ] In Profile > Push Categories, muting "digest" stops paper digests from FCM but keeps in-app delivery via WS.
- [ ] In Profile > Biometric, changing the idle threshold from 5min to 1min causes `locked.tsx` to show after 1min of background.
- [ ] Profile > Export downloads `neuralis-export-<date>.zip` containing message history + paired devices + settings. No PII redaction needed (it's user's own data).
- [ ] Global search "TechCorp" returns messages mentioning TechCorp, the job's preview file, and the Job Bot chat — all in a unified result grid.
- [ ] Killing the FastAPI server while app is open → app shows "Daemon offline" banner with `last_seen` timestamp; reconnect banner clears within 5s of restart.
- [ ] TalkBack screen-reader walk: Hub → first agent card announces "Job Bot, processing, 65 percent" with tappable hint.
- [ ] EAS Submit successful: Play Console shows internal-track build with version code 1; user receives Play Store install link.
- [ ] Smoke test on a fresh emulator install via Play Store internal link → first-launch onboarding → pair → Hub.

## 3. Out of scope

- Public Play Store release (still internal-track; production track happens after Phase 2 validates).
- iOS port (separate phase, post-validation).
- Home-screen widgets (Phase 1.5+).
- E2E encryption or sensitive data export controls beyond Tailscale (Phase 2 risk eval).
- Telegram removal (Phase 3+).
- Multi-account / role-switching (single user, β scope only).

---

## 4. Component breakdown

### 4.1 Bridge tab — full

`app/(tabs)/bridge.tsx`:

Sections (each a glass-panel rounded-xl):

1. **System health**
   - Daemon: status pill ("Running 4d 12h" / "Down — last seen 03:14"), tap → details
   - Mac caffeinate: presence check via `/api/health` (returns whether `caffeinate` PID is in process tree)
   - Cron jobs: count of registered + last-run timestamps (pulls from `/api/health/cron`)
   - Rate limits: per-platform daily caps remaining (LinkedIn 12/15, Greenhouse 5/7, etc.)
   - Last 3 errors with timestamp + source

2. **Integrations**
   - Card per integration: Notion, Drive, Gmail, GitHub, Telegram, Tailscale-self
   - Status pill (Active/Inactive/Error)
   - Tap → details modal (last sync, scope, configure link)
   - Revoke/disconnect button (server-side: `/api/integrations/<name>/revoke`)
   - Add new integration: opens a search/list view (Slack, Discord, etc. — Phase 1.5+ implementations; Phase 1C UI only stubs them)

3. **Agents**
   - Agent toggle list: each of 18 agents with on/off switch
   - Group by category (Operations: Budget/Tasks/Calendar/Gmail; Knowledge: Papers/GitHub/Memory/Fact Check; Patterns: Hierarchical/Peer Debate/...; Code: CodeGraph/Cognitive; Job: Job Bot)
   - Toggling off:
     - Hides the chat row in Chat tab
     - Suppresses FCM events from that agent (mobile filters using categories registered with FCM channel + push category map)
     - Server stores per-device agent enable/disable in `device_settings` table
     - Cron triggers still fire; agent still runs server-side (you can still see its activity in Hub stream); only mobile attention is muted

`/api/devices/<id>/agents` (POST) updates per-device settings.

### 4.2 Profile tab — full

`app/(tabs)/profile.tsx`:

Sections:

1. **Identity**
   - Avatar (initial in mint glow circle), display name (editable, stored device-side only)
   - Subtitle "Single user · Tailnet member"
   - Edit button

2. **Stats**
   - Bento 2×2: connected agents count, days since pairing, messages sent (lifetime), pushes received (lifetime)

3. **Active modules** (mockup Neural Modules) — read-only list of agent toggles for quick glance + "Manage in Bridge" link

4. **Paired devices**
   - List of all devices from `/api/auth/devices` (this device first, marked "This device")
   - Each: name, scope, last_seen, paired_at, "Revoke" button
   - "Pair another device" button → pairing flow (you on Mac generate code, share to other phone)
   - For demo invitee scope: separate section "Demo guests" with countdown to auto-revoke (24h default)

5. **Notifications**
   - Master toggle "All notifications"
   - Per-category: Approvals (cannot disable), Alerts, Activity, Digest — each with toggle + "preview" button (sends a test push)
   - Quiet hours: time range picker; suppresses non-approval categories during window
   - Sound + vibration per category (channel settings)

6. **Security**
   - Biometric required: toggle (default on) + idle threshold dropdown (1/5/15min, "On every cold start only")
   - "Change device PIN" link (deep link to Android security settings)

7. **Data**
   - Export: downloads `neuralis-export-<YYYY-MM-DD>.zip` from `/api/export`
   - Clear cache: clears mobile SQLite cache (history, queue) — does not affect server
   - Sign out: clears Keystore, returns to pairing
   - Delete this device: revokes token + clears Keystore

8. **About**
   - Version, build number, server URL (last 8 chars), Tailnet status
   - Open source notices, privacy notes
   - Send logs (uploads anonymized recent logs to a Mac-side endpoint `/api/debug/logs` for triage)

### 4.3 Global search

Top app bar search icon → modal overlay with focused input + recent searches list.

`POST /api/search` body `{q, types?: ["msg","file","agent","approval"]}` returns:
```json
{
  "results": [
    {"type":"message", "id":"...", "channel":"agent:jobs", "snippet":"...integration with TechCorp...", "ts":"...", "highlights":[]},
    {"type":"file", "id":"...", "name":"TechCorp_JD.pdf", "size":12345, "url":"/api/files/<sha>"},
    {"type":"agent", "id":"jobs", "name":"Job Bot", "icon":"work"},
    {"type":"approval", "id":"...", "company":"TechCorp", "role":"Senior Engineer"}
  ]
}
```

Server search:
- Messages: existing memory-layer FTS + recent chat history (SQLite FTS5 over `messages` table)
- Files: filename match (CV/CL exports + uploaded fixtures)
- Agents: name fuzzy match
- Approvals: company + role fuzzy match
- Results ranked by recency × type-weight × score

Mobile renders per the mockup's Global Search bento grid.

### 4.4 Empty / error / loading states

Every screen specs all four states:

| Screen | Loading | Empty | Error | Offline |
|---|---|---|---|---|
| Hub | Skeleton bento with shimmer | "No activity today. Tap Hub-FAB to run a pattern." | "Backend unreachable" + Retry | "Cached state — last seen <ts>" |
| Chat list | Skeleton rows | (never empty — always 18 agents) | Same as Hub | Same as Hub |
| Chat per-agent | Skeleton | "No messages yet. Say hi." | Inline message error + retry | "Offline — your messages are queued" |
| Bridge | Skeleton cards | "No integrations yet — add one" | Banner + Retry | "Cached — toggles disabled" |
| Profile | Skeleton | (never empty) | Banner | Cached + toggles disabled |

`components/states/ErrorBanner.tsx`, `EmptyState.tsx`, `LoadingSkeleton.tsx`, `OfflineBanner.tsx` — primitives.

Backend errors render the `errorCategory` + `message` per the error-handling spec — never raw stack traces.

### 4.5 Animation + haptics polish

- `react-native-reanimated` 3 worklets for:
  - Hub agent card pulse (high-risk = faster pulse)
  - Approval card slide-out on action
  - Chat message arrival (slide-up + fade)
  - Tab switch (color transition + scale on active tab)
  - Voice waveform bars (live amplitude → bar height)
- `expo-haptics`:
  - Light tap: send message, tab switch
  - Medium tap: approve action
  - Heavy tap: reject action, biometric fail
  - Success notification haptic on successful approve

Performance budget: 60fps minimum on Pixel 7+; verify with `--enable-fabric` and `react-native-flipper-performance`.

### 4.6 Accessibility

- All interactive components have `accessibilityRole`, `accessibilityLabel`, `accessibilityHint`.
- Color-encoded state (e.g., risk pulse) also has text label or icon.
- Min tap target 44×44pt enforced via lint rule.
- Dynamic Type: respect system font scale; clamp at 1.4× to prevent layout breakage.
- Reduced motion: `useReducedMotion` from Reanimated disables animations + replaces with instant transitions.
- High-contrast: glass-panel backgrounds get an opaque fallback bg-surface when system high-contrast is on.

### 4.7 Deep links robustness

`mobile/lib/deep-link.ts`:
- Parses: `neuralis://hub`, `neuralis://chat/<agent>`, `neuralis://chat/<agent>?msg_id=<id>`, `neuralis://pattern/<run_id>`, `neuralis://approval/<id>`, `neuralis://settings/<section>`
- Unknown links → toast "Unknown link" + Hub
- Cold-launch deep link: app initializes auth/biometric, then routes to deep target (not Hub)
- Background-state deep link: no biometric re-prompt unless idle threshold passed

### 4.8 EAS Submit + Play Console

- `eas.json` `submit.production` configured with `serviceAccountKeyPath` (CI-friendly)
- Play Console: Internal testing track set up, user's email on the testers list
- Listing minimum: app name (NEURALIS), short desc, full desc (private use, non-commercial), privacy policy URL (one-page hosted on `yashbishnoi.io/neuralis-privacy`), screenshots (3 × phone mockup-style), feature graphic
- App signing: Play App Signing enabled (Google holds the upload key)
- `eas submit --profile production --platform android` triggers internal track release

---

## 5. Data + IPC contracts

### 5.1 `/api/devices/<id>/agents` (POST)

```json
{ "agent": "budget", "enabled": false }
```
Returns the updated full settings object.

### 5.2 `/api/auth/devices` (GET)

```json
{
  "devices": [
    {"id":1, "name":"Yash-Pixel-9", "scope":"full", "paired_at":"…", "last_seen_at":"…", "this_device": true},
    {"id":2, "name":"Recruiter-Demo-Mar5", "scope":"demo", "paired_at":"…", "auto_revoke_at":"…", "this_device": false}
  ]
}
```

### 5.3 `/api/export` (GET)

Returns a `.zip` stream containing:
- `messages.jsonl` — message history per channel
- `devices.json` — paired devices snapshot
- `settings.json` — per-device settings + push prefs
- `integrations.json` — connected integrations list (no secrets)
- `metadata.json` — export timestamp, NEURALIS version, server version

### 5.4 `/api/search` (POST)

(See §4.3)

### 5.5 `/api/health` and `/api/health/cron`

Existing endpoints; ensure they include `caffeinate_alive`, `cron_count`, `last_errors`.

### 5.6 `device_settings` table

```sql
CREATE TABLE device_settings (
    device_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_id, key),
    FOREIGN KEY (device_id) REFERENCES device_tokens(id) ON DELETE CASCADE
);
```

Keys: `agents.<name>.enabled`, `push.<category>.muted`, `quiet_hours.start`, `quiet_hours.end`, `biometric.idle_threshold_min`, etc.

---

## 6. Test plan

### 6.1 Unit + integration

- `test_export.py` — zip contents structure
- `test_search.py` — mixed-type result ranking + FTS query
- `test_device_settings.py` — toggle persistence + per-device isolation
- `test_revoke_devices.py` — revoking force-disconnects WS + invalidates token
- Mobile: `test_bridge_toggles.tsx` — Zustand state + API call on toggle, optimistic UI
- Mobile: `test_profile_export.tsx` — download via Expo FileSystem
- Mobile: `test_a11y.tsx` — `accessibilityLabel` present on every interactive node

### 6.2 E2E (Maestro)

- Bridge: toggle Budget off → assert Budget chat hidden → toggle on → restored
- Profile: revoke a test pair → log into test device, assert force-logout
- Push category mute: mute Digest → trigger test paper push → assert no FCM (still in WS)
- Search: typing "TechCorp" returns mixed result types; tap on agent result → opens chat
- Cold launch deep link: open `neuralis://chat/jobs?msg_id=<id>` from a different app → unlock → land on that message

### 6.3 Accessibility audit

- `accessibility-test-android` automated scan — zero violations target
- Manual TalkBack walk through each tab; no orphan elements; logical reading order
- Contrast: primary on background ≥ 4.5:1; verify with Stark plugin

### 6.4 Performance audit

- React DevTools profiler: no component re-renders >16ms on tab switch
- Memory: `mat-android` heap dump after 30min — no retained chat instances
- Network: charles-proxy capture of 1h session — request count baseline for regression

### 6.5 Manual sign-off

- 24h dogfood as primary device
- One demo session with friend on demo-scope token; revoke after; verify clean experience
- Battery: confirm `<8%/h foreground, <0.5%/h background` from Phase 1B holds

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Play Store internal track delays from Google review (1-3 days first time) | Plan ahead; build `.apk` for sideload as backup |
| App size grows past 50MB (Play size warning) | Audit deps; lazy-load chart libs; verify Hermes enabled |
| Per-device push muting easy to misconfigure | "Reset notification settings" button in Profile |
| Export zip leaks secrets if integrations include tokens | Server filters secrets before serialization; integration test asserts no `tok_*` strings in export |
| Reanimated worklets fail on certain Android OEMs | Reduced-motion fallback exists; gate animations on `useReducedMotion` |
| TalkBack on glass panels reads garbled because of overlapping text+blur | Force opaque background when TalkBack active (`AccessibilityInfo.isAccessibilityEnabled`) |
| EAS production build fails because of native module mismatch | Pin Expo SDK; lock all `expo-*` versions; CI runs prebuild check |
| Demo tokens left active forever | Auto-revoke at 24h via cron `auto_revoke_demos`; visible countdown in Profile |

---

## 8. Files touched

**New** (mobile/):
- `app/search.tsx` (overlay route)
- `components/states/{ErrorBanner,EmptyState,LoadingSkeleton,OfflineBanner}.tsx`
- `components/bridge/{HealthCard,IntegrationCard,AgentToggleRow,IntegrationDetail}.tsx`
- `components/profile/{IdentityCard,StatsBento,DevicesList,DeviceRow,PushCategoriesPanel,SecurityPanel,DataPanel,AboutPanel,ExportButton}.tsx`
- `components/search/{SearchOverlay,ResultRow}.tsx`
- `lib/export.ts`, `lib/search.ts`, `lib/a11y.ts`
- `tests/{unit,integration,e2e}/...` extensions for above

**New** (backend):
- `mindgraph_app/devices_api.py` — `/api/auth/devices`, `/api/devices/<id>/agents`
- `mindgraph_app/export_api.py` — `/api/export`
- `mindgraph_app/search_api.py` — `/api/search`
- `cron auto_revoke_demos.py` — script + cron entry
- Schema migration for `device_settings`
- Tests

**Modified**:
- `mindgraph_app/main.py` — register new routers
- `app.config.ts` — version bump, deep link scheme finalized
- `eas.json` — `production` submit profile
- `app/(tabs)/bridge.tsx`, `profile.tsx` — full implementations

---

## 9. Definition of Done (gate to Phase 2)

- [ ] All success criteria checked.
- [ ] Internal track APK installable from Play Store on user's primary phone (not sideloaded).
- [ ] All four screens have all four states verified visually.
- [ ] Accessibility audit: zero violations from automated tooling; manual TalkBack walk through complete.
- [ ] No P0/P1 bugs in tracker.
- [ ] All Phase 0/1A/1B/1C tests pass; existing project test suite (`pytest tests/ -v`) green.
- [ ] User has used the app for 48 continuous hours without falling back to Telegram (start of "soak countdown").
- [ ] Telegram fanout still active (shadow continues).
- [ ] Spec doc `docs/superpowers/specs/mobile-app-integration/` reflects any in-flight changes (no drift).
- [ ] `CLAUDE.md` updated: mobile app section, new endpoints, updated Telegram references with "to be deprecated" notes.

When the above hold, the build is shipped. Phase 2 (dogfooding) starts the moment the user installs the production-track APK on their phone.
