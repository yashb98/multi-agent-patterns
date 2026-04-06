# /health — Full codebase health check

## Steps

1. **Lint:** `ruff check . --statistics`
2. **Tests:** `python -m pytest tests/ -v --tb=short -q`
3. **DB safety:** Use `grep_search` MCP tool: `pattern: "data/.*\\.db", glob: "tests/**/*.py"` — flag any lines that reference data/*.db without tmp_path patching.
4. **Dispatch sync:** Read AGENT_MAP keys from both `jobpulse/dispatcher.py` and `jobpulse/swarm_dispatcher.py`. Report mismatches.
5. **Secrets:** Use `grep_search` MCP tool: `pattern: "sk-|ghp_|Bearer ", glob: "*.py"` — flag any hardcoded secrets.
6. **Dependency direction:** Use `grep_search` MCP tool: `pattern: "from jobpulse|from patterns|from mindgraph", glob: "shared/**/*.py"` — should find zero results.

7. **CodeGraph index:** Use the `risk_report` MCP tool to verify the code intelligence index is healthy (nodes > 0, edges > 0). Report node/edge counts.

Print summary: tests passed/failed, lint errors, DB safety, dispatch sync, secrets, dependency violations, CodeGraph status.
