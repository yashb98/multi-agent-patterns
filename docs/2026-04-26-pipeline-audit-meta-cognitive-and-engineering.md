# Job Application Pipeline Audit
## Meta-Cognitive Self-Improvement + AI Software Engineering Best Practices

**Date:** 2026-04-26  
**Auditor:** Kimi Code CLI  
**Scope:** Full JobPulse application pipeline from discovery → submission → outcome tracking  
**Framework:**
- **Meta-cognitive score (1–10):** Does the stage learn from feedback? Does it have a closed feedback loop? Does it adapt thresholds/strategies over time? Can it explain its own decisions?
- **Engineering score (1–10):** Measured against the 7 mandatory principles from AGENTS.md — System Design, Tool & Contract Design, Retrieval Engineering, Reliability Engineering, Security & Safety, Evaluation & Observability, Product Thinking.

---

## Summary Table

| # | Stage | Meta-Cognitive | Engineering | Critical Gap |
|---|-------|---------------|-------------|--------------|
| 1 | Job Discovery (Scan) | **4/10** | **6/10** | No adaptive rate-limiting; liveness check is fire-and-forget |
| 2 | JD Analysis | **3/10** | **7/10** | Skill extraction doesn't learn from corrections; no A/B testing of extraction quality |
| 3 | Ghost Detection | **2/10** | **5/10** | Static regex list; no learning from false positives/negatives |
| 4 | Pre-Screen Gates 1–3 | **6/10** | **7/10** | Adaptive thresholds exist but gate logic is opaque; no explanation of *why* a job was rejected |
| 5 | Quality Gate 4A | **5/10** | **7/10** | Blocklist learns; spam detection is static heuristic; company background check lacks reliability scoring integration |
| 6 | Material Generation | **4/10** | **6/10** | Archetype engine is static config; CV generation doesn't learn from ATS score deltas over time |
| 7 | Gate 4B (CV Scrutiny) | **3/10** | **6/10** | LLM reviewer has no memory of past CVs it approved/rejected; no calibration loop |
| 8 | Routing by Tier | **5/10** | **8/10** | Thresholds are hardcoded but cap-aware; lacks outcome-driven threshold optimisation |
| 9 | Live Review Session | **7/10** | **8/10** | Strong human-in-the-loop; correction capture is excellent; dry-run-first is exemplary |
| 10 | Form Fill (NativeFormFiller) | **6/10** | **7/10** | Field resolver learns mappings; LLM fallback is uncalibrated; no cross-form pattern transfer |
| 11 | Screening Answers (Legacy) | **4/10** | **5/10** | Pattern-based with SQLite cache; no semantic matching; no validation |
| 12 | Screening Answers (V2 — New) | **8/10** | **8/10** | Semantic cache + intent classifier + validation + pattern extraction; strong learning loop |
| 13 | Post-Apply Hook | **7/10** | **8/10** | Strategy reflection + navigation learning + form experience recording; all non-blocking |
| 14 | Outcome Tracking | **6/10** | **8/10** | Gate effectiveness + company reliability tables exist; rejection analyzer is static |
| 15 | Correction Capture | **7/10** | **8/10** | Diff-based correction storage; feeds into optimization engine; per-field granularity |
| 16 | Scan Learning | **8/10** | **7/10** | 17-signal session learning; adaptive cooldowns; strong anti-detection meta-cognition |
| 17 | Optimization Engine | **5/10** | **6/10** | Signal bus exists but consumption is sparse; trajectory store is underutilised for policy learning |

**Overall Pipeline Average:** Meta-cognitive **5.4/10**, Engineering **6.9/10**

---

## Detailed Stage Analysis

---

### Stage 1: Job Discovery (Scan)
**Files:** `jobpulse/job_scanner.py`, `jobpulse/job_scanners/*.py`, `jobpulse/liveness_checker.py`

**What it does:**
- Dispatches to per-platform scrapers (Reed, LinkedIn, Indeed, TotalJobs, Glassdoor)
- `check_liveness_batch()` — HTTP HEAD checks to filter expired postings
- Gate 0: Title relevance filter via keyword exclusion (`senior`, `lead`, `10+ years`, etc.)

**Meta-cognitive assessment (4/10):**
- **Learning:** ❌ The liveness checker does not learn which platforms have high expiry rates at what times. It repeats HEAD requests for every job every scan.
- **Adaptation:** ❌ Gate 0 keywords are static config. If the user starts getting interviews for "Staff Engineer" roles, the gate doesn't relax.
- **Self-model:** ❌ No tracking of scan success rate per platform per time-of-day.
- **Positives:** `scan_learning.py` (Stage 16) observes scan session outcomes, but it's downstream and doesn't feed back into the scanner itself.

