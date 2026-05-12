# Phase 3 — Demote Telegram (Alert-Mirror Only)

**Time**: ~1 week active build.
**Pre-conditions**: Phase 2 DoD complete; user has signed off on demotion; 14-day zero-fallback streak documented.
**Goal**: Remove Telegram's role as a *command/intent surface*. The 5 Telegram bots stop processing user-initiated commands. They remain alive as a **passive alert mirror only** — receiving the same events the mobile app gets, as a redundancy backup. This phase validates that no one (including the user) loses a critical workflow when Telegram commands stop working.

This is a **reversible** phase — at any point in Phase 3 we can re-enable Telegram command handling by flipping a single config flag. Phase 4 (deletion) is the irreversible one.

---

## 1. Goals

1. Disable Telegram command/intent processing. User messages to Telegram bots get a polite auto-reply ("NEURALIS app handles commands now — opening your phone…") and a deep link to the relevant agent chat.
2. Telegram bots continue to receive `notification_router` events as alerts (same content as FCM), serving as a redundancy channel.
3. Deep-linking from Telegram alerts works: tapping a notification in Telegram opens NEURALIS at the relevant chat.
4. Cron and scheduled jobs continue to fire; their notifications still reach Telegram.
5. 7 days of operation in this state with zero issues before Phase 4.

## 2. Success criteria (verifiable)

- [ ] User sends `/budget` to Telegram main bot → receives auto-reply, no transaction created.
- [ ] Voice message to Telegram bot → no Whisper invocation server-side, no NLP routing, only auto-reply.
- [ ] Notification fires from `morning_briefing.py` → arrives in *both* Telegram (alert mirror) and NEURALIS FCM (primary).
- [ ] Tapping the Telegram notification opens NEURALIS deep link successfully on the phone.
- [ ] `command_router.py` no longer dispatches intents from Telegram source; logs the request and returns the auto-reply.
- [ ] `data/telegram_command_attempts.db` records every (now-rejected) command attempt for awareness.
- [ ] 7 consecutive days post-demotion with zero "user-attempted-Telegram-command" events from `data/telegram_command_attempts.db` (signals user has fully migrated mentally).
- [ ] All cron-driven notifications still fire to both channels.
- [ ] No regressions in `pytest tests/ -v`.

## 3. Out of scope

- Deletion of Telegram bots, handlers, intents (Phase 4).
- Removal of `multi_listener.py` (Phase 4).
- Removal of `notification_router`'s `TelegramSink` (Phase 4).

---

## 4. Component breakdown

### 4.1 Single config flag

`shared/config/feature_flags.py`:

```python
TELEGRAM_COMMAND_HANDLING = os.environ.get("TELEGRAM_COMMAND_HANDLING", "off").lower() in {"on", "true", "1"}
```

Default: **off** (the moment this phase ships). Flipping to `on` reverts to Phase 2 behavior (full Telegram command processing). This flag is the rollback switch.

### 4.2 `multi_listener.py` changes

When a Telegram message arrives:

```python
async def handle_telegram_message(update):
    if not feature_flags.TELEGRAM_COMMAND_HANDLING:
        # Demote: log, auto-reply, don't dispatch
        record_telegram_command_attempt(update)
        deep_link = infer_deep_link(update.message.text)
        await update.message.reply_text(
            f"📱 NEURALIS app handles commands now.\n"
            f"Opening: {deep_link}\n\n"
            f"(This bot will be retired soon. Pin NEURALIS to your home screen.)"
        )
        return
    # ... existing dispatch logic ...
```

Voice messages: same — log + auto-reply with deep link to Hub global input ("opening voice input").

### 4.3 `infer_deep_link(text)` heuristic

