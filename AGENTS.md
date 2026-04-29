# AGENTS.md — Source of Truth for AI Coding Agents

Read this first. Check subdirectory `CLAUDE.md` files for deeper module context. Full rules in `.claude/rules/`.

---

## Project

Production autonomous agent system with three subsystems:
- **`patterns/`** — 6 LangGraph orchestration patterns (Enhanced Swarm is production default)
- **`jobpulse/`** — 15+ agents: Gmail, Calendar, Budget, Job Autopilot (4-gate pre-screen + ATS apply), Skill Graph, etc. 5 Telegram bots, Slack, Discord.
- **`mindgraph_app/`** — LLM entity extraction, SQLite knowledge graph, GraphRAG, Three.js 3D viz

Stack: Python 3.12+ | LangGraph + OpenAI Agents SDK | SQLite + Qdrant + Neo4j | Playwright CDP | ReportLab | FastAPI | React + Three.js

**Dependency rule:** `shared/` NEVER imports from `jobpulse/`, `patterns/`, or `mindgraph_app/`.

---

## Structure (key files only)

```
jobpulse/
  runner.py              # CLI: daemon, briefing, job-scan, job-apply-next, multi-bot
  config.py              # ALL env vars centralized here — never os.getenv() elsewhere
  dispatcher.py          # Flat intent dispatch
  swarm_dispatcher.py    # Enhanced Swarm dispatch (GRPO + personas)
  form_engine/           # Adaptive form fill: field_scanner → field_mapper → semantic_matcher → fillers
  ats_adapters/          # Platform strategies: linkedin, greenhouse, workday, generic
  application_orchestrator_pkg/  # Playwright external apply engine
shared/
  agents.py              # get_llm(), smart_llm_call() — ALL LLM access goes here
  code_intel_cli.py      # Code intelligence CLI (use this, not grep)
  memory_layer/          # MemoryManager facade → SQLite + Qdrant + Neo4j
  cognitive/             # CognitiveEngine.think(task, domain, stakes)
  optimization/          # OptimizationEngine — signal bus + learning
  nlp_classifier.py      # 3-tier: regex → embeddings → LLM
patterns/                # enhanced_swarm (prod), hierarchical, peer_debate, dynamic_swarm, plan_and_execute, map_reduce
tests/                   # Mirrors source tree. conftest.py sets JOBPULSE_TEST_MODE=1
```

---

## Commands

```bash
python -m jobpulse.runner multi-bot          # All 5 Telegram bots
python -m jobpulse.runner job-apply-next [N]  # Apply pipeline
python -m pytest tests/ -v                    # Full test suite
python -m pytest tests/ -v -k "keyword"       # Filtered
python -m pytest tests/ -v -m live            # Live API tests
```

---

## Code Exploration — CLI, Not Grep

```bash
python shared/code_intel_cli.py find_symbol <name>
python shared/code_intel_cli.py callers_of <name>
python shared/code_intel_cli.py callees_of <name>
python shared/code_intel_cli.py impact_analysis <file>
python shared/code_intel_cli.py module_summary <file>
python shared/code_intel_cli.py semantic_search "<query>"
python shared/code_intel_cli.py risk_report [top_n]
python shared/code_intel_cli.py recent_changes [n]
```

- ALWAYS use this instead of Grep/Glob for Python code (~50ms vs 350-750ms, returns call graph + risk)
- NEVER use `python -m shared.code_intel_cli` (triggers heavy imports, 60x slower)
- Grep/Glob only for non-Python files or raw regex in configs

---

## Engineering Principles (MANDATORY)

Full checklist: `.claude/rules/seven-principles.md`. Every change must satisfy:

1. **System Design** — Clear boundaries, no import-time side effects, functions <100 lines
2. **Tool & Contract** — `get_llm()`/`smart_llm_call()` for ALL LLM calls, TypedDict/dataclass returns
3. **Retrieval** — Connection pooling, no N+1, parameterized SQL, lazy loading
4. **Reliability** — `try/finally` for Playwright, `with` for SQLite, bounded loops, circuit breakers
5. **Security** — No PII in source (`.claude/rules/pii-policy.md`), no JS string interpolation, SSRF protection, parameterized SQL
6. **Observability** — Cost tracking on all LLM calls, decision logging, no silent failures
7. **Product** — Dry-run-first, `confirm_application()`, user-actionable errors

### Style
- Minimum code. Nothing speculative. Match existing style.
- Remove only what YOUR changes made unused. Don't touch pre-existing dead code.
- State assumptions. Present alternatives if multiple exist.