**Engineering assessment (6/10):**
- **System Design:** ⚠️ Platform scrapers are modular but share little common interface. `scan_platforms()` returns raw dicts with inconsistent keys.
- **Reliability:** ✅ Retry + timeout on HTTP calls. Graceful degradation if one platform fails.
- **Observability:** ✅ `ProcessTrail` logs every step. Platform-specific success/failure counts are logged.
- **Security:** ✅ No PII in search queries; parameterized URLs.
- **Retrieval:** ❌ No connection pooling evident in scrapers. Each platform may open fresh sessions.

**Critical gap:** The scanner has no *predictive* expiry model. It could learn that Reed jobs posted >14 days ago have 80% expiry rate and skip the HEAD check, saving bandwidth.

**Recommendation:**
1. Add a `platform_expiry_model` table: `(platform, age_days, expiry_rate)`. Update after each liveness batch.
2. Gate 0 keywords should be dynamically adjusted based on `gate_effectiveness` outcomes (if "Staff Engineer" applications result in interviews, relax the gate).
3. Consolidate platform scrapers behind a typed `JobSource` protocol.

---

### Stage 2: JD Analysis & Deduplication
**Files:** `jobpulse/jd_analyzer.py`, `jobpulse/skill_extractor.py`, `jobpulse/job_deduplicator.py`

**What it does:**
- Rule-based extraction: salary, location, remote, seniority, ATS platform, easy_apply, recruiter email
- LLM-based: `extract_skills_hybrid()` → required_skills, preferred_skills
- Deduplication: SHA-256 URL hash + fuzzy company/title overlap within 30 days

**Meta-cognitive assessment (3/10):**
- **Learning:** ❌ The skill extractor does not learn from corrections. If the LLM extracts "Kubernetes" as required but the JD only mentions it in a "nice to have" section, there's no feedback loop.
- **Adaptation:** ❌ `detect_ats_platform()` uses static regex patterns. New ATS platforms (e.g., Ashby, Pinpoint) require code changes.
- **Self-model:** ❌ No tracking of extraction accuracy. The system doesn't know if its salary extraction is 60% or 90% accurate.
- **Positives:** Deduplication has a time-window heuristic (30 days) which is reasonable.

**Engineering assessment (7/10):**
- **Tool Design:** ✅ `JobListing` Pydantic model provides strong typing. `analyze_jd()` returns a consistent structure.
- **Reliability:** ✅ Try/catch around LLM skill extraction; falls back to rule-based if LLM fails.
- **Retrieval:** ⚠️ `deduplicate()` queries the full `job_listings` table. With 10k+ listings, this loads all rows. Should use a date-filtered query.
- **Cost:** ✅ 1 LLM call per job; cost is tracked via `get_llm()`.

**Critical gap:** No quality gate on JD analysis output. A job with 0 extracted skills should trigger a re-analysis or manual flag, not pass through silently.

**Recommendation:**
1. Add `jd_analysis_quality` table tracking extraction accuracy against human-verified samples.
2. Make `detect_ats_platform()` learn from successful form fills — if a job's URL pattern wasn't recognised but the form fill succeeded, add the pattern.
3. Deduplication: use `SELECT ... WHERE found_at > date('now', '-30 days')` instead of loading all rows.

---

### Stage 3: Ghost Detection
**Files:** `jobpulse/ghost_detector.py`, `jobpulse/pipeline_hooks.py`

**What it does:**
- Blocks listings matching 12 expired patterns (EN/DE/FR) or missing apply buttons
- Optional filter applied via `with_ghost_detection()` hook

