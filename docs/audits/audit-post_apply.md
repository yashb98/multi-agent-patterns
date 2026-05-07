# Subsystem 5 — `post_apply` audit

**Scope:** 17 files / 6334 LOC (`jobpulse/post_apply_hook.py` +
`correction_capture.py` + `agent_rules.py` + `auto_rule_generator.py` +
`strategy_reflector.py` + `trajectory_store.py` + `agent_performance.py` +
`drive_uploader.py` + `form_experience_db.py` + `platform_transfer.py` +
`cross_platform_field_transfer.py` + `rejection_analyzer.py` +
`browser_cleanup.py` + `rate_limiter.py` + `pre_submit_gate.py` +
`pipeline_hooks.py` + `process_logger.py`).
**Branch:** `pipeline-correctness-fixes`
**Date:** 2026-05-07
**Auditor approach:** Grep + AST reachability from
`applicator.apply_job()` / `applicator.confirm_application()` /
`post_apply_hook()`. Cross-module signal/DB wiring map. Live tests via
`pytest tests/jobpulse/test_post_apply_hook.py
tests/jobpulse/test_wiring_e2e.py
tests/jobpulse/test_post_apply_integration.py` and a 5-line repro for
the silent-transfer-signal blocker.

The two production entry points are:

1. `applicator.apply_job` (auto-submit path, applicator.py:429): runs the
   hook with `result["success"]=True`.
2. `applicator.confirm_application` (dry-run + manual approval path,
   applicator.py:601): also forces `result["success"]=True` before the
   hook fires.

Neither production caller exercises the failure branch (hook lines
49-83). It survives only via direct test calls
(`test_post_apply_hook.py:178/193/217`).

---

## STEP 1 — Function inventory + wiring categorization

Reach codes follow the prompt convention:
**A** = on apply_job runtime path · **B** = runtime-conditional
(env flag / failure branch / dry-run path) · **C** = reachable only via
tests, CLI scripts, or non-apply agents · **D** = orphan in repo ·
**E** = shadowed/overridden.

### `post_apply_hook.py` (422 LOC) — entry point for all writers

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 26 | `post_apply_hook(result, job_context, form_exp_db_path=None)` | **A** | `applicator.apply_job` (L429), `applicator.confirm_application` (L601), 5 test files |

### `correction_capture.py` (269 LOC)

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 21 | `_normalize_label(label)` | A | `record_corrections` (L100), `get_correction_count` (L162) |
| 30 | `CorrectionCapture._init_db` | A | `__init__` |
| 53 | `CorrectionCapture._connect` | A | every public method |
| 58 | `record_corrections(domain, platform, agent_mapping, final_mapping, *, job_id, source, agent_name)` | **A** | `applicator.confirm_application` (L538), `screening_outcome_recorder.py`, `ai_assist_logger.py` |
| 160 | `get_correction_count(field_label)` | C | only `get_correction_rate` (L189) and `get_high_correction_fields` (L216), both C |
| 170 | `get_correction_rate(...)` | C | only `get_high_correction_fields` (L211), itself C |
| 192 | `get_high_correction_fields(total_fills_by_field, *, threshold, min_samples)` | C | `tests/jobpulse/test_correction_capture.py` only |
| 222 | `get_domain_accuracy(domain)` | C | `form_prefetch.py:113` (cron prefetch path, not apply path) |
| 243 | `get_field_corrections_by_domain(domain, limit)` | C | `form_prefetch.py:116` |
| 253 | `get_skill_correction_values(min_occurrences)` | A | `application_materials.py:134` (CV gen — reachable from apply_job) |

### `agent_rules.py` (385 LOC)

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 23 | `_normalize_domain(value)` | A | `auto_generate_from_correction` (L256), `get_field_overrides` (L357), schema migration (L140) |
| 49 | `AgentRulesDB._init_db` (+ legacy migration + correction-pattern normalization) | A | `__init__` (every importer) |
| 156 | `AgentRulesDB._connect` | A | all public methods |
| 161 | `auto_generate_from_blocker(category, pattern, count, total)` | C | `rejection_analyzer.generate_avoidance_rules` (L332) — fired only by weekly maintenance, not apply path |
| 243 | `auto_generate_from_correction(field_label, agent_value, user_value, domain, platform)` | **A** | `applicator.confirm_application` (L548) for every correction |
| 321 | `get_active_rules(rule_type)` | A | `screening_answers.py:662`, `get_field_overrides` (L358), `get_exclude_keywords`, `get_escalation_fields` |
| 341 | `get_exclude_keywords()` | A | `recruiter_screen.py:46` (Gate 0) |
| 346 | `get_escalation_fields()` | **D / wiring-gap** | tests only — `auto_generate_from_correction` writes `action='escalate'` after 3 corrections, but no production code reads `get_escalation_fields()`, so the escalate action is write-only |
| 351 | `get_field_overrides(domain, platform)` | A | `native_form_filler.py:245` |

### `auto_rule_generator.py` (437 LOC)

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 67 | `from_corrections(domain, platform, *, min_samples, max_rules)` | C | `weekly_optimize.py` only |
| 130 | `_fetch_correction_clusters` | C | `from_corrections` |
| 161 | `_is_trivial_diff` | C | `from_corrections` |
| 168 | `_field_to_pattern` | C | `from_corrections` |
| 178 | `_infer_action` | C | `from_corrections` |
| 203 | `from_trajectories(pipeline, domain, *, min_samples, max_rules)` | C | tests + weekly maintenance |
| 270 | `_mine_trajectory_patterns` | C | `from_trajectories` |
| 322 | `validate_rule(rule, test_cases)` | C | `deploy_batch` |
| 357 | `deploy_rule(rule)` | C | `deploy_batch` |
| 425 | `deploy_batch(rules)` | C | tests + weekly maintenance |

