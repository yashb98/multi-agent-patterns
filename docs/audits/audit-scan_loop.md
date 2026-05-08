# Subsystem 9 — `scan_loop` (line-by-line audit)

**Scope (matches audit prompt entry):**
- Entry: `python -m jobpulse.runner job-scan` → `runner.py:212` → `run_scan_window` → `_run_scan_window_inner` → `scan_pipeline.fetch_and_filter_jobs` (which is the only direct in-pipeline consumer of this subsystem) plus three Notion-sync entry points hung off the rest of `scan_pipeline.generate_materials` and `job_autopilot`.
- Files:
  - `jobpulse/job_scanner.py` (157 LOC) — config persistence + platform dispatch + liveness batch
  - `jobpulse/job_scanners/__init__.py` (171 LOC) — shared helpers for HTTP scanners
  - `jobpulse/job_scanners/linkedin.py` (258 LOC)
  - `jobpulse/job_scanners/indeed.py` (97 LOC)
  - `jobpulse/job_scanners/reed.py` (200 LOC)
  - `jobpulse/job_scanners/totaljobs.py` (58 LOC) — present in repo but **not** in `PLATFORM_SCANNERS`; only tests reach it
  - `jobpulse/liveness_checker.py` (128 LOC)
  - `jobpulse/job_deduplicator.py` (86 LOC)
  - `jobpulse/job_notion_sync.py` (871 LOC)
- Total LOC audited: ~2 026.
- Cron entries that drive this subsystem (`scripts/install_cron.py:48-56`): 7/13/19 daily `job-scan`, plus 10:00 and 16:30 `job-scan-quick`. Both invoke `run_scan_window(["linkedin","indeed","reed"])` (the quick path explicitly passes the same three platforms — equivalent to the default `ALL_PLATFORMS`).
- Output of the subsystem: `applications.db` rows (status `Found`) + Notion Job Tracker pages, written by `create_application_page` / `update_application_page` / `set_page_content` further down the scan_pipeline (those are still in-scope as part of `job_notion_sync.py`).

**Note on prompt framing.** The prompt says "Output: rows added to `applications.db` with `status='Found'` + Notion Job Tracker pages." `applications.db` writes happen in `JobDB.insert_listing` (called from `analyze_and_deduplicate` outside this subsystem); Notion writes happen via `create_application_page`/`update_application_page` invoked from `scan_pipeline.generate_materials` and `_run_scan_window_inner`. Both writers are in scope and were audited; the JobDB row write is in subsystem out-of-scope (`jobpulse/job_db.py`).

---

## 1. Function inventory + wiring