**Meta-cognitive assessment (2/10):**
- **Learning:** ❌ Static regex list. No feedback from user actions (e.g., user marks a job as "already filled" doesn't update the ghost detector).
- **Adaptation:** ❌ Language coverage is hardcoded. A new ghost pattern requires a code change.
- **Self-model:** ❌ No tracking of ghost detection precision/recall.

**Engineering assessment (5/10):**
- **System Design:** ⚠️ 12 regex patterns in a single list is brittle. No categorisation by pattern type.
- **Reliability:** ✅ Non-blocking; failures don't stop the pipeline.
- **Product Thinking:** ⚠️ False positives (legitimate jobs blocked) are invisible unless the user manually reviews skipped jobs.

**Recommendation:**
1. Move ghost patterns to a database table with `(pattern, language, confidence, false_positive_count, last_seen)`.
2. When a job is marked "already filled" by the user, auto-generate a ghost pattern candidate.
3. Track ghost detection outcomes: `ghost_detections` table with `(job_id, was_ghost, user_confirmed)`.

---

### Stage 4: Pre-Screen Gates 1–3
**Files:** `jobpulse/skill_graph_store.py`, `jobpulse/scan_pipeline.py::prescreen_listings()`

**What it does:**
- Gate 1 (Kill Signals): rejects if JD requires 3+ years experience, primary skill missing, or top-3 skills in foreign domain
- Gate 2 (Must-Haves): skips if <3 of top-5 skills matched, <2 projects with 2+ skill overlap, <92% required skills matched
- Gate 3 (Competitiveness Score 0–100): Hard Skill (35) + Project Evidence (25) + Stack Coherence (15) + Domain Relevance (15) + Recency (10)
- Adaptive thresholds: base 75/55, raised to 80/60 if rejection rate >50%, lowered to 70/50 if interview rate >20%

**Meta-cognitive assessment (6/10):**
- **Learning:** ✅ Adaptive thresholds based on historical rejection/interview rates. This is genuine meta-cognition — the gate knows its own error rate and adjusts.
- **Adaptation:** ✅ `skill_gap_tracker.record_gap()` captures missing skills per job.
- **Self-model:** ⚠️ The gate knows aggregate stats but not per-job-type stats. "iOS roles" might need different thresholds than "backend roles" but the adaptation is global.
- **Explanation:** ❌ When a job is rejected, the user sees "Score 68/100" but not *which* sub-scores dragged it down. No actionable explanation.

**Engineering assessment (7/10):**
- **System Design:** ✅ Gates are clearly separated. `PreScreenResult` dataclass is well-structured.
- **Reliability:** ✅ `SkillGraphStore` init failure is caught; pipeline continues without pre-screen.
- **Retrieval:** ⚠️ `pre_screen_jd()` likely queries the skill graph per job. With 50 jobs, that's 50 queries. Should batch.
- **Observability:** ✅ Gate decisions are logged and stored in `gate_effectiveness` table.

**Critical gap:** Gate thresholds adapt globally, not per-domain. A user applying to both "ML Engineer" and "DevOps" roles needs domain-specific thresholds.

**Recommendation:**
1. Add `domain` (extracted from JD skills) to `gate_effectiveness` and adapt thresholds per-domain.
2. Generate structured rejection explanations: "Rejected: only 2/5 top skills matched (Python, SQL missing)."
3. Batch skill graph queries: `pre_screen_jds_batch(listings)`.

---

### Stage 5: Quality Gate 4A
**Files:** `jobpulse/gate4_quality.py`, `jobpulse/company_blocklist.py`

**What it does:**
- A2: Company blocklist check + spam detection
- A1: JD quality (length ≥200, ≥5 skills, boilerplate phrases <3)
- A3: Company background (generic name detection, previously-applied check)

**Meta-cognitive assessment (5/10):**
- **Learning:** ✅ `BlocklistCache` can be updated. `company_reliability` table (new) tracks per-company interview/offer rates.
- **Adaptation:** ⚠️ Spam detection uses static heuristics. The blocklist is manual.
- **Self-model:** ✅ `company_reliability` gives a self-model of which companies are worth applying to.
- **Integration:** ❌ `company_reliability` scores are not integrated into Gate 4A decisions. A company with 0% interview rate still passes if not blocklisted.

**Engineering assessment (7/10):**
- **System Design:** ✅ Gate 4A is cleanly separated from 4B. `JdQualityResult` and `CompanyBackground` are typed.
- **Security:** ✅ No PII leakage in company checks.
- **Reliability:** ✅ Blocklist refresh failure is caught; old cache is used.

**Recommendation:**
1. Integrate `company_reliability` into Gate 4A: auto-skip companies with <5% interview rate after ≥10 applications.
2. Make spam detection learn from user flags: "Mark as spam" → train a lightweight classifier.
3. Add `blocklist_reason` tracking: distinguish "user-blocked" vs "spam-detected" vs "ghost-company".

---

### Stage 6: Material Generation
**Files:** `jobpulse/scan_pipeline.py::generate_materials()`, `jobpulse/cv_templates/generate_cv.py`, `jobpulse/project_portfolio.py`

**What it does:**
- Archetype-aware project selection from MindGraph
- Synthetic CV text assembly for ATS scoring
- `build_extra_skills()` tailors skill section
- CV PDF deferred until apply time (lazy generation)

**Meta-cognitive assessment (4/10):**
- **Learning:** ❌ The archetype engine uses static config. If "Data Engineer" roles consistently score higher with project X than project Y, the engine doesn't learn.
- **Adaptation:** ⚠️ `_reorder_projects()` uses archetype priority list from static config.
- **Self-model:** ❌ No tracking of which project selections led to better ATS scores or interview outcomes.
- **Positives:** CV generation is lazy (deferred until apply), which is product-smart.

**Engineering assessment (6/10):**
- **System Design:** ⚠️ `generate_materials()` is 210 lines. It mixes CV text synthesis, ATS scoring, Notion updates, and Gate 4B. Should be split.
- **Retrieval:** ❌ `get_best_projects_for_jd()` likely queries the MindGraph per job. No batching evident.
- **Observability:** ✅ ATS score is stored. Notion page is created.

**Recommendation:**
1. Add `project_selection_outcomes` table: `(project_id, job_archetype, ats_score, outcome)`.
2. Split `generate_materials()` into: `select_projects()`, `synthesise_cv_text()`, `score_ats()`, `update_notion()`.
3. Cache `get_role_profile()` results — they are static config lookups.

---

### Stage 7: Gate 4B (CV Scrutiny)
**Files:** `jobpulse/gate4_quality.py::scrutinize_cv_deterministic()`, `scrutinize_cv_llm()`

**What it does:**
- B1: Deterministic checks — length, metrics presence, conversational text, informal words
- B2: LLM scrutiny (`gpt-5-mini` as FAANG recruiter) — score 0–10, needs_review if <7

**Meta-cognitive assessment (3/10):**
- **Learning:** ❌ The LLM reviewer has no memory of past CVs. It doesn't know "Last time I scored a CV 6/10, the user corrected it and it turned out to be a 9/10."
- **Calibration:** ❌ No tracking of LLM scrutiny score vs actual outcomes. Is a 7/10 from the LLM actually correlated with interview success?
- **Adaptation:** ❌ The scoring rubric is static prompt text.

**Engineering assessment (6/10):**
- **System Design:** ✅ B1 and B2 are separated. Deterministic first, LLM second (cost-efficient).
- **Cost:** ⚠️ 1 LLM call per job at Gate 4B. With 50 jobs, that's 50 calls ≈ $0.25–$1.00.
- **Reliability:** ✅ LLM failure is caught; job passes to review queue.

**Recommendation:**
1. Track `cv_scrutiny_calibration`: `(llm_score, b1_warnings, got_interview, user_overrode)`.
2. Periodically retrain/fine-tune the scrutiny prompt based on calibration data.
3. Cache LLM scrutiny results for identical CV+JD combinations.

---

### Stage 8: Routing by Tier
**Files:** `jobpulse/job_autopilot.py::determine_match_tier()`, `jobpulse/applicator.py::classify_action()`

**What it does:**
| ATS Score | Action |
|-----------|--------|
| ≥ 95 + easy_apply | `auto_submit` |
| ≥ 95, no easy_apply | `auto_submit_with_preview` |
| 85–94 | `send_for_review` |
| < 85 | `skip` |

**Meta-cognitive assessment (5/10):**
- **Learning:** ⚠️ Thresholds are hardcoded but the system is in draft-only mode (all jobs queued for review). The learning potential is dormant.
- **Adaptation:** ✅ Daily cap awareness (`remaining_cap`). If cap is low, high-score jobs get priority.
- **Self-model:** ❌ No tracking of "jobs I auto-submitted vs review-submitted vs skipped" and their respective outcomes.

**Engineering assessment (8/10):**
- **System Design:** ✅ Clean tier logic. `classify_action()` is pure and testable.
- **Product Thinking:** ✅ Draft-only mode is the right default. `confirm_application()` gate before real submission.
- **Observability:** ✅ Every routing decision is logged.

**Recommendation:**
1. Add `routing_outcomes` table: `(ats_score, tier, action, final_outcome, days_to_response)`.
2. Run a weekly analysis: "Did ≥95 ATS jobs actually have better interview rates than 85–94 jobs?" Adjust thresholds if not.
3. When draft mode is disabled, start with a small auto-submit batch (n=5) and monitor outcomes before scaling.

---

### Stage 9: Live Review Session
**Files:** `jobpulse/live_review_applicator.py`

**What it does:**
- Human-in-the-loop live application session manager
- Telegram bot integration for approve/reject
- Screenshots + CV/CL docs sent for human review
- `AIAssistLogger` captures AI-assisted fixes during review

**Meta-cognitive assessment (7/10):**
- **Learning:** ✅ `CorrectionCapture` diffs agent vs human final mappings.
- **Adaptation:** ✅ User rejections feed back into `gate_effectiveness`.
- **Self-model:** ✅ The system knows which jobs are pending, approved, rejected.
- **Human-AI collaboration:** ✅ Strong. Dry-run-first + screenshot review + `yes`/`no` response is exemplary.

**Engineering assessment (8/10):**
- **Product Thinking:** ✅ Dry-run-first. `confirm_application()` on successful submits. User-actionable errors.
- **Security:** ✅ No auto-submit without human approval. Rate limiter records BEFORE submission.
- **Observability:** ✅ `ProcessTrail`, Telegram stream, agent performance DB.

**Critical gap:** The review queue (`pending_review_jobs.json`) is a JSON file, not the database. Race conditions possible with multiple bots.

**Recommendation:**
1. Move review queue to SQLite with row-level locking.
2. Add "approve with correction" mode: user can edit answers inline in Telegram before approving.
3. Track review latency: time from queue to approval/rejection.

---

### Stage 10: Form Fill (NativeFormFiller)
**Files:** `jobpulse/native_form_filler.py`, `jobpulse/form_engine/`

**What it does:**
- Field discovery (`field_scanner`): a11y tree + DOM scanning
- Deterministic mapping (`field_resolver`): cached label→profile_key lookups
- LLM mapping fallback (`field_mapper`): for unresolved fields
- Screening question answers (`screening_answers.py`)
- File upload (`file_uploader`)
- Multi-page navigation (max 20 pages)

**Meta-cognitive assessment (6/10):**
- **Learning:** ✅ `field_resolver` persists label mappings. `learn_field_mapping()` improves over time.
- **Adaptation:** ⚠️ `_platform_strategy` is loaded but platform-specific strategies are sparse.
- **Cross-form transfer:** ❌ A field learned on Greenhouse doesn't transfer to Lever unless labels match exactly.
- **Self-model:** ✅ `_llm_fallback_count` tracks when deterministic mapping fails.

**Engineering assessment (7/10):**
- **System Design:** ✅ Well-decomposed into `field_scanner`, `field_resolver`, `field_mapper`, `file_uploader`.
- **Reliability:** ✅ `try/finally` for Playwright. Bounded loops (max 20 pages).
- **Security:** ✅ `assert_prompt_has_wrapped_pii()` before LLM calls. No raw PII in prompts.
- **Retrieval:** ⚠️ `field_resolver` queries SQLite per field. Should batch.

**Recommendation:**
1. Add cross-platform field embedding: embed field labels and cluster similar fields across platforms.
2. Batch field resolver queries: `resolve_fields_batch(fields)`.
3. Platform strategy should learn from `form_experience_db`: "On Workday, file upload usually appears on page 3."

---

### Stage 11: Screening Answers (Legacy)
**Files:** `jobpulse/screening_answers.py`

**What it does:**
- Pattern-based answers for ~80 common screening questions
- SQLite cache via `ats_answer_cache` table
- LLM fallback for open-ended questions
- Thread-local strategy tier tracking

**Meta-cognitive assessment (4/10):**
- **Learning:** ⚠️ SQLite cache stores answers but doesn't track success/correction (the v2 schema added this but the legacy code doesn't use it).
- **Adaptation:** ❌ Static `COMMON_ANSWERS` dict. New question patterns require code changes.
- **Validation:** ❌ No post-generation validation. An LLM-generated "As an AI..." answer could be submitted.

