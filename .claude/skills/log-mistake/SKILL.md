---
name: log-mistake
description: Log a mistake or error to the mistakes log so Claude never repeats it
---

Log this mistake to `.claude/mistakes.md`: $ARGUMENTS

1. Read `.claude/mistakes.md` to check this isn't a duplicate
2. Append a new entry at the top of the entries section with today's date:
   - **What went wrong**: Describe the error or mistake
   - **Root cause**: Why it happened
   - **Fix applied**: What was done to fix it
   - **Rule to prevent recurrence**: A clear, actionable rule for future sessions
3. Confirm the entry was saved
4. If this mistake reveals a pattern (3+ similar entries), suggest a CLAUDE.md rule or hook to enforce it automatically
