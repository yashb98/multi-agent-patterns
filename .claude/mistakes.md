# Mistakes & Errors Log

IMPORTANT: Claude MUST read this file at the start of every session and before making changes.
When Claude makes a mistake or encounters an error, it MUST append an entry here immediately.

Format for each entry:
```
### [YYYY-MM-DD] Short description
- **What went wrong**: ...
- **Root cause**: ...
- **Fix applied**: ...
- **Rule to prevent recurrence**: ...
```

---

<!-- Entries below this line. Most recent first. -->

### [2026-03-28] Budget transactions logged twice — dual-bot race condition
- **What went wrong**: User sent "spent 3 on cookies misc" and it was logged twice in SQLite and synced to Notion twice. Cookies appeared as two separate transactions.
- **Root cause**: Multi-bot listener starts Main Bot with `allowed_intents=None` (handles ALL intents) AND Budget Bot with `allowed_intents=BUDGET_INTENTS`. Both bots poll Telegram independently, both receive the same message within 0-100ms, both classify it as LOG_SPEND, both call `log_transaction()` → two INSERT statements. No dedup guard existed.
- **Fix applied**: (1) Main Bot now excludes intents claimed by dedicated bots — if Budget Bot is running, Main Bot skips budget intents. (2) Added 30-second dedup guard in `add_transaction()` — rejects duplicate (same amount + description + category + date) within 30 seconds. (3) Dedup returns the existing transaction ID instead of inserting.
- **Rule to prevent recurrence**: When multiple bots/handlers can receive the same message, ensure only ONE processes it. The Main Bot must exclude intents that dedicated bots handle. Always add dedup guards on write operations that can be triggered from concurrent handlers. Check `multi_bot_listener.py` start_all_bots() when adding new dedicated bots.

### [2026-03-25] Test suite wiped production knowledge graph on every pytest run
- **What went wrong**: Knowledge graph (mindgraph) showed 0 entities despite auto_extract logging successful extractions. All entities extracted from recruiter emails were gone.
- **Root cause**: `tests/test_mindgraph.py` called `storage.clear_all()` 4 times, operating on the **production** `data/mindgraph.db` instead of a temporary test database. Every `pytest` run deleted all knowledge entities, relations, and processed file records.
- **Fix applied**: Added a `use_temp_db` fixture (autouse) that patches `storage.DB_PATH` to a `tmp_path` SQLite file. Tests now run against isolated temporary databases. Removed all `clear_all()` calls from tests.
- **Rule to prevent recurrence**: Tests must NEVER operate on production databases. Any test that writes to SQLite must patch the DB_PATH to a tmp_path fixture. Before adding `clear_all()` or `DELETE` to any test, verify the DB path is temporary.

### [2026-03-25] Voice commands failed — Whisper adds trailing punctuation
- **What went wrong**: Saying "help" via Telegram voice produced "Help." which didn't match the `^help$` regex pattern. Same issue for all voice commands.
- **Root cause**: OpenAI Whisper transcription adds proper punctuation (periods, exclamation marks, question marks) to transcribed text. The command router's regex patterns used strict anchors (`$`) that don't account for trailing punctuation.
- **Fix applied**: Added `text = re.sub(r"[.!?]+$", "", text).strip()` in `classify()` to strip trailing punctuation before pattern matching. Fixes all voice commands, not just "help".
- **Rule to prevent recurrence**: When adding regex patterns for command matching, always account for voice input adding punctuation. The `classify()` function now strips trailing `.!?` — new patterns don't need to handle this individually.

### [2026-03-25] GitHub commits showing 0 again — pushed_at filter too strict
- **What went wrong**: Agent reported 0 commits for March 24, even though 42 commits existed in multi-agent-patterns repo.
- **Root cause**: Code filtered repos with `pushed_at != yesterday`. But `pushed_at` reflects the *most recent* push. The repo was pushed both on March 24 (42 commits) AND March 25 (10 commits), so `pushed_at` showed `2026-03-25`, which didn't equal `2026-03-24`, causing the repo to be skipped entirely.
- **Fix applied**: Changed filter from `pushed_at != yesterday` to `pushed_at < yesterday`. Any repo pushed on or after the target date could have commits from that date.
- **Rule to prevent recurrence**: The `pushed_at` field on GitHub repos is the LATEST push timestamp, not a list. NEVER use exact date equality (`==`) to filter — use `>=` or `<` comparisons. A repo pushed today may still have commits from yesterday.