Whole module is C — drained from cron `weekly-optimize`, not apply_job.

### `strategy_reflector.py` (426 LOC)

| Line | Function | Reach | Caller(s) |
|------|----------|-------|-----------|
| 34 | `get_memory_manager()` | A | `_record_failure_episode` (L366) |
| 45 | `_extract_correction_heuristics` | A | `extract_deterministic_heuristics` |
| 62 | `_extract_strategy_distribution_heuristics` | A | `extract_deterministic_heuristics` |
| 97 | `_extract_slow_field_heuristics` | A | `extract_deterministic_heuristics` |
| 121 | `extract_deterministic_heuristics(trajectories)` | A | `reflect_on_application` (L247) |
| 137 | `_build_reflection_prompt(strategy, trajectories)` | B | only via `reflect_with_llm` when det heuristics < threshold |
| 182 | `reflect_with_llm(strategy, trajectories)` | B | `reflect_on_application` (L256) when `len(det_heuristics) < 2` |
| 224 | **`reflect_on_application(store, job_id, job_context, *, llm_threshold)`** | **A** | `post_apply_hook` (L357) |
| 311 | `_feed_experience_memory(strategy, heuristics)` | A | `reflect_on_application` (L305) |
| 351 | `_record_failure_episode(strategy, heuristics)` | A | `reflect_on_application` (L306) |
| 403 | `_compute_strategy_score(strategy)` | A | `_feed_experience_memory` (L323), `_record_failure_episode` (L367) |

### `trajectory_store.py` (786 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 113 | `_is_sensitive_field(label)` | A | `log_field`, `mark_corrected`, `_build_reflection_prompt` |
| 118 | `_normalize_domain(domain_or_url)` | A | every public method |
| 133 | `TrajectoryStore.__init__` | A | `get_trajectory_store` |
| 140 | `_connect` | A | every method |
| 147 | `_ensure_schema` | A | `__init__` |
| 216 | `_get_fernet` | A | `_encrypt_if_sensitive`, `_decrypt_value` |
| 228 | `_encrypt_if_sensitive` | A | `log_field`, `mark_corrected` |
| 236 | `_decrypt_value` | A | `get_trajectories` |
| 253 | `log_field(...)` | A | `native_form_filler` (per-field journal) |
| 285 | `mark_corrected(job_id, domain, field_label, corrected_value)` | A | `correction_capture.record_corrections` (L118) |
| 308 | `get_trajectories(job_id)` | A | `strategy_reflector.reflect_on_application` (L243) |
| 335 | `get_domain_trajectories(domain, *, limit)` | C | tests / debug only |
| 367 | `save_strategy(strategy)` | A | `strategy_reflector.reflect_on_application` (L269) |
| 394 | `get_strategy(job_id)` | C | tests |
| 404 | `get_domain_strategies(domain, *, limit)` | C | tests |
| 416 | `get_platform_strategies(platform, *, limit)` | C | tests |
| 432 | `aggregate_strategy(job_id, job_context, trajectories)` | A | `strategy_reflector.reflect_on_application` (L244) |
| 481 | `save_heuristics(heuristics)` | A | `strategy_reflector.reflect_on_application` (L283) |
| 516 | `get_heuristics(domain, *, platform, include_platform)` | A | `load_heuristics_for_application` (L744-L749) — but **`load_heuristics_for_application` itself has zero production callers** |
| 555 | `record_heuristic_outcome(heuristic_id, succeeded)` | D | nobody calls it. Heuristic application outcome is never recorded — so `times_applied` / `times_succeeded` stay at 0 forever, and `invalidate_stale_heuristics` (which depends on `times_applied >= 3`) is unreachable |
| 569 | `invalidate_stale_heuristics(domain, *, threshold)` | A | `load_heuristics_for_application` (L741) — but again, that function isn't called from the apply path |
| 586 | `decay_confidence()` | C | weekly cron `learning-maintenance` |
| 602 | `prune()` | C | weekly cron |
| 666 | `stats()` | C | metrics endpoint only |
| 691 | `get_trajectory_store(db_path)` | A | `post_apply_hook.py:356` |
| 703 | `_reset_shared_store()` | C | tests |
| 714 | `load_heuristics_for_application(domain, platform, *, store)` | **D / wiring-gap** | imported nowhere in production code; the heuristic-replay loop the docstring promises is a write-only feature |

### `agent_performance.py` (167 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 46 | `AgentPerformanceDB.__init__` | A | `applicator._record_agent_performance` (L92) |
| 51 | `_get_conn` | A | every method |
| 54 | `_ensure_table` | A | `__init__` |
| 68 | `record_session(...)` | A | `applicator._record_agent_performance` (L93) on every apply (auto + confirm path) |
| 118 | `get_summary()` | C | analytics dashboards (`analytics_api`) — not apply path |
| 161 | `get_all()` | C | analytics dashboards |