**Engineering assessment (5/10):**
- **System Design:** ⚠️ `screening_answers.py` is 750 lines. Mixed concerns: pattern matching, LLM prompts, caching, skill experience, salary logic.
- **Reliability:** ✅ Thread-local strategy tracking prevents concurrent collision.
- **Security:** ❌ `assert_prompt_has_wrapped_pii()` is called but error handling is unclear.

**Recommendation:**
1. **Replace with V2 pipeline** (`screening_pipeline.py`) which has semantic cache, intent classification, validation, and pattern extraction.
2. If keeping legacy, split into: `screening_patterns.py`, `screening_cache.py`, `screening_llm.py`.

---

### Stage 12: Screening Answers (V2 — New)
**Files:** `jobpulse/screening_pipeline.py`, `jobpulse/screening_semantic_cache.py`, `jobpulse/screening_intent.py`, `jobpulse/screening_detector.py`, `jobpulse/screening_decomposer.py`, `jobpulse/screening_option_aligner.py`, `jobpulse/screening_validator.py`, `jobpulse/screening_pattern_extractor.py`

**What it does:**
- **Decompose:** splits compound questions into atomic sub-questions
- **Semantic Cache:** Qdrant vector search for paraphrased questions
- **Intent Classify:** 30-category embedding-based classifier
- **Intent Resolve:** profile field mapping
- **Regex Fallback:** fast pattern matching
- **Agent Rules:** heuristic mappings
- **LLM Fallback:** concise answer generation
- **Option Align:** matches free-text to available options
- **Validate:** catches AI self-references, length issues, option mismatches, profile contradictions

