# Phase 2 — Shadow-Mode Dogfooding and Soak

**Time**: 2–4 weeks calendar (low active dev work; high observation work).
**Pre-conditions**: Phase 1C DoD complete; APK on user's phone via Play Store internal track; Telegram still active in parallel.
**Goal**: Validate that NEURALIS is *actually* the better daily-driver. Telegram remains in shadow mode emitting and receiving everything, but the user must consciously prefer mobile. Quantify "Telegram fallback" events; close gaps. Exit when fallback rate is near-zero for 14 consecutive days.

This phase has very little new code — it is **observation, instrumentation, and bug-fix triage**.

---

## 1. Goals

1. Track every time the user reaches for Telegram instead of NEURALIS. Categorize each as: "feature gap," "trust gap," "convenience gap," or "bug."
2. Fix all P0/P1 issues that surface; defer P2/P3 to Phase 1.5.
3. Build confidence that pushes are reliable, voice transcripts are accurate, the app does not silently drop messages.
4. Battery, memory, and crash metrics stay within budget.
5. Reach a 14-day streak of zero Telegram-fallback events. **Exit Phase 2** at that point.

## 2. Success criteria (verifiable)

- [ ] `data/mobile_telemetry.db` shows every "Telegram fallback" event over the soak period.
- [ ] Weekly review document `docs/superpowers/specs/mobile-app-integration/_audit/soak-week-N.md` for each week of soak (1, 2, 3, 4) with: fallback events, gaps identified, fixes shipped, decision to extend or proceed.
- [ ] Crash-free session rate ≥ 99.5% measured over a rolling 7-day window.
- [ ] Push delivery latency p95 ≤ 5s (measured from server emit to FCM delivery).
- [ ] No silent message loss: every message sent from app appears in server `messages` table within 60s.
- [ ] Voice transcript word-error-rate ≤ 8% on user's natural speech sample (tested with 50 utterances).
- [ ] Battery: ≤ 8%/h foreground, ≤ 0.5%/h background sustained.
- [ ] **14 consecutive days with `mobile_telemetry.fallback_count == 0`**.

## 3. Out of scope

- New features (gaps that emerge as "missing-by-design" go on a Phase 1.5 backlog).
- Telegram demotion or removal (Phase 3+).
- iOS work.
- Public Play Store production-track release.

---

## 4. Instrumentation

### 4.1 Telegram fallback detection

**Server-side**, in `multi_listener.py` and `command_router.py`: when a *user-initiated command* arrives via Telegram (not just an alert acknowledged), log to `data/mobile_telemetry.db`:

```sql
CREATE TABLE telegram_fallbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                       -- ISO 8601
    intent TEXT NOT NULL,                   -- classified intent or "unknown"
    raw_text TEXT,                          -- redacted of PII via hash if needed
    voice_used INTEGER DEFAULT 0,
    reason TEXT,                            -- "user-typed" | "voice-replied"
    mobile_app_state TEXT                   -- "open"|"backgrounded"|"closed"|"unknown" (queried via /api/devices/last_seen)
);
```

`mobile_app_state` queried at log time:
- "open" → `last_seen_at` within last 30s + connection state == "ready"
- "backgrounded" → `last_seen_at` within last 5min, no active WS
- "closed" → no recent activity
- "unknown" → telemetry data missing

