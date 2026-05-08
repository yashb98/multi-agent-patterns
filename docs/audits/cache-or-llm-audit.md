# Cache-or-LLM Audit — Multi-Session Context Prompt

> **Run this in a fresh Claude Code session.** Launched with
> `--dangerously-skip-permissions`. This audit spans multiple sessions; each
> session picks up where the last left off via state-detection grepping the
> commit log for `fix(cache-llm-S<n>):` markers.
>
> **Strict directive from the project owner**: this audit must achieve **100 %
> coverage of every LLM call site** in JobPulse. Every fix must be verified
> with **a real live URL run through Chrome CDP via Playwright** — **no mocks,
> no stale fixtures, no skipped verification**. If you have even 0.1 % doubt
> that a call site is correctly fixed, run the live test again until you are
> certain.

---

## 1. Mission

JobPulse currently runs **5–7 LLM calls per job application** in the apply
flow. Empirical analysis shows **50–70 % of these calls are avoidable**: the
answer already exists in a database, a cache, a deterministic mapping, or a
templated rule. Redundant LLM calls cause:

- 3–10× higher latency per application (visible when the local Ollama model
  is slow or has reasoning overhead)
- 3–5× higher token cost
- Unnecessary retry storms when the LLM returns malformed output
- Hidden architectural drift — the cognitive engine's L0/L1/L2/L3 escalation
  is bypassed by direct LLM calls that should hit the cache layer first

**Goal**: every LLM call site in the codebase is classified as
**NECESSARY** (genuine synthesis/reasoning), **CACHE-REPLACEABLE** (answer
exists in a DB/cache), or **DETERMINISTIC** (answer derivable from a static
map / rule). For the latter two, the call is replaced with the cheaper
path, falling back to LLM only on cache miss.

**Verification protocol** for every fix: a live dry-run against a real ATS
URL via Chrome CDP + Playwright agents, with the log grep'd for the
specific pattern that proves the LLM call did NOT fire when it shouldn't have
fired.

---

## 2. Background context (read before doing anything)

### 2.1 What's already in the codebase

JobPulse already has the infrastructure for cache-or-LLM tiering. **Most of
this audit is wiring it correctly, not building new caches.**

| Infrastructure | File | Purpose | Used correctly today? |
|---|---|---|---|
| Profile DB | `data/profile.db` via `shared/profile_store.py` | All PII (name, email, address, links, screening answers) | Mostly yes; some call sites bypass and call LLM |
| Screening cache | `data/screening_cache.db` | Past Q+A by domain | Tiered (cache → intent → alignment → LLM) but `_align_to_options` re-runs LLM unnecessarily on cache hits |
| Field-label map | `jobpulse/form_engine/field_resolver.py:_FIELD_LABEL_TO_PROFILE_KEY` | Static label → profile key | Used as fallback after LLM, should be primary |
| Learned label mappings | `_persist_label_mapping()` populates `data/form_experience.db` | Per-domain learned (label, value) pairs | Persisted but read path is incomplete |
| Cognitive engine | `shared/cognitive/_engine.py` (L0 Memory Recall) | Returns templated answer with 0 LLM calls when strong template exists | Correctly used in `cognitive/think()` paths but not all callers go through cognitive |
| JD content hash | `jobpulse/content_hasher.py` | Hashes JD for dedup | Used at scan level for dedup; NOT used at CV-tailor cache key level |
| MemoryManager | `shared/memory_layer/_manager.py:get_procedural_entries()` | SQLite-backed procedural strategies | Recently fixed in pipeline-bugs S7 to read SQLite source-of-truth |

### 2.2 What's broken / wasteful (already identified)

These are confirmed waste sites — start here, but be exhaustive in finding more:

1. **CV tailoring runs unconditionally per JD** — `jobpulse/cv_tailor.py:tailor_all_sections()` is called on every job, even when 80%+ of the JD content overlaps with last week's tailored output. No `(role_archetype, company_size, seniority)` cache key.

2. **Cover letter generation has no cache** — `jobpulse/cover_letter_agent.py` regenerates from scratch per company. No template-by-archetype cache.

3. **Field mapping order is backwards** — `jobpulse/form_engine/field_mapper.py:map_fields()` calls LLM first, then validates against `_FIELD_LABEL_TO_PROFILE_KEY`. For 80%+ of fields the static dict has the answer already.