### [2026-03-24] Waited for user instead of polling Notion API
- **What went wrong**: Asked user to share Notion page, then sent Telegram asking them to reply "done" — but Telegram daemon is a separate process that can't notify this Claude session. User had to come back here to tell me.
- **Root cause**: Treated Telegram daemon as if it could communicate with this session. They're independent systems.
- **Fix applied**: Should have just polled the Notion API in a loop or with a short delay instead of waiting.
- **Rule to prevent recurrence**: NEVER wait for Telegram replies inside a Claude Code session. The daemon is a separate process. If you need to check if something changed, poll the API directly.

### [2026-03-24] GitHub commits showing 0 when commits existed
- **What went wrong**: Morning digest reported "No commits yesterday" even though Yash committed "Rag Architecture added" to Velox_AI on March 23.
- **Root cause**: Used GitHub Events API (`/users/{user}/events`) which strips the `commits` array from older PushEvents, making `payload.commits` return empty. The event existed but appeared to have 0 commits.
- **Fix applied**: Switched to Commits API (`/repos/{user}/{repo}/commits?since=...&until=...`) which returns full commit data. First fetches recently-pushed repos, then queries commits per-repo for the target date.
- **Rule to prevent recurrence**: NEVER use GitHub Events API for commit counting. Always use the Commits API per-repo. Events API is unreliable for payload data on events older than ~1 hour.

### [2026-03-24] sync_expense_to_notion not defined after budget_agent.py rewrite
- **What went wrong**: User sent "Spent 5.79 on grocery" on Telegram, got error: `name 'sync_expense_to_notion' is not defined`
- **Root cause**: When budget_agent.py was completely rewritten in Phase 1/3, the `sync_expense_to_notion()` and `_get_or_create_weekly_budget_page()` functions were not carried over from the old version. `log_transaction()` called `sync_expense_to_notion()` but it didn't exist.
- **Fix applied**: Added both functions back to the rewritten file.
- **Rule to prevent recurrence**: When rewriting a file completely, grep the old version for all function names called by other modules BEFORE deleting. Verify every function referenced in `log_transaction`, `dispatcher.py`, and `morning_briefing.py` exists in the new version.

### [2026-03-30] arXiv papers fetching failed — HTTP instead of HTTPS + no retry
- **What went wrong**: arXiv digest returned "Could not fetch papers" — 0 papers instead of ~200.
- **Root cause**: Used `http://export.arxiv.org` which triggered a 301 redirect to HTTPS, burning a rate limit slot. The subsequent HTTPS request got 429 rate-limited. No retry logic existed.
- **Fix applied**: (1) Changed to `https://` directly. (2) Added 3-attempt retry with 5/10/15s backoff on 429. (3) Added proper `User-Agent` header per arXiv API policy.
- **Rule to prevent recurrence**: Always use HTTPS for external APIs. Always handle 429 with retry + backoff. Check API documentation for required headers.

### [2026-03-30] All Telegram job commands returned "I didn't recognize" — swarm_dispatcher missing ALL job intents
- **What went wrong**: Every job command (scan jobs, show jobs, apply, reject, etc.) on the Telegram Jobs bot returned "I didn't recognize". The intent was classified correctly but the handler was missing.
- **Root cause**: `swarm_dispatcher.py` (used when `JOBPULSE_SWARM=true`) had ZERO job intents in its `AGENT_MAP`. All 9 job commands fell through to `_handle_unknown`. The regular `dispatcher.py` had the handlers, but it was never called because swarm mode was active.
- **Fix applied**: Added all 9 job intents (scan_jobs, show_jobs, approve_jobs, reject_job, job_stats, search_config, pause_jobs, resume_jobs, job_detail) to both the import list and AGENT_MAP in swarm_dispatcher.py. Also added `scan_jobs` to JOBS_INTENTS in telegram_bots.py.
- **Rule to prevent recurrence**: When adding new intents, update BOTH `dispatcher.py` AND `swarm_dispatcher.py`. The swarm dispatcher must mirror every intent the regular dispatcher handles. Always test with `JOBPULSE_SWARM=true` (the production setting).
