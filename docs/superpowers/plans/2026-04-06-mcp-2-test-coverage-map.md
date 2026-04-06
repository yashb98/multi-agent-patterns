# test_coverage_map — Test Coverage Mapping

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps.

**Goal:** Add `test_coverage_map` tool that shows which functions have test coverage, which tests cover them, and which functions are untested.

**Architecture:** Query call edges from `test_*` functions to production functions. Group by target to build coverage map. Pure SQLite — no external dependencies.

---

### Files
- Modify: `shared/code_intelligence.py` — add `test_coverage_map()` method
- Modify: `shared/code_intel_mcp.py` — wire tool
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestTestCoverageMap:
    def test_finds_tested_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.test_coverage_map()
        assert "covered" in result
        assert "uncovered" in result
        assert "coverage_pct" in result
        covered_names = [f["name"] for f in result["covered"]]
        assert any("login" in n for n in covered_names)

    def test_finds_uncovered_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.test_coverage_map()
        uncovered_names = [f["name"] for f in result["uncovered"]]
        assert any("check_access" in n for n in uncovered_names)

    def test_filter_by_file(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.test_coverage_map(file="auth.py")
        all_files = {f["file"] for f in result["covered"] + result["uncovered"]}
        assert all("auth.py" in f for f in all_files)

    def test_shows_which_tests_cover(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.test_coverage_map()
        for fn in result["covered"]:
            assert "tested_by" in fn
            assert len(fn["tested_by"]) > 0
```

### Implementation

```python
def test_coverage_map(self, file: str | None = None, top_n: int = 50) -> dict[str, Any]:
    """Map which functions are tested and which tests cover them."""
    file_filter = ""
    params: list[Any] = []
    if file:
        file_filter = "AND n.file_path LIKE ?"
        params.append(f"%{file}%")

    prod_functions = self.conn.execute(
        f"""SELECT n.qualified_name, n.name, n.file_path, n.risk_score
            FROM nodes n
            WHERE n.kind IN ('function', 'method')
              AND n.is_test = 0
              AND n.file_path NOT LIKE '%test_%'
              AND n.file_path NOT LIKE '%conftest%'
              {file_filter}
            ORDER BY n.risk_score DESC LIMIT ?""",
        params + [top_n * 3],
    ).fetchall()

    test_functions = self.conn.execute(
        "SELECT qualified_name, name, file_path FROM nodes "
        "WHERE is_test = 1 AND kind IN ('function', 'method')"
    ).fetchall()

    coverage: dict[str, list[dict[str, str]]] = {}
    for test in test_functions:
        test_qname, test_name, test_file = test[0], test[1], test[2]
        callees = self.conn.execute(
            "SELECT target_qname FROM edges WHERE kind='calls' AND source_qname=?",
            (test_qname,),
        ).fetchall()
        for callee in callees:
            coverage.setdefault(callee[0], []).append({"test": test_name, "file": test_file})

    covered, uncovered = [], []
    for fn in prod_functions:
        qname, name, fpath, risk = fn[0], fn[1], fn[2], fn[3]
        tests_hitting = coverage.get(qname, [])
        if not tests_hitting:
            for key, val in coverage.items():
                if key.endswith(f"::{name}") or key == name:
                    tests_hitting = val
                    break
        entry = {"name": qname, "file": fpath, "risk_score": round(risk or 0, 3)}
        if tests_hitting:
            entry["tested_by"] = tests_hitting[:10]
            covered.append(entry)
        else:
            uncovered.append(entry)

    total = len(covered) + len(uncovered)
    return {
        "covered": covered[:top_n], "uncovered": uncovered[:top_n],
        "total_functions": total,
        "coverage_pct": round(len(covered) / total * 100, 1) if total else 0,
    }
```

### MCP Schema

```python
{
    "name": "test_coverage_map",
    "description": "Map which functions have test coverage and which tests cover them. Shows covered/uncovered functions and coverage percentage.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Filter to a specific file (optional)"},
            "top_n": {"type": "integer", "description": "Max results per category (default 50)", "default": 50},
        },
        "required": [],
    },
}
```

### Dispatch

```python
elif name == "test_coverage_map":
    return ci.test_coverage_map(file=args.get("file"), top_n=args.get("top_n", 50))
```
