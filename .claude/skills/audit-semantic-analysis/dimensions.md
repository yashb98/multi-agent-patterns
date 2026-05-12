# Comprehensive Dimensions for Semantic-Analysis Correctness

Heavy reference for `audit-semantic-analysis`. Use this when auditing a touchpoint and you need the full checklist. The skill's main `SKILL.md` summarises the 12 categories; this file lists every dimension with a pass signal and a live-run verification method.

A touchpoint can be marked `OK` only when every applicable dimension passes. "Applicable" varies — e.g. a regex-free structural classifier skips categories E/F; an LLM call skips D2 (semantic threshold). Mark `N/A` deliberately, never silently.

## Live-Evidence + Correctness-Validation rule (governs every dimension below)

**Every PASS in this checklist must satisfy two halves**:
1. **Live evidence** — backed by a real end-to-end live apply run on a real public URL.
2. **Correctness validation** — the auditor confirms the executed result is *the right thing for this context*, not just that it executed.

Mechanical execution evidence (a row exists, a log line was written, a checkbox ticked) is NOT sufficient on its own. Per `SKILL.md → Live-Evidence + Correctness-Validation Rule`.

The "Live-run verification" column in every table below names the **artefact** that proves execution. To promote any dimension to `PASS`, the auditor must additionally inspect that artefact and confirm:
- **Right input** — the decision was given the correct context to begin with.
- **Right mechanism** — the tier that produced the answer was appropriate for the input difficulty.
- **Right output** — the value/category returned is actually correct for *this* JD/profile/page (cross-checked against profile DB, JD content, page DOM, or LLM-as-judge with a written rubric).
- **Right downstream consumption** — the consumer used the value correctly.

If the auditor cannot answer all four with concrete evidence, the dimension is `UNVERIFIED`. If any answer is "no", the dimension is `FAIL`.

### Acceptable live-run artefacts (Part 1)
- A line in the `apply_job(url)` log from a real run.
- A row in a real `data/*.db` file (queried with sqlite3, not mocked).
- A DOM readback (`page.input_value()` / `locator.text_content()`) captured during a live run.
- A Notion page / Drive URL / Telegram message produced by a live run.
- A row in `data/db_observability.db` from a real apply.
- A row mined from a prior live-run audit deliverable (`docs/audits/live-e2e-*.md`).

### Acceptable correctness-validation methods (Part 2)
- **Ground-truth join** — query the profile DB / screening cache / agent_rules DB for the expected value; compare to the live-run output. PASS only if equal (after normalisation).
- **LLM-as-judge with rubric** — load the live-run input + output + contract, ask an LLM to judge "is this the correct answer for this context?" with a written rubric. Disagreement = FAIL. Use a different model or a higher-stakes mode for judge than for the original decision.
- **Domain-reasonableness review** — for outputs that don't have ground truth (e.g. CV scrutiny "strengths"), the auditor (human or AI) judges whether the output is what a senior practitioner would write. Apply consistent rubric across runs.
- **Cross-URL consistency** — run the same decision on a second real URL with a different ATS / role; if the outputs are inconsistent in a way that suggests the first was wrong, demote the first to FAIL.

### Not acceptable as evidence (either part)
- Any pytest output, even with `@pytest.mark.live` if the test mocks any dependency.
- Static analysis (find_symbol / grep / "I read the code").
- Cached snapshots that pre-date the change being verified.
- "The row exists, therefore the value is right" — read the row's contents and validate.
- "The cache hit on the second run" — proves cache-key stability, not value correctness.
- "The downstream consumer didn't error" — proves type compatibility, not semantic correctness.

## How to apply

For each touchpoint:
1. Enumerate the dimensions that apply (the table at the bottom of this file maps mechanism→dimensions).
2. For each, supply BOTH:
   - **Evidence pointer** to a live-run artefact (apply-log line, sqlite3 query, DOM readback, Notion page URL).
   - **Correctness check** — the ground-truth/judge/reasonableness validation that confirms the artefact represents a *correct* decision.
