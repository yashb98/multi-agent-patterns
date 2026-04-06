"""Lightweight Code Graph — AST-based structural analysis for generated code.

Parses Python source files into a SQLite-backed graph of functions, classes,
imports, and call relationships. Computes impact radius and risk scores so
the Reviewer agent can prioritize inspection of high-risk code.

Inspired by code-review-graph (github.com/tirth8205/code-review-graph):
- AST parsing → nodes (functions, classes) + edges (calls, imports)
- Impact radius via BFS traversal
- Risk scoring based on security keywords, fan-in, test coverage

Usage:
    graph = CodeGraph(":memory:")  # or path to SQLite file
    graph.index_directory("/path/to/generated/project")
    risk_report = graph.risk_report()
    impact = graph.impact_radius(["src/auth.py"])
"""

import ast
import sqlite3
from pathlib import Path
from collections import deque
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ─── SECURITY KEYWORDS ────────────────────────────────────────────
# Functions containing these keywords in their name get a risk boost.

# Tier 1: HIGH-CONFIDENCE — almost always security-relevant, always flag.
_HIGH_CONFIDENCE_KEYWORDS = frozenset({
    "auth", "password", "crypt", "secret", "encrypt", "credential",
    "oauth", "jwt", "privilege", "admin",
})

# Tier 2: CONTEXT-DEPENDENT — only flag when function name also contains a
# security-context word (e.g. verify_user_token is risky, count_tokens is not).
_CONTEXT_DEPENDENT_KEYWORDS = frozenset({
    "verify", "token", "session", "sql", "hash", "key",
    "login", "socket", "sanitize", "permission",
})

_SECURITY_CONTEXT_WORDS = frozenset({
    "auth", "user", "password", "cred", "login", "access",
    "perm", "secret", "account", "secure", "cert",
})

# Union kept for backward compatibility (e.g. external callers).
SECURITY_KEYWORDS = _HIGH_CONFIDENCE_KEYWORDS | _CONTEXT_DEPENDENT_KEYWORDS

# Builtins and common stdlib/object methods that pollute the call graph.
# Stripping these avoids thousands of unresolvable edges (e.g. .get(), .append()).
_BUILTIN_SKIP = frozenset({
    # Python builtins
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "super", "type", "id", "repr", "abs", "round", "min", "max", "sum",
    "any", "all", "next", "iter", "open", "input", "hash", "callable",
    "vars", "dir", "chr", "ord", "hex", "oct", "bin", "format",
    # Common object/dict/list/str methods
    "get", "set", "items", "keys", "values", "update", "pop", "clear",
    "append", "extend", "insert", "remove", "index", "count", "copy",
    "sort", "reverse", "join", "split", "strip", "lstrip", "rstrip",
    "replace", "find", "rfind", "startswith", "endswith", "lower", "upper",
    "format", "encode", "decode", "read", "write", "close", "flush",
    "seek", "tell", "readline", "readlines", "writelines",
    # Common patterns that are always method calls on objects
    "add", "discard", "intersection", "union", "difference",
    "mkdir", "exists", "is_file", "is_dir", "unlink", "rename",
    "fetchone", "fetchall", "execute", "executemany", "commit", "rollback",
    "info", "debug", "warning", "error", "exception", "critical",
})


