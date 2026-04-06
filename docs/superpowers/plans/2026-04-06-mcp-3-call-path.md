# call_path — Transitive Call Chain Query

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps.

**Goal:** Add `call_path` tool that finds the shortest call path from function A to function B through the call graph.

**Architecture:** BFS through forward call edges from source to target. Resolves bare names to qualified names. Returns the full path with depth.

---

### Files
- Modify: `shared/code_intelligence.py` — add `call_path()` method
- Modify: `shared/code_intel_mcp.py` — wire tool
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestCallPath:
    def test_finds_direct_path(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.call_path("check_access", "login")
        assert result["found"] is True
        assert len(result["path"]) >= 2
        assert any("check_access" in n for n in result["path"])
        assert any("login" in n for n in result["path"])

    def test_no_path_returns_empty(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.call_path("login", "nonexistent_func")
        assert result["found"] is False
        assert result["path"] == []

    def test_path_includes_depth(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.call_path("check_access", "verify_token")
        assert "depth" in result

    def test_max_depth_limits_search(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.call_path("check_access", "verify_token", max_depth=1)
        assert "found" in result
```

### Implementation

```python
def call_path(self, source: str, target: str, max_depth: int = 6) -> dict[str, Any]:
    """Find shortest call path from source to target via BFS."""
    from collections import deque as _deque

    src_row = self.conn.execute(
        "SELECT qualified_name FROM nodes WHERE name=? AND kind IN ('function','method') LIMIT 1",
        (source,),
    ).fetchone()
    src_qname = src_row[0] if src_row else source

    tgt_row = self.conn.execute(
        "SELECT qualified_name FROM nodes WHERE name=? AND kind IN ('function','method') LIMIT 1",
        (target,),
    ).fetchone()
    tgt_qname = tgt_row[0] if tgt_row else target

    forward: dict[str, list[str]] = {}
    for row in self.conn.execute(
        "SELECT source_qname, target_qname FROM edges WHERE kind='calls'"
    ).fetchall():
        forward.setdefault(row[0], []).append(row[1])

    visited: dict[str, str | None] = {src_qname: None}
    queue = _deque([(src_qname, 0)])

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        if current == tgt_qname or current.endswith(f"::{target}"):
            path = [current]
            node = current
            while visited[node] is not None:
                node = visited[node]
                path.append(node)
            path.reverse()
            return {"found": True, "source": src_qname, "target": current, "path": path, "depth": len(path) - 1}

        for neighbor in forward.get(current, []):
            if neighbor not in visited:
                visited[neighbor] = current
                queue.append((neighbor, depth + 1))

    return {"found": False, "source": src_qname, "target": tgt_qname, "path": [], "depth": 0}
```

### MCP Schema

```python
{
    "name": "call_path",
    "description": "Find shortest call path from source to target function. Traces through the call graph transitively. Useful for understanding data flow.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source function name"},
            "target": {"type": "string", "description": "Target function name"},
            "max_depth": {"type": "integer", "description": "Max path length (default 6)", "default": 6},
        },
        "required": ["source", "target"],
    },
}
```

### Dispatch

```python
elif name == "call_path":
    return ci.call_path(args["source"], args["target"], max_depth=args.get("max_depth", 6))
```
