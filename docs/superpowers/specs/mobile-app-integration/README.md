# NEURALIS Mobile — Integration Spec Suite

**Status**: Design — pending implementation
**Created**: 2026-05-04
**Owner**: Yash
**Project name**: NEURALIS (mobile app), `multi_agent_patterns` (codebase)

## Mission

Replace the 5-bot Telegram interface with a single **Android-native mobile app** that fronts the entire `multi_agent_patterns` codebase: jobpulse autopilot, all 6 LangGraph orchestration patterns, mindgraph CodeGraph, cognitive engine, memory layer, optimization, fact checker, papers/arXiv, GitHub, Gmail, calendar, budget, tasks. iOS follows in a separate phase after Android validates.

## Locked decisions (verbatim)

| Decision | Choice | Rationale |
|---|---|---|
| Product framing | **X** — personal cockpit, not multi-user platform | Validate value before paying platform tax |
| Phase 1 scope | **Hub + Chat (full conversational)** | Replaces 5 Telegram bots wholesale |
| Surface | **Android-first**, iOS follows | Validate on one OS before doubling effort |
| Tech stack | **React Native + Expo + NativeWind** | Mockups are Tailwind; classes port near-1:1; cheap iOS port later |
| Backend reach | **Tailscale (A2)** | Private mesh, works globally, invitee demos via Tailnet invite |
| Auth | **β — per-device tokens with QR pairing** | Revocable, audit trail; mobile is sole control surface |
| Streaming | **WebSocket** | Bidirectional, supports voice + cancel + multiplex |
| Push | **A — full Telegram parity via `notification_router`** | Mobile becomes only channel; cannot lose alerts |
| Offline | **B — cached read + queued safe writes** | Subway/flight use cases; risky writes refused |
| Migration | **Shadow mode**, then phased Telegram deletion | Reversibility; no flag-day risk |

## Visual language (locked, from user-provided HTML mockups)

- **Primary**: Mint `#006c52` / `#98ffd9` — secondary peach `#fed9b8`
- **Background**: `#f6faf8` (light) / `#181c1c` on-bg
- **Glassmorphism**: `backdrop-blur-xl` panels with inset highlights
- **Type**: Space Grotesk (headlines, all-caps labels, 0.05–0.1em tracking) + Manrope (body)
- **Iconography**: Material Symbols Outlined (variable axis fill on active)
- **Layout**: bento grids, fully rounded pills (`rounded-full` for navs, `rounded-xl` for cards), ambient shadows, neon glows on active states
- **Brand wordmark**: `NEURALIS` — uppercase Space Grotesk, 0.1em tracking

## Information Architecture (4 bottom tabs)

1. **Hub** — Neural Inbox (live agent cards, approvals, today summary, activity) + sticky global text/voice quick-input
2. **Chat** — per-agent conversational surfaces (18 chats — see `00-design-overview.md` §2) + multi-agent pattern threads
3. **Bridge** — integrations status + agent enable/disable + system health
4. **Profile** — identity, settings, biometric, paired devices, push categories, export

## Phases (read in order)

| # | Phase | File | Time |
|---|---|---|---|
| 0 | Backend prerequisites | [`01-phase-0-backend-prereqs.md`](./01-phase-0-backend-prereqs.md) | ~1.5 weeks |
| 1A | App scaffold + auth + tab skeletons | [`02-phase-1a-scaffold-auth-skeleton.md`](./02-phase-1a-scaffold-auth-skeleton.md) | ~2.5 weeks |
| 1B | Voice + push + offline + 18 agents wired | [`03-phase-1b-voice-push-offline-agents.md`](./03-phase-1b-voice-push-offline-agents.md) | ~3 weeks |
| 1C | Bridge + Profile + polish + ship internal | [`04-phase-1c-bridge-profile-polish.md`](./04-phase-1c-bridge-profile-polish.md) | ~1.5 weeks |
| 2 | Shadow-mode dogfooding + soak | [`05-phase-2-dogfood-soak.md`](./05-phase-2-dogfood-soak.md) | 2–4 weeks calendar (low active) |
| 3 | Demote Telegram (alert-mirror only) | [`06-phase-3-demote-telegram.md`](./06-phase-3-demote-telegram.md) | ~1 week |
| 4 | Delete Telegram bots | [`07-phase-4-delete-telegram.md`](./07-phase-4-delete-telegram.md) | ~3 days |

