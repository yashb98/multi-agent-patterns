# Semantic-Analysis Audit — BLOCKED on Pre-flight

**Date**: 2026-05-10
**Branch**: `pipeline-correctness-fixes`
**Audit prompt**: `/effort max` semantic-analysis cross-ATS audit (verified deliverable)
**Status**: BLOCKED before substantive work — precondition 1 fails.
**Confidence**: 0%

---

## Pre-flight results

| # | Precondition | Result | Notes |
|---|---|---|---|
| 1 | `git status --short` empty (clean tree) | **FAIL** | 4 modified + 1 untracked (see below) |
| 2 | `docs/audits/live-e2e-2026-05-10.md` shows Confidence 100% | PASS | Verified — closing line `## Confidence: 100%` present |
| 3 | `KimiAI_API_KEY` available | PASS | `python3 -c "...load_dotenv()..."` → `Kimi OK` |
| 4 | `lsof -ti:9222` returns Chrome PIDs | PASS | PID `22611` |
| 5 | BGE-M3 reachable serving 1024-dim | PASS | `/api/embeddings` → `len(v) == 1024` |

The audit prompt is explicit: **"STOP and write docs/audits/<today>-audit-blocked.md if any fail — do not bypass"**. Per that rule, this audit halts here.

---

## Working tree contents (the blocker)

```
 M .claude/skills/audit-semantic-analysis/SKILL.md       # audit infra (skill source)
 M .claude/skills/audit-semantic-analysis/dimensions.md  # audit infra (skill source)
 M docs/audits/url-coverage-matrix.md                    # audit infra (URL matrix the audit consumes)
 M shared/code_intelligence/__init__.py                  # PRODUCTION CODE — Voyage→BGE-M3 swap
?? scripts/reindex_code_intelligence_bge_m3.py            # PRODUCTION SCRIPT — reindex helper for the swap
```

`git diff --stat HEAD` → `4 files changed, 84 insertions(+), 18 deletions(-)`.

### Why this is a blocker (not just paperwork)

1. **`shared/code_intelligence/__init__.py` is production code.** The audit relies on MCP tools (`find_symbol`, `callers_of`, `semantic_search`) for tracing semantic decisions. Those tools query the embeddings table that this file builds. The change swaps `voyage-code-3` → `bge-m3:latest` (both 1024-dim, but **occupying different semantic spaces** — the script header records `cos(voyage_stored, bge_fresh) = 0.018` on a sample doc). If MCP queries are issued before the reindex script runs, neighbours are near-random and every trace step is noisy.

2. **Live-e2e baseline (Confidence 100%) was produced on commit `828e27a`**, *before* these uncommitted production mods. Sub-goal 4 of this audit requires mining that baseline as evidence. Running new applies on top of uncommitted production code makes the new evidence non-comparable to the baseline.

3. **"Audit-supportive" is an inference, not a property of the rule.** The audit constraints forbid edits to `*.py`. Bypassing precondition 1 to begin substantive work means committing to an interpretation that the rule expressly refuses.

---

## How to unblock

Pick one before re-running the audit prompt:

### Option A — Commit the audit-infra and the production swap separately (recommended)

```bash
# Audit infra (skill + URL matrix)
git add .claude/skills/audit-semantic-analysis/SKILL.md \
        .claude/skills/audit-semantic-analysis/dimensions.md \
        docs/audits/url-coverage-matrix.md
git commit -m "chore(audit-infra): skill + URL matrix updates"

# Production swap (Voyage→BGE-M3 in code_intelligence)
git add shared/code_intelligence/__init__.py \
        scripts/reindex_code_intelligence_bge_m3.py
git commit -m "feat(code-intelligence): migrate Voyage Code 3 → BGE-M3 (1024-dim)"
```

Then **run the reindex** before audit, otherwise MCP `semantic_search` returns near-random neighbours:

```bash
python -m scripts.reindex_code_intelligence_bge_m3
# Expected: every doc UPDATEd; in-memory cache reloaded
```

### Option B — One bundled commit

If the user prefers a single commit, the existing branch convention (`828e27a chore(branch-sync): pre-existing modifications + audits + plans`) supports that pattern:

```bash
git add -p   # review each hunk explicitly
git commit -m "chore(branch-sync): voyage→bge-m3 + audit-infra updates"
python -m scripts.reindex_code_intelligence_bge_m3
```

### Option C — Stash (NOT recommended)

`git stash` discards the BGE-M3 migration work-in-progress. Loses the migration progress; the audit then runs on stale Voyage embeddings (cos=0.018 vs current vectors) so MCP-based traces are misleading — substituting one validity problem for another. Reject.

---

## What I did NOT do (per audit constraints)

- Did not edit any `*.py` file.
- Did not commit anything (audit constraints forbid commits, and the spirit of precondition 1 is that the auditor does not unilaterally bundle someone else's WIP into a commit).
- Did not run any `apply_job(url, dry_run=True)` — sub-goal 4 evidence under uncommitted production code would be invalid.
- Did not call MCP `find_symbol` / `callers_of` / `semantic_search` for trace work — same reason (Voyage-era vectors + BGE-M3 query embedder = noise).

---

## End-of-session print

- **Distance % per sub-goal**: SG1 0% / SG2 0% / SG3 0% / SG4 0% / SG5 0% — no touchpoints touched.
- **Touchpoints**: 0 promoted / 0 demoted / 0 left UNVERIFIED — pre-flight blocked all work.
- **Cross-ATS coverage**: 0 of 11 adapters validated.
- **Slices recommended**: 0 — substantive findings would be unsafe under the failed precondition.
- **Confidence**: 0%. **Next-session unblock**: commit the 4 mods + untracked script (Option A or B above), run `scripts.reindex_code_intelligence_bge_m3`, re-verify all 5 preconditions, then re-fire the same audit prompt.

**BLOCKED, see `docs/audits/2026-05-10-audit-blocked.md`**.
