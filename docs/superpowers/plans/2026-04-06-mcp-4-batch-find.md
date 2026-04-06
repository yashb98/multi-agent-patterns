# batch_find — Multi-Symbol Batch Lookup

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps.

**Goal:** Add `batch_find` tool for looking up multiple symbols at once or matching by glob pattern, eliminating multiple round-trips.

**Architecture:** Either iterate `find_symbol()` for a name list, or use SQL LIKE with glob-to-SQL conversion for pattern matching.

---

### Files
- Modify: `shared/code_intelligence.py` — add `batch_find()` method
- Modify: `shared/code_intel_mcp.py` — wire tool
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestBatchFind:
    def test_finds_multiple_symbols(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.batch_find(["login", "check_access"])
        assert len(result["found"]) == 2
        names = [r["name"] for r in result["found"]]
        assert "login" in names
        assert "check_access" in names

    def test_glob_pattern(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.batch_find(pattern="*_token")
        assert len(result["found"]) >= 1
        assert any("verify_token" in r["name"] for r in result["found"])

    def test_missing_symbols_in_not_found(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.batch_find(["login", "nonexistent_xyz"])
        assert "nonexistent_xyz" in result["not_found"]

    def test_includes_risk_comparison(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.batch_find(["login", "check_access"])
        for r in result["found"]:
            assert "risk_score" in r
```

### Implementation

```python
def batch_find(self, names: list[str] | None = None, *, pattern: str | None = None, max_results: int = 50) -> dict[str, Any]:
    """Find multiple symbols at once, or match by glob pattern."""
    found: list[dict[str, Any]] = []
    not_found: list[str] = []

    if pattern:
        sql_pattern = pattern.replace("*", "%").replace("?", "_")
        rows = self.conn.execute(
            "SELECT qualified_name, name, kind, file_path, line_start, line_end, "
            "risk_score, is_async FROM nodes "
            "WHERE name LIKE ? AND kind != 'document' ORDER BY risk_score DESC LIMIT ?",
            (sql_pattern, max_results),
        ).fetchall()
        for row in rows:
            found.append({
                "qualified_name": row[0], "name": row[1], "kind": row[2],
                "file": row[3], "line_start": row[4], "line_end": row[5],
                "risk_score": round(row[6] or 0, 3), "is_async": bool(row[7]),
            })
    elif names:
        for name in names:
            result = self.find_symbol(name)
            if result:
                found.append(result)
            else:
                not_found.append(name)

    return {"found": found[:max_results], "not_found": not_found, "total": len(found)}
```

### MCP Schema

```python
{
    "name": "batch_find",
    "description": "Find multiple symbols at once or match by glob pattern (e.g. '*_handler'). Returns all found symbols with risk scores.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}, "description": "List of symbol names to look up"},
            "pattern": {"type": "string", "description": "Glob pattern to match (e.g. '*_handler', 'test_*')"},
            "max_results": {"type": "integer", "description": "Max results (default 50)", "default": 50},
        },
        "required": [],
    },
}
```

### Dispatch

```python
elif name == "batch_find":
    return ci.batch_find(names=args.get("names"), pattern=args.get("pattern"), max_results=args.get("max_results", 50))
```