3. Record `PASS / FAIL / UNVERIFIED / N/A`. Mechanical-only evidence without correctness validation = `UNVERIFIED`. Correctness disagreement = `FAIL`.
4. The touchpoint's status (`OK / OK (graceful) / GAP / IN-FLIGHT / UNVERIFIED`) is derived from its weakest applicable dimension.

### How AI agents validate correctness

When this skill is invoked by an AI agent (subagent or future Claude session), the agent MUST:
- Read live-run artefacts in full (not just count them).
- For each PASS candidate, run the four-question correctness check explicitly and write the answer in the audit deliverable.
- Disagree with the live-run output when it's wrong, even if all checkboxes are green.
- Use LLM-as-judge for any output-quality dimension; ground-truth joins for any factual dimension.
- Refuse to mark `PASS` when ambiguous; use `UNVERIFIED` and require a second live run.
- Surface FAIL findings prominently — "I would not approve this answer because X" is the deliverable, not a green tick.

---

## A. Foundation — Models, Providers, Determinism

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **A1** | Embedder model is version-pinned | `OLLAMA_EMBED_MODEL=bge-m3@<digest>` not `:latest`; same value across components | Compare `OLLAMA_EMBED_MODEL` env in production process to value used at index time of stored vectors. |
| **A2** | LLM model selection per stakes | High-stakes use full model, classification uses mini, agent_name set | `cost_tracker` report grouped by `agent_name` shows correct model per call site. |
| **A3** | Tokenizer compatibility | Truncation by tokens (not chars) for inputs near model limit | Log a warning when `prompt_tokens / model_max > 0.9`. |
| **A4** | Provider availability + graceful degradation | Local→cloud fallback wired; embedder-None path returns sensible default | Force `OLLAMA_HOST` offline on a live run; verify components still produce answers (degraded but functional). |
| **A5** | Determinism for cacheable calls | `temperature=0`, no `top_p` randomness for classification/extraction | Same input twice → cache hit on second call (verify in audit log). |
| **A6** | Embedder singleton sharing | Every component uses `_get_embedder()` from `shared.semantic_utils` | grep for `MemoryEmbedder()` constructions outside `semantic_utils` — should be zero. |
| **A7** | Vector dimension consistency | All stored vectors match live embedder's `dims` | On startup, sample-load 1 vector per DB and compare to `embedder.dims`; fail loud on mismatch. |
| **A8** | Cold-start behaviour | First-run on a brand-new domain produces a usable answer (LLM tier reaches when no learned data) | Run `apply_job` on a domain never seen before; verify decision audit log shows the LLM-tier path fired. |
| **A9** | BGE-M3 enforcement (no silent MiniLM fallback) | BGE-M3 (1024-dim) is the only embedder used for semantic decisions; MiniLM-384 fallback either removed or made loud-fail (raises, not silently writes 384-dim vectors that mismatch the 1024-dim Qdrant collections) | Live `curl http://localhost:11434/api/embeddings` returns dim=1024; query Qdrant collections after a live run and verify every vector is 1024-dim; grep `_embedder.py` for the MiniLM fallback path — if present and reachable on BGE-M3 unavailable, that's a P1 GAP. The 2026-05-10 live-e2e session reindexed 7,063 vectors to BGE-M3; ongoing protection requires removing or hardening the fallback. |

## B. Input Hygiene — Sanitization, Truncation, PII, Multilingual

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **B1** | External text sanitised before LLM | `sanitize_user_input` called on JD/page/screening-question before prompt assembly | grep every LLM call site for `sanitize_user_input`; missing on external-text inputs = FAIL. |
| **B2** | Input truncation bounded | All free-text inputs sliced (`cv[:3000]`, `html[:2000]`, `profile_summary[:1500]`) | Log a metric `truncated=True` and verify it fires on real long inputs in production. |
| **B3** | PII minimisation | Only profile fields the prompt actually consumes are passed | Read each prompt, list fields used; cross-check against the profile dict passed in. |
| **B4** | Multilingual handling | Non-English JD/page works; embedder is multilingual (bge-m3 is) or LLM tier covers | Apply on a German/French JD live; verify Gate 1 skill match doesn't silently drop. |
| **B5** | Encoding | UTF-8 throughout; emoji / RTL / smart-quotes don't break parsing | Apply on a JD with unicode characters; verify CV/CL render correctly. |
| **B6** | Empty / edge inputs | Empty string, whitespace, single char don't crash; return structured "skip" | Inject empty/whitespace into a prompt; verify graceful handling, not exception. |
| **B7** | Schema validation at boundary | Inputs typed (TypedDict / dataclass) before reaching the decision | Read the function signature: `dict[str, Any]` is FAIL; typed model is PASS. |