### `drive_uploader.py` (244 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 27 | `_file_name_prefix()` | A | `upload_cv` (L215) |
| 49 | `_get_drive_service()` | A | `upload_to_drive` |
| 99 | `upload_to_drive(local_path, *, folder_id, filename)` | A | `upload_cv`, `upload_cover_letter` |
| 196 | `upload_cv(cv_path, company)` | **A** | `post_apply_hook.py:138` |
| 223 | `upload_cover_letter(cl_path, company)` | **A** | `post_apply_hook.py:144` |

### `form_experience_db.py` (1006 LOC)

Methods touched on the apply_job path are A; everything else is C/D.

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 24 | `__init__` | A | `post_apply_hook.py:51, 109`; `platform_transfer`, `correction_capture.get_domain_accuracy`, etc. |
| 28 | `_schema_sql` | A | `_init_db_heal` |
| 136 | `_init_db_heal` | A | `__init__` |
| 149 | `_init_db` | A | `_init_db_heal` |
| 300 | `_transfer_engine` (property) | A | `get_failure_reasons`, `get_field_mappings`, `get_container`, `get_timing`, `get_scan_strategy` |
| 307 | `normalize_domain(domain_or_url)` (staticmethod) | A | callers everywhere — `post_apply_hook` (L47, L127), all jobpulse code |
| 314 | `record(...)` | **A** | `post_apply_hook.py:52, 110` (success and failure branches) |
| 377 | `store(...)` | **D** | zero production callers — the "PRAXIS-aware" variant added for content_hash transfer learning is never wired into the apply path |
| 435 | `lookup(domain_or_url)` | A | `correction_capture.get_domain_accuracy`, `native_form_filler`, `form_prefetch`, `page_analyzer` |
| 444 | `lookup_by_content_hash(content_hash, exclude_domain)` | **D** | only called from `tests/jobpulse/test_praxis_memory.py` and `test_form_experience_real.py`. No production caller |
| 465 | `validate_against_live(domain_or_url, live_field_types, live_page_count, *, match_threshold)` | A | `form_prefetch.py:188`, `native_form_filler.py:3537` |
| 526 | `get_platform_aggregate(platform)` | A | `page_analyzer.py:229` |
| 586 | `get_stats()` | C | analytics dashboards |
| 597 | `record_failure_reason(...)` | **A** | `post_apply_hook.py:64` (failure branch) |
| 618 | `get_failure_reasons(domain_or_url, limit)` | A | `form_engine` |
| 651 | `get_platform_failure_stats(platform)` | C | tests / dashboards |
| 667 | `get_field_mappings(domain_or_url)` | A | `form_engine.field_mapper` |
| 688 | `record_fill_technique(...)` | A | `native_form_filler` |
| 715 | `get_fill_techniques(domain_or_url)` | A | `native_form_filler.py:1605` |
| 726 | `get_platform_fill_techniques(platform)` | A | `native_form_filler.py:1605` |
| 739 | `save_field_mappings(...)` | A | `form_engine.field_mapper` |
| 753 | `store_container`, 766 `get_container`, 786 `delete_container` | A | `form_engine.field_scanner` |
| 793 | `store_timing`, 822 `get_timing` | A | `form_engine.timing_collector` |
| 845 | `store_scan_strategy`, 864 `get_scan_strategy` | A | `form_engine.field_scanner` |
| 889 | `log_field_confidence(...)` | A | `form_engine.confidence_scorer` |
| 902 | `get_confidence_calibration(domain)` | **D** | only tests |
| 913 | `store_negative_exemplar(...)` | **D** | only tests |
| 938 | `get_negative_exemplars(domain)` | **D** | only tests |
| 948 | `get_negative_exemplars_by_hash(content_hash)` | **D** | only tests |
| 960 | `store_signal_correction(...)` | A | `native_form_filler.py:3772` |
| 986 | `get_signal_corrections(domain, field_label)` | A | `native_form_filler.py:641` |

### `platform_transfer.py` (396 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 43 | `__init__` | A | `post_apply_hook.py:126`, `FormExperienceDB._transfer_engine` |
| 47 | `_init_schema` | A | `__init__` |
| 82-130 | similarity helpers (`_cosine_similarity`, `_jaccard_index`, `_normalized_page_diff`, `_normalized_levenshtein`, `_token_overlap`) | A | `_compute_pair_signals` |
| 136-199 | data loaders (`_load_form_experience_data`, `_load_timing_data`, `_load_container_data`, `_load_fill_techniques`, `_load_failure_data`, `_load_correction_data`, `_load_navigation_data`) | A | `recompute_similarity_matrix` |
| 205 | `recompute_similarity_matrix(trigger_domain)` | **A** | `post_apply_hook.py:128` |
| 255 | `_compute_pair_signals(...)` | A | `recompute_similarity_matrix` |
| 310 | `get_transfer_data(target_domain, signal_type, min_similarity)` | A | `FormExperienceDB._transfer_engine` lookups |
| 344 | `_get_outcome_params(target, donor, signal_type)` | A | `get_transfer_data` |
| 362 | `record_outcome(target_domain, donor_domain, signal_type, success)` | **D / wiring-gap** | no production caller in `jobpulse/`; only `tests/jobpulse/test_platform_transfer.py`. **Plus** the body emits `signal_type="transfer"` which fails `LearningSignal.__post_init__` validation — even if it were called, the optimization signal is silently dropped at debug level |

### `cross_platform_field_transfer.py` (411 LOC)

The whole module is C. Production code never imports it (verified via
grep — only tests + `weekly_optimize.py` reference the class).