4. **Screening alignment re-runs LLM on cache hits** — `jobpulse/screening_answers.py:_align_to_options()` runs LLM to fit the cached answer into the form's options list, even when the cached answer matches an option directly via fuzzy match.

5. **Page reasoner re-runs per visit** — `jobpulse/page_analysis/page_reasoner.py` is invoked on every page navigation. Some caching by domain exists but is partial.

6. **Skill extractor is correct (use as model)** — `jobpulse/skill_extractor.py` runs deterministic rule-based extraction first, escalates to LLM only on miss. **This is the pattern to copy across the rest of the codebase.**

### 2.3 Sibling issues you'll inevitably encounter

- **Qwen3.6:35b-a3b is a reasoning model** that breaks the pipeline (see `pipeline-bugs.md` S0 if a row was added; otherwise this is unfiled). The `[openai._base_client] Retrying request to /chat/completions` log line during a hang is not a network bug — it's the model returning empty `content` because reasoning consumed the `max_tokens` budget. The recommended fix is to install a non-reasoning model (`qwen3:32b` dense, Apache 2.0) and set `LOCAL_LLM_MODEL=qwen3:32b` in `.env`. This audit assumes that's already done; if not, do it as Step 0.

- **Direct OpenAI SDK usage bypasses Ollama** — most call sites correctly use `shared.agents.get_openai_client()` which returns an Ollama-pointing client when `LLM_PROVIDER=local`. But some files still do `from openai import OpenAI; OpenAI()` directly. Tracked in `.claude/rules/seven-principles.md`. Fix as you encounter them.

### 2.4 Project rules that bind this audit

- **No PII in source code** — all personal data from DBs at runtime
  (`.claude/rules/pii-policy.md`)
- **Dynamic over hardcoded** — runtime-resolved values, no regex for
  classification (`.claude/rules/seven-principles.md` §8)
- **OPRAL loop on every error** — Observe, Plan, Reason, Act, Learn
- **Never modify pre-existing dead code** — surgical changes only
- **Tests NEVER touch `data/*.db`** — use `tmp_path`. Live runs against
  production DBs are OK during verification (they ARE the verification)
- **Live runs use real Chrome CDP + headed Playwright** — `python -m
  jobpulse.runner chrome-pw` first; never headless

---

## 3. State detection (run every invocation)

```bash
# Last completed cache-llm session
LAST_DONE=$(git log --oneline | grep -oE 'fix\(cache-llm-S[0-9]+\)' \
  | grep -oE '[0-9]+' | sort -n | tail -1)
NEXT_SESSION=$((${LAST_DONE:-0} + 1))
echo "Next session: S${NEXT_SESSION}"
```

Each session picks one cluster of related call sites and fixes them
end-to-end with live verification.

---

## 4. Methodology

### 4.1 Catalog every LLM call site (do this once, in S1)

```bash
# Grep for every LLM call site
grep -rln "client\.chat\.completions\.create\|smart_llm_call\|get_llm()\|get_openai_client()" \
  --include="*.py" jobpulse/ shared/ 2>/dev/null \
  | grep -v __pycache__ | grep -v worktrees | sort > /tmp/llm-call-sites.txt
wc -l /tmp/llm-call-sites.txt   # should be ~30-50 files
```

For each file, note:
- The function/method
- The intent (what is the LLM being asked?)
- The input source (parameters, profile DB, JD, form context)
- The output destination (what does the caller do with the response?)
- The classification: NECESSARY / CACHE-REPLACEABLE / DETERMINISTIC

Save the catalog as `docs/audits/cache-llm-catalog.md`. Mark each row with
its classification + the proposed cache key (for replaceable) or the
deterministic source (for static).

### 4.2 Classify each call site

| Class | Definition | Fix shape |
|---|---|---|
| **NECESSARY** | Genuine synthesis or novel reasoning. Cache key would be too sparse to hit in practice. | Leave alone. Document why in a comment. |
| **CACHE-REPLACEABLE** | Same input → same output 80%+ of the time. Cache key is well-defined. | Add cache lookup before LLM call. On miss, run LLM, persist result. |
| **DETERMINISTIC** | Answer is in a DB / static map / rule. LLM is being used because of historical accident. | Replace with DB query / dict lookup / rule. LLM only as fallback when the static path returns no match. |