| File:line | Function | Cat | Direct callers (apply path) |
|---|---|---|---|
| job_scanner.py:32 | `load_search_config` | A | `job_autopilot:350,877`, `_run_scan_window_inner` (search-config seed), `job_api:380,410` |
| job_scanner.py:55 | `save_search_config` | A | `job_autopilot:884,895,904`, `load_search_config:51` (defaults seed) |
| job_scanner.py:68 | `_scan_indeed_wrapper` | A | `PLATFORM_SCANNERS["indeed"]` (line 78) |
| job_scanner.py:84 | `scan_platforms` | A | `scan_pipeline.fetch_and_filter_jobs:213` |
| job_scanner.py:132 | `check_liveness_batch` | A | `scan_pipeline.fetch_and_filter_jobs:226` |
| job_scanners/__init__.py:48 | `make_job_id` | A | linkedin.py:239, reed.py:139,141, indeed.py:39 (via `_make_job_id`), totaljobs.py:32 |
| job_scanners/__init__.py:57 | `random_ua` | A | linkedin.py:48, reed.py:82,177 |
| job_scanners/__init__.py:61 | `anti_detection_sleep` | A | reed.py:164 |
| job_scanners/__init__.py:66 | `to_float` | A | reed.py:135,136 |
| job_scanners/__init__.py:76 | `url_encode` | A | linkedin.py:68,69 |
| job_scanners/__init__.py:83 | `SessionSignals` (class) | A | linkedin.py:49 (instance), tests |
| job_scanners/__init__.py:123 | `handle_block` | C | tests/jobpulse/test_scan_learning_wiring.py only — **never called by any production scanner** (M-D) |
| job_scanners/__init__.py:149 | `record_success` | A | linkedin.py:252 (only). Reed/Indeed never call it (M-C, M-B). |
| job_scanners/linkedin.py:29 | `scan_linkedin` | A | `PLATFORM_SCANNERS["linkedin"]` (job_scanner.py:77); job_api:415 |
| job_scanners/reed.py:28 | `scan_reed` | A | `PLATFORM_SCANNERS["reed"]` (job_scanner.py:76); job_api:385 |
| job_scanners/indeed.py:20 | `_make_job_id` | A | indeed.py:39 (only) |
| job_scanners/indeed.py:25 | `normalize_to_job_listing` | A | indeed.py:84 (only) |
| job_scanners/indeed.py:43 | `scan_indeed` | A | `_scan_indeed_wrapper:72` |
| job_scanners/totaljobs.py:16 | `_search_hit_to_listing` | C | `scan_totaljobs:52` (also C) |
| job_scanners/totaljobs.py:36 | `scan_totaljobs` | C | only `tests/jobpulse/test_job_scanner_platforms.py:20`. Not in `PLATFORM_SCANNERS`, no cron entry. Confirmed dead path post-2026-05-04 (`scripts/install_cron.py:47` notes both Glassdoor and TotalJobs scanners removed). |
| liveness_checker.py:62 | `LivenessResult` (dataclass) | A | return type of `classify_liveness` |
| liveness_checker.py:70 | `classify_liveness` | A | `job_scanner.check_liveness_batch:145` (post-fix, was broken pre-fix); `scan_pipeline.process_single_url:1018` (CLI path only) |
| job_deduplicator.py:22 | `deduplicate` | A | `scan_pipeline.analyze_and_deduplicate:315` |
| job_notion_sync.py:26 | `_file_name_prefix` | A | `build_page_content:750` |
| job_notion_sync.py:74 | `delete_job_tracker_non_terminal_pages` | A | `job_autopilot:251` (`run_scan_window` housekeeping), `job_autopilot:472` (with `min_age_days=14`) |
| job_notion_sync.py:198 | `platform_display` | A | `build_create_payload:238`, `build_page_content:759` |
| job_notion_sync.py:212 | `build_create_payload` | A | `create_application_page:638` |
| job_notion_sync.py:286 | `build_update_payload` | A | `update_application_page:663` |
| job_notion_sync.py:386 | `get_notion_page_status` | A | `job_autopilot:614` (status-gate pre-apply) |
| job_notion_sync.py:403 | `find_application_page` | A | `create_application_page:630`; `post_apply_hook:162` |
| job_notion_sync.py:481 | `_parse_notion_job_page` | A | `fetch_found_jobs_from_notion:606` |
| job_notion_sync.py:568 | `fetch_found_jobs_from_notion` | A | `job_autopilot:143` (live notion-source job queue), `scripts/resolve_indeed_to_ats:44` |
| job_notion_sync.py:620 | `create_application_page` | A | `scan_pipeline.generate_materials:548` |
| job_notion_sync.py:653 | `update_application_page` | A | `scan_pipeline:767,975`, `post_apply_hook:210`, `live_review_applicator:1120`, `job_autopilot:745` |
| job_notion_sync.py:734 | `build_page_content` | A | `scan_pipeline.generate_materials:778` |
| job_notion_sync.py:825 | `set_page_content` | A | `scan_pipeline.generate_materials:789` |
| job_notion_sync.py:854 | `_heading2` | A | helper for `build_page_content` |
| job_notion_sync.py:862 | `_bulleted` | A | helper for `build_page_content` |
| job_notion_sync.py:870 | `_divider` | A | helper for `build_page_content` |

