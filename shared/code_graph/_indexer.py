"""AST indexer — parses Python files into the code graph's SQLite tables.

Handles directory walking, AST traversal, node/edge creation, and
call-edge resolution (import-aware disambiguation).
"""

import ast
import sqlite3
from pathlib import Path
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Builtins and common stdlib/object methods that pollute the call graph.
_BUILTIN_SKIP = frozenset({
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "super", "type", "id", "repr", "abs", "round", "min", "max", "sum",
    "any", "all", "next", "iter", "open", "input", "hash", "callable",
    "vars", "dir", "chr", "ord", "hex", "oct", "bin", "format",
    "get", "set", "items", "keys", "values", "update", "pop", "clear",
    "append", "extend", "insert", "remove", "index", "count", "copy",
    "sort", "reverse", "join", "split", "strip", "lstrip", "rstrip",
    "replace", "find", "rfind", "startswith", "endswith", "lower", "upper",
    "format", "encode", "decode", "read", "write", "close", "flush",
    "seek", "tell", "readline", "readlines", "writelines",
    "add", "discard", "intersection", "union", "difference",
    "mkdir", "exists", "is_file", "is_dir", "unlink", "rename",
    "fetchone", "fetchall", "execute", "executemany", "commit", "rollback",
    "info", "debug", "warning", "error", "exception", "critical",
})


class ASTIndexer:
    """Parses Python files and populates the graph's nodes/edges tables."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def index_directory(self, root: str, extensions: tuple = (".py",),
                        path_prefix: str = ""):
        """Parse all matching files under root and build the graph."""
        root_path = Path(root)
        if not root_path.exists():
            logger.warning("Directory not found: %s", root)
            return

        files = sorted(root_path.rglob("*"))
        indexed = 0
        for fp in files:
            if fp.suffix in extensions and fp.is_file():
                self._index_file(fp, root_path, path_prefix)
                indexed += 1

        self._resolve_call_edges()
        logger.info("Indexed %d files from %s", indexed, root)

    def _index_file(self, filepath: Path, root: Path, prefix: str = ""):
        """Parse a single Python file and add its nodes/edges."""
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
                    if id(node) not in class_method_ids:
                        qname = f"{file_path}::{node.name}"
                        self._add_node(
                            "function", node.name, qname, file_path,
                            node.lineno, node.end_lineno or node.lineno,
                            is_test=node.name.startswith("test_"),
                            is_async=isinstance(node, ast.AsyncFunctionDef),
                        )
                        self._extract_calls(node, qname, file_path)

                        for dec in node.decorator_list:
                            dec_name = self._get_name(dec.func if isinstance(dec, ast.Call) else dec)
                            if dec_name and "." in dec_name:
                                self._add_edge("references", f"{file_path}::{dec_name}",
                                               qname, file_path, node.lineno)
            except (AttributeError, TypeError):
                continue  # Malformed AST node

        # Module-level reference extraction: detect function refs in top-level
        # dicts, lists, tuples (e.g. PLATFORM_SCANNERS = {"reed": scan_reed})
        module_qname = f"{file_path}::__module__"
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value if isinstance(node, ast.Assign) else node.value
                if value is not None:
                    self._extract_references(node, module_qname, file_path)

            elif isinstance(node, ast.If):
                if self._is_main_guard(node):
                    self._extract_calls(node, module_qname, file_path)
                    self._extract_references(node, module_qname, file_path)

    @staticmethod
    def _is_main_guard(node: ast.If) -> bool:
        """Check if this is ``if __name__ == '__main__':``."""
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            left = test.left
            right = test.comparators[0] if test.comparators else None
            if (isinstance(left, ast.Name) and left.id == "__name__" and
                    isinstance(right, ast.Constant) and right.value == "__main__"):
                return True
        return False

    def _extract_calls(self, func_node, caller_qname: str, file_path: str):
        """Extract function calls from a function body.

        Three enhancements beyond basic AST call extraction:
        1. self.method() → resolved to ClassName::method at extraction time
        2. Builtin filtering — skips print/len/get/append etc.
        3. Assignment tracking — when `x = foo()` then `x.bar()`, creates
           an edge to `foo` so the call graph captures the relationship.
        """
        parts = caller_qname.split("::")
        enclosing_class_qname = None
        if len(parts) >= 3:
            enclosing_class_qname = f"{parts[0]}::{parts[1]}"

        # Pass 1: collect variable assignments `x = foo()` for tracking
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

                    if enclosing_class_qname and callee.startswith("self."):
                        method_name = callee[5:]
                        if "." not in method_name:
                            callee = f"{enclosing_class_qname}::{method_name}"

                    bare = callee.rsplit(".", 1)[-1] if "." in callee else callee
                    if bare in _BUILTIN_SKIP:
                        continue

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
                except (AttributeError, TypeError):
                    continue  # Malformed AST node

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
        """Extract a dotted name from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._get_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        elif isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        return None

    def _add_node(self, kind, name, qname, file_path, line_start, line_end,
                  is_test=False, is_async=False):
        """Insert a node, ignoring duplicates."""
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO nodes (kind, name, qualified_name, file_path, line_start, line_end, is_test, is_async) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (kind, name, qname, file_path, line_start, line_end, int(is_test), int(is_async)),
            )
        except sqlite3.IntegrityError:
            pass

    def _add_edge(self, kind, source, target, file_path, line):
        """Insert an edge."""
        self.conn.execute(
            "INSERT INTO edges (kind, source_qname, target_qname, file_path, line) VALUES (?,?,?,?,?)",
            (kind, source, target, file_path, line),
        )

    def _resolve_call_edges(self):
        """Resolve bare call/reference edge targets to fully qualified node names.

        Import-aware resolution: uses import edges to disambiguate.
        Handles calls AND references (both stored with bare names from AST).
        """
        # Build lookup: simple_name -> [qualified_name, ...]
        name_to_qnames: dict[str, list[str]] = {}
        for row in self.conn.execute("SELECT name, qualified_name FROM nodes"):
            name_to_qnames.setdefault(row[0], []).append(row[1])

        # Build import map: (file, imported_name) -> module_path
        import_map: dict[tuple[str, str], str] = {}
        for row in self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind='imports'"
        ).fetchall():
            source_file = row[0].split("::")[0] if "::" in row[0] else ""
            import_target = row[1]
            if "." in import_target:
                module_path = import_target.rsplit(".", 1)[0]
                imported_name = import_target.rsplit(".", 1)[1]
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

            bare_name = target.rsplit(".", 1)[-1] if "." in target else target

            candidates = name_to_qnames.get(bare_name, [])
            if not candidates:
                continue

            if len(candidates) == 1:
                updates.append((candidates[0], rowid))
                resolved_count += 1
            else:
                # Try import-aware resolution first
                import_module = import_map.get((source_file, bare_name))
                if import_module:
                    module_file = import_module.replace(".", "/") + ".py"
                    import_match = [c for c in candidates if c.startswith(module_file + "::")]
                    if len(import_match) == 1:
                        updates.append((import_match[0], rowid))
                        resolved_count += 1
                        continue

                # Prefer same file
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

        self.conn.executemany(
            "UPDATE edges SET target_qname=? WHERE rowid=?", updates
        )
        logger.debug("Resolved %d/%d call/reference edges", resolved_count, len(unresolved))
