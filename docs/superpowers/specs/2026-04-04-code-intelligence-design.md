# Unified Code Intelligence Layer — Design Spec

> Persistent AST graph + semantic search, auto-updated in real-time,
> exposed to Claude Code via MCP server + hooks.

**Date:** 2026-04-04
**Status:** Approved
**Author:** Yash + Claude

---

## Problem

Claude Code navigates this codebase (1,002 Python files, ~1,583 functions) using brute-force Grep/Read. Finding a function costs 2-4 Grep calls + 1-2 Read calls (~4,000 tokens). Understanding callers costs 3-8 Grep/Read round-trips (~12,000 tokens). Impact analysis is impossible. A typical debug session burns ~80,000 tokens on navigation alone.

The codebase already has `CodeGraph` (AST-based structural analysis) and `HybridSearch` (FTS5 + vector + RRF), but both run as ephemeral `:memory:` instances — never persistent, never connected to Claude Code.

## Solution

A **Unified Code Intelligence Layer** that:
1. Merges CodeGraph + HybridSearch into a single persistent SQLite database
2. Indexes **every file** in the repo (AST for Python, text search for everything else)
3. Auto-updates in real-time via file watcher + hooks
4. Exposes 8 query tools to Claude Code via MCP server
5. Injects a codebase fingerprint at session start

**Token reduction: 96% per session. Retrieval accuracy: ~60% to ~98%.**

---

## Architecture

```
Claude Code Session
  |
  |-- SessionStart Hook --> Injects codebase fingerprint (400 tokens, once)
  |
  |-- User asks question --> Claude calls MCP tools as needed
  |       |                     |
  |       v                     v
  |  Write/Edit file    MCP Server (stdio, auto-started)
  |       |              |-- find_symbol
  |       v              |-- callers_of / callees_of
  |  PostToolUse Hook    |-- impact_analysis
  |  (reindex file)      |-- risk_report
  |       |              |-- semantic_search
  |       v              |-- module_summary
  |  code_intelligence   |-- recent_changes
  |       .db            |
  |       ^              v
  |       |         File Watcher (watchdog, in MCP process)
  |       |              |-- Monitors project root
  |       |              |-- 500ms debounce
  |       |              |-- reindex_file() on change
  |       |
  |       +---- Git post-commit hook (incremental, background)
```

### Five Components

| Component | File | Responsibility |
|-----------|------|---------------|
| **CodeIntelligence** | `shared/code_intelligence.py` | Unified DB — wraps existing CodeGraph + HybridSearch. Single SQLite file with AST nodes/edges + FTS5 + vector embeddings. All query methods. |
| **MCP Server** | `shared/code_intel_mcp.py` | Stdio MCP server — imports CodeIntelligence, exposes 8 tools. Auto-started by Claude Code via settings.json. Embeds file watcher thread. |
| **PostToolUse Hook** | `.claude/hooks/scripts/reindex-file.py` | After Write/Edit, re-indexes the edited file. Fallback if MCP server isn't running. Target: <200ms. |
| **SessionStart Hook** | `.claude/hooks/scripts/session-primer.py` | On conversation start, prints codebase fingerprint to stdout (injected as context). |
| **Git Hook** | `.git/hooks/post-commit` | Re-indexes changed `.py` files from `git diff`. Runs in background. Safety net. |

### Design Decision: Wrap, Don't Rewrite

`CodeIntelligence` composes the existing `CodeGraph` and `HybridSearch` classes. It does not duplicate their logic. Both classes get a one-line change: accept a file path instead of defaulting to `:memory:`. The wrapper adds the glue layer — unified DB, incremental reindex, session primer, MCP query methods.

Existing tests (101 for CodeGraph, all for HybridSearch) stay green. `risk_aware_reviewer_node` in `shared/agents.py` continues using CodeGraph with `:memory:` for LLM draft analysis — separate use case, unchanged.

---

## Database Schema

Single file at `data/code_intelligence.db`:

