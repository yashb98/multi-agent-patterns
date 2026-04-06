# MCP Tool Wiring Reference (shared across all 7 feature plans)

Every new MCP tool follows these 3 steps in `shared/code_intel_mcp.py`:

## 1. Add to TOOL_NAMES (line ~38)

```python
TOOL_NAMES = [
    # ... existing tools ...
    "your_new_tool",
]
```

## 2. Add tool schema to _TOOL_DEFS (line ~139)

```python
{
    "name": "your_new_tool",
    "description": "One-line description for Claude Code tool panel.",
    "inputSchema": {
        "type": "object",
        "properties": { ... },
        "required": [ ... ],
    },
},
```

## 3. Add dispatch case in _dispatch() (line ~520)

```python
elif name == "your_new_tool":
    return ci.your_new_tool(args["param"], optional=args.get("optional", default))
```

## Test pattern (tests/test_code_intelligence.py)

```python
@pytest.fixture
def ci(tmp_path):
    db_path = str(tmp_path / "test_ci.db")
    instance = CodeIntelligence(db_path=db_path)
    yield instance
    instance.close()

@pytest.fixture
def sample_project(tmp_path):
    # See existing fixture — creates auth.py, utils.py, test_auth.py, README.md
```

## Final step (Task 8 — after all 7 features)

- Update `get_primer()` MCP tools line in `shared/code_intelligence.py:1136`
- Update docstring in `shared/code_intel_mcp.py:1` from "13 tools" to "20 tools"