class CodeGraph:
    """SQLite-backed code knowledge graph with impact analysis."""

    def __init__(self, db_path: str = ":memory:", conn: sqlite3.Connection | None = None):
        if conn is not None:
            self.conn = conn
        else:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,          -- 'function', 'class', 'method'
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                line_start INTEGER,
                line_end INTEGER,
                is_test INTEGER DEFAULT 0,
                is_async INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,          -- 'calls', 'imports', 'inherits', 'contains'
                source_qname TEXT NOT NULL,
                target_qname TEXT NOT NULL,
                file_path TEXT,
                line INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
            CREATE INDEX IF NOT EXISTS idx_nodes_qname ON nodes(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_qname);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_qname);
        """)

        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "pagerank" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN pagerank REAL DEFAULT 0.0")
        if "community_id" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN community_id INTEGER")
        if "fan_in" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fan_in INTEGER DEFAULT 0")
        if "fan_out" not in existing:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN fan_out INTEGER DEFAULT 0")

        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_community ON nodes(community_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_pagerank ON nodes(pagerank DESC)")
        self.conn.commit()

    # ─── INDEXING ──────────────────────────────────────────────────

    def index_directory(self, root: str, extensions: tuple = (".py",),
                        path_prefix: str = ""):
        """Parse all matching files under root and build the graph.

        Args:
            root: Directory to recursively scan.
            extensions: File extensions to parse.
            path_prefix: Prefix prepended to relative paths (e.g., "shared/"
                         when indexing a subdirectory but wanting paths relative
                         to the project root).
        """
        root_path = Path(root)
        files = [f for f in root_path.rglob("*") if f.suffix in extensions
                 and "__pycache__" not in str(f)
                 and ".venv" not in str(f)
                 and "node_modules" not in str(f)]

        for filepath in files:
            try:
                self._index_file(filepath, root_path, path_prefix)
            except Exception as e:
                logger.debug("Failed to parse %s: %s", filepath.name, e)

        self.conn.commit()
        self._resolve_call_edges()
        self.conn.commit()
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        resolved = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls' AND target_qname LIKE '%::%'"
        ).fetchone()[0]
        total_calls = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls'"
        ).fetchone()[0]
        logger.info(
            "Indexed %d files → %d nodes, %d edges (%d/%d call edges resolved)",
            len(files), node_count, edge_count, resolved, total_calls,
        )

    def _index_file(self, filepath: Path, root: Path, prefix: str = ""):
        """Parse a single Python file into nodes and edges."""
        source = filepath.read_text(encoding="utf-8", errors="replace")
        rel_path = (prefix.rstrip("/") + "/" + str(filepath.relative_to(root))) if prefix else str(filepath.relative_to(root))

        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            return

        self._walk_ast(tree, rel_path)

    def _walk_ast(self, tree: ast.Module, file_path: str):
        """Extract functions, classes, calls, imports from an AST.

        Uses a parent-tracking visitor instead of ast.walk to correctly
        distinguish top-level functions from class methods.
        """
        # First pass: collect class bodies so we know which FunctionDefs are methods
        class_method_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    class_method_ids.add(id(item))

        for node in ast.walk(tree):
            try:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.asname or alias.name
                        self._add_edge("imports", f"{file_path}::__module__",
                                       name, file_path, node.lineno)

                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        self._add_edge("imports", f"{file_path}::__module__",
                                       f"{module}.{alias.name}", file_path, node.lineno)

                elif isinstance(node, ast.ClassDef):
                    qname = f"{file_path}::{node.name}"
                    self._add_node("class", node.name, qname, file_path,
                                   node.lineno, node.end_lineno or node.lineno,
                                   is_test=node.name.startswith("Test"))

                    for base in node.bases:
                        base_name = self._get_name(base)
                        if base_name:
                            self._add_edge("inherits", qname, base_name, file_path, node.lineno)

                    # Methods inside the class
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            method_qname = f"{qname}::{item.name}"
                            self._add_node(
                                "method", item.name, method_qname, file_path,
                                item.lineno, item.end_lineno or item.lineno,
                                is_test=item.name.startswith("test_"),
                                is_async=isinstance(item, ast.AsyncFunctionDef),
                            )
                            self._add_edge("contains", qname, method_qname, file_path, item.lineno)
                            self._extract_calls(item, method_qname, file_path)

                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Top-level functions only — methods are handled in the ClassDef branch
                    if id(node) not in class_method_ids:
                        qname = f"{file_path}::{node.name}"
                        self._add_node(
                            "function", node.name, qname, file_path,
                            node.lineno, node.end_lineno or node.lineno,
                            is_test=node.name.startswith("test_"),
                            is_async=isinstance(node, ast.AsyncFunctionDef),
                        )
                        self._extract_calls(node, qname, file_path)

                        # Decorator detection: @router.get(), @app.post(), etc.
                        # create a reference edge so decorated functions aren't dead
                        for dec in node.decorator_list:
                            dec_name = self._get_name(dec.func if isinstance(dec, ast.Call) else dec)
                            if dec_name and "." in dec_name:
                                self._add_edge("references", f"{file_path}::{dec_name}",
                                               qname, file_path, node.lineno)
            except Exception:
                # Skip nodes that fail to process — don't abort the whole file
                continue

        # Module-level reference extraction: detect function refs in top-level
        # dicts, lists, tuples (e.g. PLATFORM_SCANNERS = {"reed": scan_reed})
        module_qname = f"{file_path}::__module__"
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value if isinstance(node, ast.Assign) else node.value
                if value is not None:
                    self._extract_references(node, module_qname, file_path)

            # if __name__ == "__main__": — extract calls as edges from __main__
            elif isinstance(node, ast.If):
                if self._is_main_guard(node):
                    self._extract_calls(node, module_qname, file_path)
                    self._extract_references(node, module_qname, file_path)

    @staticmethod
    def _is_main_guard(node: ast.If) -> bool:
        """Check if an ast.If is `if __name__ == "__main__":`."""
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            if isinstance(test.ops[0], ast.Eq):
                left = test.left
                right = test.comparators[0] if test.comparators else None
                left_is_name = isinstance(left, ast.Name) and left.id == "__name__"
                right_is_main = isinstance(right, ast.Constant) and right.value == "__main__"
                return left_is_name and right_is_main
        return False

    def _extract_calls(self, func_node, caller_qname: str, file_path: str):
        """Extract function calls from a function body.

        Three enhancements beyond basic AST call extraction:
        1. self.method() → resolved to ClassName::method at extraction time
        2. Builtin filtering — skips print/len/get/append etc.
        3. Assignment tracking — when `x = foo()` then `x.bar()`, creates
           an edge to `foo` so the call graph captures the relationship.
        """
        # Determine enclosing class for self.* resolution
        # caller_qname format: "file.py::ClassName::method_name"
        parts = caller_qname.split("::")
        enclosing_class_qname = None
        if len(parts) >= 3:
            # file::Class::method — class is parts[0]::parts[1]
            enclosing_class_qname = f"{parts[0]}::{parts[1]}"

        # Pass 1: collect variable assignments `x = foo()` for tracking
        # Only tracks simple Name = Call patterns within the function body
        var_to_func: dict[str, str] = {}
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    func_name = self._get_name(node.value.func)
                    if func_name:
                        var_to_func[target.id] = func_name

        # Pass 2: extract call edges
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                try:
                    callee = self._get_name(node.func)
                    if not callee:
                        continue

                    # Resolve self.method() → Class::method (same class)
                    if enclosing_class_qname and callee.startswith("self."):
                        method_name = callee[5:]  # strip "self."
                        # Only resolve simple self.method, not self.attr.method
                        if "." not in method_name:
                            callee = f"{enclosing_class_qname}::{method_name}"

                    # Skip builtins — they pollute the graph with unresolvable noise
                    bare = callee.rsplit(".", 1)[-1] if "." in callee else callee
                    if bare in _BUILTIN_SKIP:
                        continue

                    # Assignment tracking: x.bar() → also link to foo if x = foo()
                    if "." in callee:
                        var_name = callee.split(".")[0]
                        if var_name in var_to_func and var_name != "self":
                            source_func = var_to_func[var_name]
                            source_bare = source_func.rsplit(".", 1)[-1] if "." in source_func else source_func
                            if source_bare not in _BUILTIN_SKIP:
                                self._add_edge("calls", caller_qname, source_func,
                                               file_path, getattr(node, "lineno", 0))

                    self._add_edge("calls", caller_qname, callee, file_path,
                                   getattr(node, "lineno", 0))
                except Exception:
                    continue

        # Pass 3: detect dynamic function references (dict values, list elements,
        # keyword args, positional args that are bare Names — not calls)
        self._extract_references(func_node, caller_qname, file_path)

    def _extract_references(self, func_node, caller_qname: str, file_path: str):
        """Detect function references passed dynamically (not as direct calls).

        General approach: collect ALL ast.Name nodes in the function body,
        then subtract the ones already emitted as call-edge targets.
        The remainder are potential function references (dict values, list
        elements, return values, assignments, default args, callbacks, etc.).

        False positives (variable names that aren't functions) are eliminated
        at resolution time — unresolved references that don't match any known
        function/class node in the graph are simply left unresolved and ignored
        by callers_of/dead_code queries.
        """
        # Collect names already used as call targets (the .func of ast.Call)
        call_targets: set[str] = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                callee = self._get_name(node.func)
                if callee:
                    bare = callee.rsplit(".", 1)[-1] if "." in callee else callee
                    call_targets.add(bare)

        # Collect function parameter names (these are local variables, not references)
        param_names: set[str] = set()
        if isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in func_node.args.args + func_node.args.posonlyargs + func_node.args.kwonlyargs:
                param_names.add(arg.arg)
            if func_node.args.vararg:
                param_names.add(func_node.args.vararg.arg)
            if func_node.args.kwarg:
                param_names.add(func_node.args.kwarg.arg)

            # Also extract references from default argument values
            for default in func_node.args.defaults + func_node.args.kw_defaults:
                if isinstance(default, ast.Name) and default.id not in _BUILTIN_SKIP:
                    self._add_edge("references", caller_qname, default.id,
                                   file_path, getattr(default, "lineno", 0))

        # Walk all ast.Name nodes — any bare name that isn't a call target,
        # parameter, builtin, or constant is a potential function reference
        seen: set[str] = set()
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Name):
                continue
            name = node.id
            if name in seen or name in call_targets or name in param_names:
                continue
            if name in _BUILTIN_SKIP:
                continue
            # Skip ALL_CAPS (constants like MAX_RETRIES), single-char (i, x, _)
            if name.isupper() or len(name) <= 1:
                continue
            # Skip 'self', 'cls', common non-function names
            if name in ("self", "cls", "True", "False", "None"):
                continue
            seen.add(name)
            self._add_edge("references", caller_qname, name,
                           file_path, getattr(node, "lineno", 0))

    def _get_name(self, node) -> Optional[str]:
        """Extract a name string from an AST node. Returns None for complex expressions."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._get_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        elif isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        elif isinstance(node, ast.Call):
            # e.g., foo()() — extract the inner function name
            return self._get_name(node.func)
        return None

    def _add_node(self, kind, name, qname, file_path, line_start, line_end,
                  is_test=False, is_async=False):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO nodes (kind, name, qualified_name, file_path, line_start, line_end, is_test, is_async) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (kind, name, qname, file_path, line_start, line_end, int(is_test), int(is_async)),
            )
        except sqlite3.IntegrityError:
            pass

    def _add_edge(self, kind, source, target, file_path, line):
        self.conn.execute(
            "INSERT INTO edges (kind, source_qname, target_qname, file_path, line) VALUES (?,?,?,?,?)",
            (kind, source, target, file_path, line),
        )

    # ─── POST-INDEX EDGE RESOLUTION ─────────────────────────────

    def _resolve_call_edges(self):
        """Resolve bare call-edge targets to fully qualified node names.

        Call edges are stored with bare names (e.g. 'set_run_id',
        'self._init_schema') because AST only provides the local name.
        This pass matches them to actual node qualified_names after all
        files are indexed, dramatically increasing graph connectivity.

        Import-aware resolution: uses import edges to disambiguate.
        When file A does `from shared.agents import get_llm`, the import
        edge maps `shared.agents.get_llm` -> `shared/agents.py::get_llm`.
        This lets us resolve calls like `get_llm()` in file A to the
        correct definition even with multiple candidates.
        """
        # Build lookup: simple_name -> [qualified_name, ...]
        name_to_qnames: dict[str, list[str]] = {}
        for row in self.conn.execute("SELECT name, qualified_name FROM nodes"):
            name_to_qnames.setdefault(row[0], []).append(row[1])

        # Build import map: (file, imported_name) -> module_path
        # e.g. ("patterns/peer_debate.py", "get_llm") -> "shared.agents"
        import_map: dict[tuple[str, str], str] = {}
        for row in self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind='imports'"
        ).fetchall():
            source_file = row[0].split("::")[0] if "::" in row[0] else ""
            import_target = row[1]  # e.g. "shared.agents.get_llm"
            if "." in import_target:
                module_path = import_target.rsplit(".", 1)[0]  # "shared.agents"
                imported_name = import_target.rsplit(".", 1)[1]  # "get_llm"
                import_map[(source_file, imported_name)] = module_path

        # Process all unresolved call/reference edges (those without :: in target)
        unresolved = self.conn.execute(
            "SELECT rowid, target_qname, source_qname FROM edges "
            "WHERE kind IN ('calls', 'references') AND target_qname NOT LIKE '%::%'"
        ).fetchall()

        resolved_count = 0
        updates = []

        for edge in unresolved:
            rowid, target, source_qname = edge[0], edge[1], edge[2]
            source_file = source_qname.split("::")[0] if "::" in source_qname else ""

            # Strip prefixes: self.foo -> foo, module.foo.bar -> bar
            bare_name = target.rsplit(".", 1)[-1] if "." in target else target

            candidates = name_to_qnames.get(bare_name, [])
            if not candidates:
                continue

            if len(candidates) == 1:
                # Unambiguous — single match
                updates.append((candidates[0], rowid))
                resolved_count += 1
            else:
                # Try import-aware resolution first: check if source file
                # imported this name from a specific module
                import_module = import_map.get((source_file, bare_name))
                if import_module:
                    # Convert module path to file path: shared.agents -> shared/agents.py
                    module_file = import_module.replace(".", "/") + ".py"
                    import_match = [c for c in candidates if c.startswith(module_file + "::")]
                    if len(import_match) == 1:
                        updates.append((import_match[0], rowid))
                        resolved_count += 1
                        continue

                # Disambiguate: prefer same file, then same directory
                same_file = [c for c in candidates if c.startswith(source_file + "::")]
                if same_file:
                    updates.append((same_file[0], rowid))
                    resolved_count += 1
                    continue

                # Same directory
                source_dir = source_file.rsplit("/", 1)[0] if "/" in source_file else ""
                if source_dir:
                    same_dir = [c for c in candidates if c.startswith(source_dir + "/")]
                    if len(same_dir) == 1:
                        updates.append((same_dir[0], rowid))
                        resolved_count += 1
                        continue

                # Pick the non-test candidate if only one remains
                non_test = [c for c in candidates if "test_" not in c.lower()]
                if len(non_test) == 1:
                    updates.append((non_test[0], rowid))
                    resolved_count += 1

        # Batch update
        self.conn.executemany(
            "UPDATE edges SET target_qname=? WHERE rowid=?", updates
        )
        logger.debug("Resolved %d/%d call edges", resolved_count, len(unresolved))

    # ─── QUERIES ──────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return aggregate graph statistics."""
        nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        files = self.conn.execute("SELECT COUNT(DISTINCT file_path) FROM nodes").fetchone()[0]
        functions = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE kind IN ('function','method')").fetchone()[0]
        classes = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='class'").fetchone()[0]
        tests = self.conn.execute("SELECT COUNT(*) FROM nodes WHERE is_test=1").fetchone()[0]
        return {
            "nodes": nodes, "edges": edges, "files": files,
            "functions": functions, "classes": classes, "tests": tests,
        }

    def callers_of(self, name: str) -> list[dict]:
        """Find all functions that call or reference the given name."""
        rows = self.conn.execute(
            "SELECT source_qname, file_path, line FROM edges "
            "WHERE kind IN ('calls', 'references') AND target_qname LIKE ?",
            (f"%{name}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def callees_of(self, qname: str) -> list[dict]:
        """Find all functions called by the given qualified name."""
        rows = self.conn.execute(
            "SELECT target_qname, file_path, line FROM edges WHERE kind='calls' AND source_qname=?",
            (qname,),
        ).fetchall()
        return [dict(r) for r in rows]

    def functions_in_file(self, file_path: str) -> list[dict]:
        """List all functions/methods in a file."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE file_path=? AND kind IN ('function','method') ORDER BY line_start",
            (file_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── IMPACT RADIUS ─────────────────────────────────────────────

    def impact_radius(self, changed_files: list[str], max_depth: int = 2,
                       max_results: int = 100) -> dict:
        """Compute blast radius from changed files via BFS.

        Uses hub-node dampening: nodes with fan_in above the p95 threshold
        are not expanded further (they connect to too much of the graph).
        Uses exact qname matching for backward edges instead of LIKE wildcards.
        Pre-loads adjacency lists for batch efficiency.

        Returns:
            impacted_files: set of files that could be affected
            impacted_nodes: list of node dicts in the blast radius
            depth_map: {file: min_depth} showing how far the impact reaches
        """
        # Collect all nodes in changed files
        seed_qnames = set()
        for f in changed_files:
            rows = self.conn.execute(
                "SELECT qualified_name FROM nodes WHERE file_path=?", (f,)
            ).fetchall()
            seed_qnames.update(r[0] for r in rows)

        if not seed_qnames:
            return {"impacted_files": set(), "impacted_nodes": [], "depth_map": {}}

        # Hub-node dampening: compute fan_in threshold (p95)
        p95_row = self.conn.execute(
            "SELECT fan_in FROM nodes WHERE fan_in > 0 "
            "ORDER BY fan_in DESC LIMIT 1 OFFSET "
            "(SELECT MAX(1, COUNT(*)/20) FROM nodes WHERE fan_in > 0)"
        ).fetchone()
        hub_threshold = max(p95_row[0] if p95_row else 20, 10)

        # Pre-load fan_in for dampening checks
        hub_qnames: set[str] = set()
        for row in self.conn.execute(
            "SELECT qualified_name FROM nodes WHERE fan_in > ?", (hub_threshold,)
        ).fetchall():
            hub_qnames.add(row[0])

        # Pre-load forward adjacency: source -> [targets]
        forward: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind='calls'"
        ).fetchall():
            forward.setdefault(row[0], []).append(row[1])

        # Pre-load backward adjacency: target -> [sources]
        backward: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT target_qname, source_qname FROM edges WHERE kind='calls'"
        ).fetchall():
            backward.setdefault(row[0], []).append(row[1])

        # BFS with hub dampening
        visited: dict[str, int] = {qn: 0 for qn in seed_qnames}  # qname -> depth
        queue = deque((qn, 0) for qn in seed_qnames)
        depth_map = {f: 0 for f in changed_files}

        while queue:
            qname, depth = queue.popleft()
            if depth >= max_depth:
                continue

            # Skip expanding hub nodes (too many connections, pollutes results)
            if qname in hub_qnames and qname not in seed_qnames:
                continue

            next_depth = depth + 1

            # Forward: what does this call?
            for target in forward.get(qname, []):
                if target not in visited:
                    visited[target] = next_depth
                    queue.append((target, next_depth))

            # Backward: who calls this? (exact qname match, not LIKE)
            for source in backward.get(qname, []):
                if source not in visited:
                    visited[source] = next_depth
                    queue.append((source, next_depth))

        # Resolve visited qnames to nodes (batch query, cap at max_results)
        # Sort by depth first (closest impact first), then limit
        sorted_qnames = sorted(visited.keys(), key=lambda q: visited[q])
        if len(sorted_qnames) > max_results:
            sorted_qnames = sorted_qnames[:max_results]

        impacted_files: set[str] = set()
        impacted: list[dict] = []

        # Batch fetch in chunks to avoid SQL parameter limits
        for i in range(0, len(sorted_qnames), 500):
            chunk = sorted_qnames[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE qualified_name IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                node = dict(row)
                node["impact_depth"] = visited.get(row["qualified_name"], max_depth)
                impacted.append(node)
                fp = row["file_path"]
                impacted_files.add(fp)
                if fp not in depth_map:
                    depth_map[fp] = visited.get(row["qualified_name"], max_depth)

        return {
            "impacted_files": impacted_files,
            "impacted_nodes": impacted,
            "depth_map": depth_map,
        }

    # ─── RISK SCORING ──────────────────────────────────────────────

    def compute_risk_score(self, qname: str) -> float:
        """Compute a 0-1 risk score for a function/method.

        Factors:
        - Security keyword in name: +0.25
        - High fan-in (many callers): +0.05 per caller, cap 0.20
        - No test coverage: +0.30
        - Large function (>50 lines): +0.15
        - Cross-file callers: +0.10
        """
        node = self.conn.execute(
            "SELECT * FROM nodes WHERE qualified_name=?", (qname,)
        ).fetchone()
        if not node:
            return 0.0

        score = 0.0
        name_lower = node["name"].lower()

        # Security keywords — two-tier matching to reduce false positives
        has_high_confidence = any(kw in name_lower for kw in _HIGH_CONFIDENCE_KEYWORDS)
        has_context_dependent = any(kw in name_lower for kw in _CONTEXT_DEPENDENT_KEYWORDS)
        has_security_context = any(ctx in name_lower for ctx in _SECURITY_CONTEXT_WORDS)
        if has_high_confidence or (has_context_dependent and has_security_context):
            score += 0.25

        # Fan-in (callers) — use exact name suffix match to reduce false positives
        callers = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls'",
            (f"%{node['name']}",),
        ).fetchone()[0]
        score += min(callers * 0.05, 0.20)

        # Cross-file callers
        cross_file = self.conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM edges WHERE target_qname LIKE ? AND kind='calls' AND file_path != ?",
            (f"%{node['name']}", node["file_path"]),
        ).fetchone()[0]
        if cross_file > 0:
            score += 0.10

        # Test coverage
        tested = self.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_qname LIKE ? AND kind='calls' AND source_qname LIKE '%test_%'",
            (f"%{node['name']}",),
        ).fetchone()[0]
        if tested == 0:
            score += 0.30

        # Function size
        line_span = (node["line_end"] or 0) - (node["line_start"] or 0)
        if line_span > 50:
            score += 0.15

        return min(score, 1.0)

    def risk_report(self, top_n: int = 20) -> list[dict]:
        """Return top-N highest-risk functions across the codebase.

        Used by the Reviewer agent to prioritize what to inspect.
        """
        functions = self.conn.execute(
            "SELECT qualified_name, name, file_path, line_start, line_end FROM nodes "
            "WHERE kind IN ('function', 'method') ORDER BY file_path, line_start"
        ).fetchall()

        scored = []
        for fn in functions:
            risk = self.compute_risk_score(fn["qualified_name"])
            if risk > 0.0:
                scored.append({
                    "qualified_name": fn["qualified_name"],
                    "name": fn["name"],
                    "file_path": fn["file_path"],
                    "line_start": fn["line_start"],
                    "line_end": fn["line_end"],
                    "risk_score": risk,
                })

        scored.sort(key=lambda x: x["risk_score"], reverse=True)
        return scored[:top_n]

    # ─── GRAPH SIGNALS ─────────────────────────────────────────────

    def compute_fan_in_out(self) -> None:
        """Compute and cache fan-in/fan-out counts for all nodes."""
        self.conn.execute("UPDATE nodes SET fan_in = 0, fan_out = 0")
        self.conn.execute("""
            UPDATE nodes SET fan_in = (
                SELECT COUNT(*) FROM edges
                WHERE edges.target_qname = nodes.qualified_name
                  AND edges.kind IN ('calls', 'references')
            )
        """)
        self.conn.execute("""
            UPDATE nodes SET fan_out = (
                SELECT COUNT(*) FROM edges
                WHERE edges.source_qname = nodes.qualified_name AND edges.kind = 'calls'
            )
        """)
        self.conn.commit()

    def compute_pagerank(self, iterations: int = 15, damping: float = 0.85) -> None:
        """Compute PageRank over the call graph. Undirected edges (Sourcegraph finding)."""
        nodes = self.conn.execute("SELECT qualified_name FROM nodes").fetchall()
        if not nodes:
            return

        qnames = [r[0] for r in nodes]
        n = len(qnames)
        rank = {q: 1.0 / n for q in qnames}

        # Build adjacency (undirected — both directions count)
        neighbors: dict[str, list[str]] = {q: [] for q in qnames}
        edges = self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind = 'calls'"
        ).fetchall()
        for src, tgt in edges:
            if src in neighbors:
                neighbors[src].append(tgt)
            if tgt in neighbors:
                neighbors[tgt].append(src)

        degree = {q: len(neighbors[q]) for q in qnames}

        for _ in range(iterations):
            new_rank = {}
            for q in qnames:
                s = sum(rank.get(nb, 0) / max(degree.get(nb, 1), 1) for nb in neighbors[q])
                new_rank[q] = (1 - damping) / n + damping * s
            rank = new_rank

        updates = [(rank[q], q) for q in qnames]
        self.conn.executemany("UPDATE nodes SET pagerank = ? WHERE qualified_name = ?", updates)
        self.conn.commit()

    def compute_communities(self) -> None:
        """Compute Leiden communities. Falls back to file-based grouping."""
        nodes = self.conn.execute("SELECT qualified_name, file_path FROM nodes").fetchall()
        if not nodes:
            return

        qnames = [r[0] for r in nodes]
        file_paths = {r[0]: r[1] for r in nodes}

        try:
            import igraph as ig
            import leidenalg

            # Build igraph
            qname_to_idx = {q: i for i, q in enumerate(qnames)}
            g = ig.Graph(n=len(qnames), directed=False)

            edges_data = self.conn.execute(
                "SELECT source_qname, target_qname FROM edges WHERE kind = 'calls'"
            ).fetchall()
            ig_edges = []
            for src, tgt in edges_data:
                if src in qname_to_idx and tgt in qname_to_idx:
                    ig_edges.append((qname_to_idx[src], qname_to_idx[tgt]))
            if ig_edges:
                g.add_edges(ig_edges)

            partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
            updates = [(partition.membership[i], q) for i, q in enumerate(qnames)]

        except ImportError:
            logger.info("leidenalg/igraph not installed — using file-based communities")
            # Fallback: group by file_path hash
            file_to_id: dict[str, int] = {}
            counter = 0
            updates = []
            for q in qnames:
                fp = file_paths.get(q, "unknown")
                if fp not in file_to_id:
                    file_to_id[fp] = counter
                    counter += 1
                updates.append((file_to_id[fp], q))

        self.conn.executemany("UPDATE nodes SET community_id = ? WHERE qualified_name = ?", updates)
        self.conn.commit()

    def close(self):
        self.conn.close()