```sql
-- ============================================================
-- Structural layer (from CodeGraph)
-- ============================================================

CREATE TABLE nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- 'function', 'class', 'method', 'document'
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    signature TEXT DEFAULT '',       -- full def line (Python only)
    docstring TEXT DEFAULT '',       -- first 200 chars (Python only)
    is_test INTEGER DEFAULT 0,
    is_async INTEGER DEFAULT 0,
    risk_score REAL DEFAULT 0.0,    -- cached, recomputed on reindex
    last_indexed REAL DEFAULT 0.0   -- timestamp for staleness check
);

CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- 'calls', 'imports', 'inherits', 'contains'
    source_qname TEXT NOT NULL,
    target_qname TEXT NOT NULL,
    file_path TEXT,
    line INTEGER
);

CREATE INDEX idx_nodes_file ON nodes(file_path);
CREATE INDEX idx_nodes_qname ON nodes(qualified_name);
CREATE INDEX idx_nodes_risk ON nodes(risk_score DESC);
CREATE INDEX idx_edges_source ON edges(source_qname);
CREATE INDEX idx_edges_target ON edges(target_qname);

-- ============================================================
-- Semantic layer (from HybridSearch)
-- ============================================================

CREATE TABLE documents (
    rowid INTEGER PRIMARY KEY,
    doc_id TEXT UNIQUE NOT NULL,     -- = qualified_name (links to nodes)
    text TEXT NOT NULL,              -- signature + docstring + body keywords
    metadata TEXT DEFAULT '{}'       -- JSON: {kind, file, risk, is_async}
);

CREATE VIRTUAL TABLE documents_fts USING fts5(
    doc_id, text,
    content='documents',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER docs_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, doc_id, text)
    VALUES (new.rowid, new.doc_id, new.text);
END;

CREATE TRIGGER docs_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, doc_id, text)
    VALUES ('delete', old.rowid, old.doc_id, old.text);
END;

CREATE TABLE embeddings (
    doc_id TEXT PRIMARY KEY,         -- = qualified_name
    vector BLOB NOT NULL             -- 512 floats as bytes
);

PRAGMA journal_mode=WAL;
```

### Indexing Tiers

| Tier | Files | Structural (nodes/edges) | Semantic (FTS5 + vector) |
|------|-------|--------------------------|--------------------------|
| **Full AST** | `.py` | Functions, classes, methods, calls, imports, risk scoring | Signature + docstring + body keywords |
| **Text search** | Everything else (except exclusions) | One node per file (kind=`document`) | Full text content (truncated to 5000 chars) |

### Indexing Configuration

```python
# Full AST parsing — structural nodes/edges + semantic search
FULL_INDEX_EXTENSIONS = (".py",)

# Text-only indexing — one document node + semantic search, no AST
# Everything not in EXCLUDE_PATTERNS and not in FULL_INDEX_EXTENSIONS
# gets text-indexed automatically.

EXCLUDE_PATTERNS = {
    ".env", ".env.*",           # Secrets
    "*.pyc", "__pycache__/",    # Bytecode
    ".git/",                    # Git internals
    "node_modules/",            # Dependencies
    "*.db", "*.sqlite",         # Our own databases
    "*.png", "*.jpg", "*.ico", "*.gif", "*.svg",  # Binary images
    "*.woff", "*.ttf", "*.woff2",                  # Fonts
    "*.pdf",                    # Binary docs
    "*.lock",                   # Lock files
    "venv/", ".venv/",          # Virtual environments
    ".claude/worktrees/",       # Claude Code worktrees
}
```

Everything not matching an exclusion pattern is indexed. Python files get full AST analysis. All other text files get FTS5 + vector search.

### Risk Scoring Formula (Python functions only)

| Factor | Weight | Condition |
|--------|--------|-----------|
| Security keywords | +0.25 | auth, password, token, crypt, secret, sql, socket, encrypt, verify, admin, privilege, session, credential, login, permission, sanitize, hash, key, oauth, jwt |
| Fan-in (callers) | +0.05/caller | `min(callers * 0.05, 0.20)` |
| Cross-file callers | +0.10 | Any edge where caller is in a different file |
| No test coverage | +0.30 | Zero `test_*` functions calling it |
| Large function | +0.15 | `line_end - line_start > 50` |
| **Total** | **capped at 1.0** | `min(sum_of_factors, 1.0)` |

---

## MCP Server Tools

8 tools, each with strict output budgets:

### find_symbol
- **Input:** `{ name: string }`
- **Output:** `{ qualified_name, kind, file, line_start, line_end, signature, risk_score, is_async, callers_count, callees_count }`
- **Budget:** ~150 tokens
- **Method:** Exact match on `nodes.name`, fallback to `LIKE %name%`

### callers_of
- **Input:** `{ name: string, max_results?: int (default 20) }`
- **Output:** `{ target, callers: [{ name, file, line }], total }`
- **Budget:** ~200-500 tokens
- **Method:** Indexed edge lookup on `edges.target_qname`