## C. Anchors & Prototypes — Coverage, Versioning, Tests

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **C1** | Anchor coverage | Anchor set covers every output category the component can return | Read the `Enum` / output set; every value has ≥1 anchor sentence. |
| **C2** | Anchor disambiguation | No two anchors are within `min_score` margin of each other on real inputs | Embed all anchors; compute pairwise cosine; flag pairs > 0.85. |
| **C3** | Anchor freshness | Anchors reviewed when the relevant domain's pages change | Each anchor set has a "last_reviewed" date or version tag; drift > 90 days = FAIL. |
| **C4** | Anchor versioning in cache key | Cache invalidates when anchor set changes | Cache key includes hash of anchor list. |
| **C5** | Anchor golden test coverage | `tests/jobpulse/test_semantic_quality.py` has ≥10 cases per anchor type | Run the suite; per-component accuracy ≥90%. |
| **C6** | Negative examples | Inputs that should NOT match any anchor are tested | Golden set includes "unrelated text"→ no_match cases; component returns "I don't know". |
| **C7** | Real-embedder validation | Golden set runs against the production embedder, not mocked vectors | `test_semantic_quality.py` currently mocks vectors — promote to `@pytest.mark.live` with real bge-m3. |

## D. Mechanism & Threshold — Tier, Calibration, OOD, Cross-Component Signals

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **D1** | Tier order cheap→expensive | Exact match → alias → embedding → LLM | Read code; reordered tiers (LLM first) = FAIL. |
| **D2** | Threshold calibrated, not magic | `min_score` loaded from `get_adaptive_weights` or DB, not hardcoded | grep for `min_score=0.` literals — every one is a candidate FAIL. |
| **D3** | Adaptive threshold updates from outcomes | `record_weight_outcome` called on success/failure | Run live applications; query `data/adaptive_weights.db` and verify rows added per component. |
| **D4** | OOD ("I don't know") path | When no tier passes, return structured "unknown" → escalate to LLM/human | Inject deliberately-OOD input; verify component does NOT silently return least-bad match. |
| **D5** | Confidence propagation | Caller sees `confidence` on the result and routes accordingly | Result type includes `confidence: float` field; downstream uses it. |
| **D6** | Tie-breaker logic | Top-2 within threshold margin → escalate | Verify with a near-tie input that LLM tier fires. |
| **D7** | Cross-component signal sharing | E.g. screening_intent → option_aligner gets intent hint | Read function signatures; isolated components = FAIL when signal would help. |
| **D8** | No regex for semantic decisions | Regex used only per `.claude/rules/jobpulse.md` allowed list | grep `re\.(search|match|compile)` in semantic functions = FAIL unless demoted to graceful fallback. |
| **D9** | Profile + JD context drives every value-producing decision | Decision = f(profile, JD, page, learned-corrections); not a static answer; not a profile-blind or JD-blind constant | Two URLs with materially-different JD context (e.g. UK + US) from the same profile must produce **different** answers when context warrants it. Cache key includes `profile_state_hash` and `jd_context_hash`; verify in audit log that different contexts produced different cache entries. |
| **D10** | Profile-state changes invalidate dependent caches | When profile.visa_status / location / salary / notice_period changes, every cached decision that used that field is invalidated | Bump a profile field on a test profile; verify next live apply does NOT serve the stale cached answer. |

## E. Prompt Construction (when mechanism = LLM or Hybrid)

