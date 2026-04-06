"""MCP stdio server for Code Intelligence — 13 tools + file watcher.

Exposes CodeIntelligence query methods as MCP tools for Claude Code.
Auto-started via .claude/settings.json MCP configuration.

IMPORTANT: All heavy imports (code_intelligence, numpy, voyageai, langchain)
are deferred to first tool call so the MCP server can respond to `initialize`
in <100ms. This prevents Claude Code from timing out the connection.

The key insight: `import shared.code_intel_mcp` triggers shared/__init__.py
which imports agents.py → langchain_openai → numpy → 2961 modules (3.6s).
We avoid this by using direct file-level imports instead of package imports.

Run directly: python shared/code_intel_mcp.py
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

# Use stdlib logging directly — avoids importing shared.logging_config
# which triggers shared/__init__.py → 2961 modules → 3.6s delay.
_logger = logging.getLogger("code_intel_mcp")

def _get_logger() -> logging.Logger:
    return _logger

# Lazy-loaded on first tool call — set by _ensure_loaded()
_is_excluded = None  # populated when CodeIntelligence is imported

# ─── TOOL NAMES ───────────────────────────────────────────────────

TOOL_NAMES = [
    "find_symbol",
    "callers_of",
    "callees_of",
    "impact_analysis",
    "risk_report",
    "semantic_search",
    "module_summary",
    "recent_changes",
    "dead_code_report",
    "complexity_hotspots",
    "dependency_cycles",
    "similar_functions",
    "grep_search",
    "diff_impact",
    "test_coverage_map",
    "call_path",
    "batch_find",
    "boundary_check",
]

# ─── FILE WATCHER ─────────────────────────────────────────────────


def _start_file_watcher(
    ci,  # type: CodeIntelligence
    root: str,
    debounce_ms: int = 500,
) -> None:
    """Start a watchdog-based file watcher that reindexes changed files.

    Uses a debounce mechanism to batch rapid writes (e.g. editor auto-save).
    Runs as two daemon threads so it never blocks the process from exiting.
    Silently does nothing if watchdog is not installed.
    """
    try:
        from watchdog.events import FileSystemEventHandler  # type: ignore[import]
        from watchdog.observers import Observer  # type: ignore[import]
    except ImportError:
        _get_logger().info("watchdog not installed — file watcher disabled")
        return

    debounce_sec = debounce_ms / 1000.0
    root_path = Path(root)

    class ReindexHandler(FileSystemEventHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self._pending: dict[str, float] = {}  # abs_path -> scheduled_time
            self._lock = threading.Lock()

        def _schedule(self, abs_path: str) -> None:
            """Schedule a reindex with debounce."""
            try:
                rel = str(Path(abs_path).relative_to(root_path))
            except ValueError:
                return  # outside root
            if _is_excluded(rel) or _is_excluded(Path(abs_path).name):
                return
            with self._lock:
                self._pending[abs_path] = time.monotonic() + debounce_sec

        def on_modified(self, event) -> None:  # type: ignore[override]
            if not event.is_directory:
                self._schedule(event.src_path)

        def on_created(self, event) -> None:  # type: ignore[override]
            if not event.is_directory:
                self._schedule(event.src_path)

        def flush(self) -> None:
            """Process all pending items whose debounce period has elapsed."""
            now = time.monotonic()
            to_process: list[str] = []
            with self._lock:
                ready = [p for p, t in self._pending.items() if now >= t]
                for p in ready:
                    del self._pending[p]
                to_process = ready

            for abs_path in to_process:
                try:
                    rel = str(Path(abs_path).relative_to(root_path))
                    _get_logger().debug("reindexing %s", rel)
                    ci.reindex_file(rel, root)
                except Exception as exc:
                    _get_logger().warning("reindex_file failed for %s: %s", abs_path, exc)

    handler = ReindexHandler()
    observer = Observer()
    observer.schedule(handler, root, recursive=True)
    observer.daemon = True
    observer.start()
    _get_logger().info("file watcher started on %s", root)

    def flush_loop() -> None:
        while True:
            time.sleep(debounce_sec / 2 or 0.1)
            handler.flush()

    flush_thread = threading.Thread(target=flush_loop, daemon=True, name="ci-flush")
    flush_thread.start()


# ─── TOOL SCHEMAS ─────────────────────────────────────────────────

_TOOL_DEFS: list[dict] = [
    {
        "name": "find_symbol",
        "description": (
            "Find a function, class, or method by name. "
            "Returns qualified name, file path, line range, risk score, "
            "and caller/callee counts. Exact match first, fuzzy LIKE fallback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name to look up"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "callers_of",
        "description": (
            "Find all functions that call the given function or method by name. "
            "Returns up to max_results caller records."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Function name to find callers for"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of callers to return (default 20)",
                    "default": 20,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "callees_of",
        "description": (
            "Find all functions called by the given function or method. "
            "Returns up to max_results callee records."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Function name to find callees for"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of callees to return (default 20)",
                    "default": 20,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "impact_analysis",
        "description": (
            "Compute the blast radius from a list of changed files. "
            "Returns changed functions, impacted nodes, impacted files, "
            "and the maximum risk score in the impact zone."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of relative file paths that changed",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "BFS depth limit for impact traversal (default 2)",
                    "default": 2,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum impacted nodes to return (default 100)",
                    "default": 100,
                },
            },
            "required": ["files"],
        },
    },
    {
        "name": "diff_impact",
        "description": (
            "Compute blast radius from a git diff or branch ref. "
            "Pass raw diff text OR a git ref (e.g. 'HEAD~3', 'main..feature'). "
            "Returns changed files, changed functions, impacted nodes, and max risk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff_text": {
                    "type": "string",
                    "description": "Raw unified diff text (from clipboard or PR)",
                    "default": "",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref to diff against (e.g. 'HEAD~3', 'main..feature-branch')",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "BFS depth limit for impact traversal (default 2)",
                    "default": 2,
                },
            },
            "required": [],
        },
    },
    {
        "name": "risk_report",
        "description": (
            "Return the top-N highest-risk functions in the codebase, "
            "optionally filtered to a specific file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of high-risk functions to return (default 10)",
                    "default": 10,
                },
                "file": {
                    "type": "string",
                    "description": "Relative file path to filter by (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "semantic_search",
        "description": (
            "Hybrid FTS5 + Voyage Code 3 semantic search with graph-boosted scoring. "
            "Returns matching nodes with name, file path, score, and snippet. "
            "Optionally provide context_symbol for proximity-aware ranking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language or code query"},
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 10)",
                    "default": 10,
                },
                "context_symbol": {
                    "type": "string",
                    "description": "Qualified name of the symbol being worked on (enables proximity boost)",
                },
                "search_context": {
                    "type": "string",
                    "enum": ["general", "review", "security", "impact"],
                    "description": "Search context for risk boosting (default: general)",
                    "default": "general",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "module_summary",
        "description": (
            "Summarise a source file: its classes, top-level functions, "
            "average risk score, and inter-module dependencies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Relative path to the Python file (e.g. shared/code_graph.py)",
                },
            },
            "required": ["file"],
        },
    },
    {
        "name": "recent_changes",
        "description": (
            "Cross-reference recent git commits with the code graph. "
            "Returns commits with changed files, hotspot files, and new high-risk functions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "n_commits": {
                    "type": "integer",
                    "description": "Number of recent commits to inspect (default 3)",
                    "default": 3,
                },
            },
            "required": [],
        },
    },
    {
        "name": "dead_code_report",
        "description": (
            "Find functions with zero callers — potential dead code. "
            "Cross-checks resolved and unresolved edges to reduce false positives. "
            "Returns confirmed dead functions, removable lines, and dead code percentage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of dead code candidates to return (default 20)",
                    "default": 20,
                },
                "file": {
                    "type": "string",
                    "description": "Filter to a specific file (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "complexity_hotspots",
        "description": (
            "Find functions that are high-risk AND high fan-in — complexity hotspots. "
            "These have maximum blast radius: many callers + high risk. "
            "Sorted by danger_score = risk_score × fan_in."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Number of hotspots to return (default 15)",
                    "default": 15,
                },
            },
            "required": [],
        },
    },
    {
        "name": "dependency_cycles",
        "description": (
            "Detect circular dependencies between modules (file-level A→B→C→A cycles). "
            "These make the codebase hard to refactor and indicate tight coupling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum cycle length to detect (default 4)",
                    "default": 4,
                },
            },
            "required": [],
        },
    },
    {
        "name": "similar_functions",
        "description": (
            "Find functions semantically similar to a given function using Voyage embeddings. "
            "Useful for finding duplicate code, refactoring opportunities, and understanding patterns."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Function or method name to find similar code for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of similar functions to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "grep_search",
        "description": (
            "Search the codebase via regex or literal string, enriched with code graph context. "
            "Each match in a .py file includes enclosing function, risk score, fan-in, and caller count. "
            "Results sorted by risk score (high-risk matches first) by default."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for (or literal if fixed_string=true)",
                },
                "glob": {
                    "type": "string",
                    "description": "File glob filter (e.g. '*.py', '*.md'). Defaults to all files.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to return (default 50)",
                    "default": 50,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context before/after each match (default 0)",
                    "default": 0,
                },
                "fixed_string": {
                    "type": "boolean",
                    "description": "Treat pattern as a literal string (default false)",
                    "default": False,
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["risk", "file"],
                    "description": "Sort order: 'risk' (high-risk first) or 'file' (file order)",
                    "default": "risk",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "test_coverage_map",
        "description": (
            "Map which functions have test coverage and which tests cover them. "
            "Shows covered/uncovered functions and coverage percentage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Filter to a specific file (optional)"},
                "top_n": {"type": "integer", "description": "Max results per category (default 50)", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "call_path",
        "description": (
            "Find shortest call path from source to target function. "
            "Traces through the call graph transitively. "
            "Useful for understanding data flow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source function name"},
                "target": {"type": "string", "description": "Target function name"},
                "max_depth": {"type": "integer", "description": "Max path length (default 6)", "default": 6},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "batch_find",
        "description": (
            "Find multiple symbols at once or match by glob pattern (e.g. '*_handler'). "
            "Returns all found symbols with risk scores."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "description": "List of symbol names to look up"},
                "pattern": {"type": "string", "description": "Glob pattern to match (e.g. '*_handler', 'test_*')"},
                "max_results": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "boundary_check",
        "description": (
            "Check architectural boundary rules — detect forbidden cross-module imports. "
            "Default: shared/ cannot import from jobpulse/, patterns/, mindgraph_app/."
        ),
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
    },
]


# ─── SERVER FACTORY ───────────────────────────────────────────────


def _ensure_loaded():
    """Lazy-load CodeIntelligence on first tool call.

    This keeps the MCP server responsive to initialize in <100ms while
    deferring the import chain until actually needed.

    Note: shared/__init__.py is bypassed at startup (see __main__ block)
    so `from shared.code_intelligence import ...` only pulls in
    code_graph + hybrid_search + logging_config (~157ms, 259 modules)
    instead of the full shared package (~3600ms, 2961 modules).
    """
    global _ci_instance, _is_excluded

    if _ci_instance is not None:
        return _ci_instance

    from shared.code_intelligence import CodeIntelligence, _is_excluded  # noqa: F811

    db_path = os.environ.get("CI_DB_PATH", "data/code_intelligence.db")
    project_root = os.environ.get("CI_PROJECT_ROOT", str(Path.cwd()))

    ci = CodeIntelligence(db_path)

    # Bootstrap: run full index if DB is empty
    node_count: int = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    if node_count == 0:
        _get_logger().info("DB is empty — running full index of %s", project_root)
        stats = ci.index_directory(project_root)
        _get_logger().info("initial index complete: %s", stats)

    # Start file watcher (in background, non-blocking)
    _start_file_watcher(ci, project_root)

    _ci_instance = ci
    return ci


_ci_instance = None


def create_mcp_server():  # type: ignore[return]
    """Create and configure the MCP Server with 13 Code Intelligence tools.

    The server responds to `initialize` immediately (<100ms). Heavy imports
    (numpy, voyageai, code_intelligence) are deferred to the first tool call.
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server("code-intelligence")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _TOOL_DEFS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            ci = _ensure_loaded()
            result = _dispatch(ci, name, arguments)
        except Exception as exc:
            _get_logger().exception("tool %s raised: %s", name, exc)
            result = {
                "status": "error",
                "errorCategory": "transient",
                "message": str(exc),
                "isRetryable": True,
            }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