### callees_of
- **Input:** `{ name: string, max_results?: int (default 20) }`
- **Output:** `{ source, callees: [{ name, file, line }], total }`
- **Budget:** ~200-500 tokens
- **Method:** Indexed edge lookup on `edges.source_qname`

### impact_analysis
- **Input:** `{ files: list[string], max_depth?: int (default 2) }`
- **Output:** `{ changed_functions, impacted: [{ name, file, depth, risk }], impacted_files, total_functions, max_risk }`
- **Budget:** ~400-1000 tokens (capped at 30 impacted nodes)
- **Method:** BFS traversal of call graph from changed files' functions

### risk_report
- **Input:** `{ top_n?: int (default 10) }` OR `{ file: string }`
- **Output:** `{ functions: [{ name, file, risk, factors: [string] }] }`
- **Budget:** ~300-800 tokens
- **Method:** Query `nodes` ordered by `risk_score DESC`

### semantic_search
- **Input:** `{ query: string, top_k?: int (default 10) }`
- **Output:** `{ results: [{ name, file, score, snippet }], method: "fts5+vector+rrf" }`
- **Budget:** ~350-800 tokens
- **Method:** FTS5 BM25 + bag-of-words cosine similarity, merged via RRF (k=60)

### module_summary
- **Input:** `{ file: string }`
- **Output:** `{ file, classes: [{ name, methods, lines }], functions: [], avg_risk, high_risk: [string], imports_from: [string], imported_by: [string] }`
- **Budget:** ~300-600 tokens
- **Method:** Query nodes + edges filtered by file_path

### recent_changes
- **Input:** `{ n_commits?: int (default 3) }`
- **Output:** `{ commits: [{ sha, message, files_changed, functions_affected, max_risk_delta }], hotspots: [string], new_high_risk: [] }`
- **Budget:** ~250-500 tokens
- **Method:** `git log` + cross-reference with nodes table

---

## Hooks

### SessionStart Hook

**File:** `.claude/hooks/scripts/session-primer.py`
**Trigger:** Every conversation start
**Output:** ~400 tokens to stdout, auto-injected as context

```
=== Code Intelligence: Codebase Fingerprint ===
Repo: multi_agent_patterns (1,002 py files, 1,583 functions, 4,812 edges)
Last indexed: 2026-04-04 14:32 UTC (7bea95f)

High-risk functions (top 5):
  1. jobpulse/account_manager.py:create_account (0.85)
  2. jobpulse/application_orchestrator.py:_handle_login (0.70)
  3. jobpulse/account_manager.py:get_credentials (0.65)
  4. shared/agents.py:risk_aware_reviewer_node (0.45)
  5. jobpulse/ext_bridge.py:_send_command (0.40)

Recent changes (last 3 commits):
  7bea95f fix(jobpulse): fix _execute_action bug (15 files)
  dc20929 feat(shared): add streaming LLM output (4 files)
  88a392f feat(patterns): integrate experiential learning (6 files)

MCP tools: find_symbol, callers_of, callees_of, impact_analysis,
  risk_report, semantic_search, module_summary, recent_changes
===================================================
```

**Behavior:**
- DB doesn't exist → full `index_directory()` (~3-5s), then print
- DB exists and fresh (<1 hour) → query and print (~50ms)
- DB exists but stale (>1 hour) → incremental update from git log, then print

### PostToolUse Hook

**File:** `.claude/hooks/scripts/reindex-file.py`
**Trigger:** After every Write/Edit
**Action:** Parse file path from tool input, skip if not indexable, call `CodeIntelligence.reindex_file(path)`
**Output:** Silent (no stdout, zero token cost)
**Target:** <200ms
**Purpose:** Fallback if MCP server isn't running

### Git Post-Commit Hook

**File:** `.git/hooks/post-commit`
**Trigger:** After every `git commit`
**Action:** `git diff --name-only HEAD~1 HEAD` → reindex changed files
**Runs in background** (`&`) — doesn't block the commit
**Purpose:** Safety net — catches changes made outside Claude Code

### File Watcher (inside MCP server)

**Library:** `watchdog`
**Runs as:** Background thread in the MCP server process
**Monitors:** Project root, all files except EXCLUDE_PATTERNS
**Debounce:** 500ms — coalesces rapid saves, deduplicates by path
**Action:** `reindex_file()` for each changed path
**Dies when:** Claude Code exits (MCP server stops)

