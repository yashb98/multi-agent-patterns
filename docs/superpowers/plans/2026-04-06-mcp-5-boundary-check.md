# boundary_check — Architectural Boundary Enforcement

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps.

**Goal:** Add `boundary_check` tool that validates architectural dependency rules (e.g. shared/ must not import from jobpulse/) using import + call edges from the code graph.

**Architecture:** Check import edges and call edges where source file is in the restricted module and target references a forbidden module. Default rules match the project's CLAUDE.md rules.

---

### Files
- Modify: `shared/code_intelligence.py` — add `_DEFAULT_BOUNDARY_RULES` constant + `boundary_check()` method
- Modify: `shared/code_intel_mcp.py` — wire tool
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestBoundaryCheck:
    def test_detects_violation(self, ci, tmp_path):
        src = tmp_path / "project"
        src.mkdir()
        (src / "shared").mkdir()
        (src / "jobpulse").mkdir()
        (src / "shared" / "utils.py").write_text(textwrap.dedent("""\
            from jobpulse.runner import start
            def helper():
                return start()
        """))
        (src / "jobpulse" / "runner.py").write_text(textwrap.dedent("""\
            def start():
                return True
        """))
        ci.index_directory(str(src))
        rules = [{"module": "shared", "cannot_import": ["jobpulse", "patterns", "mindgraph_app"]}]
        result = ci.boundary_check(rules)
        assert len(result["violations"]) >= 1
        assert result["violations"][0]["source_module"] == "shared"

    def test_no_violation_when_clean(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        rules = [{"module": "shared", "cannot_import": ["jobpulse"]}]
        result = ci.boundary_check(rules)
        assert result["violations"] == []

    def test_default_rules(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.boundary_check()
        assert "violations" in result
        assert "rules_checked" in result
```

### Implementation

Add constant before the class:

```python
_DEFAULT_BOUNDARY_RULES = [
    {"module": "shared", "cannot_import": ["jobpulse", "patterns", "mindgraph_app"]},
]
```

Add method to `CodeIntelligence`:

```python
def boundary_check(self, rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Check architectural boundary rules — detect forbidden imports."""
    if rules is None:
        rules = _DEFAULT_BOUNDARY_RULES

    violations: list[dict[str, Any]] = []
    for rule in rules:
        source_module = rule["module"]
        forbidden = rule["cannot_import"]

        import_edges = self.conn.execute(
            "SELECT source_qname, target_qname, file_path, line "
            "FROM edges WHERE kind='imports' AND file_path LIKE ?",
            (f"{source_module}/%",),
        ).fetchall()

        call_edges = self.conn.execute(
            "SELECT e.source_qname, e.target_qname, e.file_path, e.line "
            "FROM edges e JOIN nodes n ON n.qualified_name = e.target_qname "
            "WHERE e.kind='calls' AND e.file_path LIKE ? AND n.file_path IS NOT NULL",
            (f"{source_module}/%",),
        ).fetchall()

        for edge in list(import_edges) + list(call_edges):
            target_str = str(edge[1])
            for forbidden_mod in forbidden:
                if (target_str.startswith(f"{forbidden_mod}.")
                        or target_str.startswith(f"{forbidden_mod}/")
                        or f"/{forbidden_mod}/" in target_str):
                    violations.append({
                        "source_module": source_module, "source_file": edge[2],
                        "source_function": edge[0], "target": target_str,
                        "forbidden_module": forbidden_mod, "line": edge[3],
                    })

    seen = set()
    unique = []
    for v in violations:
        key = (v["source_file"], v["target"], v["line"])
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return {"violations": unique, "rules_checked": len(rules), "clean": len(unique) == 0}
```

### MCP Schema

```python
{
    "name": "boundary_check",
    "description": "Check architectural boundary rules — detect forbidden cross-module imports. Default: shared/ cannot import from jobpulse/, patterns/, mindgraph_app/.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "rules": {
                "type": "array",
                "items": {"type": "object", "properties": {"module": {"type": "string"}, "cannot_import": {"type": "array", "items": {"type": "string"}}}},
                "description": "Boundary rules (uses project defaults if omitted)",
            },
        },
        "required": [],
    },
}
```

### Dispatch

```python
elif name == "boundary_check":
    return ci.boundary_check(rules=args.get("rules"))
```
