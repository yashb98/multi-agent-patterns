# Rules: Tests (tests/**/*)

## Database Isolation
CRITICAL: Tests MUST NEVER touch production databases in data/*.db.
Always use tmp_path fixtures or monkeypatch DB paths.
Incident: 2026-03-25 — test_mindgraph.py wiped production mindgraph.db via storage.clear_all().
Fix: use_temp_db autouse fixture patches DB_PATH to tmp_path.

## Test Structure
- Tests mirror source: tests/jobpulse/ ↔ jobpulse/, tests/patterns/ ↔ patterns/
- Shared fixtures in conftest.py (root) and tests/conftest.py
- Use pytest markers: @pytest.mark.slow for integration tests

## Running Tests
```
python -m pytest tests/ -v                    # Full suite
python -m pytest tests/ -v --cov              # With coverage
python -m pytest tests/ -v -k "budget"        # Budget tests only
python -m pytest tests/ -v -k "dispatch"      # Dispatcher tests only
python -m pytest tests/ -v -k "fact"          # Fact-checker tests only
```

## What to Test for New Features
- Intent routing: test in BOTH dispatcher AND swarm_dispatcher
- Budget: test parsing, recurring, alerts, undo, CSV export, weekly comparison
- NLP: test regex tier, embedding tier, and LLM fallback tier
- Telegram: test command parsing with Whisper-style punctuation ("Help." not "help")
- Database: test with tmp_path fixture, verify no data/*.db references in test files