| Line | Function | Reach |
|------|----------|-------|
| 73 | `__init__` | C |
| 80 | `_init_db` | C |
| 107 | `_init_vector_stores` | C |
| 127 | `record_mapping(...)` | C |
| 174 | `_embed_field` | C |
| 186 | `_upsert_to_qdrant` | C |
| 213 | `find_transfers(...)` | C |
| 251 | `_search_qdrant` | C |
| 308 | `_search_sqlite_brute_force` | C |
| 363 | `_text_overlap_ranking` | C |
| 393 | `get_stats` | C |

### `rejection_analyzer.py` (346 LOC)

Drained from weekly cron, not the apply path. All C.

| Line | Function | Reach |
|------|----------|-------|
| 21 | `classify_outcome(status)` | C |
| 75 | `classify_blocker(reason)` | C |
| 117 | `compute_funnel(applications)` | C |
| 126 | `compute_score_by_outcome(applications)` | C |
| 157 | `compute_blocker_frequency(applications)` | C |
| 188 | `generate_recommendations(...)` | C |
| 246 | `generate_full_report(applications)` | C |
| 295 | `generate_avoidance_rules(applications, *, rules_db_path)` | C — fires `agent_rules.auto_generate_from_blocker` from weekly maintenance only |

### `browser_cleanup.py` (171 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 54 | `flush_browser_caches(page)` | A | `applicator.apply_job` post-submit |
| 106 | `_purge_dirs(dir_list)` | A | `cleanup_chrome_profile_caches`, `deep_clean_chrome_profile` |
| 122 | `cleanup_chrome_profile_caches()` | A | `applicator.apply_job` (L692), `confirm_application` (L635) |
| 134 | `deep_clean_chrome_profile()` | A | `restart_chrome` |
| 148 | `should_restart_chrome()` | A | `applicator.apply_job`, `confirm_application` |
| 155 | `reset_app_counter()` | C | scan window start (not apply path) |
| 161 | `restart_chrome()` | A | `applicator` when `should_restart_chrome()` returns True |

### `rate_limiter.py` (228 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 45 | `RateLimiter.__init__` | A | `applicator.apply_job`, `confirm_application` |
| 49 | `_init_db`, 82 `_today`, 87 `_now_iso`, 91 `_get_platform_count` | A | internal |
| 99 | `get_platform_count` | C | analytics |
| 103 | `get_total_today` | A | `can_apply` (L117) |
| 112 | `can_apply(platform)` | A | `applicator.apply_job` pre-check |
| 127 | `record_application(...)` | **A** | `applicator.apply_job` (auto path), `confirm_application` (L510) |
| 177 | `get_remaining()` | C | analytics |
| 187 | `get_application_log(days)` | C | analytics |
| 200 | `should_take_break()` | A | `applicator.apply_job` |
| 205 | `cleanup_old(retention_days)` | C | weekly cron |
| 221 | `reset_daily()` | C | maintenance |

### `pre_submit_gate.py` (269 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 40 | `_yes_no(value)` | A — `_deterministic_consistency_checks` |
| 52 | `_deterministic_consistency_checks(filled, profile)` | A — `check_semantic_correctness` |
| 108 | `PreSubmitGate.__init__` | C — instantiated only by `applicator.scan_and_submit` (which itself is the auto-submit path); see callers |
| 114 | `review(filled_answers, jd_keywords, company_research)` | A — `applicator.apply_job` Phase B and `live_review_applicator` |
| 167 | `check_semantic_correctness(...)` | A — applicator calls it before submit |
| 213 | `_llm_field_judge(...)` | A — `check_semantic_correctness` |

### `pipeline_hooks.py` (142 LOC)

All wrapper functions are B (env-flag gated). They run on the SCAN
path (not apply_job).

| Line | Function | Reach | Env flag |
|------|----------|-------|----------|
| 20 | `feature_enabled(env_var)` | A | n/a |
| 30 | `_ensure_ghost_detector()` | B | indirectly via `with_ghost_detection` |
| 37 | `with_ghost_detection(listings, jd_texts)` | B | `JOBPULSE_GHOST_DETECTION` |
| 71 | `with_archetype_detection(listing)` | B | `JOBPULSE_ARCHETYPE_ENGINE` |
| 96 | `enhanced_generate_materials(...)` | B | `JOBPULSE_ATS_NORMALIZE` (other branch is pass-through) |
| 131 | `with_tone_filter(answer, question, listing)` | B | `JOBPULSE_TONE_FRAMEWORK` |

### `process_logger.py` (230 LOC)

Used in budget/gmail/calendar/dispatcher agents — not on apply_job.

| Line | Function | Reach |
|------|----------|-------|
| 26 | `_get_conn()` | A | every public method |
| 30 | `init_process_db()` | **A (import-time side effect)** | every importer of the module — fires at import via L229 |
| 59 | `ProcessTrail.__init__` | C (apply path) / A (other agents) | `dispatcher`, `swarm_dispatcher`, `gmail_agent`, `calendar_agent`, `budget_agent` |
| 69 | `log_step(...)` | C / A | as above |
| 97 | `step(step_type, step_name, step_input)` | C / A | as above |
| 121 | `finalize(final_output)` | C / A | as above |
| 132 | `get_trail(run_id)` | C | dashboards |
| 143 | `get_recent_runs(agent_name, limit)` | C | dashboards |
| 166 | `get_runs_for_day(day_date)` | C | dashboards |
| 183 | `get_agent_stats()` | C | dashboards |
| 201 | `cleanup_old_trails(retention_days)` | **A (import-time side effect)** | runs at import via L230 — every import issues a DELETE |
| 218 | `_row_to_dict(row)` | C | internal |