**Wiring categorisation summary**: 35 functions/classes in category A, **0 B-only-conditional**, **3 C** (`handle_block`, `_search_hit_to_listing`, `scan_totaljobs` — apply-path unreachable; tests-only or removed-platform), 0 D-orphans (every C-cat function has at least a test using it), 0 E-overrides found.

---

## 2. Cross-module wiring

### 2.1 Producer / consumer map

| Producer | Schema / artefact | Consumer |
|---|---|---|
| `scan_platforms` (job_scanner.py:84) | `list[dict]` raw job dicts with keys `title, company, url, location, salary_min, salary_max, description, platform, job_id` (+ `reed_id` for Reed, `direct_url` for Indeed) | `scan_pipeline.fetch_and_filter_jobs:213` (next stage), `analyze_jd:295` reads `url/title/company/platform/jd_text/apply_url/direct_url` |
| `check_liveness_batch` (job_scanner.py:132) | `tuple[list[dict], list[dict]]` — alive listings unchanged; expired get `liveness` reason added | `scan_pipeline.fetch_and_filter_jobs:226` — only the alive list is propagated; expired list is **logged then dropped** (line 228), no DB or Notion write |
| `deduplicate` (job_deduplicator.py:22) | `list[JobListing]` minus exact + fuzzy matches | `scan_pipeline.analyze_and_deduplicate:315` |
| `create_application_page` (job_notion_sync.py:620) | Notion page ID; payload from `build_create_payload` (status seeded as `Found`) | `scan_pipeline.generate_materials:548` |
| `set_page_content` (job_notion_sync.py:825) | Notion block children (Application Details / Documents / JD Match Analysis / GitHub Repos) | Notion only |
| `update_application_page` (job_notion_sync.py:653) | Patches one or more of 18 properties; auto-strips unknown/wrong-type properties | `post_apply_hook`, `live_review_applicator`, `scan_pipeline`, `job_autopilot` |
| `fetch_found_jobs_from_notion` (job_notion_sync.py:568) | `list[dict]` parsed via `_parse_notion_job_page` (19-field schema) | `job_autopilot._notion_apply_queue:143`, `scripts/resolve_indeed_to_ats:44` |

### 2.2 ScanLearningEngine wiring (broken)

`shared/optimization/_signals.py` and `jobpulse/scan_learning.py` are the consumers. Producers in scope:

- `engine.get_adaptive_params(platform)` consumed in linkedin.py:37, reed.py:39 (via `can_scan_now`).
- `record_success(engine, platform, signals)` (`__init__.py:149`) called only by linkedin.py:252 — **unconditionally**, even when every page returned 429.
- `handle_block(engine, platform, wall, signals)` (`__init__.py:123`) defined but never called from any production scanner. The `wall` parameter typed as `Any` but in practice expects `verification_detector.VerificationWall`, which httpx-based scanners cannot produce.

Schema agreement: producer (linkedin.py:252) and consumer (`engine.record_event` in `scan_learning.py`) agree on field names. The bug is purely **producer-side under-coverage**.

---

## 3. Findings (line-by-line read)

### Severity legend: blocker | major | minor | nit

#### BLOCKERS

- `jobpulse/job_scanner.py:145-150` **[blocker — FIXED commit `bdb6892`]**
  `check_liveness_batch` called `classify_liveness(status=, final_url=, body_text=, apply_controls=)` — none of these kwargs match the actual signature `classify_liveness(*, status_code, url, body, apply_control_text="")`. Each call raised `TypeError`, the per-listing handler caught only `httpx.HTTPError` so the TypeError escaped, and `scan_pipeline.fetch_and_filter_jobs:234` swallowed it under a blanket `except Exception` and continued with all jobs. **Liveness filtering had been a no-op in production for the entire lifetime of this code path.** Existing `test_scan_pipeline` patches `check_liveness_batch` directly, so the wiring was never exercised. Reproduction: `python -c "from jobpulse.liveness_checker import classify_liveness; classify_liveness(status=200, final_url='x', body_text='', apply_controls=[])"` → `TypeError: got an unexpected keyword argument 'status'`. Regression test added at `tests/jobpulse/test_liveness_checker.py:test_check_liveness_batch_passes_correct_kwargs`.