(The 8 dimensions from the SKILL "Prompt-level audit" section, restated here for completeness.)

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **E1** (W1) | Wrapper | `cognitive_llm_call` / `smart_llm_call` / `get_llm()` — no direct `ChatOpenAI()` | grep `ChatOpenAI(\|client.chat.completions.create` outside wrappers = FAIL. |
| **E2** (W2) | Message structure | `[SystemMessage, HumanMessage]` proper list | Flattened `f"SYSTEM:\n...\nUSER:\n..."` = DEGRADED. |
| **E3** (C1) | Context payload completeness | Every needed input is interpolated | Read the prompt and check against the decision's information requirements. |
| **E4** (C2) | Truncation on every free-text input | Explicit `[:N]` slicing | Log truncation events; verify in production. |
| **E5** (O1) | Output schema | `response_format={"type":"json_object"}` or tool_use | Raw text without parser = DEGRADED. |
| **E6** (O2) | Output validation | Parsed result cross-checked against allowed values | `OptionAligner` / enum check / dataclass parse with try/except. |
| **E7** | Anti-leak guard | "Never mention you are an AI" / similar | Grep prompts; missing on user-facing-output prompts = FAIL. |
| **E8** | Few-shot from learned experience | Top-K relevant `ExperienceMemory` / `AgentRulesDB` entries injected | Read prompt code; `inject_examples` / equivalent must run before prompt assembly. |
| **E9** | System role explicit | SystemMessage has clear role definition | Read text; vague "you are helpful" = DEGRADED. |
| **E10** | Stop sequences set when relevant | `stop=` parameter for structured output | Optional. |

## F. Caching

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **F1** | Cache mechanism | Semantic > hash > LRU > none (per call frequency) | "no cache" only for one-shot recovery. |
| **F2** | TTL aligned with input freshness | CV scrutiny 30d, page reasoning 7d, screening 90d (or stable) | Read TTL; production cache age in DB. |
| **F3** | Cache key includes prompt-template version + profile-state + JD-context | Hash of system prompt + anchor set + model id + `profile_state_hash` + `jd_context_hash` | When a prompt template changes, cache invalidates. When profile state changes (visa, location, salary), dependent decisions invalidate. When JD context differs (different country, different role-level), the cache must NOT serve another JD's answer. Verify on a live run by applying the same question to two URLs with different JD contexts; cache must produce different entries. |
| **F4** | Cache invalidation on logic change | Bumping a version constant clears stale entries | Manual cache-clear path exists. |
| **F5** | Cache hit-rate monitored | `cache_hit_rate` metric per component | Dashboard or daily log line. |
| **F6** | Cold-start safe | Empty cache produces correct first-run answer (no off-by-one) | Wipe cache DB; live run still works. |

## G. Reliability & Fallback

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **G1** | Retry with exponential backoff | `smart_llm_call` provides this | Verify config for 3 attempts × 5/10/15s on transient errors. |
| **G2** | Circuit breaker | Trips after N consecutive failures | `shared/llm_retry.py:_CircuitBreaker` consulted before retries. |
| **G3** | Timeout per call | Explicit `timeout=30` not provider default | grep `timeout=` on every `get_llm` call. |
| **G4** | Provider fallback | Local→cloud, primary→backup | Read fallback paths; e.g. `is_local_llm() → force_cloud=True`. |
| **G5** | Structured error degradation | Returns typed error result, not silent `pass` or raw string | Per `.claude/rules/error-handling.md`. |
| **G6** | Human escalation when all else fails | Telegram alert + bounded poll wait (per jobs.md security wall pattern) | Per `.claude/rules/jobs.md → Security Wall Bypass`. |
| **G7** | Bounded loops on retry/recovery | No unbounded `while`; max iteration count enforced | Per seven-principles #4. |
| **G8** | Resource cleanup in finally | Browser, DB connections, files cleaned on exception | `try/finally` blocks reviewed. |