**Meta-cognitive assessment (8/10):**
- **Learning:** ✅ `PatternExtractor.observe()` records every answer with success flag. `ScreeningSemanticCache.record_outcome()` updates success/correction counters.
- **Adaptation:** ✅ `ScreeningIntentClassifier.add_intent_example()` learns new intent prototypes from corrections.
- **Self-model:** ✅ The pipeline knows its own confidence at every stage and reports source + confidence.
- **Validation:** ✅ Post-generation validation prevents hallucinated answers from reaching submission.
- **Gap:** The pattern extractor's template extraction is rudimentary. It doesn't yet generate new answer *patterns*, just clusters.

**Engineering assessment (8/10):**
- **System Design:** ✅ Clean separation of concerns across 8 focused modules.
- **Tool Design:** ✅ Typed interfaces (`CacheHit`, `AnswerPattern`, `ValidationResult`).
- **Reliability:** ✅ Graceful degradation: if Qdrant/embedder fails, falls back to regex + exact cache.
- **Security:** ✅ Validation layer catches PII leakage and AI self-references.
- **Observability:** ✅ Every stage reports source, confidence, and metadata.
- **Retrieval:** ✅ Batch methods (`embed_batch`, `get_by_ids`). Qdrant for vector, SQLite for metadata.

**Recommendation:**
1. Wire V2 into `native_form_filler.py` behind a feature flag (`SCREENING_V2=true`).
2. Add `screening_answer_outcomes` table linking `(question_hash, intent, answer_source, validation_passed, user_corrected)`.
3. Enhance pattern extractor to generate *abstract templates* (e.g., "I have {N} years of {skill} experience") rather than just clustering.

