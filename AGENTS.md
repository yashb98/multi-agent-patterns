# Subagent Instructions

These instructions apply to ALL subagents spawned via the Agent tool.

## Code Exploration — Use CLI, Not Grep/Glob

You do NOT have access to MCP tools. Instead, use the Code Intelligence CLI via Bash for all code exploration:

```bash
python shared/code_intel_cli.py find_symbol <name>        # Locate function/class definition
python shared/code_intel_cli.py callers_of <name>          # Who calls this function?
python shared/code_intel_cli.py callees_of <name>          # What does this function call?
python shared/code_intel_cli.py impact_analysis <file>     # Blast radius of a change
python shared/code_intel_cli.py risk_report [top_n]        # High-risk functions
python shared/code_intel_cli.py module_summary <file>      # Module overview
python shared/code_intel_cli.py semantic_search "<query>"  # Find code by meaning (~4s)
python shared/code_intel_cli.py dead_code [top_n]          # Unreachable functions
python shared/code_intel_cli.py recent_changes [n]         # Git log + graph context
```

**Rules:**
- ALWAYS use `python shared/code_intel_cli.py` via Bash instead of Grep/Glob for Python code queries
- These queries take ~50ms (vs 350-750ms for grep) and return richer data (risk scores, call graph context)
- Use Grep/Glob ONLY for non-Python files, raw regex in configs, or when the CLI doesn't cover your query
- NEVER use `python -m shared.code_intel_cli` (triggers heavy imports, 60x slower)

## Database Safety

- Production DBs live in `data/*.db` — NEVER modify or touch these directly
- All test fixtures must use `tmp_path` or monkeypatch DB paths

## Dual Dispatcher Rule

When investigating intents or dispatch: check BOTH `jobpulse/dispatcher.py` AND `jobpulse/swarm_dispatcher.py`.