## H. Observability & Audit

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **H1** | Per-decision audit log | One row per semantic decision: `(application_id, component, input, mechanism, threshold, score, output, validation_result, confidence)` | Query `data/semantic_decisions.db` after a live application; row count == decisions made. |
| **H2** | Cost tracking per call | `agent_name` set; recorded in `cost_tracker` | `python -m jobpulse.runner cost-report` shows per-component spend. |
| **H3** | Latency p50/p95 per call site | Timing logged | Daily report; alert when p95 > SLA. |
| **H4** | Confidence distribution monitored | Histogram per component | Dashboard; alert when median confidence drops > 0.1 over 7-day window. |
| **H5** | Decision log level appropriate | INFO for major decisions, DEBUG for sub-checks | Read logger calls in code. |
| **H6** | Replay capability | Reconstruct decision chain from `application_id` | Given a failed apply, can you re-derive every semantic step? |
| **H7** | Trace ID propagation | Same `application_id` / `request_id` across components | Search logs for the ID; appears in every relevant component. |
| **H8** | Decision linked to outcome | Apply success/failure label propagates back to its decisions | Join `semantic_decisions` to `applications` table on `application_id`. |

## I. Learning Loop

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **I1** | Correction capture | Human corrections recorded in `CorrectionCapture` | Make a correction during dry-run; query `field_corrections.db`. |
| **I2** | Correction routing | Correct DB receives data: fill→AgentRulesDB, quirk→GotchasDB, nav→NavigationLearner, screening→cache | Per CLAUDE.md; verify each landing site. |
| **I3** | Learning consumption | Stored rules actually fire on next run | Re-run same form; verify the previously-corrected field is correct without intervention. |
| **I4** | Continual recalibration | Thresholds update from evidence | `record_weight_outcome` called on every decision outcome. |
| **I5** | Strategy reflection | Reflexion fires on failures | `_reflexion.py` triggered post-failure; new heuristic stored. |
| **I6** | Cross-domain learning | Lessons from one platform apply to another via ExperienceMemory | Verify by querying ExperienceMemory after Greenhouse run, then running on Lever. |
| **I7** | Forgetting | Stale corrections decay (LRU eviction by quality×0.6 + recency×0.4) | Verify ExperienceMemory eviction matches policy. |
| **I8** | A/B testing infrastructure | New prompt vs old, decided by outcome data | `ab_testing.py` is wired and reports back. |

## J. Quality Assurance

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **J1** | Unit tests per component | Coverage on the decision function | `pytest --cov` shows >70% for the file. |
| **J2** | Integration tests with real I/O | `@pytest.mark.live`, real JD/CV/page | Run `pytest -m live`; passes. |
| **J3** | End-to-end live tests | Real apply on a real JD, full chain verified | A canary domain that runs nightly. |
| **J4** | Regression tests for prior failures | Each fixed bug has a test that re-runs | Open `tests/jobpulse/test_*regression*.py`. |
| **J5** | Drift detection in production | Golden re-eval on schedule, alert on degradation | Cron-scheduled accuracy check + Telegram alert. |
| **J6** | CI gate on accuracy | Merge blocked if `test_semantic_quality.py` accuracy drops below 90% | GitHub Action enforces. |
| **J7** | Adversarial / red-team tests | `shared/adversarial/_injection_tester.py` covers prompt injection | Run injection tester per LLM call site. |
| **J8** | Real embedder in golden tests | Tests run against bge-m3, not mocked vectors | Promote `test_semantic_quality.py` to `@pytest.mark.live`. |

## K. Live-Run Verification (the user's emphasis)

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **K1** | Real-application audit log written | Every semantic decision per `application_id` recorded | After live apply, query `data/semantic_decisions.db` and reconstruct. |
| **K2** | Post-application replay | Given a failure, replay the chain and identify the broken step | Tooling: `python -m jobpulse.runner replay-decisions <application_id>`. |
| **K3** | Telegram alert on low confidence | Decisions below threshold notify human in real time | Watch live; verify alert fires on a known-borderline JD. |
| **K4** | Notion column for decision trace | Per-application: which page-type was detected, which intents matched, which screening answers came from cache vs LLM | Notion update path includes decision summary. |
| **K5** | Dry-run-first verified | Human reviews before submit; corrections captured | Per CLAUDE.md mandatory rule. |
| **K6** | Failure attribution | "This apply failed because X decision returned Y" | Join audit log with apply outcome. |
| **K7** | Outcome → decision feedback loop | Applied/Rejected/Interview status propagates back to the decisions made for that apply | Cron job updates `semantic_decisions` outcome column from Notion / Gmail follow-up. |
| **K8** | Multi-day learning verification | Same JD seen twice → second decision improves (cache hit / threshold relaxed) | Same domain, two consecutive days; verify second run reuses learning. |