def _dispatch(ci: CodeIntelligence, name: str, args: dict) -> object:
    """Dispatch a tool call to the appropriate CodeIntelligence method."""
    if name == "find_symbol":
        return ci.find_symbol(args["name"])
    elif name == "callers_of":
        return ci.callers_of(args["name"], max_results=args.get("max_results", 20))
    elif name == "callees_of":
        return ci.callees_of(args["name"], max_results=args.get("max_results", 20))
    elif name == "impact_analysis":
        return ci.impact_analysis(
            args["files"],
            max_depth=args.get("max_depth", 2),
            max_results=args.get("max_results", 100),
        )
    elif name == "diff_impact":
        return ci.diff_impact(
            diff_text=args.get("diff_text", ""),
            ref=args.get("ref"),
            max_depth=args.get("max_depth", 2),
        )
    elif name == "risk_report":
        return ci.risk_report(top_n=args.get("top_n", 10), file=args.get("file"))
    elif name == "semantic_search":
        return ci.semantic_search(
            args["query"],
            top_k=args.get("top_k", 10),
            context_symbol=args.get("context_symbol"),
            search_context=args.get("search_context", "general"),
        )
    elif name == "module_summary":
        return ci.module_summary(args["file"])
    elif name == "recent_changes":
        return ci.recent_changes(n_commits=args.get("n_commits", 3))
    elif name == "dead_code_report":
        return ci.dead_code_report(top_n=args.get("top_n", 20), file=args.get("file"))
    elif name == "complexity_hotspots":
        return ci.complexity_hotspots(top_n=args.get("top_n", 15))
    elif name == "dependency_cycles":
        return ci.dependency_cycles(max_depth=args.get("max_depth", 4))
    elif name == "similar_functions":
        return ci.similar_functions(args["name"], top_k=args.get("top_k", 5))
    elif name == "grep_search":
        return ci.grep_search(
            args["pattern"],
            glob=args.get("glob"),
            max_results=args.get("max_results", 50),
            context_lines=args.get("context_lines", 0),
            fixed_string=args.get("fixed_string", False),
            sort_by=args.get("sort_by", "risk"),
        )
    elif name == "test_coverage_map":
        return ci.test_coverage_map(file=args.get("file"), top_n=args.get("top_n", 50))
    elif name == "call_path":
        return ci.call_path(args["source"], args["target"], max_depth=args.get("max_depth", 6))
    elif name == "batch_find":
        return ci.batch_find(names=args.get("names"), pattern=args.get("pattern"), max_results=args.get("max_results", 50))
    elif name == "boundary_check":
        return ci.boundary_check(rules=args.get("rules"))
    else:
        raise ValueError(f"Unknown tool: {name}")