---

## STEP 2 — Findings (severity-tagged)

### Blockers

| ID | Location | Description |
|---|---|---|
| 🔴 B-1 | `platform_transfer.py:391` | `record_outcome` emits `signal_type="transfer"` — not in `VALID_SIGNAL_TYPES` (`{correction, failure, success, adaptation, score_change, rollback}`). `LearningSignal.__post_init__` raises `ValueError`, swallowed at debug level. **0 transfer signals** ever reach the bus. Verified by 5-line repro that prints "transfer signals: 0" after `record_outcome(...)`. The producer believes it emitted; the consumer never sees the data. |
| 🔴 B-2 | `post_apply_hook.py:96-103` vs `post_apply_hook.py:410-420` | Before/after measurement is theatrical. `_before` reads `len(result.get("field_types"))`, `result.get("pages_filled")`, `result.get("time_seconds")` from `result`. The hook never mutates `result`, so `_after` reads the same values for those three keys. The other `_after` keys (`drive_cv_uploaded`, `notion_updated`, `nav_learned`, …) have **no `_before` counterpart** — `_tracker.after_learning_action` only computes deltas on `set(before) & set(after)` (see `_tracker.py:178-195`), so they're dropped. Net result: **every post-apply records a zero-delta `learning_actions` row**, blinding the optimization tracker to whether the hook actually did anything. |
| 🔴 B-3 | `agent_rules.py:307-318` | `auto_generate_from_correction` emits `signal_type="adaptation"` with `payload={"field": ..., "old_value": ..., "new_value": ..., "platform": ...}`. The aggregator at `_aggregator.py:341` reads `adapt.payload.get("param", "unknown")` and uses that string in the `adaptation_worked` insight evidence. Every correction-driven adaptation insight therefore says "Adaptation 'unknown' on …", losing the field-label provenance. The matching producer in `auto_rule_generator.deploy_rule` (L411) and `agent_rules.auto_generate_from_blocker` (L231) get the schema right (they both pass `param=`) — only the correction path is broken. |

### Majors (deferred unless adjacent to a blocker)

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-1 | `process_logger.py:229-230` | Module imports trigger `init_process_db()` and `cleanup_old_trails(30)`. Every import (15+ jobpulse modules + tests + scripts) fires a CREATE TABLE + DELETE on `mindgraph.db`. Violates Principle 1 (no import-time side effects). | Touches the daemon startup path; safer to defer to a dedicated change with broader regression run. Logged as a deferred-major. |
| 🔴 M-2 | `agent_rules.py:346-349` (and consumers) | `get_escalation_fields()` returns fields where `auto_generate_from_correction` set `action='escalate'` after 3+ corrections, but **no production code calls it** (only tests). `auto_rule_generator._infer_action` (L189-190) similarly creates "escalate" actions that nothing reads. Result: the rule action column has values, but the form filler / screening pipeline never short-circuit on them. | Pure feature-wiring gap, no incorrect output today. Worklist for a later session. |
| 🔴 M-3 | `trajectory_store.py:555-584`, `714-787` | `record_heuristic_outcome` + `invalidate_stale_heuristics` + `load_heuristics_for_application` are all unreferenced in production code. `times_applied/times_succeeded` stay at 0 forever, `invalidate_stale_heuristics` never finds a candidate, and the GRPO heuristic-replay loop the docstring promises never runs. | Same pattern as M-2 — entire heuristic-replay subsystem is write-only. |
| 🔴 M-4 | `cross_platform_field_transfer.py` (whole module) | No production importer; only tests + weekly_optimize. The cross-platform Qdrant/embedding transfer path is dormant. | Possibly intentional; needs product owner decision. |
| 🔴 M-5 | `form_experience_db.py:377` (`store`), `:444` (`lookup_by_content_hash`), `:902-958` (negative exemplars + confidence calibration) | "PRAXIS-aware" cross-domain content_hash matching is wired only in tests. `post_apply_hook` calls `record(...)` (line 314), never `store(...)`. `content_hash` column exists but is always `''`. | Defer; large-file scope creep. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `correction_capture.py:140`, `:155` | LLM/Optimization signal failure paths use `logger.debug` — fine for transient infra issues, but mask schema regressions. Should be `logger.warning` when the underlying call raises a *non-retryable* error (`ValueError`, `KeyError`). Pure quality issue. |
| 🟡 m-2 | `agent_rules.py:122-127` | Bare `except sqlite3.OperationalError: pass` for ALTER TABLE migration — fine in itself but loses the "column already exists" vs "table missing" distinction. Acceptable. |
| 🟡 m-3 | `cross_platform_field_transfer.py:34, 76-77` | `from typing import Optional` used; `Any` referenced via `Optional[Any]` but `Any` is **not imported**. With `from __future__ import annotations` the annotation never evaluates, but it's still a latent defect for any future `typing.get_type_hints()` consumer. |
| 🟡 m-4 | `post_apply_hook.py:79`, `:299`, `:138` | Session IDs use raw `company` string (which can contain spaces and unicode) inside `f"fe_fail_{company}_…"`. Not security-relevant, but makes optimization.db queries by `session_id LIKE` brittle. |
| 🟡 m-5 | `drive_uploader.py:46` | `_file_name_prefix` falls back to literal `"Resume"` when both ProfileStore and `APPLICANT_*` config are missing. Currently CLI prints a warning at apply time — fine, but the fallback name is also embedded in upload metadata, so a misconfigured deploy will silently upload as `Resume_Acme.pdf`. |
| 🟡 m-6 | `process_logger.py:69-72` | `step_input: str = None` etc. mis-typed as `str` instead of `str | None`. Cosmetic. |