## L. Cross-Cutting

| ID | Dimension | Pass signal | Live-run verification |
|---|---|---|---|
| **L1** | Security — no PII in source | All PII from DBs at runtime | Per `.claude/rules/pii-policy.md`. |
| **L2** | Privacy — PII redacted in logs | Logger calls truncate / mask PII | grep `logger.*profile\|logger.*answer` for un-redacted PII. |
| **L3** | Cost ceiling | Hourly/daily LLM cost cap with circuit-break | Verify `cost_tracker` enforces a budget. |
| **L4** | Rate-limit handling | 429 backoff per provider | Test by simulating 429; verify retry + backoff. |
| **L5** | Explainability | "Why was X chosen?" answerable for every decision | Reasoning logged in audit log. |
| **L6** | SSRF protection | URL fetches validate scheme + host | Per seven-principles #5. |
| **L7** | Parameterised SQL | No f-string SQL in any query semantic decisions depend on | grep for `f"SELECT` / `f"INSERT`. |
| **L8** | Reproducibility | Same input → same output (caches key on inputs only) | Two runs of `replay-decisions` on the same `application_id` produce the same trace. |

---

## Applicability matrix (which dimensions per mechanism)

| Mechanism | Categories that apply | Skip |
|---|---|---|
| Regex / Hardcoded (semantic) | A1, A6, B (relevant), C, D8, H, J, K, L | E, F, G (LLM-specific), I (some) |
| Embedding only | A1-A8, B, C, D1-D7, F, G (relevant), H, I, J, K, L | E (LLM-specific) |
| LLM only | A1-A5, A8, B, D8, E (all 10), F, G, H, I, J, K, L | C (anchor-specific) |
| Hybrid (embedding → LLM) | All categories | (none) |
| Structural (DOM, format) | A1 (where embedder used), B (encoding), G, H | C, D, E, F, I (semantic-specific) |

## Total: 73 dimensions across 12 categories.

Mark each `PASS / FAIL / UNVERIFIED / N/A`, with a live-run evidence pointer + a correctness-check note.

---

## Profile-Driven Decisions — full decision-context table

The Profile-Driven rule (SKILL.md rule 4) requires every value-producing decision to be `f(profile, JD, page context, learned corrections)`. Concrete contexts per decision:

| Decision | Contexts that determine the answer |
|---|---|
| Visa sponsorship | `profile.visa_status` × `profile.visa_expiry` × `jd.location` × `jd.start_date` |
| Authorisation to work | `profile.work_auth[country]` × `jd.country` |
| Salary expectation | `profile.expected_range` × `jd.role_level` × `jd.location_currency` × `jd.market_rate` |
| Notice period | `profile.current_notice_period` (NOT a static "1 month") |
| Relocation answer | `profile.willing_to_relocate` × `profile.current_city` × `jd.location` × `jd.remote_policy` |
| Years of experience | `profile.experience` filtered by `jd.role_relevance` (DA experience → 3y if JD asks DA; → 2y if JD asks DE) |
| DEI answers | per-question `profile.disclosure_preferences` × `company.policy` |
| Languages | intersection of `profile.languages` × `jd.required_languages` × `jd.preferred_languages` |
| CV role profile | embedding similarity `jd.role` ↔ `profile.role_templates` |
| "Also proficient in" extras | `jd.required_skills` − `profile.featured_skills` ∩ `profile.proficient_skills` |
| Project selection | embedding overlap `jd.required_skills` ↔ `profile.projects[i].skills` |
| Hiring-manager message | LLM with `(profile, jd, company)` context — never a template |
| Cover letter content | LLM with `(profile, jd, company)` context — never a template |

### Worked example — visa sponsorship

Profile: UK-based candidate, Graduate Visa, expires 2028.

