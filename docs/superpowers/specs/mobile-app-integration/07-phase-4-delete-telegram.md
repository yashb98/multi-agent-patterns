# Phase 4 — Delete Telegram

**Time**: ~3 days active build.
**Pre-conditions**: Phase 3 DoD complete; user has signed off; 7-day passive-mirror operation has been clean.
**Goal**: Permanently remove the Telegram bot infrastructure from the codebase. The mobile app is the sole user-facing surface. `notification_router` no longer fans out to Telegram; the `TelegramSink`, `multi_listener`, and all bot tokens / handlers are removed. Documentation is updated everywhere.

This phase is **irreversible** — committing it deletes code paths. Restoration requires `git revert`. The decision to enter Phase 4 is therefore explicit and documented.

---

## 1. Goals

1. Remove `TelegramSink` from `notification_router`.
2. Delete `jobpulse/multi_listener.py` (the 5-bot daemon entry point).
3. Delete unused Telegram bot handlers, intents, and command parsing helpers.
4. Remove Telegram cron jobs (`telegram-poll.yml` GitHub Actions, any cron entries).
5. Delete unused dependencies (`python-telegram-bot` if exclusively used here).
6. Update CLAUDE.md, AGENTS.md, README.md, ARCHITECTURE.md to reflect post-Telegram reality.
7. Verify zero broken imports, zero dead routes, zero references to Telegram in `data/*.db` schemas (other than archival).
8. Tag a release `v1.0-mobile-only`.

## 2. Success criteria (verifiable)

- [ ] `grep -r "telegram" --include="*.py" jobpulse/ shared/ mindgraph_app/ | grep -v "_archive\|test_archive"` returns zero results in functional code paths (only allowed: archival/docs comments and an explicit `data/telegram_command_attempts.db` archive).
- [ ] `python -m pytest tests/ -v` passes 100%.
- [ ] `python -m jobpulse.runner multi-bot` errors with "Telegram bots have been retired — use the NEURALIS mobile app." exit code 0 (graceful) — actually we delete the command entirely; the runner help text no longer mentions it.
- [ ] `notification_router` still emits to FCM and WS; calling it does not attempt Telegram delivery.
- [ ] Daemon (launchd) entry no longer references `multi-bot`; instead launches just the FastAPI server + cron + Playwright sessions.
- [ ] `requirements.txt` no longer pins `python-telegram-bot` (or any other Telegram-only dep).
- [ ] CLAUDE.md, README.md, ARCHITECTURE.md, AGENTS.md all updated; no stale mentions of "5 Telegram bots" or `/budget`-style commands.
- [ ] Git tag `v1.0-mobile-only` pushed.

## 3. Out of scope

- The mobile app codebase itself (no changes — already complete from Phases 1A-1C).
- Any new feature beyond cleanup.
- Restoration of any deleted code (use `git revert` if ever needed; committed history preserves it).

---

## 4. Component breakdown

### 4.1 Files to delete (full removal)

```
jobpulse/multi_listener.py
jobpulse/dispatcher.py                # if it's exclusively the Telegram-routing dispatcher; verify
jobpulse/swarm_dispatcher.py          # same — verify mobile uses /api/intents not the dispatcher
jobpulse/command_router.py            # if Telegram-only; mobile NLP uses nlp_classifier directly
shared/telegram_client.py
shared/notifications/sinks/telegram.py
.github/workflows/telegram-poll.yml
```

**Important verification before delete**: `dispatcher.py` and `swarm_dispatcher.py` may be used by both Telegram *and* HTTP. If so, only the Telegram-specific surfaces get removed; the dispatcher classes remain.

Do `callers_of` MCP query for each file before deletion. Mobile's `/api/intents/` should reach the same handler logic via `handler_registry.get_handler_map()`, not via `dispatcher.py`.

### 4.2 Files to modify (Telegram-paths excised)

- `notification_router.NotificationRouter.__init__` — drop `TelegramSink`
- `mindgraph_app/main.py` — no Telegram-specific imports
- `jobpulse/runner.py` — remove `multi-bot` subcommand; keep `webhook`, `briefing`, `export`, etc.
- `requirements.txt` — drop `python-telegram-bot` and any Telegram-only utilities
- `scripts/install_cron.py` — drop Telegram-poll backup workflow comments; remove if any cron only existed for Telegram
- `com.jobpulse.brain.json` (launchd plist) — `ProgramArguments` no longer includes `multi-bot`
- `tests/jobpulse/test_*` — remove tests that exclusively cover Telegram surfaces; convert any HTTP-shared test to plain HTTP tests

### 4.3 Documentation updates

| File | Change |
|---|---|
| `CLAUDE.md` | Remove "5 Telegram bots" stat; remove Telegram commands section; add NEURALIS app section; update Quick Reference |
| `README.md` | Replace "Remote Control via Telegram" section with "Remote Control via NEURALIS Mobile" |
| `docs/ARCHITECTURE.md` | Update component diagrams (remove Telegram block from infrastructure picture) |
| `AGENTS.md` | Remove Telegram from agent surfaces inventory |
| `.claude/rules/jobpulse.md` | Remove "One handler per message. Main bot MUST exclude dedicated bot intents" section |
| `.claude/rules/jobs.md` | Update notification rules — references mobile FCM not Telegram |
| `docs/superpowers/specs/mobile-app-integration/README.md` | Mark Phase 4 complete; update "Status" header |

### 4.4 Stats refresh