### Nits (record only)

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `form_experience_db.py:28-134` (`_schema_sql`) vs `:149-298` (`_init_db`) | Same DDL written twice — once for the self-healing fallback path, once for normal init. Drift risk if a future ALTER lands in only one. |
| ⚪ n-2 | `agent_performance.py:88-95` | `INSERT` SQL string uses positional placeholders without column count assertion. Adding a column without updating the placeholder list silently inserts shifted values. |
| ⚪ n-3 | `trajectory_store.py:402` | `ApplicationStrategy(**{k: row[k] for k in row.keys()})` — passes raw `success` (int 0/1) to a `success: bool` dataclass field. Python is permissive but type checkers will flag. |

### Dead code (in this scope)

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `correction_capture.py:160-220` | `get_correction_count`, `get_correction_rate`, `get_high_correction_fields` connected island, only test-reached. |
| 💀 d-2 | `agent_rules.py:346-349` | `get_escalation_fields` test-reached only — see M-2. |
| 💀 d-3 | `cross_platform_field_transfer.py` | Whole module, see M-4. |
| 💀 d-4 | `form_experience_db.py:377, 444, 902-958` | "PRAXIS-aware" subsystem, see M-5. |
| 💀 d-5 | `platform_transfer.py:362-396` | `record_outcome` not called in production — see B-1. |
| 💀 d-6 | `trajectory_store.py:555-584, 714-787` | `record_heuristic_outcome`, `invalidate_stale_heuristics`, `load_heuristics_for_application` — see M-3. |

---

## STEP 3 — Cross-module wiring map

### Optimization signals emitted from this subsystem

| Producer | Signal type | Payload keys | Consumer | Schema agreement |
|---|---|---|---|---|
| `post_apply_hook.py:75-80` (failure branch) | `failure` | `error`, `pages_reached` | `_aggregator._detect_systemic_failures` | ✅ — consumer doesn't need `param` |
| `correction_capture.py:127-138` | `correction` | `field`, `old_value`, `new_value`, `platform`, `source` | `_aggregator._detect_systemic_failures` (uses `field`) | ✅ |
| `agent_rules.py:226-233` (`auto_generate_from_blocker`) | `adaptation` | `param="blocker_avoidance"`, `old_value`, `new_value`, `reason` | `_aggregator._detect_adaptation_effectiveness` (reads `param`) | ✅ |
| `agent_rules.py:307-316` (`auto_generate_from_correction`) | `adaptation` | `field`, `old_value`, `new_value`, `platform` | same consumer reads `param` | ❌ B-3 |
| `auto_rule_generator.py:407-419` (`deploy_rule`) | `adaptation` | `param=rule_type`, `old_value`, `new_value`, `reason` | reads `param` | ✅ |
| `form_experience_db.py:364-373` (`record`) | `success` / `failure` | `action`, `adapter`, `pages` | `_detect_systemic_failures` / `_detect_emerging_pattern` | ✅ |
| `platform_transfer.py:388-394` (`record_outcome`) | `transfer` | `donor_domain`, `signal`, `success` | none — `transfer` is **invalid** | ❌ B-1 |
| `strategy_reflector.py:289-300` | `success` / `failure` | `heuristics_extracted`, `fields_total`, `fields_corrected` | `_aggregator` general | ✅ |

### Tracker writes

| Producer | Method | Wired? |
|---|---|---|
| `post_apply_hook.py:101-103` | `before_learning_action("post_apply", domain, _before)` | ✅ |
| `post_apply_hook.py:420` | `after_learning_action(opt_action_id, _after)` | ✅ but **B-2 zero-delta** |

### DB writes

| Producer | DB | Table | Read by |
|---|---|---|---|
| `post_apply_hook` → `FormExperienceDB.record` | `form_experience.db` | `form_experience` | `lookup`, `validate_against_live`, `_load_form_experience_data` |
| `post_apply_hook` → `FormExperienceDB.record_failure_reason` | `form_experience.db` | `form_failure_reasons` | `get_failure_reasons`, `_load_failure_data` |
| `post_apply_hook` → `JobDB.mark_applied` (already audited in subsystem-1 area) | `applications.db` | `applications` | downstream Notion / analytics |
| `post_apply_hook` → `JobDB.save_outcome` | `applications.db` | `application_outcomes` | analytics |
| `post_apply_hook` → `JobDB.update_company_reliability` | `applications.db` | `company_reliability` | Gate 0 |
| `post_apply_hook` → `reflect_on_application` → `TrajectoryStore.save_strategy` | `trajectory.db` | `application_strategies` | reflection display |
| `post_apply_hook` → `TrajectoryStore.save_heuristics` | `trajectory.db` | `heuristics` | **`get_heuristics` is wired but only via `load_heuristics_for_application`, which has no production caller (M-3)** |
| `post_apply_hook` → `NavigationLearner.save_sequence` | `navigation_learning.db` | `sequences` | nav replay (subsystem 3) |
| `correction_capture.record_corrections` | `field_corrections.db` | `field_corrections` | `auto_rule_generator._fetch_correction_clusters`, `get_correction_count`, `_load_correction_data` |
| `agent_rules.auto_generate_from_correction` | `agent_rules.db` | `agent_rules` | `recruiter_screen.get_exclude_keywords`, `native_form_filler.get_field_overrides`, `screening_answers` (correction_override) |
| `platform_transfer.record_outcome` | `form_experience.db` | `transfer_outcomes` | `_get_outcome_params` (Thompson Sampling) |

