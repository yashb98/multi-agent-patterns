# Mistakes Log

Read this FIRST every session. Append on error. Re-check before committing.

---

### [2026-04-04] Explore subagents burned ~190k tokens with Grep/Glob instead of CodeGraph MCP tools
- **Cause**: Spawned 2 Explore agents (53 tool calls, ~190k tokens) to trace code dependencies — work that `find_symbol` + `callers_of` could answer in 2-3 MCP calls (~5k tokens).
- **Fix**: Added CodeGraph-first rule to CLAUDE.md, rules, commands, and skills. Subagent prompts must include "Use MCP tools first."
- **Rule**: ALWAYS use CodeGraph MCP tools (find_symbol, callers_of, callees_of, impact_analysis, semantic_search) BEFORE Grep/Glob for code exploration. Brief subagents to do the same.

### [2026-04-01] Ethnicity regex matched "city" in "ethnicity" → returned location instead
- **Cause**: Pattern `your.*city` in COMMON_ANSWERS matched "your ethni**city**" — the word contains "city".
- **Fix**: Changed to `what.*city.*live|which.*city` — requires full city-in-context phrasing.
- **Rule**: NEVER use substring-matchable words in regex patterns. Test every pattern against ALL other question types (ethnicity, disability, etc.) before deploying.

### [2026-04-01] LinkedIn Easy Apply — specific regex patterns matched by general ones first
- **Cause**: "What is your Right to Work Type?" matched `right to work` (general → "Yes") before `right.*work.*type` (specific → "Graduate Visa"). Python dicts preserve insertion order but patterns were ordered general-first.
- **Fix**: Reordered COMMON_ANSWERS — SPECIFIC multi-word patterns BEFORE general ones.
- **Rule**: In regex pattern dicts, always put specific/longer patterns BEFORE general/shorter ones. Test with the actual questions from the form.

### [2026-04-01] LinkedIn salary field rejected formatted currency string
- **Cause**: Salary field is `type=numeric` on LinkedIn. Value `£27,000-32,000` triggered "Enter a decimal number larger than 0".
- **Fix**: Changed to plain number `30000` (no currency symbol, no commas, no range).
- **Rule**: For numeric ATS fields, always use plain integers. No currency symbols, commas, or ranges.

### [2026-04-01] LinkedIn stuck-page detection false positive on page 3
- **Cause**: Comparing first 200 chars of modal text for stuck detection. All pages start with `"Dialog content start..."` (generic wrapper), so every page matched as "stuck". By page 3, `stuck_count` hit 2 and bailed.
- **Fix**: Compare chars 300-700 of modal text (skips generic wrapper, captures actual question content).
- **Rule**: When comparing page content for stuck detection, skip generic container/wrapper text. Use a meaningful content slice.

### [2026-04-01] LinkedIn guest layout served despite logged-in session
- **Cause**: Direct navigation to `/jobs/view/` URL served guest layout (sign-in wall) even when logged in on `/feed/`. LinkedIn's auth context doesn't propagate to all URL patterns equally.
- **Fix**: Navigate to `/jobs/` first (establishes logged-in jobs context), then navigate to the specific job URL. Also added sign-in overlay dismiss + page reload retry.
- **Rule**: For LinkedIn, always navigate to the section root (`/jobs/`) before specific URLs. Add overlay dismiss and reload fallback for auth edge cases.

### [2026-04-01] LinkedIn Easy Apply badge is an `<a>` tag, not `<button>`
- **Cause**: Newer LinkedIn layout renders the green "Easy Apply" pill as an `<a>` element. All button selectors failed. Initially removed `<a>` selectors thinking the badge wasn't clickable — but it IS the button.
- **Fix**: Added `a:has-text('Easy Apply')` as fallback AFTER all button selectors.
- **Rule**: LinkedIn changes its UI frequently. Always have fallback selectors for different element types (`button`, `a`, `span`). Log all matching elements for diagnosis when primary selectors fail.

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

### [2026-04-03] Jobs scoring 90-94 ATS silently skipped — tier/action routing mismatch
- **Cause**: `determine_match_tier()` returns "auto" at >=90, but `classify_action()` requires >=95 for auto_submit. Routing checked `tier == "auto" AND action in (auto_submit, ...)` — always false for 90-94. Then `tier == "review"` also false (tier is "auto"), so it fell to the else:Skip branch.
- **Fix**: Route by `action` (from `classify_action()`), not by `tier`. Tier is for display/DB only, action is for routing.
- **Rule**: NEVER mix tier and action in routing conditions. Use `classify_action()` for routing decisions, `determine_match_tier()` for display/storage only.