### 4.3 Per-session fix protocol

Each session targets ONE cluster of call sites (e.g., S1 = field-mapping
cluster, S2 = CV-tailor cluster, S3 = cover-letter cluster, S4 = screening
cluster, S5 = page-reasoner cluster, S6 = cognitive bypasses, etc.).

For each cluster:

1. **Read the catalog row(s)** for this cluster.
2. **Audit the actual call site** via MCP `find_symbol`, `callers_of`,
   `callees_of`. Confirm the input/output contract.
3. **Identify the cache or DB layer** to use. Check it exists (often it
   does; just not wired). If it doesn't exist, build it.
4. **Implement the fix**. Cache lookup before LLM. Persist result on miss.
5. **Write a regression test** that fails on the bug pattern (LLM was
   called when cache should have hit). Use `tmp_path` for DBs.
6. **Verify pre-fix failure via stash drill** — `git stash; pytest
   <new_test>; git stash pop` — confirm the test catches the regression.
7. **Live URL run via Chrome CDP** — see §5.
8. **Mark progress in `docs/audits/cache-llm-catalog.md`** — `🟡 →` change
   to `✅ S<n> <commit-hash>`.
9. **Commit** with prefix `fix(cache-llm-S<n>): <cluster name>`.
10. **Backfill commit hash** in the catalog as a `docs(...)` follow-up,
    matching the pattern `8aa82fc` / `4848230` from the pipeline-bugs audit.

### 4.4 Tools to use

- **MCP `find_symbol`, `callers_of`, `callees_of`, `impact_analysis`,
  `grep_search`** — primary code exploration. Faster + risk-aware compared
  to raw Grep/Glob.
- **MCP `module_summary`** — for understanding entire modules at once.
- **MCP `recent_changes`** — for git context.
- **`advisor()`** — call before substantive work on each session, and
  before declaring done. Especially valuable here because the audit spans
  many subsystems and an outside reviewer catches scope drift.

---

## 5. Live verification protocol

**No fix is considered "done" until it has passed a live URL run.** The
pattern below is required for every cluster.

### 5.1 Pre-flight

```bash
# 1. Confirm Chrome CDP is up
curl -sf -m 2 http://localhost:9222/json/version >/dev/null && echo "CDP OK" || \
  echo "Run: python -m jobpulse.runner chrome-pw"

# 2. Confirm Ollama is reachable AND a non-reasoning model is loaded
curl -s http://localhost:11434/api/tags | python3 -c "
import sys, json
d = json.load(sys.stdin)
non_reasoning = [m['name'] for m in d.get('models', [])
                 if not m['name'].startswith('qwen3.6')]
print('Non-reasoning models available:', non_reasoning)
"
# If empty: ssh to remote, run `ollama pull qwen3:32b`, then continue.

# 3. Confirm OPENAI_API_KEY (used as fallback when Ollama is down)
grep -q "^OPENAI_API_KEY=sk" .env && echo "OPENAI key set" || echo "MISSING openai key"
```

### 5.2 Pull a fresh live URL from production data

```bash
# Direct Greenhouse URL — the most common ATS we test against
sqlite3 data/applications.db "
  SELECT j.url, j.company, j.title, j.found_at
  FROM job_listings j
  WHERE j.url LIKE '%boards.greenhouse.io%'
     OR j.url LIKE '%job-boards.greenhouse.io%'
  ORDER BY j.found_at DESC LIMIT 5"

# If DB is empty for direct ATS URLs, fall back to a known-active board
# (Anthropic and Stripe boards are reliably populated):
curl -sL https://job-boards.greenhouse.io/anthropic | grep -oE 'jobs/[0-9]+' | sort -u | head -3
```

Verify the URL is 200 BEFORE running the full pipeline:

```bash
URL="https://job-boards.greenhouse.io/anthropic/jobs/<id>"
curl -sI -m 10 -L "$URL" | head -3
# Expect HTTP/2 200, not 302 to ?error=true (that's expired)
```

### 5.3 Run the live dry-run

```bash
JOB_AUTOPILOT_AUTO_SUBMIT=false JOBPULSE_FAST_FILL=true \
  python -m jobpulse.runner job-process-url "$URL" 2>&1 \
  | tee /tmp/cache-llm-S<n>-live.log
```

Monitor the log via `Monitor` tool with this filter:

