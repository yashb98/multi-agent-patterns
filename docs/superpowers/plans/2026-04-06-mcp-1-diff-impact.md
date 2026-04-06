# diff_impact — Change-Aware Impact Analysis

> **For agentic workers:** See `2026-04-06-mcp-wiring-reference.md` for MCP wiring steps. Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Add `diff_impact` tool that takes raw diff text or a git ref and returns the blast radius of uncommitted/PR changes.

**Architecture:** Parse unified diff for file paths (regex), or run `git diff <ref> --name-only` subprocess, then delegate to existing `impact_analysis()`.

---

### Files
- Modify: `shared/code_intelligence.py` — add `diff_impact()` after `impact_analysis()` (~line 889)
- Modify: `shared/code_intel_mcp.py` — add tool schema + dispatch (see wiring reference)
- Test: `tests/test_code_intelligence.py`

### Tests

```python
class TestDiffImpact:
    def test_diff_impact_detects_changed_functions(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        diff_text = textwrap.dedent("""\
            diff --git a/auth.py b/auth.py
            --- a/auth.py
            +++ b/auth.py
            @@ -4,7 +4,7 @@ class AuthManager:
                 def verify_token(self, token: str) -> bool:
            -        return hashlib.sha256(token.encode()).hexdigest() == self._stored
            +        return hashlib.sha256(token.encode()).hexdigest() == self._secret
        """)
        result = ci.diff_impact(diff_text)
        assert "changed_files" in result
        assert "auth.py" in result["changed_files"]
        assert "impacted" in result
        assert result["total_impacted"] >= 0

    def test_diff_impact_empty_diff(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.diff_impact("")
        assert result["changed_files"] == []
        assert result["total_impacted"] == 0

    def test_diff_impact_from_git(self, ci, sample_project):
        ci.index_directory(str(sample_project))
        result = ci.diff_impact(ref="HEAD", root=str(sample_project))
        assert "changed_files" in result
```

### Implementation

```python
def diff_impact(
    self, diff_text: str = "", *, ref: str | None = None,
    root: str | None = None, max_depth: int = 2, max_results: int = 100,
) -> dict[str, Any]:
    """Blast radius from a git diff or ref."""
    import re as _re

    if ref and not diff_text:
        _root = root or self._project_root
        try:
            result = subprocess.run(
                ["git", "diff", ref, "--name-only"],
                capture_output=True, text=True, timeout=5, cwd=_root,
            )
            changed_files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()] if result.returncode == 0 else []
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            changed_files = []
    elif diff_text:
        changed_files = list(dict.fromkeys(
            m.group(1) for m in _re.finditer(r"^(?:---|\+\+\+) [ab]/(.+)$", diff_text, _re.MULTILINE)
        ))
    else:
        return {"changed_files": [], "changed_functions": [], "impacted": [], "impacted_files": [], "total_impacted": 0, "max_risk": 0.0}

    if not changed_files:
        return {"changed_files": [], "changed_functions": [], "impacted": [], "impacted_files": [], "total_impacted": 0, "max_risk": 0.0}

    result = self.impact_analysis(changed_files, max_depth=max_depth, max_results=max_results)
    result["changed_files"] = changed_files
    return result
```

### MCP Schema

```python
{
    "name": "diff_impact",
    "description": "Compute blast radius from a git diff or branch ref. Pass raw diff text OR a git ref (e.g. 'HEAD~3', 'main..feature'). Returns changed files, impacted nodes, and max risk.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "diff_text": {"type": "string", "description": "Raw unified diff text", "default": ""},
            "ref": {"type": "string", "description": "Git ref to diff against (e.g. 'HEAD~3', 'main..feature-branch')"},
            "max_depth": {"type": "integer", "description": "BFS depth limit (default 2)", "default": 2},
        },
        "required": [],
    },
}
```

### Dispatch

```python
elif name == "diff_impact":
    return ci.diff_impact(diff_text=args.get("diff_text", ""), ref=args.get("ref"), max_depth=args.get("max_depth", 2))
```
