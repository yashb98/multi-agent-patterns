# /fix-pipeline-bugs — Run one session of the pipeline-bugs fix plan

> **LAUNCH WITH `--dangerously-skip-permissions`** — this command edits files,
> runs `git commit`, and runs `python -m jobpulse.runner job-process-url`
> against live ATS pages in dry-run mode. Without that flag every tool call
> blocks on a permission prompt and the session can't run unattended.
>
> ```bash
> claude --dangerously-skip-permissions
> # then in the session:
> /fix-pipeline-bugs
> ```

## What this does

One invocation = **one session** of the 18-session plan that systematically
fixes everything in `docs/audits/pipeline-bugs.md`. The runner:

1. **Detects state** by greping `git log` for `fix(pipeline-bugs-S<n>):`
   commits. Next session = `max(n) + 1`. Starts from S1 if no prior commits.
2. **Loads the protocol** for that session from `docs/audits/pipeline-bugs-runner.md`.
3. **Pulls a live ATS URL** from production DBs (`data/applications.db`,
   `data/form_experience.db`, `data/screening_cache.db`) — only if the
   session needs one.
4. **Implements the fix + a regression test** that fails on the bug *pattern*,
   not just the instance.
5. **Runs a live `dry_run=True` reproducer** against the real URL when applicable
   (browser headed via `chrome-pw`, `JOB_AUTOPILOT_AUTO_SUBMIT=false`,
   `JOBPULSE_FAST_FILL=true`).
6. **Commits** with `fix(pipeline-bugs-S<n>): ...` and **marks the row** in
   `docs/audits/pipeline-bugs.md` with `✅ FIXED <commit-hash>`.

## Protocol

Read and follow `docs/audits/pipeline-bugs-runner.md` exactly. That file is
the durable prompt — this command is the entry point.

After reading it:

1. Run the **state detection** block.
2. Announce the session number + scope to the user (≤ 3 lines).
3. Execute steps 1-7 of the per-session protocol in `pipeline-bugs-runner.md`.
4. End with the **post-session checklist** before declaring done.

## When to stop

A session ends when **any** of these is true:

- ✅ Acceptance criteria met → commit + mark FIXED + report.
- 🛑 Live reproducer requires user input (Telegram captcha, account creation,
  paid ATS interaction) → stop, ask user, do **not** commit partial work.
- 🛑 Fix touches > 2 subsystems → stop, ask user whether to split.
- 🛑 Wider regression sweep introduces > 1 new failure → stop, revert, advisor.

If you're tempted to "just keep going to the next session" — don't. One
session per invocation. The state-detection in the next invocation will pick
up cleanly.

## Reading order on first invocation

1. This file (you are here).
2. `docs/audits/pipeline-bugs-runner.md` — full session protocol + 18-session table.
3. `docs/audits/pipeline-bugs.md` — the bug catalog you are draining.
4. `docs/audits/audit-followup-worklist.md` — per-subsystem source detail.