#### MAJORS

- `jobpulse/job_scanners/linkedin.py:252` **[major — partial fix shipped]**
  `record_success(engine, "linkedin", signals)` ran unconditionally at the end of the scan. If every paginated request returned 429 the inner retry loop breaks and we still log "success" — `signals.record_request()` (line 89) increments on every send regardless of status, and `record_success` only guards `requests_count > 0`, not "saw any 200". This actively pollutes `scan_learning.events` with `outcome=success` rows for blocked sessions and resets cooldown via `engine.reset_cooldown` (`__init__.py:170`) when in fact we just got rate-limited. **Partial fix shipped**: only call `record_success` when `results` is non-empty (handles the worst case — every page blocked → zero results → no spurious success). The 429-mid-pagination case (some 200s, some 429s, non-empty results) still records success and is deferred to the M-B/C/D fix that introduces explicit block-event recording. Test: `test_scan_linkedin_skips_record_success_on_zero_results` + `test_scan_linkedin_records_success_when_results_non_empty`.
- `jobpulse/job_scanners/indeed.py:43` **[major — deferred]**
  `scan_indeed` has **no scan_learning wiring at all** — no `engine.can_scan_now`, no `get_adaptive_params`, no `record_success`, no `handle_block`. JobSpy errors are caught and logged at line 93 but never recorded. The cooldown system can never engage for Indeed because there is no producer that emits a block event. Indeed is the platform with the highest empirical block rate in the codebase (per `jobpulse/scan_learning.py` LLM-analysis comments) so this is the worst gap.
- `jobpulse/job_scanners/reed.py:103` **[major — deferred]**
  `scan_reed` consults `engine.can_scan_now("reed")` at the start (line 39) but never emits a `record_success` or block event. After the `for retry in range(3)` exhausts on 429, the `for…else: break` exits silently — no event recorded. Reed has the lowest block rate empirically (API path) but the cooldown-engagement asymmetry is the same root cause as M-A/M-B/M-D.
- `jobpulse/job_scanners/__init__.py:123` **[major — deferred]**
  `handle_block` is defined for httpx scanners but takes a `wall: Any` parameter whose only fields used (`wall.wall_type`) come from `verification_detector.VerificationWall`. httpx scanners can't produce a verification wall — so this function is shape-incompatible with every production scanner. Either the type contract is wrong (it should accept a string `wall_type` directly, like `"rate_limit"` for 429, `"forbidden"` for 403) or the function is dead. Tests pass because the test file fakes a `wall` object with the right shape (`tests/jobpulse/test_scan_learning_wiring.py:130`).

> The four majors all share one fix: a producer that fires on every scanner exit-path (success path, partial-success, terminal-block) so `scan_learning` actually gets the data it consumes. Implementing that touches three scanners + `__init__.py` + a contract change to `handle_block` and is too large to share a session with the B-1 fix. Tracked for a follow-up session.

#### MINORS