---

### Stage 13: Post-Apply Hook
**Files:** `jobpulse/post_apply_hook.py`

**What it does:**
1. Record form experience → `FormExperienceDB`
2. Upload CV/CL → Google Drive
3. Update Notion → status, applied date, follow-up, links
4. Mark applied → `JobDB`
5. Strategy reflection → `strategy_reflector.reflect_on_application()`
6. Navigation learning → `navigation_learner.save_sequence()`

**Meta-cognitive assessment (7/10):**
- **Learning:** ✅ Strategy reflection extracts heuristics from fill trajectory. Navigation learning persists successful sequences.
- **Adaptation:** ✅ Form experience records per-domain field types, pages, timing.
- **Self-model:** ✅ The system knows how many fields it filled, how many LLM fallbacks, how long it took.
- **Gap:** Strategy reflection heuristics are not automatically applied to future forms. They need manual review or a policy engine.

**Engineering assessment (8/10):**
- **System Design:** ✅ Unified hook — both cron and manual paths call it.
- **Reliability:** ✅ Every sub-step is wrapped in try/except. Failure in Drive upload doesn't block Notion update.
- **Observability:** ✅ Structured log with `drive_cv`, `drive_cl`, `notion`, `nav` flags.

**Recommendation:**
1. Auto-apply high-confidence heuristics from strategy reflection (e.g., "On Lever, always click 'Apply with LinkedIn' before manual fill").
2. Track form experience quality: `(domain, avg_time, avg_pages, success_rate)`.
3. Navigation sequences should have a confidence score; low-confidence sequences should trigger re-learning.

---

### Stage 14: Outcome Tracking & Analytics
**Files:** `jobpulse/job_db.py`, `jobpulse/rejection_analyzer.py`, `jobpulse/followup_tracker.py`, `jobpulse/job_analytics.py`

