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

### [2026-03-24] GitHub commits showing 0 when commits existed
- **What went wrong**: Morning digest reported "No commits yesterday" even though Yash committed "Rag Architecture added" to Velox_AI on March 23.
- **Root cause**: Used GitHub Events API (`/users/{user}/events`) which strips the `commits` array from older PushEvents, making `payload.commits` return empty. The event existed but appeared to have 0 commits.
- **Fix applied**: Switched to Commits API (`/repos/{user}/{repo}/commits?since=...&until=...`) which returns full commit data. First fetches recently-pushed repos, then queries commits per-repo for the target date.
- **Rule to prevent recurrence**: NEVER use GitHub Events API for commit counting. Always use the Commits API per-repo. Events API is unreliable for payload data on events older than ~1 hour.