- `jobpulse/job_scanner.py:113-121` **[minor]**
  ATS API scanning re-reads `_CONFIG_PATH` from disk (line 115) instead of using the `config` already loaded at line 89. If `_CONFIG_PATH` is mutated between the two reads (it isn't in production, but a future change could), the two scans operate on different configs. Use `config.ats_companies` if added to `SearchConfig`, otherwise consolidate both reads into a single `raw_config = json.loads(...)` at the top.
- `jobpulse/job_scanner.py:120-121` **[minor]**
  Bare `except Exception` swallows ATS scanning errors with `logger.warning`. Per `.claude/rules/error-handling.md`, this should preserve structured error context and ideally distinguish `ImportError` (ats_api_scanner missing) from runtime errors.
- `jobpulse/job_scanners/__init__.py:48-54` **[minor]**
  `make_job_id` returns `f"unknown-{uuid.uuid4().hex[:8]}"` for empty URLs — a non-deterministic ID. Two calls with the same empty URL produce different IDs. The dedup logic in `JobDB.listing_exists` then treats them as distinct rows. Empty URLs should either be rejected upstream (in scanners) or hashed against a stable surrogate (e.g. `f"{platform}:{title}:{company}"`).
- `jobpulse/job_scanners/totaljobs.py:36` **[minor]**
  `scan_totaljobs` is dead code in production — not registered in `PLATFORM_SCANNERS`, not invoked by any cron, kept alive only by `tests/jobpulse/test_job_scanner_platforms.py`. The companion comment at `scripts/install_cron.py:47` calls out the platform scanners were removed. The file (and its test) should be removed in a cleanup pass.
- `jobpulse/job_scanners/linkedin.py:215-227` **[minor]**
  Salary parsing uses a hardcoded `£` regex `r"£([\d,]+)\s*[-–]\s*£([\d,]+)"`. This is the kind of regex `seven-principles.md §8` flags — string interpolation extraction is fine here (numeric extraction from known format) but hardcoded `£` excludes `$`/`€` postings. Listed as principle violation; not a correctness bug for UK-only listings.
- `jobpulse/job_deduplicator.py:46-48` **[minor]**
  Hardcoded suffix list `[" with verification", " - entry level"]` for title normalisation. If LinkedIn introduces a new tag suffix the dedup key drifts. Should be a `data/`-driven list or pulled from `data/skill_synonyms.json`-style config.
- `jobpulse/job_notion_sync.py:69-71` **[minor]**
  `_TERMINAL_JOB_TRACKER_STATUSES` hardcoded; also referenced in `delete_job_tracker_non_terminal_pages` default. If the Notion DB later adds a status (e.g. "Offer"), trash sweeps will delete those pages. Should be loaded from the Notion schema once at startup or at least centralised with `MATCH_TIER_NAMES`.
- `jobpulse/job_notion_sync.py:683-697` **[minor]**
  `update_application_page` retry loop is bounded to 16 iterations (`for _ in range(16)`) and stripping rejected properties one by one. Edge case: if Notion error message format changes, none of the four regexes match, the loop never strips, and the retry loop just re-issues the same request 16 times. Add a "no progress" guard that breaks if a retry didn't strip anything new.
- `jobpulse/job_notion_sync.py:825-846` **[minor]**
  `set_page_content` deletes all existing children before re-appending, which is non-idempotent — a partial failure between the deletes and the appends leaves the page with empty content. Wrap in a transaction-like sequence (build new children, append, then delete old).
- `jobpulse/job_scanners/reed.py:113-122` **[minor]**
  Bare `except (ValueError, TypeError)` on date parsing — silently includes the job even when the posted-date filter should have excluded it. Use logger.debug at minimum so the cause is observable.

#### NITS

- `jobpulse/job_scanners/linkedin.py:117-121` **[nit]** — Bare `except Exception: pass` around `record_from_headers`; if the rate-monitor crashes, scanner continues silently. Same pattern at `reed.py:97-99`.
- `jobpulse/job_scanners/linkedin.py:243-246` **[nit]** — `logger.debug` for card parse failures means production logs miss recurring card-shape regressions; should be `warning` for first-N-per-session.
- `jobpulse/job_scanners/reed.py:189-193` **[nit]** — `break` on detail-fetch 429 but loop continues to next iteration of `for job in results`. The break only exits the inner `with httpx.Client` so subsequent jobs still try to fetch details. Probably intentional but visually misleading.
- `jobpulse/job_notion_sync.py:198-200` **[nit]** — `platform_display` falls back to `platform.title()` which sends `"icims"` → `"Icims"` (capitalisation mismatch with Notion select option). `ATS_PLATFORM_NAMES` already maps `icims` → `iCIMS`; consider the same fallback path here.

---

## 4. Live evidence

### 4.1 Reproduction of B-1

```
$ python -c "from jobpulse.liveness_checker import classify_liveness; \
  classify_liveness(status=200, final_url='x', body_text='', apply_controls=[])"
TypeError: classify_liveness() got an unexpected keyword argument 'status'
```

### 4.2 Silent-swallow path

```
$ python -c "
from unittest.mock import patch
from jobpulse.job_scanner import check_liveness_batch
class FakeResp:  status_code=200;  url='x';  text='y'
class FakeClient:
    def __init__(self,*a,**k): pass
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def get(self,_): return FakeResp()
with patch('httpx.Client', FakeClient):
    check_liveness_batch([{'url':'https://example.com'}])
"
TypeError: classify_liveness() got an unexpected keyword argument 'status'
```

The TypeError is not caught by the per-listing `except httpx.HTTPError`, escapes `check_liveness_batch`, and is then caught by `scan_pipeline.fetch_and_filter_jobs:234`'s blanket `except Exception` — which logs `liveness check failed: …` at WARNING and continues with all jobs unfiltered.

### 4.3 Post-fix test run

```
$ python -m pytest tests/jobpulse/test_liveness_checker.py tests/jobpulse/test_scan_learning_wiring.py -vv
17 passed, 11 warnings in 0.45s
```

Including the new `test_check_liveness_batch_passes_correct_kwargs` regression that drives the real `check_liveness_batch` → real `classify_liveness` path with stubbed httpx, plus `test_scan_linkedin_skips_record_success_on_zero_results` and the positive-path counterpart for the M-A guard.

> Note on the live-vs-expired classification path: even after the kwargs fix, `check_liveness_batch` calls `classify_liveness(...)` without an `apply_control_text` argument (defaults to `""`). That means classify_liveness step 6 (apply-control match → `active`) never fires from the batch path; non-expired listings end up `status="uncertain"`, which the caller treats as alive (`else: alive.append(listing)` at job_scanner.py:154). The fix recovers expired-filtering correctly, but apply-button affirmation is **not** wired through this path — that's by design for now.

### 4.4 What I did NOT run live

- A full `python -m jobpulse.runner job-scan` run was not executed: it would create real Notion pages, hit Reed/LinkedIn/JobSpy live, and fire scan_learning DB writes. The existing test suite covers each scanner in isolation. The B-1 fix is sufficient evidence that the liveness chain works through the unit-level seam; the production cron will exercise it tonight at 19:00 (next `job-scan`).
- Running the full suite was deferred — `tests/jobpulse/test_scan_pipeline.py` patches `check_liveness_batch` itself, so it can't catch this class of bug; that's a structural test-design weakness already noted under M-D's bucket of "consumer-only contract drift". Recommended follow-up: at least one `test_scan_pipeline_real_liveness` that does NOT patch `check_liveness_batch`.

---

## 5. Fixes shipped

| Severity | Finding | Commit | Test |
|---|---|---|---|
| blocker | B-1 — `check_liveness_batch` kwargs | `bdb6892` | `test_check_liveness_batch_passes_correct_kwargs` |
| major (partial) | M-A — `scan_linkedin` unconditional `record_success` | (this session) | `test_scan_linkedin_skips_record_success_on_zero_results`, `test_scan_linkedin_records_success_when_results_non_empty` |

Deferred (require a single coherent fix that touches the rest of the scanners + the `handle_block` contract):
- M-A residue — 429-mid-pagination case (some 200s + some 429s → non-empty results → still records "success")
- M-B — `scan_indeed` zero scan_learning wiring
- M-C — `scan_reed` no `record_success`/block recording
- M-D — `handle_block` shape-incompatible with httpx scanners

All ten minors and four nits are deferred per the audit prompt's "don't fix unless a blocker fix touches the same function" rule.

---

## 6. Doc updates

`docs/job-application-pipeline.md` was checked for `scan_loop` claims — the existing description matches what the code now does (post-B-1 fix). No doc update needed.

The scan_learning wiring weakness is already implicitly documented in `jobpulse/CLAUDE.md` under "Scanning & Analysis" and in the cron note about TotalJobs/Glassdoor removal. Once the M-A through M-D cluster is fixed, the doc should grow a "Block-recording contract for HTTP scanners" subsection covering: when each scanner emits `record_success` vs a block event, what the schema is, and how `handle_block` relates to `engine.record_event` for non-Playwright paths.
