# Rules: Tests (tests/**/*)

## Database Isolation
CRITICAL: Tests MUST NEVER touch production databases in data/*.db.
Always use tmp_path fixtures or monkeypatch DB paths.
Incident: 2026-03-25 — test_mindgraph.py wiped production mindgraph.db via storage.clear_all().
Fix: use_temp_db autouse fixture patches DB_PATH to tmp_path.

## Test Structure
- Tests mirror source: tests/jobpulse/ ↔ jobpulse/, tests/patterns/ ↔ patterns/, tests/shared/ ↔ shared/
- Not all subdirectories have mirrored test dirs — some tests live as flat files (e.g., `test_page_analysis.py` instead of `tests/jobpulse/page_analysis/`)
- Shared fixtures in conftest.py (root) and tests/conftest.py
- Use pytest markers: @pytest.mark.slow for integration tests, @pytest.mark.live for real-data tests

### Additional Test Directories
- `tests/lint/` — 3 lint enforcement tests (no blocking sleep, no raw requests.get, profile prompt wrapping)
- `tests/papers/` — Paper pipeline tests (10 files)
- `tests/shared/adversarial/` — Adversarial evaluation tests
- `tests/shared/evals/` — Agent evaluation tests
- `tests/shared/execution/` — Durable execution tests (14 files)
- `tests/shared/governance/` — Auth, sanitizer, policy, score validator tests
- `tests/shared/prompts/` — Prompt registry tests
- `tests/jobpulse/integration/` — Live integration tests
- `tests/fixtures/live_snapshots/` — Indeed/LinkedIn page snapshots + manifest

## Running Tests
```
python -m pytest tests/ -v                    # Full suite
python -m pytest tests/ -v --cov              # With coverage
python -m pytest tests/ -v -k "budget"        # Budget tests only
python -m pytest tests/ -v -k "dispatch"      # Dispatcher tests only
python -m pytest tests/ -v -k "fact"          # Fact-checker tests only
```

## Real Data + Wiring Verification (MANDATORY)
Every new feature/function must pass TWO gates before it's done:
1. **Real data test** — real URLs, real APIs, real DBs, real scraping/fetching. Never mocks, stubs, synthetic fixtures, or stale snapshots. `tmp_path` for output only. Mark `@pytest.mark.live`.
2. **Wiring verification** — run end-to-end and confirm every downstream system actually fires: hooks, signals, DB writes, learning chains, Notion syncs, Telegram notifications. If a feature emits signals, test that receivers consumed them. If it writes to a DB, query the DB. If it triggers a chain (`post_apply_hook` → `CorrectionCapture` → `AgentRulesDB` → `strategy_reflector` → `OptimizationEngine`), verify each link.

A feature that passes unit tests but isn't wired end-to-end is not done.

## Goal-Driven Testing
- Transform "fix the bug" → write a test that reproduces it, then make it pass.
- Transform "add validation" → write tests for invalid inputs, then make them pass.
- Transform "refactor X" → ensure tests pass before and after.
- Don't add error handling or test coverage for scenarios that can't happen.

## OPRAL Error Loop in Tests
When a test fails: **Observe** (read the actual error, not just the traceback) → **Plan** (trace to root cause — is it a code bug, a wiring gap, or stale data?) → **Reason** (which learning DB should prevent this class of failure?) → **Act** (fix with real data, never mock the failure away) → **Learn** (if the error reveals a wiring gap, add a wiring test that verifies the DB write). Never fix a test by mocking — fix the underlying system.

## Database Wiring Tests
19 DBs are wired in code but have zero rows in production. When writing tests for pipeline features, verify the DB actually receives data — query it after a run. Wiring tests exist in `test_wiring_e2e.py`, `test_gate4_wiring.py`, `test_snapshot_wiring.py`, `test_cognitive_wiring.py`, `test_scan_learning_wiring.py`. Priority empties remaining: `user_profile.db`, `project_selection_outcomes.db`.

## What to Test for New Features
- Intent routing: test in BOTH dispatcher AND swarm_dispatcher
- Budget: test parsing, recurring, alerts, undo, CSV export, weekly comparison
- NLP: test embedding tier and LLM fallback tier (do NOT add new regex-tier tests — regex tier is legacy, migrate to embeddings when touched)
- Telegram: test command parsing with Whisper-style punctuation ("Help." not "help")
- Database: test with tmp_path fixture, verify no data/*.db references in test files
- Classification: verify dynamic classification (LLM/embeddings/semantic) — never assert regex pattern matches for semantic routing