**What it does:**
- `application_outcomes`: outcome, stage_reached, feedback, days_to_response
- `gate_effectiveness`: gate_name, decision, final_outcome, count
- `company_reliability`: company, total_applied, interview_rate, offer_rate, avg_days_to_response
- `rejection_analyzer`: classifies outcomes and generates recommendations
- `followup_tracker`: urgency-based follow-up cadence

**Meta-cognitive assessment (6/10):**
- **Learning:** ✅ `gate_effectiveness` enables threshold adaptation. `company_reliability` enables company-level filtering.
- **Adaptation:** ✅ Follow-up tracker adjusts cadence based on response patterns.
- **Self-model:** ✅ The system knows its conversion funnel: scan → apply → interview → offer.
- **Gap:** `rejection_analyzer` uses static classification rules. It doesn't learn from patterns in rejection emails (e.g., "We chose a candidate with more X experience" → skill gap insight).

**Engineering assessment (8/10):**
- **System Design:** ✅ Well-normalised schema. Separate tables for outcomes, gates, company reliability.
- **Retrieval:** ✅ Indices on `applications(status, match_tier, applied_at)` and `job_listings(company, platform, found_at)`.
- **Observability:** ✅ `job_analytics.py` provides conversion funnel, platform breakdown, gate stats.

**Recommendation:**
1. Add NLP rejection email parsing: extract skill gaps, salary mismatch, experience mismatch from auto-replies.
2. Surface company reliability scores in the Telegram review UI: "⚠️ TechCorp: 0% interview rate (12 applications)."
3. Gate effectiveness should auto-trigger threshold suggestions: "Gate 3 rejected 80% of jobs that later got interviews. Consider lowering threshold from 75 to 70."

---

### Stage 15: Correction Capture
**Files:** `jobpulse/correction_capture.py`

**What it does:**
- Diffs `agent_mapping` vs `final_mapping` from dry-run approvals
- Stores per-field corrections in SQLite
- Feeds into optimization engine

**Meta-cognitive assessment (7/10):**
- **Learning:** ✅ Captures exactly what the agent got wrong and what the human corrected it to.
- **Granularity:** ✅ Per-field, per-domain, per-platform.
- **Feedback loop:** ✅ Emits correction signals to optimization engine.
- **Gap:** Corrections are stored but not automatically converted into agent rules. A human or periodic job must process them.

**Engineering assessment (8/10):**
- **System Design:** ✅ Simple, focused module. `CorrectionCapture` has one job.
- **Reliability:** ✅ SQLite with WAL mode. Index on `field_label` and `domain`.
- **Security:** ✅ No PII in correction records (values are stored as-is, but this is local SQLite).

