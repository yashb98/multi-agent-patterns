# Form-Fill Followups + Cache-LLM Audit Backlog — Post Screening-Pipeline Hardening

**Branch (existing work to preserve):** `pipeline-correctness-fixes`
**Latest related commits:** `14e2422`, `f4bad9c`, `10dbc41` (screening-pipeline shape correctness — already landed)
**LLM provider:** Kimi (Moonshot) — `OPENAI_BASE_URL=https://api.moonshot.ai/v1`, `OPENAI_API_KEY=$KimiAI_API_KEY`, models `kimi-k2.6` (reasoning) / `moonshot-v1-auto` (content). OpenAI key is exhausted.
**Related docs:**
- `docs/audits/cache-llm-completion-report.md` — closed audit (S1–S8), §7 = deferred backlog this plan absorbs.
- `docs/audits/cache-llm-catalog.md` — 70-row call-site catalog.

---

## NON-NEGOTIABLE EXECUTION RULES

These apply to **every** item in this plan. No exceptions.

1. **100% verified via brutal scrutiny — no stale tests, no mocks.** Each item must be proven on a **live test run** against a real URL, real DB, real LLM. Unit tests with mocked dependencies do NOT count as verification. If you can't reproduce the bug live, you can't claim the fix.
2. **If you are not 100% sure, do it again.** Re-run the live test as many times as needed to reach 100% confidence. A single passing run on a flaky path is not enough — repeat until the result is deterministic. State your confidence percent in your final report; if you'd write anything below 100%, the item is not done.
3. **Verify the actual fill on the page, not just the log line.** Logs can lie (truncated values, intermediate states, retries). Read back the live DOM after fill — `await page.input_value(selector)` or `await page.locator(...).text_content()` — and compare against the intended value. The fill is correct only when the page state matches.
4. **Acceptance criteria are gates, not suggestions.** Every item lists acceptance criteria. All of them must pass before the item is marked done. If any criterion is unverifiable in your environment, surface that explicitly — don't silently skip.
5. **No working around root causes.** If a fix patches over a deeper bug, the deeper bug becomes a follow-up item, not an excuse to declare done. Per OPRAL: every error must make the system smarter; if the same class of error can recur, the fix is incomplete.
6. **Live URL of record:** `https://job-boards.greenhouse.io/anthropic/jobs/4017331008` (Anthropic Greenhouse). Every form-fill claim must be re-verified against this URL.

---

## How to use this plan

Paste the section you want to work on into a fresh Claude Code session as the prompt. Each section is self-contained: it states the bug or task, gives the live evidence (where applicable), points at the code, names the fix shape, and lists acceptance criteria. The agent picking it up should be able to execute without re-deriving context.

When in doubt, the live regression URL is the one above. Use `python3 /tmp/cache-llm-S2-apply.py` (or rewrite with proper `job_context` — the script currently hardcodes `company: Ohme`) to reproduce.