---

## STEP 4 — Live evidence

### Baseline pytest

```
$ python -m pytest tests/jobpulse/test_post_apply_hook.py \
    tests/jobpulse/test_wiring_e2e.py \
    tests/jobpulse/test_post_apply_integration.py -x --no-header
======================= 11 passed, 17 warnings in 6.56s ========================
```

### B-1 reproducer (transfer signal silently dropped)

```python
# scripts run inline, captured 2026-05-07
from shared.optimization import get_optimization_engine
eng = get_optimization_engine()

try:
    eng.emit(signal_type="transfer", source_loop="t",
             domain="a.com", agent_name="t", payload={}, session_id="t1")
except Exception as exc:
    print("emit raised:", type(exc).__name__, exc)
# → emit raised: ValueError Invalid signal_type 'transfer'.
#   Must be one of: adaptation, correction, failure, rollback, score_change, success

from jobpulse.platform_transfer import PlatformTransferEngine
PlatformTransferEngine(db_path="/tmp/x.db").record_outcome(
    "a.com", "b.com", "field_types", True,
)
# DEBUG:jobpulse.platform_transfer:Transfer optimization signal failed: …
# (signal silently dropped)

print(len([s for s in eng._bus.query() if s.signal_type == "transfer"]))
# → 0
```

### B-2 reproducer (zero-delta before/after)

Inspecting `_before` (line 96-100) and `_after` (line 410-419) shows
the three common keys (`fields_filled`, `pages_filled`, `time_seconds`)
all read from the same `result` dict that the hook never mutates. The
tracker's delta logic at `_tracker.py:178-195`:

```python
common_keys = set(before.keys()) & set(after.keys())
# common_keys = {fields_filled, pages_filled, time_seconds}
# diff = after_val - before_val = 0 for all three
```

Confirmed by adding a debug print to a local pytest run; every call
returns `improvement={fields_filled: 0, pages_filled: 0, time_seconds: 0.0}`.

### B-3 reproducer (adaptation payload key mismatch)

```python
# from agent_rules.auto_generate_from_correction line 307-316
payload={"field": "Salary", "old_value": "30000", "new_value": "45000", "platform": "greenhouse"}

# _aggregator._detect_adaptation_effectiveness line 341
param = adapt.payload.get("param", "unknown")
# → "unknown" — the field label is lost
```

---

## STEP 5 — Fixes applied

### B-1 — transfer signal silently dropped

`shared/optimization/_signals.py:14-22`: added `"transfer"` to
`VALID_SIGNAL_TYPES` with a comment recording the silent-drop incident.
`shared/optimization/_engine.py:460`: appended `"transfer"` to the
`daily_report` enumeration so transfer signals show up in the by-type
breakdown.

Test: `tests/jobpulse/test_platform_transfer.py
::TestThompsonSampling::test_record_outcome_signal_actually_persists`.
It instantiates a real `OptimizationEngine` against a tmp-path
`optimization.db`, calls `PlatformTransferEngine.record_outcome(...)`,
and asserts the signal lands on the bus with the expected payload.
Pre-fix the assertion `len(signals) == 1` would have read 0; the
existing FakeEngine-based test bypassed the validation that
production hits.

### B-2 — zero-delta before/after measurement

`jobpulse/post_apply_hook.py:91-117` (before-block) and
`post_apply_hook.py:405-422` (after-block): replaced the form-fill
metrics (`fields_filled`, `pages_filled`, `time_seconds`) with the
outcomes the hook actually owns (`drive_cv_uploaded`,
`drive_cl_uploaded`, `notion_updated`, `nav_learned`,
`elapsed_seconds`). All four boolean keys baseline to `0` in
`_before` and the after-block writes `int(bool(outcome))` so the
tracker sees deltas like `0→1`. Also fixed `notion_updated` to use
the verified flag from the L228-262 verification block instead of
`bool(notion_page_id)` (which only checked whether a page id was
resolved).

Test: `tests/jobpulse/test_post_apply_hook.py
::test_optimization_before_after_records_meaningful_delta`. It
routes through the real `OptimizationEngine`, fires the hook, and
asserts at least one common-key delta is non-zero. Pre-fix every
delta was 0 because the only common keys read from an unchanging
`result` dict.

### B-3 — adaptation payload key mismatch

`jobpulse/agent_rules.py:307-329`: `auto_generate_from_correction`
now emits `payload={"param": "correction_override", "field": ...,
"old_value": ..., "new_value": ..., "platform": ...}`. The aggregator
at `shared/optimization/_aggregator.py:341` reads
`payload.get("param", "unknown")` for the `adaptation_worked`
insight evidence — pre-fix every correction-driven adaptation
insight reported `Adaptation 'unknown' on …`. The blocker path
(`auto_generate_from_blocker` at L226-232) and
`auto_rule_generator.deploy_rule` (L411) already used `param`; this
brings the third producer in line.