# ─── ENTRY POINT ──────────────────────────────────────────────────


async def main() -> None:
    from mcp.server.stdio import stdio_server

    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    import sys
    import types

    # ── Resolve paths ──
    # Claude Code runs: python shared/code_intel_mcp.py (cwd = project root)
    # Python sets sys.path[0] = "shared/" but we need project root on path
    # for transitive imports (code_intelligence → code_graph, hybrid_search, etc.)
    _script_dir = Path(__file__).resolve().parent  # .../shared/
    _project_root = str(_script_dir.parent)  # .../multi_agent_patterns/
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    # ── CRITICAL: MCP stdio servers must NEVER write non-JSON to stdout ──
    # shared/logging_config.py attaches StreamHandler(sys.stdout) to root logger.
    # Any log message to stdout corrupts the MCP JSON-RPC protocol and causes
    # Claude Code to drop the connection (this was the root cause of tools not loading).
    #
    # The MCP SDK uses sys.stdout.buffer (the raw binary fd), so we:
    # 1. Save a reference to the real stdout buffer for the SDK
    # 2. Replace sys.stdout with stderr so all print()/logging goes to stderr
    _real_stdout_buffer = sys.stdout.buffer
    sys.stdout = sys.stderr  # All Python-level stdout → stderr

    # ── Bypass shared/__init__.py ──
    # Pre-register 'shared' as an empty namespace package so that
    # `from shared.code_intelligence import ...` loads only the files it needs
    # (~157ms, 259 modules) instead of the full package (~3600ms, 2961 modules).
    if "shared" not in sys.modules:
        pkg = types.ModuleType("shared")
        pkg.__path__ = [str(_script_dir)]
        pkg.__package__ = "shared"
        sys.modules["shared"] = pkg

    # Patch main() to pass the real stdout buffer to stdio_server
    async def _main_patched() -> None:
        from mcp.server.stdio import stdio_server
        from io import TextIOWrapper
        import anyio

        real_stdout = anyio.wrap_file(TextIOWrapper(_real_stdout_buffer, encoding="utf-8"))
        server = create_mcp_server()
        async with stdio_server(stdout=real_stdout) as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_main_patched())