The items are independent and can be done in any order. Three clusters:
- **Items 1–5 + Bonus**: form-fill bugs and screening-tier hardening (this session's spillover).
- **Items 6–13 + Setup-tooling**: cache-LLM audit deferred work (§7 of the completion report).
- **Items 14–15**: cross-cutting reliability — DB retrieval observability, firecrawl evaluation.

---

## Item 1: Greenhouse country-picker hijacks "Yes/No" combobox fills

**Severity:** user-visible bug. Visa-sponsorship field ends up with `"Norway"` typed into it instead of `"No"`.

**Live evidence** (4th run, `/tmp/apply.log` line 2668):
```
fill ✓ 'Will you now or will you in the future require employment vi' = 'Norway' [tech=combobox_type_to_search, expected='Norway']
```
Final `agent_mapping` for the same field shows `"No"` — so the screening pipeline correctly resolved "No", but at fill time the value typed into the React Select autocomplete matched **`Norway`** in an adjacent country picker (intl-tel-input, `class="iti__country"`) and that's what got selected.

Earlier in the run, the same DOM hijack triggered the `ax_options` UnboundLocalError on Hispanic/Latino (see Item 2):
```
react_select_click_option failed for 'Are you Hispanic/Latino?': Locator.click: Timeout 30000ms exceeded.
Call log:
  - waiting for locator(".select__option, [role='option']").filter(has_text="No").first
    - locator resolved to <li ... id="iti-0__item-lb" ... class="iti__country" data-country-code="lb">…</li>
```

That `iti__country` locator is the **phone-number country picker** — it's matching `[role='option']` globally instead of within the visa-sponsorship combobox subtree.

**Root cause:** `combobox_type_to_search` / `react_select_click_option` look up `[role='option']` on the page, not scoped to the field's own listbox container. When two React Selects are mounted simultaneously (visa dropdown + phone country dropdown), the wrong listbox wins.

**Fix shape:**
1. In `jobpulse/native_form_filler.py` find `react_select_click_option` and `combobox_type_to_search`.
2. Before clicking the option, derive the field's own listbox id from the combobox input's `aria-controls` / `aria-owns` attribute.
3. Scope the option locator to that listbox: `page.locator(f'#{listbox_id} [role="option"]')` instead of the global `.select__option, [role='option']` selector.
4. If `aria-controls` is missing, fall back to the closest ancestor with `data-react-select` or `[role="listbox"]`.

**Files (probable):**
- `jobpulse/native_form_filler.py` — methods named `_react_select_click_option`, `_combobox_type_to_search`, `_click_custom_dropdown_option`.
- `jobpulse/playwright_driver.py` — if option click goes through `driver.click_option` etc.

**Acceptance:**
- Re-run apply on the Anthropic URL. `fill ✓ 'Will you now... visa...' = 'No'` (NOT `Norway`) in the log.
- Hispanic/Latino fill no longer triggers the `iti-0__item-lb` locator capture.
- Existing tests in `tests/jobpulse/test_combobox_option_scan.py` still pass.

---

## Item 2: `UnboundLocalError: ax_options` on field-fill exception path

**Severity:** Python bug — surfaces as an opaque `Field fill failed` warning that hides the real error.

**Live evidence** (4th run, `/tmp/apply.log` after the Hispanic/Latino click timeout):
```
Field fill failed for 'Are you Hispanic/Latino?': cannot access local variable 'ax_options' where it is not associated with a value
```

**Root cause:** in one of the fill methods, `ax_options` is declared inside a try/branch that doesn't always execute, but a later branch (likely an `except` or fallback path) reads it unconditionally.

**Fix shape:**
1. `grep -nE "ax_options" jobpulse/native_form_filler.py` to find the variable.
2. Identify the path where it's read without being assigned.
3. Initialise `ax_options = []` (or `None`) at the top of the method, before the try/except.

**Acceptance:**
- The "cannot access local variable 'ax_options'" warning never appears in apply logs.
- When the inner click fails, the surrounding code logs the actual fill failure (timeout / wrong locator) instead of the secondary Python error.
- Add a regression unit test that simulates the failure path and asserts no `UnboundLocalError`.

---

## Item 3: CV/CL upload reliability — `set_input_files` not verifying

**Severity:** Silent data-loss risk. The CV may not actually attach, and the CL is never uploaded today.

**Live evidence** (4th run, line 2728):
```
upload_pdf: set_input_files returned without files attached for Yash_Bishnoi_Unknown_Company.pdf (files.length=-1) — page widget may have rejected or swapped the input; downstream nav may not advance
```

`files.length=-1` means the JS readback couldn't find the input it just wrote to (the form swapped or removed it). The orchestrator logs a warning and continues — there is no retry, no fallback, and no human signal.

**Cover letter status:** the test apply script at `/tmp/cache-llm-S2-apply.py` calls `apply_job(cv_path=CV_PATH, dry_run=True, ...)` with **no** `cl_generator` — so cover letter generation is skipped entirely. In production, scan_pipeline → applicator passes `cl_generator` only when the JD analysis flagged a cover-letter field, but that signal isn't reliably set on Greenhouse forms (which use a generic "Attach" label twice — one for resume, one for cover letter, no semantic distinction).

User memory: `[Cover letter in additional attachments](feedback_cover_letter_attachment.md) — Upload cover letter to "Additional attachments" field when available.`

**Two sub-fixes:**

### 3a. Verify upload landed
1. In `jobpulse/form_engine/file_uploader.py:upload_pdf`, after `set_input_files`, read `input.files[0].name` via `page.evaluate`.
2. If `files.length` is 0 or -1, retry once via the closest visible `[role="button"]` "Attach" trigger + `set_input_files` on the freshly-revealed input.
3. If still failing after one retry, raise a structured `FileUploadError` instead of returning a warning. Caller can either (a) Telegram-alert the human, (b) skip the application with a clear failure reason.

### 3b. Cover letter path discovery
1. When the form has **two** file inputs whose labels are both "Attach" (typical Greenhouse pattern), the FIRST is resume / CV, the SECOND is cover letter.
2. Add detection in `field_scanner.py` (or wherever file inputs get scanned): if multiple `(label='Attach', type='file')` inputs exist, tag the second one as `purpose='cover_letter'` so the form-fill loop knows to upload `cl_path` to it.
3. Disambiguate by reading the closest ancestor heading / section title — Greenhouse usually has a `<h3>Resume/CV</h3>` and `<h3>Cover Letter</h3>` above each input.
4. Make sure `apply_job()` callers always pass a `cl_generator` (lazy CL builder) when the form has a cover-letter slot. Currently optional → make it a hard requirement when slot is detected.

**Acceptance:**
- On the Anthropic Greenhouse URL: CV uploads to the first Attach, CL uploads to the second Attach (or "Additional attachments" field if present).
- `files.length=-1` warning treated as a failure, not a success.
- Live verify: refresh page after fill → both filenames visible in the form's file list, not just "Yash_Bishnoi_Unknown_Company.pdf" once.
- Add `tests/jobpulse/test_file_upload_two_attach.py` simulating the two-Attach Greenhouse layout.

**Note:** the test script bug (CV named `Unknown_Company.pdf` because `job_context` was hardcoded for Ohme) is a separate issue — fix `/tmp/cache-llm-S2-apply.py` to derive `company` from URL/JD before calling `apply_job`.

---

## Item 4: Migrate `COMMON_ANSWERS` regex map → dynamic answers

**Severity:** Rules violation + active bug source. The hardcoded regex emits answers that don't fit current form options.

**Live evidence** — from 2026-05-09 audit:
- `screening_answers.py:154` `r"willing.*relocate|open.*relocation|relocate.*within|relocate.*to" → "Yes, within the UK"` was the actual root cause of the relocation-fill bug. Form options were `["Yes", "No"]` — `"Yes, within the UK"` doesn't fit. We worked around it with `_align_screening_to_options` (commit `10dbc41`), but the underlying regex remains.
- `screening_answers.py:228` `r"disability|...": "No"` returns `"No"` for "Disability Status" — but the EEO dropdown options are 3-item, none of them just `"No"`. Same workaround applied.
- `screening_answers.py:229` `r"veteran|military": "No"` — same pattern, same problem.

**Project rule violated:** `.claude/rules/jobpulse.md` — "No regex for classification (MANDATORY): Regex MUST NOT be used for screening question classification." Same rule appears in `.claude/rules/seven-principles.md` Principle 8.

**Fix shape:**
1. `screening_answers.COMMON_ANSWERS` (lines ~124-240) is a regex→answer dict. Each entry is a semantic intent ("relocation", "disability declaration", "criminal record", etc.).
2. Build an embedding-prototype set: for each intent, write 3-5 example questions and the canonical answer. Store as YAML in `data/screening_intents.yaml`.
3. At runtime, `try_instant_answer` becomes: embed the question, find best-matching intent (cosine ≥ 0.75), return the canonical answer.
4. Canonical answers themselves can stay coarse (e.g. `"yes_to_relocation"`) — the option-aligner already maps them onto current form options.
5. Migrate one intent group at a time; keep regex as fallback during migration; flip the order (embeddings first, regex fallback) once parity is verified.
6. Delete the regex dict entirely once all intents are migrated.

**Files:**
- `jobpulse/screening_answers.py` — `COMMON_ANSWERS` (delete), `try_instant_answer` (rewrite), `_resolve_placeholder` (keep — still needed for `JOB_LOCATION` / `SKILL_EXPERIENCE` placeholders).
- `data/screening_intents.yaml` — new
- `jobpulse/screening_intent.py` — already has IntentClassifier with 612 prototypes; extend with the new intent set or merge.

**Acceptance:**
- `try_instant_answer` no longer references `re.search` or `COMMON_ANSWERS`.
- Lint test `tests/lint/test_no_regex_classification.py` (create if missing) flags any `re.search` in screening files.
- All tests in `tests/jobpulse/test_screening_*.py` still pass.
- Live re-run on Anthropic URL: relocation question gets `"yes_to_relocation"` → option-aligner maps to `"Yes"` (matches user preference per `user_relocation.md` memory). Today (post-Item-3 workaround) it gets `"No"` because the LLM has no relocation-default in its prompt.

---

## Item 5: Wrong-shape values in `user_profile.db screening_defaults`

**Severity:** Secondary leak source. Even after Item 4, profile-stored answers can be wrong-shape.

**Live evidence:**
```
sqlite> SELECT * FROM user_profile.db screening_defaults;
('relocation', 'Yes, within the UK')          ← wrong shape for Yes/No options
('commuting', 'Yes, willing to commute to any UK office')   ← wrong shape
```

The other 23 entries look fine.

**Fix shape:** these are user-supplied defaults stored from prior corrections. Two options:
1. **Cleanup-only:** rewrite the wrong-shape rows to short canonical values (`"Yes"` / `"No"`) — one-time SQL update.
2. **Read-side normalisation:** wherever `screening_defaults` is read, route through `OptionAligner.align_answer` against the live field options (which we already do in commit `10dbc41`'s `_align_screening_to_options`). If the stored value can't align, log + skip.

Option 2 is the structural fix; do option 1 first to stop the bleeding.

**Files:**
- `data/user_profile.db` — table `screening_defaults`
- `shared/profile_store.py` — `screening_default()` accessor; ensure callers always pass field options.

**Acceptance:**
- `screening_defaults` rows for `relocation` / `commuting` are short canonical values.
- Live re-run: no `"screening answer 'Yes, within the UK' did not align"` warnings in the log (the workaround is no longer needed because the source is clean).
- Audit: grep for any other DB or JSON file storing user defaults that may have similar wrong-shape values.

---

---

# Cluster 2: Cache-LLM audit backlog (§7 of `cache-llm-completion-report.md`)

These are the deferred items left after audit sessions S1–S8 closed. None blocks the pipeline; each is a measured-saving cache add or a cognitive-routing migration. Pattern to follow for caches: see commit `4509f6d` (S4 — `tailored_cv_cache`) or `7aba244` (S5 — `cover_letter_cache`) for the canonical key + TTL + `JOBPULSE_TEST_MODE` guard shape.

---

## Item 6: Cache `_generate_jd_aware_bullets` (portfolio variants)

**Source:** §7 S2-EXT (catalog row 17).
**File:** `jobpulse/portfolio_variants.py:195`

**Why:** Generates JD-aware bullet rewrites for portfolio entries — runs unconditionally per JD per project. Same JD applied across multiple roles re-pays the LLM cost every time.

**Fix shape:**
1. Add `portfolio_bullets_cache` table: `(jd_hash, project_id, bullets_json, created_at)`.
2. Hash the JD text (strip whitespace + lowercase before SHA256).
3. TTL 14 days (matches `tailored_cv_cache`).
4. Read-through: if hit, skip the LLM call entirely; return cached bullets.
5. Write-through after successful generation.
6. Test-mode guard: `if os.environ.get("JOBPULSE_TEST_MODE") == "1": return None` in the read path.

**Acceptance:**
- `test_portfolio_bullets_cache.py` with stash-drill: cache miss → LLM called → cache hit → LLM **not** called.
- Live re-apply on the same JD: 0 LLM calls for `_generate_jd_aware_bullets` on second run.

---

## Item 7: Cache `generate_portfolio_entry` (portfolio variants)

**Source:** §7 S2-EXT (catalog row 18).
**File:** `jobpulse/portfolio_variants.py:262`

Same shape as Item 6, same key `(jd_hash, project_id)`. Likely the two end up sharing a single cache table — `portfolio_variant_cache` with a `kind` column (`bullets` / `entry`) — to keep migrations manageable.

**Acceptance:** same as Item 6. Combined: 2 LLM calls per project saved per JD repeat.

---

## Item 8: Cache `scan_learning.run_llm_analysis`

**Source:** §7 S2-DEF (catalog row 28).
**File:** `jobpulse/scan_learning.py:434`

**Caveat:** the audit explicitly says **"requires measurement first"** — the LLM analysis runs on aggregate scan signals, not per-JD. Whether `(jd_pattern_hash)` even repeats often enough to justify cache infra is unknown. Don't add the cache before measuring hit-rate on a week of real scans.

**Fix shape (only after measurement justifies it):**
1. Log every `run_llm_analysis` call with the input signal-set hash for one week.
2. If hit-rate ≥ 30%, add cache by signal-set hash with 7-day TTL.
3. If hit-rate < 30%, document the measurement and close the item.

**Acceptance:** measurement document at `docs/audits/scan_learning_cache_measurement.md` with hit-rate evidence. Cache only added if justified.

---

## Item 9: Cache `screening_decomposer._llm_decompose`

**Source:** §7 S3-EXT (catalog row 13).
**File:** `jobpulse/screening_decomposer.py:133`

**Why:** Splits compound questions ("Are you authorised to work in the UK and willing to relocate?") into sub-questions. Same compound question hits this LLM call every time it appears on a different form.

**Fix shape:**
1. Add `screening_decomposition_cache` table: `(question_text_hash, sub_questions_json, created_at)`.
2. Hash the question text (strip + lowercase before SHA256).
3. TTL 30 days (compound-question phrasings are stable).
4. Read-through + write-through.

**Acceptance:** stash-drill test like S3's `test_hiring_message_cache.py`. Live re-apply: 0 LLM calls for `_llm_decompose` on second encounter of the same compound question.

---

## Item 10: Cache `gate4_quality.scrutinize_cv_llm`

**Source:** §7 S4-DEF (catalog row 51).
**File:** `jobpulse/gate4_quality.py:254`

**Why:** Recruiter-style CV review. One LLM call per application. Same `(cv_hash, jd_hash)` pair = same review.

**Fix shape:**
1. Add `cv_scrutiny_cache` table: `(cv_hash, jd_hash, score, feedback_json, created_at)`.
2. CV hash = SHA256 of the rendered PDF bytes (or the source CV JSON if available pre-render).
3. JD hash = SHA256 of normalised JD text.
4. TTL 30 days.
5. Read-through + write-through.

**Acceptance:** stash-drill test. Live re-apply on same CV+JD: 0 LLM calls. Independent of Item 6/7 since gate4 runs at a different pipeline stage.

---

## Item 11: Cache `_vision_detect` (page_analyzer)

**Source:** §7 S6-DEF (catalog row 22).
**File:** `jobpulse/page_analyzer.py:45`

**The hard one.** Vision LLM input is a PNG screenshot — pixel-level differences (cursor, animations, scroll position) defeat plain `screenshot_hash` equality. The audit deferred this because the cache key requires plumbing domain context through the caller.

**Fix shape:**
1. Cache key: `(domain, content_hash)` where `content_hash` = hash of stable DOM text + structural features (number of fields, button labels), NOT the screenshot pixels. Borrow the pattern `PageReasoner` already uses (`shared.page_reasoner._content_hash`).
2. Plumb `domain` through `_vision_detect`'s caller (`page_analyzer.classify_page_type`).
3. Cache table `vision_detect_cache` with `(domain, content_hash, page_type, confidence, created_at)`.
4. TTL 1 hour (page layouts change; this matches `PageReasoner`'s TTL).
5. **Skip rule:** if the page is mid-navigation (URL just changed, content hash unstable), don't cache — same heuristic `PageReasoner` uses.

**Acceptance:** test that documents the pixel-hash failure mode and verifies the content-hash approach hits on the second call. Live verify: vision-tier cost on a repeat domain drops to 0.

---

## Item 12: Cache `classify_page_type_from_screenshot` (vision_tier)

**Source:** §7 S6-DEF (catalog row 23).
**File:** `jobpulse/vision_tier.py:117`

Same caveat as Item 11 — same fix. Likely shares the cache table with Item 11 (`vision_classification_cache` with a `tier` column).

**Acceptance:** same as Item 11. Combined: vision tier cost on repeat domains → 0.

---

## Item 13: Cognitive migration `gmail_agent._classify_email`

**Source:** §7 S7-EXT (catalog row 7).
**File:** `jobpulse/gmail_agent.py:102`

**Why:** The audit's S7 migrated `strategy_reflector.reflect_with_llm` from raw `smart_llm_call` (which bypassed the cognitive engine entirely) to `cognitive_llm_call(domain="strategy_reflection")`, gaining L0 Memory Recall (cache-by-similar-task) for free. `gmail_agent._classify_email` is the same shape and is the last bypass row in the catalog.

**Reference commit:** `0a3cfda` (S7) — the migration is mechanical: replace `get_llm` + `smart_llm_call` with `cognitive_llm_call(domain="email_classification", stakes="medium", task=prompt)`.

**Fix shape:**
1. Open `jobpulse/gmail_agent.py:_classify_email`.
2. Replace the LLM call site with `cognitive_llm_call(domain="email_classification", stakes="medium", task=prompt)` — leave the prompt unchanged.
3. Verify `email_classification` exists in the cognitive engine's domain registry (`shared/cognitive/_classifier.py`); if not, add it with sensible L0/L1/L2 thresholds.
4. Make sure the existing email-preclassifier (`jobpulse/email_preclassifier.py`) still runs first — it's a free regex/keyword tier that handles 70-85 % of mail before this LLM is even called.

**Acceptance:**
- Pytest `tests/jobpulse/test_gmail_agent.py` still passes.
- Live: send a test email to the dev mailbox, run `python -m jobpulse.runner gmail-check`, confirm the cognitive engine logs `[L0|L1|L2]` for `domain=email_classification`.
- Cost-tracker shows the call attributed to `cognitive_llm_call`, not `smart_llm_call`.

---

## Setup-tooling backlog (out of band)

These two were surfaced during S2's live verification but left out of audit commits because they were mixed with pre-existing edits in the working tree. Bundle into a single `chore(setup):` commit.

### Setup-1: `get_openai_client` default timeout 30s → 180s

**File:** `shared/agents.py` — `get_openai_client` constructor.

**Why:** local Ollama 32b models (qwen3:32b, kimi-k2.6) routinely take 30–60 s per call. The default 30 s timeout aborts before the model finishes. Cloud providers don't need 180 s but the larger value is harmless there.

**Fix:** raise the default `timeout=` kwarg to 180 (seconds). Surface as `OPENAI_CLIENT_TIMEOUT` env var override.

**Acceptance:** confirm via apply log on a live run: no `httpx.ReadTimeout` after 30 s for local LLM calls.

### Setup-2: `cv_tailor.tailor_all_sections` auto-scale parallelism

**File:** `jobpulse/cv_tailor.py` — `tailor_all_sections`.

**Why:** runs 4 LLM calls in parallel via `ThreadPoolExecutor(max_workers=4)`. Single-tenant Ollama serialises requests internally and returns empty content for 2–3 of 4 under that load — observed during S4's live verification.

**Fix:** detect local LLM via `shared.agents.is_local_llm()` and set `max_workers=1` when true; keep 4 for cloud providers.

**Acceptance:** stash-drill test that toggles `is_local_llm()` and confirms `max_workers` flips. Live verify: no empty-content errors from `tailor_all_sections` on local Ollama.

---

---

# Cluster 3: Cross-cutting reliability

## Item 14: DB-retrieval accuracy instrumentation

**Severity:** observability gap. Today we have no idea which DB lookups are returning wrong data, returning nothing when they should hit, or being silently bypassed. The 2026-05-09 audit only surfaced the wrong-shape `relocation: "Yes, within the UK"` because we tripped over it during a live run — there's no general way to spot these.

**Goal:** every DB read across the apply pipeline reports (a) whether it hit, (b) whether the value it returned was actually used (vs. dropped/aligned/overridden downstream), and (c) when used downstream, whether the final fill matched. Aggregate to a per-DB success rate. When a DB drops below threshold, log the root cause loud.

**Scope — DBs to instrument** (priority order, derived from this session's diagnostics):
- `data/applications.db` — `ats_answer_cache` (legacy screening cache)
- `data/screening_semantic_cache.db` — semantic Qdrant + SQLite hybrid cache
- `data/user_profile.db` — `screening_defaults`, `sensitive_fields`, `identity`, `experience`, etc.
- `data/form_experience.db` — `field_label_mappings`, `field_types`, `container_selectors`, `fill_techniques`
- `data/field_label_mappings.db` — older label→profile-key store
- `data/agent_rules.db` — correction-derived rules
- `data/gotchas.db` — platform quirks
- `data/skill_graph.db` — skill matching for CV tailoring
- `data/cv_cache.db`, `data/cover_letter_cache.db`, `data/hiring_message_cache.db`, `data/page_reasoner_cache.db` — content caches added during the audit
- `data/trajectory.db` — historical fills (already noisy — read-only audit only)

**Fix shape:**

1. **Wrap every DB accessor with a measurement decorator.** Add `shared/db_observability.py` with:
   ```python
   @observe_lookup(db_name, table)
   def get_xxx(self, key): ...
   ```
   The decorator records: `(db, table, key_hash, hit/miss, value_dropped_downstream, latency_ms, ts)`.
2. **Plumb a "consumed" callback** so callers report whether the returned value made it into the final fill mapping (vs. being dropped by `_align_screening_to_options`, overridden by a later tier, or rejected by validation). Without this, "hit rate" is misleading — a DB that returns wrong-shape values 100 % of the time looks like a 100 %-hit DB even though everything it returns gets thrown away.
3. **Aggregate to `data/db_observability.db`** with one row per lookup. Run a daily summary cron that emits per-DB:
   ```
   db.table          hits  misses  consumed  dropped  drop_rate  top_drop_reason
   screening_defaults  47     3       12        35      74 %       option_misalignment
   ```
4. **Auto-OPRAL on high drop rates.** When any `(db, table)` exceeds 50 % drop rate over a 7-day window, the daily summary fires a Telegram alert AND opens an entry in `.claude/mistakes.md` with a templated investigation prompt: "DB X is returning data that's getting dropped 74 % of the time. Top drop reason: option_misalignment. Trace one example: …".
5. **Root-cause logger:** when a value is dropped, log the exact reason (option mismatch, validation failure, override) plus a sample of the dropped value. Don't just count — explain.

**Live verification (per the rules above):**
- Run the apply pipeline against the Anthropic Greenhouse URL three times.
- Inspect `data/db_observability.db` after each run.
- Confirm:
  - Every screening-pipeline DB lookup is logged.
  - The `relocation → "Yes, within the UK"` drop (current known wrong-shape) shows up with reason `option_misalignment`.
  - The `Veteran Status → veteran_status` lookup against the empty profile slot is logged as `hit_returned_empty` (or similar — needs naming).
  - Hit-rate / consumed-rate numbers add up.
- Run the daily summary manually and verify the Telegram alert fires for the known-bad rows.

**Acceptance:**
- [ ] Every accessor in the listed DBs goes through the observability decorator.
- [ ] `data/db_observability.db` populated after a live apply, with at least 30 rows of lookups.
- [ ] Daily summary script + cron entry (`scripts/db_observability_summary.py` + `scripts/install_cron.py`).
- [ ] Live verify on Anthropic URL: known-bad `screening_defaults.relocation` flagged with reason `option_misalignment`.
- [ ] Drop rate per DB visible in summary output.
- [ ] At least one OPRAL signal-emit on a real drop, and verify the entry lands in `.claude/mistakes.md`.

**Out of scope:** automated remediation. This item only adds visibility; fixes are separate items (e.g., Item 5 for `screening_defaults`).

---

## Item 15: Firecrawl spike — evaluate replacing scan-side scrapers

**Source:** discussion 2026-05-10 — "if I use firecrawl will it be better than the stuff we are using right now?"

**TL;DR conclusion from the web-search check:** Firecrawl is a **scan-side complement**, not an apply-side replacement. It's an API that takes a URL and returns clean markdown / structured JSON via a schema + LLM. Their new `/interact` endpoint markets itself as Playwright-replacement for forms, but **don't try to use it for apply** — your pipeline needs stateful Playwright for SSO, persistent profile, file uploads, dry-run, and the post-apply learning chain (`CorrectionCapture` → `AgentRulesDB` → `strategy_reflector` → `OptimizationEngine`). Firecrawl can't carry that state.

**Where firecrawl plausibly helps (worth a 1-week spike):**
- `jobpulse/platform_scanners/` — Reed/LinkedIn/Indeed listing extraction. Today: bespoke selectors per platform, fragile. Firecrawl: define a schema (`{title, company, location, salary, jd}`), one prompt, one API call.
- `jobpulse/jd_analyzer.py` — structured JD-field extraction (skills, salary, requirements). Today: LLM + regex glue. Firecrawl's schema mode is cleaner.
- Company research for cover letters — clean markdown of "About Us" pages.

**Where firecrawl is a bad fit (don't try):**
- The actual form fill — needs stateful Playwright.
- CAPTCHA bypass with human Telegram fallback.
- The 6-stage security wall pipeline.
- Cross-domain login / session reuse.

**Spike design:**
1. **Bake-off on `jd_analyzer.py`.** Pick 20 JDs from the last week's scans. Run both:
   - Current path: `jd_analyzer.analyze(jd_text)` (LLM + heuristics).
   - Firecrawl: `firecrawl.scrape(url, schema=JDSchema)` returning the same fields.
   Compare on: latency, cost, field-extraction accuracy (manual diff vs. ground truth), failure modes.
2. **Bake-off on `platform_scanners/reed_scanner.py`.** Same shape — 20 listing pages, compare current path vs. firecrawl on title/company/location/salary extraction.
3. **Decision matrix:** if firecrawl wins on accuracy AND cost-per-call ≤ our LLM cost, migrate. If accuracy ties but cost is higher, keep current. If accuracy loses, abandon.
4. **Migration scope (only if spike wins):** wrap firecrawl behind a feature flag (`USE_FIRECRAWL_SCAN=true`) so we can A/B safely. Keep the fallback path. Migrate one platform at a time.

**Live verification (per the rules above):**
- Bake-off must use **real JDs** scraped today — not last-month's snapshots.
- Manual diff on at least 20 pairs — anything below 95 % field-match rate is a fail.
- Cost: actual `$$$` measured from API invoice + LLM cost-tracker logs over the bake-off batch.

**Acceptance:**
- [ ] Bake-off report at `docs/audits/firecrawl-spike-2026-05.md` with tables for (latency, cost, accuracy) per scenario.
- [ ] Decision documented: migrate / keep current / abandon — with the numbers behind it.
- [ ] If migrate: at least one scanner migrated behind `USE_FIRECRAWL_SCAN`, with parallel execution (both run, results diffed, alert on divergence) for the first week.
- [ ] Test plan executed live, not on saved fixtures.

**Out of scope:** apply-side firecrawl. Don't try.

**Sources for the spike:**
- [Firecrawl vs Playwright for Web Scraping](https://www.firecrawl.dev/blog/playwright-vs-firecrawl)
- [How do I use Firecrawl with Playwright for browser automation?](https://webscraping.ai/faq/firecrawl/how-do-i-use-firecrawl-with-playwright-for-browser-automation)
- [Firecrawl alternative to Playwright: is it actually the smarter pick?](https://filipkonecny.com/2026/04/01/firecrawl-alternative-to-playwright/)

---

## Bonus: BGE-M3 / Ollama instability

Throughout the 2026-05-09 audit the BGE-M3 model on Ollama hit `HTTP 500` errors and NaN-encoding bugs for certain question texts (e.g. "Will you in the future require employer sponsorship..." would crash the model, but stripping the trailing `*`/`?` made it succeed).

The cache-write/read dim guards (commit `14e2422`) prevent silent corruption when this happens, but Ollama itself is unreliable. If you decide to harden:

1. Pin the cache writer/reader to a SINGLE embedder (BGE-M3 only, no MiniLM fallback). When BGE fails, refuse the operation; don't silently fall back to a different-dim model.
2. Pre-clean question texts before embedding: strip trailing punctuation (`*`, `?`), normalise unicode.
3. Consider switching to Voyage 3 Large (cloud, paid) for cache writes — same 1024 dims, no Ollama instability.

This isn't a hard bug — guards + fallback already keep the system correct. It's a reliability/cost tradeoff.