- Apply to UK job, start date 2026 (before visa expiry) → correct answer **"No"** (already authorised).
- Apply to US job → correct answer **"Yes"** (needs H-1B / equivalent).
- Apply to UK job, start date 2029 (after visa expiry) → correct answer **"Yes"** (will need sponsorship before start).

Three different correct answers from the same profile. A hardcoded `"Yes"` is wrong on case 1; a hardcoded `"No"` is wrong on cases 2 & 3; a cached `"Yes"` from a US apply served on a UK apply is wrong. The decision MUST be computed per-(profile, JD), with `profile_state_hash` + `jd_context_hash` in the cache key.

---

## How AI agents validate correctness

When this skill is invoked by an AI agent (subagent or future Claude session), the agent MUST:

- Read live-run artefacts in full — not just count them.
- For each PASS candidate, run the four-question correctness check explicitly and write the answer in the audit deliverable.
- Disagree with the live-run output when it's wrong, even if all checkboxes are green.
- Use **LLM-as-judge with a written rubric** for any output-quality dimension (page classification, screening answer, CV scrutiny, role-profile selection); use **ground-truth joins** (profile DB / screening cache / agent_rules DB) for any factual dimension.
- Treat ambiguous outputs as `UNVERIFIED` — never PASS. Force a second live run on a different URL/profile to disambiguate.
- Surface FAIL findings prominently — "I would not approve this answer because X" is the deliverable, not a green tick.
- Refuse to optimise for green checkboxes. A 60% PASS with real findings beats a 100% PASS that lowered the bar.

---

## Rationalisations to avoid

The audit's quality depends on rejecting these excuses. When you catch yourself or another agent making one, STOP and re-classify.

### Mechanism-level

| Excuse | Reality |
|---|---|
| "It's just a fast-exit optimisation." | Only OK if the embedding/LLM tier is reached when the literal misses. Verify the fall-through on a live run. |
| "The regex is for normalisation." | Normalisation = whitespace/punctuation/case. Pattern-matching button text or labels is NOT normalisation. |
| "Not enough test cases to migrate yet." | Mark IN-FLIGHT with a graceful-demotion plan, not OK. |
| "It's structural — DOM-only." | If the same code branch reads `label.text` and decides intent, it's semantic. |
| "We use LLM as fallback if regex fails." | Inverted from policy. Embedding/LLM is primary; regex is the fallback. Reorder. |
| "0 regex, so it's fine." | Hardcoded dicts and string-equality routing are the same violation in different syntax. Read the code. |

### Live-evidence

| Excuse | Reality |
|---|---|
| "The unit test is comprehensive — effectively a live test." | If it imports `MagicMock`, redirects DBs to `tmp_path`, or mocks the embedder, it is not live. `UNVERIFIED`. |
| "We have a `@pytest.mark.live` test that uses real I/O." | Only the `apply_job(url)` path counts. A `live` marker without a real apply doesn't satisfy the rule. |
| "Live apply is slow / costs money." | <$0.10 per apply. The cost of a wrong `OK` on production (rejected JD, leaked PII) is multiple orders higher. |
| "The page hasn't changed — cached snapshot is fine." | Real DOM is the source of truth. Snapshots are useful for development, not sign-off. |
| "I read the code; the logic is clearly correct." | Static analysis flags candidates; does not verify. `UNVERIFIED` until live evidence. |
| "The live-e2e session already covered this URL." | Mine its log/DB rows as evidence and reference. If the URL didn't cover the touchpoint, run again on one that does. |
| "Mock just one external dep to make the test runnable." | Mocking any dependency in the verification path invalidates verification. |

### Correctness-validation (mechanical PASS ≠ correct)