This is the key signal: a fallback while app is "open" is a **feature gap**; a fallback while "closed" is a **convenience gap** (user didn't bother opening the app).

### 4.2 Mobile-side telemetry

`mobile/lib/telemetry.ts`:
- App lifecycle events (cold start, background, foreground, kill)
- Crash reports (via `expo-application` + `expo-error-recovery`)
- Action timing (send-to-reply latency for each agent)
- Push delivery confirmation (FCM message receipt → app records arrival time → delta from server emit)

Posted to `/api/telemetry` daily (or on next online if offline). Server stores in `data/mobile_telemetry.db`.

### 4.3 Push delivery audit

Every notification emit logs `{ts_emit, message_id, category, source, dedup_key}`.
Mobile receipt logs `{ts_received, message_id}`.
Daily cron computes p50/p95/p99 latency and missed-delivery rate. Posted to `data/push_telemetry.db`.

### 4.4 Voice WER measurement

A weekly batch test script `scripts/voice_wer_test.py`:
- Replays 50 stored audio fixtures (recorded by user during normal use, anonymized labels)
- Compares Whisper output to ground-truth transcripts (user labels)
- Computes WER; emits notification "Weekly WER: X.X% — N samples"

User can flag bad transcriptions in-app via long-press → "report bad transcript" — adds to fixture set.

---

## 5. Soak protocol (per week)

Each week of soak, conduct a **review session** documented in `_audit/soak-week-N.md`:

**Day 1 (Monday)** — review last 7 days:
- Pull `telegram_fallbacks` table → categorize each entry
- Check crash report counts and battery/memory metrics
- Run WER test
- Triage any P0/P1 issues (open GitHub issues, assign self)

**Days 2-5** — fix triaged issues; ship updates via EAS Update OTA (no re-build) where possible

**Day 6 (Saturday)** — verify fixes shipped; reset weekly counters

**Day 7 (Sunday)** — weekly summary doc:

```markdown
# Soak Week N — YYYY-MM-DD to YYYY-MM-DD

## Metrics
- Telegram fallback events: N (down from N-1 last week)
- Crash-free sessions: 99.X%
- Push p95 latency: Xs
- WER: X.X%

## Top 3 fallbacks
1. [date] [intent] — [analysis] — [resolution]
2. ...

## Issues fixed this week
- #IDS, with brief description

## Issues deferred to 1.5+
- #IDS, with rationale

## Decision
- [ ] Proceed to Phase 3 (zero-fallback streak ≥14 days)
- [x] Continue Phase 2
```

---

## 6. Failure mode playbook

### 6.1 Push reliability gap

**Symptom**: critical approval push delayed > 30s or missing.

**Triage**:
1. Check `push_telemetry.db` for the specific event — was emit timestamp recorded?
2. Check device's FCM token in `device_tokens.fcm_token` is non-null.
3. Inspect FCM admin console for delivery failure reason.
4. If OEM battery saver suspected — surface a Profile help banner: "Battery saver may delay alerts. Tap here for instructions."

### 6.2 Voice quality gap

**Symptom**: WER > 12%.

**Triage**:
1. Inspect failing samples — accent, background noise, length?
2. Try Whisper model size upgrade (small → medium) on server with cost analysis
3. Add audio preprocessing: noise gate + normalization before Whisper
4. Adjust mic gain in `expo-av` recording config

### 6.3 WS reliability gap

**Symptom**: app connection state flapping ("ready" ↔ "reconnecting") on certain networks.

**Triage**:
1. Check Tailscale logs for DERP relay usage (signals NAT punching failure)
2. Verify heartbeat interval; consider raising to 60s if cellular triggers idle close
3. Check if specific carrier's CGNAT drops idle WS — workaround: keepalive pings via FCM silent push

### 6.4 Feature gap (user wanted X but it's not in app)

Add to `_audit/feature-gaps.md` with priority. Decision rule:
- If used >2× in a week → P1, consider for Phase 1.5 inclusion before Phase 3
- If used 1× → P2, Phase 1.5 backlog
- If duplicated by existing surface (e.g., user used `/budget` because they didn't see Budget chat) → not a gap, fix discoverability (P1)

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Soak drags past 4 weeks | Hard time-box at 6 weeks; if zero-streak not achieved, extend Phase 1.5 budget to fill specific gaps before Phase 3 |
| User sentiment: "I prefer Telegram for X" feels valid → causes fatigue | Reframe each fallback as data; the app's job is to absorb usage, not feel "won" against |
| OTA update breaks on the day of a critical workflow | EAS Update has rollback; major fixes go through full builds with QA; OTA only for non-critical UI tweaks |
| Telemetry overhead degrades app perf | Sample telemetry events at 100% in soak, drop to 10% afterward |
| User's voice samples grow faster than fixture pipeline can absorb | Cap fixture set at 200 most-recent; rotate older out |
| Cyber-creep: more telemetry = more privacy surface | Telemetry stays on user's own server; never leaves Tailnet; explicit `data/mobile_telemetry.db` separate from production DBs |

---

## 8. Files touched

**New**:
- `data/mobile_telemetry.db` — telemetry storage
- `data/push_telemetry.db` — push latency
- `mindgraph_app/telemetry_api.py` — `/api/telemetry`
- `scripts/voice_wer_test.py`
- `scripts/soak_week_summary.py` — generates `_audit/soak-week-N.md` template from telemetry
- `mobile/lib/telemetry.ts`
- `_audit/soak-week-1.md` … `soak-week-N.md` (one per week)
- `_audit/feature-gaps.md`
- `_audit/notifications-parity.md` (verified completion)

**Modified**:
- `multi_listener.py` — log Telegram fallbacks
- `command_router.py` — same
- `mindgraph_app/main.py` — register `telemetry_router`
- `scripts/install_cron.py` — `voice_wer_test` weekly, `soak_week_summary` weekly

---

## 9. Definition of Done (gate to Phase 3)

- [ ] 14 consecutive days with zero `telegram_fallbacks` entries.
- [ ] Crash-free sessions ≥ 99.5% over the last 14 days.
- [ ] Push p95 ≤ 5s, p99 ≤ 30s.
- [ ] WER ≤ 8% over the last 50 samples.
- [ ] Battery + memory budgets met.
- [ ] All P0/P1 bugs resolved; P2/P3 documented in `_audit/feature-gaps.md` for Phase 1.5 backlog.
- [ ] User confirms (in writing in `_audit/decision-to-demote-telegram.md`) intent to proceed with Phase 3.
- [ ] Telegram parity audit (notifications) re-verified — every event emitted in last 14 days reached both surfaces.

When the above hold, the user has lived without falling back to Telegram. Proceed to **Phase 3** to demote Telegram bots.
