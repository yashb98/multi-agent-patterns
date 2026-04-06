# suggest_extract — Refactor Extraction Suggestions

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps.

**Goal:** Add `suggest_extract` tool that identifies functions that could benefit from extraction — large functions and functions with too many callees.

**Architecture:** Two SQL queries: (1) functions exceeding `min_lines` threshold, (2) functions with >8 callees via JOIN on edges. Pure SQLite.

---

### Files
- Modify: `shared/code_intelligence.py` — add `suggest_extract()` method
- Modify: `shared/code_intel_mcp.py` — wire tool
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestSuggestExtract:
    def test_suggests_extraction_for_long_function(self, ci, tmp_path):
        src = tmp_path / "project"
        src.mkdir()
        lines = ["def big_function():"]
        for i in range(65):
            lines.append(f"    x{i} = {i}")
        lines.append("    return x0")
        (src / "big.py").write_text("\n".join(lines))
        ci.index_directory(str(src))
        result = ci.suggest_extract()
        assert len(result["suggestions"]) >= 1
        assert result["suggestions"][0]["reason"] == "large_function"

    def test_no_suggestions_for_small_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.suggest_extract(min_lines=200)
        assert len(result["suggestions"]) == 0

    def test_filter_by_file(self, ci, tmp_path):
        src = tmp_path / "project"
        src.mkdir()
        lines = ["def big_function():"]
        for i in range(65):
            lines.append(f"    x{i} = {i}")
        lines.append("    return x0")
        (src / "big.py").write_text("\n".join(lines))
        (src / "small.py").write_text("def tiny(): pass\n")
        ci.index_directory(str(src))
        result = ci.suggest_extract(file="big.py")
        files = {s["file"] for s in result["suggestions"]}
        assert all("big.py" in f for f in files)
```

### Implementation

```python
def suggest_extract(self, file: str | None = None, min_lines: int = 50, top_n: int = 20) -> dict[str, Any]:
    """Suggest functions that could benefit from extraction/refactoring."""
    file_filter = ""
    params: list[Any] = []
    if file:
        file_filter = "AND file_path LIKE ?"
        params.append(f"%{file}%")

    suggestions: list[dict[str, Any]] = []

    # Large functions
    large = self.conn.execute(
        f"""SELECT qualified_name, file_path, line_start, line_end, risk_score, fan_in
            FROM nodes WHERE kind IN ('function', 'method')
            AND (line_end - line_start) > ? {file_filter}
            ORDER BY (line_end - line_start) DESC LIMIT ?""",
        [min_lines] + params + [top_n],
    ).fetchall()

    for row in large:
        size = (row[3] or 0) - (row[2] or 0)
        callees = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls' AND source_qname=?", (row[0],),
        ).fetchone()[0]
        suggestions.append({
            "name": row[0], "file": row[1], "lines": size,
            "risk_score": round(row[4] or 0, 3), "fan_in": row[5] or 0,
            "callees_count": callees, "reason": "large_function",
            "suggestion": f"Function is {size} lines. Consider extracting logical blocks into helpers.",
        })

    # Functions with too many callees
    busy = self.conn.execute(
        f"""SELECT n.qualified_name, n.file_path, n.line_start, n.line_end,
                   n.risk_score, n.fan_in, COUNT(e.id) as callee_count
            FROM nodes n
            JOIN edges e ON e.source_qname = n.qualified_name AND e.kind = 'calls'
            WHERE n.kind IN ('function', 'method') AND (n.line_end - n.line_start) > 20
            {file_filter.replace('file_path', 'n.file_path')}
            GROUP BY n.qualified_name HAVING callee_count > 8
            ORDER BY callee_count DESC LIMIT ?""",
        params + [top_n],
    ).fetchall()

    seen = {s["name"] for s in suggestions}
    for row in busy:
        if row[0] in seen:
            continue
        suggestions.append({
            "name": row[0], "file": row[1],
            "lines": (row[3] or 0) - (row[2] or 0),
            "risk_score": round(row[4] or 0, 3), "fan_in": row[5] or 0,
            "callees_count": row[6], "reason": "too_many_callees",
            "suggestion": f"Function calls {row[6]} other functions. Consider splitting responsibilities.",
        })

    return {"suggestions": suggestions[:top_n], "total": len(suggestions)}
```

### MCP Schema

```python
{
    "name": "suggest_extract",
    "description": "Suggest functions that could benefit from extraction or refactoring. Finds large functions (>50 lines) and functions with too many callees (>8).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Filter to a specific file (optional)"},
            "min_lines": {"type": "integer", "description": "Min function size to flag (default 50)", "default": 50},
            "top_n": {"type": "integer", "description": "Max suggestions (default 20)", "default": 20},
        },
        "required": [],
    },
}
```

### Dispatch

```python
elif name == "suggest_extract":
    return ci.suggest_extract(file=args.get("file"), min_lines=args.get("min_lines", 50), top_n=args.get("top_n", 20))
```
