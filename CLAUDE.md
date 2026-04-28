# JobPulse — Multi-Agent Automation System

Production autonomous agent system: LangGraph + OpenAI + Enhanced Swarm + RLM.

## Quick Reference

```bash
python -m jobpulse.runner multi-bot    # Start all 5 Telegram bots
python -m jobpulse.runner stop         # Stop all daemons
python -m jobpulse.runner webhook      # API server (port 8080, Swagger at /docs)
python -m jobpulse.runner briefing     # Morning digest
python -m jobpulse.runner export       # Full data backup
python -m jobpulse.runner profile-sync # Refresh skill/project graph (3am cron)
python -m jobpulse.runner skill-gaps   # Show top missing skills + export CSV
python -m jobpulse.runner chrome-pw     # Launch Chrome with CDP for Playwright
```

## Code Intelligence (use for ALL code exploration)
MCP tools are 10-250x faster than Grep (1-28ms vs 350-750ms, pre-indexed SQLite).
- `find_symbol` — locate definition | `callers_of` / `callees_of` — call graph
- `impact_analysis` — blast radius | `risk_report` — high-risk functions
- `semantic_search` — find code AND docs by meaning (all .md files are indexed)
- `module_summary` — module overview | `recent_changes` — git log + graph
- Grep/Glob only for non-Python files or raw regex in configs
- **Never use Explore agents for code understanding** — they can't access MCP, burn 50-100k tokens

### Subagent Code Intelligence (auto-injected via AGENTS.md)
Subagents automatically get CLI instructions via `AGENTS.md` — no manual briefing needed.
CLI uses direct SQLite (~50ms) vs `python -m` path (~4s, heavy `shared/__init__.py` imports).

## Coding Principles

### Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.

### Simplicity First
- Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked. No abstractions for single-use code.
- No error handling for impossible scenarios. If 200 lines could be 50, rewrite it.

### Surgical Changes
- Don't "improve" adjacent code, comments, or formatting.
- Match existing style. Don't refactor things that aren't broken.
- Remove imports/variables/functions that YOUR changes made unused — don't touch pre-existing dead code.
- Every changed line should trace directly to the request.

### Goal-Driven Execution
- Transform tasks into verifiable goals with success criteria.
- For multi-step tasks, state a brief plan with verification checks per step.
- Loop until verified — weak criteria ("make it work") require clarification first.

## Eight Engineering Principles (MANDATORY)
Every feature, function, and file MUST satisfy all 8 principles. Full checklist: `.claude/rules/seven-principles.md`
1. **System Design** — Clear boundaries, no import-time side effects, no duplicated logic
2. **Tool & Contract Design** — Typed interfaces, centralized LLM factories, consistent return types
3. **Retrieval Engineering** — Connection pooling, no N+1, cached lookups, lazy loading
4. **Reliability Engineering** — Resource cleanup in finally, guarded LLM calls, bounded loops
5. **Security & Safety** — No PII in source, no injection vectors, SSRF protection, parameterized SQL
6. **Evaluation & Observability** — Cost tracking on all LLM calls, decision logging, structured errors
7. **Product Thinking** — Dry-run-first, confirm_application(), OS-aware paths, user-actionable errors
8. **Dynamic Over Hardcoded** — All pipeline values resolved at runtime, never hardcoded for specific forms/platforms

## Live Pipeline Observation Protocol (MANDATORY for every application)

Every job application — whether triggered by Claude, AI agents, or cron — MUST run through the actual live pipeline with full step-by-step observation. No mocks, no shortcuts, no simulated data.

### The Observation Loop

When applying to a job, Claude MUST observe every step of the live pipeline in real time:

```
1. Pre-Screen  →  2. CV/CL Generation  ���  3. Form Fill  →  4. Dry Run Review  →  5. Submit  →  6. Post-Apply Learning
     ↑                                                                                              |
     └──────────────────────── Fix issues, update rules, improve agents ←───────────────────────────┘
```

### Step-by-Step Observation Checklist

**Step 1: Pre-Screen Pipeline** — Observe Gates 0-3 + Gate 4 running on the real JD
- Verify `recruiter_screen.py` (Gate 0) title/keyword filter against real JD text
- Verify `skill_graph_store.py` (Gates 1-3) skill extraction and scoring with real profile
- Verify `gate4_quality.py` (Gate 4) JD quality + company blocklist + CV scrutiny
- Check: are real skills being matched? Are scores accurate? Are kill signals correct?

**Step 2: CV/CL Generation** — Observe generation with real company data
- Verify `sync_verified_to_profile()` pulls latest verified skills from Notion
- Verify `generate_cv_pdf()` produces correct content (projects, metrics, skills match JD)
- Verify cover letter lazy generation triggers only when CL field detected
- Check: does the CV match the JD? Are metrics real? Is the 2-page limit respected?

**Step 3: Form Fill** — Observe every field fill on the real ATS form
- Watch `NativeFormFiller` scan fields via CDP `Accessibility.getFullAXTree`
- Watch `field_scanner.py` container resolution (Learned → Auto-detect → Strategy)
- Watch `field_mapper.py` map profile values to form fields (seed_mapping + LLM fallback)
- Watch `semantic_matcher.py` match dropdown/radio options (5-tier cascade)
- Watch `screening_answers.py` generate answers from JD+CV context
- Check: every field filled correctly? Options matched? Screening answers accurate?

