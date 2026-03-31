# /health — Full codebase health check

## Steps

1. **Lint:** `ruff check . --statistics`
2. **Tests:** `python -m pytest tests/ -v --tb=short -q`
3. **DB safety:** Check for production DB references in test files:
   `grep -rn "data/" tests/ --include="*.py" | grep -v "tmp_path\|mock\|fixture\|#\|conftest"`
   Flag any lines that reference data/*.db without tmp_path patching.
4. **Dispatch sync:** Read AGENT_MAP keys from both `jobpulse/dispatcher.py` and `jobpulse/swarm_dispatcher.py`. Report mismatches.
5. **Secrets:** `grep -rn "sk-\|ghp_\|Bearer " --include="*.py" jobpulse/ shared/ patterns/ mindgraph_app/`
6. **Dependency direction:** `grep -rn "from jobpulse\|from patterns\|from mindgraph" shared/ --include="*.py"` — should find zero results.

Print summary: tests passed/failed, lint errors, DB safety, dispatch sync, secrets, dependency violations.
