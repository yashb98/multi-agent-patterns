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
