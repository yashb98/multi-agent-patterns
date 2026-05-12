r"""Extract (file, function, line, call_pattern) for every LLM call site.

Used by `docs/audits/cache-llm-catalog.md` (S1 of the cache-or-LLM audit).
AST-based so multi-line strings and decorators don't confuse the
enclosing-function lookup. Run via:

    python3 docs/audits/_tools/extract_llm_call_sites.py \
        $(grep -rln "cognitive_llm_call\|smart_llm_call\
\|chat\.completions\.create\|chat\.completions\.acreate\
\|responses\.create\|ChatOpenAI(\|get_llm()\|get_openai_client()" \
        --include="*.py" jobpulse/ shared/ \
        | grep -v __pycache__ | grep -v worktrees | sort) \
        shared/dynamic_agent_factory.py \
        shared/experiential_learning.py \
        shared/persona_evolution.py \
        shared/prompt_optimizer.py \
        shared/parallel_executor.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

CALL_PATTERNS = [
    (r'cognitive_llm_call\s*\(', 'cognitive_llm_call'),
    (r'smart_llm_call\s*\(', 'smart_llm_call'),
    (r'\.chat\.completions\.create\s*\(', 'chat.completions.create'),
    (r'\.chat\.completions\.acreate\s*\(', 'chat.completions.acreate'),
    (r'\.responses\.create\s*\(', 'responses.create'),
    (r'\bChatOpenAI\s*\(', 'ChatOpenAI'),
    (r'\bself\._llm\.invoke\s*\(', 'self._llm.invoke'),
    (r'\bself\.llm\.invoke\s*\(', 'self.llm.invoke'),
    (r'\bllm\.invoke\s*\(', 'llm.invoke'),
    (r'\bllm\.ainvoke\s*\(', 'llm.ainvoke'),
    (r'litellm\.completion\s*\(', 'litellm.completion'),
    (r'litellm\.acompletion\s*\(', 'litellm.acompletion'),
]
SKIP_DEFS = {"cognitive_llm_call", "smart_llm_call", "_direct_llm_call"}


def function_table(tree: ast.AST) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}{child.name}" if prefix else child.name
                rows.append((child.lineno, child.end_lineno or child.lineno, qname))
                walk(child, prefix=qname + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")
            else:
                walk(child, prefix=prefix)

    walk(tree)
    return rows


def find_enclosing(rows: list[tuple[int, int, str]], line: int) -> tuple[int, int, str] | None:
    best = None
    for s, e, name in rows:
        if s <= line <= e:
            if best is None or (e - s) < (best[1] - best[0]):
                best = (s, e, name)
    return best


def scan_file(path: str) -> list[dict]:
    text = Path(path).read_text(errors='ignore')
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    fn_table = function_table(tree)
    lines = text.splitlines()
    rows: list[dict] = []
    for i, line in enumerate(lines):
        ln = i + 1
        stripped = line.lstrip()
        if stripped.startswith(('#', 'from ', 'import ')):
            continue
        if any(f"def {n}" in line for n in SKIP_DEFS):
            continue
        for rx, label in CALL_PATTERNS:
            if re.search(rx, line):
                fn = find_enclosing(fn_table, ln)
                rows.append({
                    'file': path,
                    'function': fn[2] if fn else '<module>',
                    'fn_line': fn[0] if fn else 0,
                    'call_line': ln,
                    'pattern': label,
                })
                break
    return rows


def main(files: list[str]) -> None:
    all_rows: list[dict] = []
    for f in files:
        all_rows.extend(scan_file(f))
    by_fn: dict[tuple[str, str], dict] = {}
    for r in all_rows:
        key = (r['file'], r['function'])
        if key not in by_fn:
            by_fn[key] = {
                'file': r['file'], 'function': r['function'], 'fn_line': r['fn_line'],
                'count': 0, 'patterns': set(), 'call_lines': [],
            }
        by_fn[key]['count'] += 1
        by_fn[key]['patterns'].add(r['pattern'])
        by_fn[key]['call_lines'].append(r['call_line'])
    print('file\tfunction\tfn_line\tcount\tpatterns\tcall_lines')
    for v in sorted(by_fn.values(), key=lambda x: (x['file'], x['fn_line'])):
        print(
            f"{v['file']}\t{v['function']}\t{v['fn_line']}\t{v['count']}\t"
            f"{','.join(sorted(v['patterns']))}\t{','.join(str(l) for l in v['call_lines'])}"
        )


if __name__ == '__main__':
    main(sys.argv[1:])
