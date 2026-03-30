# Mistakes Log

Read this FIRST every session. Append on error. Re-check before committing.

---

### [2026-03-30] swarm_dispatcher missing ALL job intents → "I didn't recognize"
- **Cause**: `swarm_dispatcher.py` AGENT_MAP had zero job handlers. Regular dispatcher had them but was bypassed by `JOBPULSE_SWARM=true`.
- **Fix**: Added all 9 job intents to swarm_dispatcher imports + AGENT_MAP. Added `scan_jobs` to JOBS_INTENTS.
- **Rule**: New intents → update BOTH `dispatcher.py` AND `swarm_dispatcher.py`. Always test with swarm=true.

### [2026-03-30] arXiv HTTP→HTTPS redirect burned rate limit
- **Cause**: `http://export.arxiv.org` → 301 redirect → wasted quota → 429 on real request. No retry.
- **Fix**: HTTPS directly + 3-attempt retry (5/10/15s) + User-Agent header.
- **Rule**: Always HTTPS. Always handle 429 with backoff. Check API docs for required headers.

### [2026-03-28] Budget logged twice — dual-bot race
- **Cause**: Main + Budget bot both polled same message, both called `log_transaction()`.
- **Fix**: Main bot excludes dedicated bot intents. 30s dedup guard in `add_transaction()`.
- **Rule**: One handler per message. Dedup on concurrent write paths. Check `start_all_bots()`.

### [2026-03-25] Tests wiped production mindgraph DB
- **Cause**: `test_mindgraph.py` called `storage.clear_all()` on production `data/mindgraph.db`.
- **Fix**: `use_temp_db` autouse fixture patches DB_PATH to tmp_path.
- **Rule**: Tests NEVER touch production DBs. Patch DB_PATH to tmp_path.

### [2026-03-25] Voice "help" → "Help." didn't match regex
- **Cause**: Whisper adds punctuation. `^help$` fails on "Help."
- **Fix**: Strip `[.!?]+$` before classification.
- **Rule**: `classify()` strips trailing punctuation. No need per-pattern.

### [2026-03-25] GitHub commits=0 — pushed_at filter wrong
- **Cause**: `pushed_at != yesterday` skipped repos pushed on multiple days.
- **Fix**: Changed to `pushed_at < yesterday` (>=, not ==).
- **Rule**: `pushed_at` = latest push only. Never use `==` for date filtering.

### [2026-03-24] Telegram wait deadlock
- **Cause**: Asked user to reply on Telegram from Claude Code session. Separate processes.
- **Rule**: NEVER wait for Telegram replies in Claude Code. Poll the API directly.

### [2026-03-24] GitHub Events API returns empty commits
- **Cause**: Events API strips commit arrays from older PushEvents.
- **Fix**: Switched to Commits API per-repo.
- **Rule**: NEVER use Events API for commit counting. Use Commits API.

### [2026-03-24] sync_expense_to_notion missing after rewrite
- **Cause**: Full rewrite of budget_agent.py dropped two functions callers depended on.
- **Rule**: Before rewriting a file, grep old version for all function names used by other modules.