Quick mapping (shared with mobile's NLP classifier):

```python
def infer_deep_link(text: str) -> str:
    intent = nlp_classifier.classify(strip_trailing_punct(text))
    chat_map = {
        "budget.add": "neuralis://chat/budget",
        "budget.summary": "neuralis://chat/budget",
        "tasks.add": "neuralis://chat/tasks",
        "calendar.add": "neuralis://chat/calendar",
        "gmail.summary": "neuralis://chat/gmail",
        # ...
    }
    return chat_map.get(intent, "neuralis://hub")
```

The auto-reply text includes a clickable `neuralis://...` link. Telegram desktop and mobile both can open the link (tested in Phase 1A deep-link infrastructure).

### 4.4 `data/telegram_command_attempts.db`

```sql
CREATE TABLE telegram_command_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    text TEXT,
    voice INTEGER DEFAULT 0,
    inferred_intent TEXT,
    deep_link TEXT,
    user_acknowledged INTEGER DEFAULT 0   -- whether they ever opened the deep link
);
```

`user_acknowledged` populated when mobile receives the deep link click event.

### 4.5 Notification routing during Phase 3

`notification_router` continues fanout to **all three sinks** (FCM, WS, Telegram). No change. This guarantees the redundancy.

Telegram alert messages get a footer: `(via NEURALIS app — open: <deep_link>)` to nudge the user toward mobile when they see an alert in Telegram.

### 4.6 Phase 3 telemetry

`/api/telemetry/phase-3-summary` (CLI command + endpoint):
```bash
python -m jobpulse.runner phase3-status
```
Outputs:
- Last 7 days of `telegram_command_attempts` count
- Last 7 days of `notification_router` event count by sink (assert all three sinks fire equally)
- WS connection uptime % (mobile's primary surface)
- FCM delivery success rate

### 4.7 Documentation update

`CLAUDE.md` Quick Reference section:
- Mark Telegram bot commands as **deprecated** with strikethrough
- Add NEURALIS app as the official command surface
- Note that Telegram bots remain as alert mirror

`README.md` Telegram section: same treatment.

---

## 5. Data + IPC contracts

No new endpoints. Two new tables (`telegram_command_attempts`) and one new env flag.

---

## 6. Test plan

### 6.1 Integration

- `tests/integration/test_telegram_demote.py`:
  - With `TELEGRAM_COMMAND_HANDLING=off`, send a `/budget` message simulation → assert no budget transaction created → assert auto-reply sent → assert row in `telegram_command_attempts`
  - With `TELEGRAM_COMMAND_HANDLING=on`, same input → assert budget transaction is created (rollback works)

- `tests/integration/test_notification_parity_phase3.py`:
  - Emit a fixture notification → assert it lands in FCM, WS, and Telegram

### 6.2 Manual

- User attempts a known Telegram command → verifies auto-reply appears with correct deep link → tapping the link opens NEURALIS at correct chat
- Trigger a real notification (e.g., `python -m jobpulse.runner briefing`) → verify both Telegram and FCM receive it within 5s of each other
- Cron-fired event (papers daily) → verify reaches both channels

### 6.3 Rollback drill

- Set `TELEGRAM_COMMAND_HANDLING=on` mid-phase → send a Telegram command → assert full processing returns
- Set back to `off` → resume demotion

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| User has muscle-memory of Telegram commands; auto-reply is annoying | Auto-reply is one-line + a tap to deep link; not chatty |
| Cron jobs assumed Telegram could process replies (e.g., user pinning a message) | None known in the codebase; integration tests assert no cron path triggers Telegram dispatch |
| Telegram alert mirror noise spams user (parallel to FCM) | Same notification source, so dedup at user perception level — they see the same content; will be removed in Phase 4 |
| Some intent handler depends on Telegram-specific context (e.g., `chat_id`) | Search for `chat_id` and `update.message` references in handlers; ensure HTTP path supplies equivalent `device.name` |
| User loses Telegram + phone simultaneously (rare) | Backend SSH access + CLI `python -m jobpulse.runner` commands remain as ultimate fallback; document in Profile > Help |
| `infer_deep_link` misroutes some intents | Default to `neuralis://hub`; user can navigate from there |

---

## 8. Files touched

**New**:
- `shared/config/feature_flags.py` (or extend existing config)
- `data/telegram_command_attempts.db` (created at first demote run)
- `tests/integration/test_telegram_demote.py`
- `tests/integration/test_notification_parity_phase3.py`

**Modified**:
- `jobpulse/multi_listener.py` — feature flag gate; auto-reply path
- `jobpulse/command_router.py` — same (text-message dispatch path)
- `notification_router` (TelegramSink) — append deep-link footer to message body
- `CLAUDE.md`, `README.md` — deprecation notices
- `scripts/install_cron.py` — no removal yet (just docs)

---

## 9. Definition of Done (gate to Phase 4)

- [ ] All success criteria checked.
- [ ] 7 consecutive days post-demotion with zero `telegram_command_attempts` rows.
- [ ] Notification parity audit shows 100% three-sink delivery for the past 7 days.
- [ ] Rollback drill has been performed at least once and verified working.
- [ ] User signs off in `_audit/decision-to-delete-telegram.md` to proceed.

When the above hold, Telegram has been a *passive mirror* for a full week, and we have evidence that the user is fully migrated. Proceed to **Phase 4** to delete the Telegram code.