---

## Hard Rules

### No PII in Source (`.claude/rules/pii-policy.md`)
ALL personal data (name, email, address, screening answers, skills, links, DEI, salary, visa) retrieved from databases at runtime. Never hardcoded in Python, tests, or docs.

### Dual Dispatcher
New intents MUST go in BOTH `dispatcher.py` AND `swarm_dispatcher.py`. `JOBPULSE_SWARM=true` (default) = Enhanced Swarm.

### Database Safety
- Production DBs: `data/*.db` — NEVER touch directly. WAL mode required.
- Tests: ALWAYS `tmp_path` or monkeypatch. Never touch `data/*.db`.
- Access: `MemoryManager` for memory, `OptimizationEngine` for learning — no direct DB queries.

### Dynamic Over Hardcoded
All pipeline values resolved at runtime from DOM/databases/LLM/config. Never hardcode field values, selectors, timing, screening answers, or platform behavior.

### No Regex for Semantic Work
Regex is for text sanitization, security stripping, and structural format validation (email/phone/date) ONLY. For classification, intent routing, question categorization, consent detection, field matching, and command parsing — use dynamic approaches: LLM, embeddings, semantic matching, DOM/a11y inspection, or DB lookups. When touching a file with regex-based classification, migrate those patterns to dynamic.

---

## Live Pipeline (MANDATORY)

All applications run real, headed, live. No mocks, no headless.

**Claude = orchestrator, not doer.** Invoke the actual agents (`job-apply-next`, `apply_job()`, `ApplicationOrchestrator`). Observe, diagnose, direct — don't write ad-hoc scripts that bypass agents. Agents only learn from runs they performed.

Steps: Pre-Screen → CV/CL → Form Fill → Dry Run (human review) → Submit (`confirm_application()`) → Learning

On error: Trace via CLI (`find_symbol`/`callers_of`) → fix surgically → re-run → route to correct DB → emit `adaptation` signal → verify persisted.

Learning chain must fire: `post_apply_hook` → `CorrectionCapture` → `AgentRulesDB` → `strategy_reflector` → `OptimizationEngine` → `AgentPerformanceDB`

---

## Testing

- Tests NEVER touch `data/*.db` — use `tmp_path`
- **Real data + wiring verification (MANDATORY)**: Every new feature tested with real URLs/APIs/DBs/scraping (never mocks or stale data), then verified end-to-end that all downstream systems fire (hooks, signals, DB writes, learning chains). Not wired = not done. Mark `@pytest.mark.live`.
- Goal-driven: reproduce bug as test → make it pass
- Fixtures: `mock_openai`, `mock_telegram`, `in_memory_db`

---

## Security

- **PII**: Never in source — from DBs at runtime (`.claude/rules/pii-policy.md`)
- **Credentials**: `.env` (gitignored), Fernet-encrypted in SQLite, `0o600` permissions
- **Injection**: No string interpolation in `page.evaluate()`, allowlist on remote shell, parameterized SQL
- **SSRF**: URL scheme + host validation before external fetches
- **LLM**: `get_llm()` caps `max_tokens=4096`, prompt defense strips injection tags

---

## Key Config

All env vars via `jobpulse/config.py`. Never `os.getenv()` elsewhere.

Critical: `OPENAI_API_KEY` | `TELEGRAM_BOT_TOKEN`/`CHAT_ID` | `NOTION_API_KEY` | `JOBPULSE_SWARM` (true=swarm) | `FAST_FILL` (true=zero delays) | `COGNITIVE_ENABLED` | `OPTIMIZATION_ENABLED`

---

## Module Context (read when working in that area)

| Directory | Doc |
|-----------|-----|
| `jobpulse/` | `jobpulse/CLAUDE.md` |
| `patterns/` | `patterns/CLAUDE.md` |
| `mindgraph_app/` | `mindgraph_app/CLAUDE.md` |
| `shared/` | `shared/CLAUDE.md` |
| `shared/cognitive/` | `shared/cognitive/CLAUDE.md` |
| `shared/memory_layer/` | `shared/memory_layer/CLAUDE.md` |
| `shared/optimization/` | `shared/optimization/CLAUDE.md` |

Rules: `.claude/rules/` — `seven-principles.md`, `pii-policy.md`, `jobs.md`, `jobpulse.md`, `patterns.md`, `shared.md`, `testing.md`, `frontend.md`