```bash
tail -F /tmp/cache-llm-S<n>-live.log | grep -E --line-buffered \
  "cache hit|cache miss|llm_call|skipping LLM|Rule-based extracted|nav: clicked|verification wall|captcha|FAILED|ERROR|Pipeline complete"
```

### 5.4 Evidence required for every fix

For each cluster's commit, the message must quote the specific log line that
proves the cache hit fired and the LLM call did NOT fire. Example:

```
Live evidence (Anthropic Greenhouse, 2026-05-09T14:22:00):
- Pre-fix log: "[jobpulse.field_mapper] LLM mapped 12 fields"
- Post-fix log: "[jobpulse.field_mapper] dict-mapped 11 fields,
                 LLM mapped 1 field" (the novel one)
- Token reduction: 11/12 calls eliminated for this URL.
```

Without that quoted log evidence, **the fix is not complete**. Per the
project owner's directive, no fix ships until live evidence is captured.

### 5.5 If the live run hits a captcha / SSO

Per `.claude/rules/jobs.md`, the agent's 6-stage bypass pipeline runs first
(15s auto-wait → human simulation → Turnstile click → reload → second
reload → Telegram fallback). If after all 6 stages the wall persists, the
runner protocol's stop condition fires:

> 🛑 Live reproducer requires user input → stop, ask user, do **not**
> commit partial work.

In that case: revert the staged changes (`git stash`), report to the user,
and try a different URL on the next invocation.

---

## 6. Cluster sequencing (proposed S1–S8)

Each is one session per `/cache-llm-audit` invocation. Runner detects
state and picks up next.

| Session | Cluster | Files touched | Cache layer | Live verification |
|---|---|---|---|---|
| **S1** | Catalog every LLM call site | (audit-only, no code changes) | `docs/audits/cache-llm-catalog.md` | None — but sibling hash check that the catalog covers every grep hit |
| **S2** | Field-mapping (DETERMINISTIC) | `field_mapper.py`, `field_resolver.py` | `_FIELD_LABEL_TO_PROFILE_KEY` static map + `_persist_label_mapping()` learned map | Live URL: form gets filled with the same fields, log shows `dict-mapped` for ≥80 % of fields |
| **S3** | Screening alignment (CACHE-REPLACEABLE) | `screening_answers.py:_align_to_options`, `screening_pipeline.py` | `screening_cache.db` + fuzzy option match | Live URL with screening Qs: log shows `cache hit, skipping LLM alignment` |
| **S4** | CV tailoring (CACHE-REPLACEABLE) | `cv_tailor.py`, `content_hasher.py` | New `(role_archetype, jd_hash, profile_version)` cache table | Live URL: second run on same JD pulls cached bullets, no LLM call |
| **S5** | Cover letter (CACHE-REPLACEABLE) | `cover_letter_agent.py` | New `(company, role_archetype, profile_version)` cache | Live URL: first run generates + caches, second run pulls from cache |
| **S6** | Page reasoner (CACHE-REPLACEABLE) | `page_analysis/page_reasoner.py` | Per-`(domain, page_signature)` cache | Live URL: navigation across pages doesn't re-call LLM if signature unchanged |
| **S7** | Cognitive bypasses | files that call LLM directly bypassing `cognitive/think()` | Migrate to cognitive engine entry point | Live URL: all-cluster log shows L0 Memory Recall hits before any LLM call |
| **S8** | Final reconciliation + sweep | All previous | Verify catalog has every row marked ✅ | End-to-end full pipeline run on Greenhouse + Workday URL with **0 cache misses on the second run** |

---

## 7. Stop conditions

A session ends when **any** is true:

- ✅ Acceptance criteria met → live evidence captured → commit + mark FIXED
- 🛑 Live reproducer requires user input (captcha, SSO, account creation) →
  stop, ask user, do not commit partial work
- 🛑 Fix touches > 2 subsystems → stop, ask user whether to split
- 🛑 Wider regression sweep introduces > 1 new failure → stop, revert,
  advisor