**Step 4: Dry Run Review** — Human-in-the-loop verification
- Screenshot the filled form, compare agent values against expected values
- Identify any mismatches, wrong options, missing fields, misaligned data
- Record what the agent got wrong (these become correction signals)

**Step 5: Submit** — Observe the submission path
- Watch `applicator.py` rate limiter, mutex, and quota reservation
- Watch adapter `fill_and_submit()` execute (async bridging, external redirects)
- Verify `confirm_application()` runs after user approval (MANDATORY)
- Check: did the submission succeed? Any errors? Any external redirects handled?

**Step 6: Post-Apply Learning** — Observe all learning systems fire
- Watch `post_apply_hook()` record form experience to `FormExperienceDB`
- Watch `correction_capture.py` diff agent_mapping vs final_mapping
- Watch `agent_rules.py` auto-generate rules from corrections
- Watch `strategy_reflector.py` extract heuristics (deterministic + LLM)
- Watch `AgentPerformanceDB` record fill stats
- Watch `OptimizationEngine` emit signals (correction, success, adaptation)
- Watch `screening_outcome_recorder` store screening results
- Check: are all learning databases updated? Are signals emitted? Are rules generated?

### When Something Breaks — Fix and Teach

When Claude or an AI agent encounters an error during any pipeline step:

1. **Diagnose** — Read the error, trace it to the exact function and line using MCP tools (`find_symbol`, `callers_of`, `callees_of`)
2. **Fix** — Apply the surgical fix to the failing code (match existing style, minimal change)
3. **Test** — Re-run the same pipeline step with the same real data to verify the fix works
4. **Teach** — Feed the fix into the learning systems so AI agents improve:
   - Emit an `adaptation` signal via `OptimizationEngine` with before/after metrics
   - If it's a form fill issue: store the correction in `CorrectionCapture` + `AgentRulesDB`
   - If it's a platform quirk: store in `GotchasDB` + update `.claude/rules/jobs.md`
   - If it's a screening answer: cache the correct answer in `screening_answers.py` SQLite
   - If it's a navigation issue: update `NavigationLearner` with the working sequence
   - Log the trajectory step via `OptimizationEngine.log_step()` for JSONL export
5. **Verify Learning** — Confirm the learning was persisted (query the DB, check the signal bus)

### Meta-Cognitive Self-Adaptation

The pipeline uses three layers of self-improvement that Claude must observe and verify:

**Layer 1: Correction → Rule → Consumption Loop**
- `CorrectionCapture` records field-level diffs → `AgentRulesDB` generates rules → `NativeFormFiller` consumes rules on next fill
- Claude verifies: corrections are being captured, rules are being generated, rules are being applied

**Layer 2: Strategy Reflection**
- `strategy_reflector.py` runs after every application: deterministic heuristic extraction + LLM reflection
- Heuristics feed `TrajectoryStore` + `ExperienceMemory` for cross-domain learning
- Claude verifies: heuristics are being extracted, experience scores are accurate

**Layer 3: Cognitive Engine Escalation**
- `CognitiveEngine` escalates reasoning when needed: L0 Memory → L1 Single Shot → L2 Reflexion → L3 Tree of Thought
- `OptimizationEngine` tracks domain stats → feeds `EscalationClassifier` with success rates
- Claude verifies: escalation is happening at the right level, strategy templates are being stored

### What Claude Must Do Differently

- **Never skip observation** — Even if a step "looks fine", verify it ran and produced correct output
- **Never use mock data** — All URLs, profiles, JDs, and form data must be real and live
- **Always trace errors to root cause** — Don't just retry; understand WHY it failed
- **Always close the learning loop** — Every fix must flow back into the learning databases
- **Always check downstream impact** — Use `callers_of` and `impact_analysis` before changing any function
- **Report what you observed** — After each application, summarize: what worked, what broke, what was learned

## Critical Rules
- Update BOTH dispatcher.py AND swarm_dispatcher.py for new intents
- Always HTTPS for external APIs | Tests NEVER touch data/*.db — use tmp_path
- Never rewrite a file without checking `callers_of` (or Grep) for all function names used by other modules
- Log errors to `.claude/mistakes.md` | Full rules in `.claude/rules/`
- Use `semantic_search` to retrieve detailed rules/docs on demand — they're all indexed

## Dispatch
Enhanced Swarm (default). `JOBPULSE_SWARM=false` for flat dispatcher.

## Stats
~143,500 LOC | 680 Python files | 57 databases | 3378 tests | 5 dashboards | 5 Telegram bots | 3 platforms
> Auto-updated by pre-commit hook. Manual: `python scripts/update_stats.py`

## Module Context (loaded when working in that directory)
- `jobpulse/CLAUDE.md` — Agents, dispatch, Telegram, extension engine, application orchestrator
- `patterns/CLAUDE.md` — 4 LangGraph orchestration patterns
- `mindgraph_app/CLAUDE.md` — Code Review Graph, risk scoring, Mermaid/DOT viz
- `shared/CLAUDE.md` — Cross-cutting utilities, NLP, fact-checker
- `shared/cognitive/CLAUDE.md` — 4-level cognitive engine: memory recall, single shot, reflexion, tree of thought
- `shared/memory_layer/CLAUDE.md` — 3-engine memory: SQLite (truth) + Qdrant (vectors) + Neo4j (graph)
- `shared/optimization/CLAUDE.md` — Continuous learning: signal bus, aggregator, tracker, policy, trajectories
- `.claude/rules/` — Domain-specific rules (jobs, testing, patterns, shared, frontend, error-handling)
