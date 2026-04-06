# rename_preview — Rename Impact Preview

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps.

**Goal:** Add `rename_preview` tool that shows all locations that would change if a symbol is renamed — definition, call sites, imports. Read-only preview, no file modifications.

**Architecture:** Query nodes table for definitions, edges table for call sites and import references. Aggregate affected files.

---

### Files
- Modify: `shared/code_intelligence.py` — add `rename_preview()` method
- Modify: `shared/code_intel_mcp.py` — wire tool
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestRenamePreview:
    def test_finds_all_references(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.rename_preview("login", "authenticate")
        assert result["symbol"] == "login"
        assert result["new_name"] == "authenticate"
        assert result["total_locations"] >= 1
        assert any(loc["kind"] == "definition" for loc in result["locations"])

    def test_includes_callers(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.rename_preview("login", "authenticate")
        caller_locs = [loc for loc in result["locations"] if loc["kind"] == "caller"]
        assert len(caller_locs) >= 1

    def test_nonexistent_symbol(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.rename_preview("nonexistent_xyz", "new_name")
        assert result["total_locations"] == 0

    def test_shows_files_affected(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.rename_preview("login", "authenticate")
        assert "files_affected" in result
        assert len(result["files_affected"]) >= 1
```

### Implementation

```python
def rename_preview(self, symbol: str, new_name: str) -> dict[str, Any]:
    """Preview all locations that would change if a symbol is renamed. Read-only."""
    locations: list[dict[str, Any]] = []

    # Definitions
    defs = self.conn.execute(
        "SELECT qualified_name, file_path, line_start, line_end, kind "
        "FROM nodes WHERE name=? AND kind != 'document'",
        (symbol,),
    ).fetchall()
    for d in defs:
        locations.append({
            "kind": "definition", "qualified_name": d[0],
            "file": d[1], "line": d[2], "symbol_kind": d[4],
        })

    # Call sites
    callers = self.conn.execute(
        "SELECT source_qname, file_path, line FROM edges "
        "WHERE kind='calls' AND (target_qname LIKE ? OR target_qname=?)",
        (f"%::{symbol}", symbol),
    ).fetchall()
    for c in callers:
        locations.append({"kind": "caller", "qualified_name": c[0], "file": c[1], "line": c[2]})

    # Import references
    imports = self.conn.execute(
        "SELECT source_qname, file_path, line FROM edges "
        "WHERE kind='imports' AND target_qname LIKE ?",
        (f"%.{symbol}",),
    ).fetchall()
    for imp in imports:
        locations.append({"kind": "import", "qualified_name": imp[0], "file": imp[1], "line": imp[2]})

    files_affected = list({loc["file"] for loc in locations if loc.get("file")})
    return {
        "symbol": symbol, "new_name": new_name,
        "locations": locations, "total_locations": len(locations),
        "files_affected": files_affected,
    }
```

### MCP Schema

```python
{
    "name": "rename_preview",
    "description": "Preview all locations that would change if a symbol is renamed. Shows definition, call sites, and imports. Read-only — does NOT modify files.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Current symbol name"},
            "new_name": {"type": "string", "description": "Proposed new name"},
        },
        "required": ["symbol", "new_name"],
    },
}
```

### Dispatch

```python
elif name == "rename_preview":
    return ci.rename_preview(args["symbol"], args["new_name"])
```