| Excuse | Reality |
|---|---|
| "Row count went up — the fix works." | Row count proves the wiring fired. Doesn't prove the value is correct. Read the row, compare to ground truth. |
| "Field filled and form submitted." | Mechanical success ≠ correctness. The value being right for *this* candidate vs *this* JD is correctness. |
| "Log line shows the answer was returned." | The log shows what was returned. The audit's job is to ask whether what was returned was correct. |
| "All checkboxes ticked." | Checkboxes are an execution checklist, not a correctness rubric. Apply the four-question check. |
| "LLM-as-judge is overkill." | For any output-quality dimension, LLM-as-judge with rubric is the *minimum* bar. |
| "User-correction will catch it if wrong." | Correction loop is downstream. Don't let downstream rescue mask upstream bugs. |
| "Promote to PASS now and circle back." | UNVERIFIED entries are the audit's value. Demoting them to look complete defeats the audit. |
| "Mechanism is right — answer must be right." | Correct mechanism + wrong inputs = wrong outputs. Check both. |
| "Cache hit on second run = logic correct." | Cache hits prove key stability, not value correctness. Read the cached value and validate. |
| "Agent classified page as login_form and clicked Login." | Internal consistency ≠ correct classification. Cross-check classification against DOM features. |

### Error handling

| Excuse | Reality |
|---|---|
| "Same root cause as the previous bug — fix both at once." | If same root cause, fixing one fixes both. If different, fix one at a time. Bundling reverses, the diff becomes harder to review. |
| "Trace points to two places — fix both." | Trace failed; one is the symptom site. Re-trace. If genuinely two roots, fix the upstream one and re-run. |
| "Blast-radius check is overkill — one-line fix." | One-line fixes have caused production outages. The check costs minutes; regression costs hours. |
| "Suppress this error temporarily to see the next." | Two errors in a row means the second is downstream of the first. Fix the first; the second may evaporate. |
| "Add a `# TODO: fix properly` and move on." | Marker debt accumulates. The audit can't promote anything to PASS while a TODO marks the path. |
| "Fix touches more files than expected." | Verify the trace. Cross-cutting fixes have higher regression risk; the blast-radius check applies to every changed file. |
| "Re-running the live apply is slow." | A wrong-fix landing is slower (discover regression + re-trace + revert + re-fix + re-run). The original re-run was free. |

### Multi-ATS

| Excuse | Reality |
|---|---|
| "Two URLs is enough — they're different enough." | Ten ATS adapters means ten DOM dialects. 2-of-10 = 20% coverage = not done. |
| "We don't have a Workday/iCIMS URL handy — skip those." | Skipping = unvalidated = `UNVERIFIED`. Ask the user; do not silently drop coverage. |
| "Generic adapter handles unknowns — no need to test specifics." | Generic is a fallback; specific adapters have their own quirks (SmartRecruiters shadow DOM, iCIMS iframes, Workday React-controlled inputs). |
| "Hardcode for common ATS, Generic for rare." | Hardcoded paths break silently when the ATS updates. Stay dynamic. |
| "Same logic for every ATS — testing one tests all." | Sometimes true — verifiable only by *actually testing all*. |
| "Cross-ATS is slow — rely on learning loop in production." | The bar is "works perfectly", not "self-corrects over time". |

### Profile-driven

| Excuse | Reality |
|---|---|
| "We have the answer in the screening cache from last time." | Last time was a different JD, possibly different profile state. Cache hits must verify `(profile_state_hash, jd_context_hash)` match — not just question-text match. |
| "User always says Yes to visa sponsorship — default to that." | "Always" is a function of *the JDs they applied to so far*. A new JD in a different country flips the right answer. There is no global default. |
| "Profile fields don't change often — caching by question text is fine." | They change exactly when they matter: visa renewed, moved country, salary revised. The bar is "always correct", not "fine often". |
| "Hiring manager won't notice if we say Yes instead of No." | They will. An incorrect "needs sponsorship" gets the candidate filtered out by ATS rules. The wrong answer costs the apply. |
| "Hardcode the common case; LLM tier handles edges." | "Common case" is profile-and-JD-dependent. Make every decision a function of context; cache by context; no defaults. |
| "Computing per-apply is slow." | <$0.001 per LLM call with caching. Cost of a wrong answer is multiple orders higher. |
| "User corrected this once — reuse the correction." | Reuse only if `(profile, JD)` context matches. A correction from a UK Greenhouse apply doesn't apply to a US Workday apply. Corrections are context-bound. |