`scripts/update_stats.py` regenerates the `~161,000 LOC | 763 Python files | 49 databases | 4162 tests | 4 dashboards | 5 Telegram bots | 3 platforms` line. After Phase 4: "5 Telegram bots" disappears; LOC count drops by deleted code.

### 4.5 Archive of `data/telegram_*.db`

Don't delete `data/telegram_command_attempts.db` and similar telemetry — they're historical record. Move to `data/_archive/` directory with a `README` noting Phase 4 retirement date.

### 4.6 Final verification command

```bash
# Run before commit:
git ls-files | xargs grep -l "telegram" 2>/dev/null \
  | grep -v "^docs/superpowers/specs/" \
  | grep -v "^data/_archive/" \
  | grep -v "^.git/"
```

Expected output: empty (or only docs files that explicitly note "Telegram retired in Phase 4").

---

## 5. Test plan

### 5.1 Pre-deletion safety

- Run `python -m pytest tests/ -v` — note baseline pass count
- Run `git diff --stat HEAD~1 HEAD` — confirm no changes in flight from other branches
- Take a full backup: `python -m jobpulse.runner export` (saves a `.zip` to `exports/`)
- Confirm last `data/_backups/` snapshot is recent

### 5.2 Post-deletion

- Run `python -m pytest tests/ -v` — must equal baseline pass count minus the deleted Telegram-specific tests
- Run `python scripts/update_stats.py` — verify stats line updates
- Restart daemon: `launchctl unload ... && launchctl load ...` — verify no errors, FastAPI starts cleanly
- Manual: trigger a `morning_briefing` → verify it lands in NEURALIS FCM only, no errors about missing Telegram client

### 5.3 Smoke test on mobile

- App launches, all flows work
- Receive a real notification (e.g., daily papers) — arrives via FCM only
- Run a pattern → completes
- Approve a dry run → succeeds

### 5.4 Code health

- `ruff check .` clean
- `mypy` clean (existing baseline)
- No new MCP `dead_code_report` findings related to deletion (would indicate incomplete removal)

---

## 6. Rollback procedure (last-resort)

If Phase 4 commit causes a critical regression:

1. `git revert <phase-4-merge-commit>` — restores all deleted files
2. Set `TELEGRAM_COMMAND_HANDLING=on` (re-activates command processing)
3. Restart daemon
4. Confirm Telegram bots come back online
5. Open issue documenting the regression
6. Re-attempt Phase 4 only after fix

This procedure is **for emergencies only**. It is documented but not expected to be invoked. Mobile app should be fully validated by the time we reach this phase.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hidden import or runtime call still references Telegram | Pre-deletion `callers_of` MCP audit on every targeted file |
| `dispatcher.py` deletion breaks mobile path | Verify mobile uses `/api/intents/` → `handler_registry` directly, not via `dispatcher.py` |
| `python-telegram-bot` dep removal cascades to other deps | `pip-deptree` audit before removal; reinstall and test if needed |
| Stats line auto-updates incorrectly post-deletion | Manual verification of `scripts/update_stats.py` output before commit |
| User has unread Telegram messages with important context | Pre-archive: export Telegram chat history once before deletion (out-of-band; user does this manually via Telegram Desktop) |
| Rollback procedure tested only theoretically | Run rollback drill on a branch before Phase 4 merges to main |
| Some test in `tests/jobpulse/test_dispatch.py` etc. tests both paths | Refactor those tests to test only HTTP path; delete Telegram-only assertions |

---

## 8. Files touched (summary)

**Deleted** (~10 files, ~3000 LOC):
- `jobpulse/multi_listener.py`
- `jobpulse/dispatcher.py` (if Telegram-only)
- `jobpulse/swarm_dispatcher.py` (if Telegram-only)
- `jobpulse/command_router.py` (if Telegram-only)
- `shared/telegram_client.py`
- `shared/notifications/sinks/telegram.py`
- `.github/workflows/telegram-poll.yml`
- Various Telegram-specific tests in `tests/jobpulse/`

**Modified**:
- `notification_router` — drop sink
- `mindgraph_app/main.py` — clean imports
- `jobpulse/runner.py` — drop `multi-bot`
- `requirements.txt` — drop deps
- `scripts/install_cron.py` — drop entries
- `com.jobpulse.brain.json` — drop launch arg
- `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/ARCHITECTURE.md`
- `.claude/rules/jobpulse.md`, `.claude/rules/jobs.md`
- `docs/superpowers/specs/mobile-app-integration/README.md` — "Status" → "Implemented"

**Archived**:
- `data/telegram_command_attempts.db` → `data/_archive/`

**Tagged**:
- Git tag `v1.0-mobile-only`

---

## 9. Definition of Done (project complete)

- [ ] All success criteria checked.
- [ ] All tests pass; lints pass; mypy passes.
- [ ] Daemon restarts cleanly.
- [ ] Mobile app on user's phone receives notifications correctly post-deployment.
- [ ] All documentation updated; `grep -r "telegram"` returns only archive + this spec doc.
- [ ] `v1.0-mobile-only` tag pushed.
- [ ] Final celebratory entry in `_audit/phase-4-complete.md` documenting:
  - Total time from Phase 0 start → Phase 4 complete
  - Total LOC added (mobile + backend) vs deleted (Telegram)
  - Net reduction in user-facing surfaces (5 Telegram bots → 1 mobile app)
  - User retrospective on the migration

When all of the above hold, the project is complete. The codebase has a single user-facing interface — NEURALIS mobile — and the multi-agent system is more cohesive, more polished, and (importantly) less duplicated than when the project began.