**Three layers of freshness guarantee:**
1. File watcher (primary) — catches ALL changes in real-time
2. PostToolUse hook (fallback) — catches Claude Code edits if MCP server is down
3. Git post-commit hook (safety net) — catches anything watcher missed

---

## CodeIntelligence Class API

```python
class CodeIntelligence:
    """Unified code intelligence — structural graph + semantic search."""

    def __init__(self, db_path: str = "data/code_intelligence.db"):
        """Persistent SQLite DB. Wraps CodeGraph + HybridSearch."""

    # === Indexing ===
    def index_directory(self, root: str) -> dict:
        """Full repo index. Returns {nodes, edges, documents, time_ms}."""

    def reindex_file(self, file_path: str) -> dict:
        """Incremental single-file update.
        1. DELETE old nodes/edges/docs for this file
        2. AST-parse (Python) or text-index (other) -> INSERT
        3. Update FTS5 + embeddings
        4. Recompute risk for affected nodes + their callers
        Returns {nodes_added, edges_added, risk_updated, time_ms}."""

    def reindex_changed(self, since_commit: str = "HEAD~1") -> dict:
        """Re-index files changed since a commit."""

    # === MCP queries ===
    def find_symbol(self, name: str) -> dict | None:
    def callers_of(self, name: str, max_results: int = 20) -> dict:
    def callees_of(self, name: str, max_results: int = 20) -> dict:
    def impact_analysis(self, files: list[str], max_depth: int = 2) -> dict:
    def risk_report(self, top_n: int = 10, file: str | None = None) -> dict:
    def semantic_search(self, query: str, top_k: int = 10) -> list[dict]:
    def module_summary(self, file: str) -> dict:
    def recent_changes(self, n_commits: int = 3) -> dict:

    # === Session primer ===
    def get_primer(self, top_risk: int = 5, n_commits: int = 3) -> str:
        """Formatted codebase fingerprint for SessionStart hook."""

    # === Lifecycle ===
    def close(self) -> None:
```

### Integration with Existing Code

```
CodeIntelligence
  +-- self._graph: CodeGraph(db_path)      <-- existing class, unchanged
  |     +-- nodes, edges tables
  |     +-- index_directory(), callers_of(), risk_report(), etc.
  |
  +-- self._search: HybridSearch(db_path)  <-- existing class, same DB
  |     +-- documents, documents_fts, embeddings tables
  |     +-- add(), query(), remove()
  |
  +-- Glue layer (NEW):
        +-- reindex_file() -- coordinates graph + search updates
        +-- find_symbol() -- graph lookup + search fallback
        +-- semantic_search() -- delegates to HybridSearch
        +-- get_primer() -- queries both for formatted summary
```

**Changes to existing code:**
- `CodeGraph.__init__` — accept file path parameter (default still `:memory:`)
- `HybridSearch.__init__` — accept file path parameter (default still `:memory:`)
- Both share the same SQLite connection when given the same `db_path`

**No changes to:**
- All existing CodeGraph methods (101 tests stay green)
- All existing HybridSearch methods
- `risk_aware_reviewer_node` (still uses `:memory:` for LLM drafts)
- `PatternMemory` (still uses `:memory:` for pattern reuse)

---

## Error Handling

| Failure | Recovery | User Impact |
|---------|----------|-------------|
| DB doesn't exist | SessionStart runs full index (~3-5s) | One-time wait |
| DB corrupted | `PRAGMA integrity_check` on open; if fail, delete and rebuild | Transparent |
| AST parse fails (syntax error) | Skip file, log warning, keep old data | Zero — stale data for one file |
| MCP server crashes | Claude falls back to Grep/Read | Graceful degradation |
| PostToolUse hook timeout (>5s) | Exit silently; file reindexed by watcher or git hook | Zero |
| Concurrent access | WAL mode handles concurrent reads + single writer | Zero |

**Defensive defaults:**
- All MCP tools return `{"error": "..."}` on exception, never crash
- All hooks exit 0 on error, never block Claude Code
- `reindex_file()` wrapped in transaction — atomic or rollback
- FTS5 triggers are AFTER triggers — consistent on partial failure

---

## Settings Configuration

### `.claude/settings.json` (additions)

```json
{
  "mcp": {
    "servers": {
      "code-intelligence": {
        "type": "stdio",
        "command": "python",
        "args": ["shared/code_intel_mcp.py"],
        "env": {
          "CI_DB_PATH": "data/code_intelligence.db"
        }
      }
    }
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/hooks/scripts/session-primer.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/hooks/scripts/reindex-file.py $TOOL_INPUT_PATH"
          }
        ]
      }
    ]
  }
}
```

