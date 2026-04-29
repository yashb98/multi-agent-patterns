import ast
from pathlib import Path


ROOTS = [
    Path(__file__).resolve().parents[2] / "jobpulse",
    Path(__file__).resolve().parents[2] / "shared",
]


def _iter_python_files():
    for root in ROOTS:
        yield from root.rglob("*.py")


def test_no_raw_requests_get_in_agent_code():
    violations: list[str] = []

    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get":
                owner = func.value
                if isinstance(owner, ast.Name) and owner.id == "requests":
                    violations.append(str(path))

    assert not violations, f"Use shared.safe_fetch instead of requests.get: {violations}"