**Total active build**: ~8 weeks. **Calendar to Telegram-deleted**: ~12–14 weeks.

> **About "Phase 1.5"** — referenced informally in several phase docs as the *implicit backlog* of deferred-but-known items (home-screen widgets, on-device Whisper, third-party integrations like Slack/Discord, advanced agent surfaces for `shared/adversarial`/`shared/governance`). It is **not** a numbered phase in this plan. Items tagged "Phase 1.5+" go into `_audit/feature-gaps.md` during Phase 2 and become a follow-up project after Phase 4 completes.


The cross-cutting design context lives in [`00-design-overview.md`](./00-design-overview.md) — read it before any phase doc.

## Repo layout

```
multi_agent_patterns/                  (this repo)
├── jobpulse/                          (existing)
├── mindgraph_app/                     (existing — backend gets WS endpoint)
├── shared/                            (existing — gets notification_router)
├── mobile/                            (NEW — Expo app, this spec)
│   ├── app/                           (Expo Router screens)
│   ├── components/
│   ├── lib/                           (WS client, offline queue, auth, push)
│   ├── stores/                        (Zustand)
│   ├── theme/                         (NativeWind config matching mockup tokens)
│   └── tests/
└── docs/superpowers/specs/mobile-app-integration/  (this folder)
```

## Phase 0 prerequisites (must be true before any code is written)

- [ ] Mac running daemon with `caffeinate -d` in launchd plist (Mac never sleeps while plugged in)
- [ ] Tailscale installed on Mac, signed in, MagicDNS enabled
- [ ] Tailscale installed on phone, joined Tailnet
- [ ] FastAPI reachable from phone at `http://<mac-magic-dns-name>:8000/health`
- [ ] Apple Developer / Google Play Console account (Play needed by Phase 1C)
- [ ] Node 20+, Bun or pnpm, Expo CLI installed locally

## Acceptance for "spec complete"

- [ ] Each phase doc lists explicit Definition-of-Done gating into next phase
- [ ] Every backend touchpoint has a contract (route, payload schema, response schema)
- [ ] Every UI screen has a content + behavior spec, not just a layout sketch
- [ ] Every dynamic-over-hardcoded principle in `.claude/rules/seven-principles.md` is honored
- [ ] No PII in spec docs (per `.claude/rules/pii-policy.md`)
- [ ] All offline / error / empty / loading states are described per screen

## Out of scope (Phase 1)

- iOS app (Phase 2+ separate effort)
- Public marketplace / public network ("Y-platform" — deferred indefinitely)
- Multi-user authentication (β tokens are per-device, all on a single Tailnet identity)
- Web companion (mobile is the only frontend)
- Wear OS / Android Auto / widgets (Phase 1.5+)
- LLM-on-device (all inference stays server-side)
- Real-time multi-user collaboration (single-user app)
- E2E encryption beyond Tailscale's WireGuard (defense-in-depth via β tokens already)

## Risks tracked across the suite

| Risk | Phase introduced | Mitigation |
|---|---|---|
| Mac sleep kills connectivity | 0 | `caffeinate -d` + launchd KeepAlive; phone shows "host unreachable" with last-seen timestamp |
| WebSocket flaky on cellular carriers | 1A | Reconnect with backoff + `Last-Event-ID`-style resume; SSE fallback prototype reserved |
| Whisper voice latency on long audio | 1B | Hard-cap recording at 60s; show partial transcripts via streaming Whisper |
| Push notification permission denied | 1B | Detect at first launch; degrade gracefully (in-app only) with "Enable in Settings" CTA |
| Telegram-only intent handlers missed in Phase 0 audit | 0 | Coverage test: list every Telegram intent name; assert each has an HTTP route |
| User reaches for Telegram instead of mobile during shadow mode | 2 | Telegram bots log a "fallback used" event; review weekly to find gaps |
| Token leak from device | β auth | Per-device revocation via CLI; biometric on every cold start; 5-min idle re-lock |
| Battery drain from always-connected WS | 1A/2 | Foreground-only WS; background uses FCM only; reconnect on app foreground |
| FCM delivery delay during high-priority approval | 1B | Approval pushes use `priority=high` channel; WS-if-connected delivers first |

## Authority

This spec set is the source of truth for the NEURALIS mobile build. Implementation plans (forthcoming via `writing-plans` skill) reference these docs by file. Changes to scope require updating the relevant phase doc *before* implementation diverges.