---

## Sizing Estimates

### Database Size (1,002 Python files + ~250 other files)

| Table | Rows | Size |
|-------|------|------|
| nodes (AST) | ~1,583 | ~200 KB |
| nodes (document) | ~250 | ~30 KB |
| edges | ~5,000 | ~300 KB |
| documents + FTS5 | ~1,850 | ~1.5 MB |
| embeddings | ~1,850 | ~3.8 MB |
| Indexes (5) | — | ~500 KB |
| **Total** | — | **~8 MB** |

### Runtime Resources

| Resource | Cost |
|----------|------|
| MCP server process | ~15-25 MB RAM |
| Watchdog thread | ~2-5 MB RAM |
| Full index time | ~3-5 seconds |
| Incremental reindex (1 file) | ~100-200ms |
| Branch switch (50 files) | ~5-10s (debounced batch) |
| MCP tool query latency | ~5-10ms |

### New Code Estimate

| Component | Lines |
|-----------|-------|
| `shared/code_intelligence.py` | ~300-400 |
| `shared/code_intel_mcp.py` | ~150 |
| `.claude/hooks/scripts/session-primer.py` | ~30 |
| `.claude/hooks/scripts/reindex-file.py` | ~30 |
| `.git/hooks/post-commit` (addition) | ~15 |
| Tests | ~200-300 |
| **Total** | **~750-900 lines** |

---

## Before vs After

### Token Cost

| Workflow | Before | After | Reduction |
|----------|--------|-------|-----------|
| Find function | ~4,000 | ~150 | 96% |
| "Who calls X?" | ~12,000 | ~200 | 98% |
| Impact analysis | ~20,000+ (incomplete) | ~400 | 98% |
| Risk assessment | Not available | ~300 | New |
| Semantic search | ~3,000 (exact only) | ~350 | 88% |
| File overview | ~6,000 | ~300 | 95% |
| Session cold start | ~8,000 (exploration) | ~400 (primer) | 95% |
| **Debug session (10 lookups)** | **~80,000** | **~3,500** | **96%** |
| **Daily (3 sessions)** | **~400,000** | **~15,000** | **96%** |

### Time Complexity

| Operation | Before | After |
|-----------|--------|-------|
| Find function | O(F * L) — grep all files | O(log N) — B-tree index |
| Callers of X | O(F * L) — grep all files | O(C) — indexed edges |
| Impact analysis | Not feasible | O(V + E) — bounded BFS |
| Semantic search | O(F * L) — grep exact only | O(D * 512) — FTS5 + cosine |
| Incremental update | N/A | O(N_file) — single file AST |

### Quality

| Dimension | Before | After |
|-----------|--------|-------|
| Retrieval accuracy | ~60% (Grep false positives) | ~98% (AST-indexed) |
| Capabilities | 3 (find, read, grep) | 11 (8 MCP + 3 hooks) |
| Graph freshness | N/A | <1 second (real-time watcher) |
| Cross-language search | Exact keyword only | Semantic across all file types |
| Disk cost | 0 | 8 MB |
| RAM cost | 0 | 25 MB |

---

## Testing Plan

| Component | Tests |
|-----------|-------|
| `CodeIntelligence` — full index | Index test repo, verify node/edge/doc counts |
| `CodeIntelligence` — incremental reindex | Modify file, reindex, verify updated nodes + risk |
| `CodeIntelligence` — text-only files | Index `.js`, `.md`, verify document nodes + FTS5 |
| MCP `find_symbol` | Exact match, fuzzy match, not found |
| MCP `callers_of` / `callees_of` | Known call chains, max_results cap |
| MCP `impact_analysis` | Single file, multiple files, depth boundary |
| MCP `risk_report` | Top-N, per-file, verify scoring factors |
| MCP `semantic_search` | Keyword hit, conceptual hit, cross-language |
| MCP `module_summary` | File with classes, file with functions only |
| MCP `recent_changes` | With commits, empty repo |
| SessionStart hook | DB exists, DB missing, DB stale |
| PostToolUse hook | Python file edit, non-Python edit, non-file edit |
| File watcher | Single change, rapid changes (debounce), branch switch |
| Error cases | Corrupted DB, syntax error in file, concurrent access |
| Existing tests | CodeGraph 101 tests still pass, HybridSearch tests still pass |