Tests: `tests/jobpulse/test_agent_rules.py
::TestAdaptationSignalSchema::test_correction_emits_adaptation_with_param_key`
and `test_blocker_emits_adaptation_with_param_key`. The first asserts
the new key is present; the second locks in that the
already-correct blocker path keeps its `param`.

### Verification

```
$ python -m pytest tests/jobpulse/ \
    -k "post_apply or platform_transfer or agent_rules or correction \
        or form_experience or learning_loop or wiring or strategy_reflect \
        or trajectory" --no-header
======== 335 passed, 4 skipped, 1740 deselected, 41 warnings in 37.62s =========
```

---

## STEP 6 — Remaining work (worklist S5 entries)

### Deferred majors (per advisor, scope-creep risk)

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-1 | `process_logger.py:229-230` | Module-level `init_process_db()` + `cleanup_old_trails(30)` fire at every import (15+ jobpulse modules import this). Import-time side-effect is a Principle 1 violation, and the `DELETE` runs on every cron tick / test-runner startup. | Touches daemon startup path; needs broader regression run. Revisit when next touching `process_logger`. |
| 🔴 M-2 | `agent_rules.py:346-349` (and consumers) | `get_escalation_fields()` returns fields with `action='escalate'` (set by `auto_generate_from_correction` after 3+ corrections), but **no production caller** consumes it. `auto_rule_generator._infer_action` (L189-190) similarly produces "escalate" actions that nothing reads. Result: the action value is written but the form filler / screening pipeline never short-circuit on it. | Pure feature-wiring gap; no incorrect output today. Worklist for the form_fill_dispatch / screening_pipeline session that re-touches consumers. |
| 🔴 M-3 | `trajectory_store.py:555-584`, `:714-787` | `record_heuristic_outcome` + `invalidate_stale_heuristics` + `load_heuristics_for_application` are all unreferenced in production code. Heuristics get *written* by `strategy_reflector`, but `times_applied` / `times_succeeded` stay at 0 forever, `invalidate_stale_heuristics` finds no candidates, and the GRPO replay loop the docstring promises never runs. | Same pattern as M-2 — entire heuristic-replay subsystem is write-only. Defer until owner decides whether to wire or delete. |
| 🔴 M-4 | `cross_platform_field_transfer.py` (whole module) | No production importer; only `tests/` + `weekly_optimize.py` reference the class. Cross-platform Qdrant/embedding transfer dormant. | Possibly intentional; needs product owner decision. |
| 🔴 M-5 | `form_experience_db.py:377` (`store`), `:444` (`lookup_by_content_hash`), `:902-958` (negative exemplars + confidence calibration) | "PRAXIS-aware" cross-domain `content_hash` matching wired only in tests. `post_apply_hook` calls `record(...)` (line 314), never `store(...)`. The `content_hash` column always carries `''`. | Defer; large-file scope creep. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `correction_capture.py:140`, `:155` | LLM/Optimization signal failure paths use `logger.debug`; should be `logger.warning` for non-retryable errors so future schema regressions surface. |
| 🟡 m-2 | `agent_rules.py:122-127` | Bare `except sqlite3.OperationalError: pass` for ALTER TABLE migration loses "column already exists" vs "table missing" distinction. Acceptable but could log. |
| 🟡 m-3 | `cross_platform_field_transfer.py:34, 76-77` | `Optional[Any]` referenced but `Any` not imported; latent defect (only saved by `from __future__ import annotations`). |
| 🟡 m-4 | `post_apply_hook.py:79`, `:299`, `:138` | Session IDs use raw `company` string with spaces/unicode → brittle `LIKE` queries on `optimization.db`. |
| 🟡 m-5 | `drive_uploader.py:46` | `_file_name_prefix` falls back to literal `"Resume"` when both ProfileStore and `APPLICANT_*` config are missing — silent name drift. |
| 🟡 m-6 | `process_logger.py:69-72` | `step_input: str = None` etc. mis-typed as `str` instead of `str | None`. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `form_experience_db.py:28-134` (`_schema_sql`) vs `:149-298` (`_init_db`) | Same DDL written twice — drift risk if a future ALTER lands in only one. |
| ⚪ n-2 | `agent_performance.py:88-95` | `INSERT` SQL uses positional placeholders without column count assertion. |
| ⚪ n-3 | `trajectory_store.py:402` | Raw `success` int passed to `success: bool` dataclass field. |

### Dead code

| ID | Location |
|---|---|
| 💀 d-1 | `correction_capture.py:160-220` — `get_correction_count`, `get_correction_rate`, `get_high_correction_fields` (test-only island) |
| 💀 d-2 | `agent_rules.py:346-349` — `get_escalation_fields` (see M-2) |
| 💀 d-3 | `cross_platform_field_transfer.py` (whole module, see M-4) |
| 💀 d-4 | `form_experience_db.py:377, 444, 902-958` — PRAXIS-aware subsystem (see M-5) |
| 💀 d-5 | `platform_transfer.py:362-396` — `record_outcome` has no production caller despite the signal-emit path now working post-B-1 |
| 💀 d-6 | `trajectory_store.py:555-584, 714-787` — `record_heuristic_outcome`, `invalidate_stale_heuristics`, `load_heuristics_for_application` (see M-3) |
