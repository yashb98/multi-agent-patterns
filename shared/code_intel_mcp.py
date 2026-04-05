"""MCP stdio server for Code Intelligence — 8 tools + file watcher.

Exposes CodeIntelligence query methods as MCP tools for Claude Code.
Auto-started via .claude/settings.json MCP configuration.

Run directly: python shared/code_intel_mcp.py
"""

import json
import os
import threading
import time
from pathlib import Path

from shared.code_intelligence import CodeIntelligence, _is_excluded
from shared.logging_config import get_logger

logger = get_logger(__name__)

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
]

# ─── FILE WATCHER ─────────────────────────────────────────────────


def _start_file_watcher(
    ci: CodeIntelligence,
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
        logger.info("watchdog not installed — file watcher disabled")
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
                    logger.debug("reindexing %s", rel)
                    ci.reindex_file(rel, root)
                except Exception as exc:
                    logger.warning("reindex_file failed for %s: %s", abs_path, exc)

    handler = ReindexHandler()
    observer = Observer()
    observer.schedule(handler, root, recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("file watcher started on %s", root)

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
            },
            "required": ["files"],
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
]


# ─── SERVER FACTORY ───────────────────────────────────────────────


def create_mcp_server():  # type: ignore[return]
    """Create and configure the MCP Server with 8 Code Intelligence tools.

    Reads CI_DB_PATH and CI_PROJECT_ROOT from environment variables,
    creates a CodeIntelligence instance, runs a full index if the DB is
    empty, starts the file watcher, and registers all tool handlers.

    Returns the configured mcp.server.Server instance.
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    db_path = os.environ.get("CI_DB_PATH", "data/code_intelligence.db")
    project_root = os.environ.get("CI_PROJECT_ROOT", str(Path.cwd()))

    ci = CodeIntelligence(db_path)

    # Bootstrap: run full index if DB is empty
    node_count: int = ci.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    if node_count == 0:
        logger.info("DB is empty — running full index of %s", project_root)
        stats = ci.index_directory(project_root)
        logger.info("initial index complete: %s", stats)

    # Start file watcher
    _start_file_watcher(ci, project_root)

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
            result = _dispatch(ci, name, arguments)
        except Exception as exc:
            logger.exception("tool %s raised: %s", name, exc)
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
        return ci.impact_analysis(args["files"], max_depth=args.get("max_depth", 2))
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

    asyncio.run(main())