- 🛑 Cache layer doesn't exist and building it would touch a separate
  subsystem (e.g. need to add a new SQLite table that's used elsewhere) →
  scope-split, ask user

**Do not commit partial work.** Per project owner's directive: 100 %
certainty per session, or stop and ask.

---

## 8. Reading order on first invocation

1. **This file** (`docs/audits/cache-llm-audit.md`) — you are here.
2. `docs/audits/pipeline-bugs.md` — context on prior 18-session audit; the
   cache-llm audit is structured as a sibling.
3. `docs/audits/pipeline-bugs-runner.md` — the protocol pattern that this
   audit mirrors.
4. `.claude/rules/seven-principles.md` — coding rules every fix must satisfy.
5. `.claude/rules/jobs.md` — live-run rules (browser headed, captcha
   fallback, etc.).
6. `CLAUDE.md` — project conventions.
7. `shared/agents.py` — the central LLM factory; understand
   `get_openai_client()` and `is_local_llm()` before classifying calls.
8. `shared/cognitive/_engine.py` — the existing reasoning escalation
   layer; reading this lets you understand the L0/L1/L2/L3 model that
   call sites should route through.
9. `jobpulse/skill_extractor.py` — the **gold-standard pattern** for
   "deterministic first, LLM on miss". Every fix should match this shape.

---

## 9. First commands the new session should run

```bash
# 1. State detection
LAST_DONE=$(git log --oneline | grep -oE 'fix\(cache-llm-S[0-9]+\)' \
  | grep -oE '[0-9]+' | sort -n | tail -1)
NEXT_SESSION=$((${LAST_DONE:-0} + 1))
echo "Next session: S${NEXT_SESSION}"

# 2. Pre-flight check
curl -sf -m 2 http://localhost:9222/json/version >/dev/null && echo "CDP OK" \
  || echo "Need: python -m jobpulse.runner chrome-pw"
curl -sf -m 2 http://localhost:11434/api/tags >/dev/null && echo "Ollama OK" \
  || echo "Ollama unreachable"

# 3. Read this file end-to-end before doing anything
cat docs/audits/cache-llm-audit.md

# 4. Read the pipeline-bugs runner so you understand the protocol shape
cat docs/audits/pipeline-bugs-runner.md
```

Then announce to the user: "Starting cache-llm-audit S<n>: <cluster name>",
read the cluster row from §6 above, and execute the per-session protocol
from §4.3.

---

## 10. What "100 % certainty" means in this audit

The project owner's standard: **if there is even 0.1 % doubt that the fix
works correctly in production, run another live URL test until that doubt
is resolved.** Multiple URLs from different ATS platforms (Greenhouse,
Workday, Lever, Ashby) per session if needed. Quote every log line. Compare
pre-fix and post-fix tokens-used metrics. Don't rely on mocked tests as
sufficient evidence — they catch regression patterns; only live URL runs
prove the fix works in the conditions that matter.

If a live run is flaky (the same URL produces different log output across
runs), that's itself a finding worth investigating before declaring the
fix complete. Flakiness usually means the cache key isn't deterministic
enough or the order of operations is wrong.

---

## 11. Out of scope

- The `pipeline-bugs.md` 18-session audit is sibling, not parent. Do not
  reopen `pipeline-bugs-S1`–`S18` rows in this audit.
- Do not migrate from Ollama to a different runtime. The user has chosen
  Ollama for local-first. The audit assumes Ollama is reachable at
  `http://localhost:11434` (via SSH tunnel from a remote host).
- Do not delete `form_engine/engine.py` — that's tracked in
  `pipeline-bugs.md` S10/S10b/S10c/S10d.
- Do not change the cognitive engine's L0/L1/L2/L3 escalation thresholds.
  This audit makes call sites route through cognitive correctly; it
  doesn't redefine cognitive itself.

---

## 12. End-state — when this audit is complete

- `docs/audits/cache-llm-catalog.md` has every LLM call site marked ✅
  with the commit that closed it.
- `docs/audits/cache-llm-completion-report.md` exists with:
  - Total call sites audited (count)
  - Distribution: NECESSARY / CACHE-REPLACEABLE / DETERMINISTIC
  - Pre-audit vs post-audit avg LLM calls per apply (measured on the same
    Greenhouse + Workday URLs)
  - Pre-audit vs post-audit avg latency per apply (same)
  - Live evidence summary linking each session's commit to its log line
- The same live URL run twice in a row produces **0 LLM calls on the
  second run** for the deterministic + cache-replaceable clusters. Only
  NECESSARY clusters (cover letter on a brand-new company, novel
  screening question) will fire LLM on the second run.