**Recommendation:**
1. Add a `correction_to_rule` pipeline: weekly job that converts high-frequency corrections into `AgentRulesDB` entries.
2. Track correction *trends*: if "salary expectation" is corrected 10 times in a row, flag the profile as stale.
3. Corrections should feed into `ats_answer_cache` success/correction counters (they currently don't).

---

### Stage 16: Scan Learning
**Files:** `jobpulse/scan_learning.py`

**What it does:**
- 17-signal scan session learning: time bucket, requests, delay, session age, user agent, VPN, mouse simulation, referrer, search query, pages before block, fingerprint, page load time, outcome, wall type
- Learns verification wall triggers → adaptive cooldowns + recommendations

**Meta-cognitive assessment (8/10):**
- **Learning:** ✅ This is the most meta-cognitive module in the pipeline. It learns its own detection footprint.
- **Adaptation:** ✅ Adaptive cooldowns based on session signals.
- **Self-model:** ✅ Knows which signals trigger blocks and adjusts behaviour accordingly.
- **Anti-detection meta-cognition:** The system is explicitly modelling the *detector's* model of itself.

**Engineering assessment (7/10):**
- **System Design:** ✅ Signal-based architecture. `ScanLearningEngine` is modular.
- **Reliability:** ✅ Session state is tracked. Recommendations are generated, not enforced (safe).
- **Observability:** ✅ Every signal is logged.

**Recommendation:**
1. Feed scan learning recommendations back into `job_scanner.py` (currently they're logged but not auto-applied).
2. Add a "stealth score" per session: predict likelihood of block before it happens and abort early.
3. Cross-platform signal sharing: if LinkedIn blocks after 5 requests, warn before Indeed scan in same session.

---

### Stage 17: Optimization Engine
**Files:** `shared/optimization/`

**What it does:**
- Signal bus for cross-module events
- Trajectory store for step-level timing + outcomes
- Continuous learning infrastructure

**Meta-cognitive assessment (5/10):**
- **Infrastructure:** ✅ Signal bus and trajectory store exist.
- **Consumption:** ❌ Sparse consumption. Many modules log to trajectories but few read from them for policy decisions.
- **Policy learning:** ❌ No reinforcement learning or bandit algorithm for decision optimisation.
- **Gap:** The engine collects data but doesn't yet *act* on it autonomously.

**Engineering assessment (6/10):**
- **System Design:** ⚠️ Underutilised. The infrastructure is there but the business logic consuming it is thin.
- **Reliability:** ✅ Signal bus is asynchronous; producers don't block.

**Recommendation:**
1. Implement a weekly `OptimizationPolicy` job that reads trajectory store + gate_effectiveness + correction_capture and generates config suggestions.
2. Add a contextual bandit for project selection: explore different project combinations, exploit those with higher ATS scores.
3. Use trajectories to train a lightweight model predicting application success from scan-time features.

---

## Cross-Cutting Concerns

### Cost Tracking
| Stage | LLM Calls | Est. Cost per 50 Jobs | Tracked? |
|-------|-----------|----------------------|----------|
| JD Analysis (skill extraction) | 50 | ~$0.50–$2.00 | ✅ |
| Gate 4B (CV scrutiny) | 50 | ~$0.25–$1.00 | ✅ |
| Form Fill (field mapping fallback) | 0–10 | ~$0.00–$0.50 | ✅ |
| Screening Answers (LLM fallback) | 0–20 | ~$0.00–$1.00 | ✅ |
| **Total** | **100–130** | **~$0.75–$4.50** | ✅ |

All LLM calls go through `get_llm()` which integrates `shared.cost_tracker`. Good.

### Security Checklist
- ✅ No PII in source code
- ✅ `assert_prompt_has_wrapped_pii()` before LLM calls
- ✅ Parameterized SQL throughout
- ✅ Rate limiting before submission
- ⚠️ Screening answer cache stores plaintext answers in SQLite (local only, acceptable)
- ❌ `screening_answers.py` LLM prompts include raw profile values — need to verify PII wrapping is enforced

### Test Coverage
- ✅ 1,045 jobpulse tests passing
- ✅ 714 patterns/shared tests passing
- ✅ 63 new screening v2 tests passing
- ⚠️ No integration tests for the full pipeline end-to-end
- ⚠️ `scan_learning.py` tests are likely thin

---

## Top 10 Priority Improvements

| Priority | Improvement | Impact | Effort |
|----------|-------------|--------|--------|
| **P0** | Wire Screening V2 into `native_form_filler.py` | Eliminates hallucinated answers, improves cache hit rate | 1 day |
| **P0** | Integrate `company_reliability` into Gate 4A decisions | Auto-skip ghost companies | 2 hours |
| **P1** | Add per-domain gate threshold adaptation | Better matching for diverse role types | 1 day |
| **P1** | NLP rejection email parsing | Close the learning loop on outcomes | 2 days |
| **P1** | Batch skill graph + field resolver queries | Eliminate N+1 at scale | 1 day |
| **P2** | Auto-convert corrections to agent rules | Reduce human correction burden over time | 2 days |
| **P2** | Predictive expiry model for scanner | Save bandwidth, reduce blocking risk | 1 day |
| **P2** | CV scrutiny calibration loop | Improve LLM reviewer accuracy | 1 day |
| **P3** | Cross-platform field embedding transfer | Learn once, apply everywhere | 3 days |
| **P3** | Weekly optimization policy job | Auto-suggest config changes from data | 2 days |

---

## Conclusion

The JobPulse pipeline is **well-engineered but under-learning**. The infrastructure for meta-cognition exists (correction capture, trajectory store, signal bus, gate effectiveness, company reliability, scan learning, strategy reflection), but the *consumption* of this data for autonomous improvement is sparse. Most learning loops require human intervention or periodic batch jobs that don't yet exist.

**The biggest wins:**
1. **Deploy Screening V2** — immediate quality improvement with strong validation.
2. **Close the outcome loop** — rejection emails and interview outcomes need to automatically feed back into gates, project selection, and company filtering.
3. **Make the optimizer act** — the trajectory store and signal bus are ready; now they need policy jobs that read from them and propose (or auto-apply) config changes.
