#!/usr/bin/env python3
"""Auto-update stats line in CLAUDE.md and README.md.

Counts Python files, LOC, tests, and databases, then patches the Stats section.
Run after code changes to keep docs in sync with reality.

Usage:
    python scripts/update_stats.py          # Update both files
    python scripts/update_stats.py --check  # Check only, exit 1 if stale
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
README_MD = ROOT / "README.md"

EXCLUDE_DIRS = {
    ".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".claude",
    ".worktrees", "frontend/node_modules", "dist", "build", ".eggs", ".tox", ".nox",
    "site-packages",
}


def count_python_files() -> int:
    return len([
        p for p in ROOT.rglob("*.py")
        if not any(exc in p.parts for exc in EXCLUDE_DIRS)
    ])


def count_loc() -> int:
    total = 0
    for p in ROOT.rglob("*.py"):
        if any(exc in p.parts for exc in EXCLUDE_DIRS):
            continue
        try:
            total += len(p.read_text().splitlines())
        except Exception:
            pass
    return total


def count_tests() -> int:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--co", "-q"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=30
        )
        # Last line: "148 tests collected in 4.97s"
        match = re.search(r"(\d+) tests? collected", result.stdout)
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


def count_databases() -> int:
    """Count production databases in data/ (top-level only, excludes browser profiles)."""
    data_dir = ROOT / "data"
    if not data_dir.exists():
        return 0
    return len(list(data_dir.glob("*.db")))


def count_dashboards() -> int:
    """Count HTML dashboards served by the app (frontend + static)."""
    count = 0
    for d in [ROOT / "frontend", ROOT / "static"]:
        if d.exists():
            count += len(list(d.glob("*.html")))
    return count


def build_stats_line(loc: int, files: int, tests: int, databases: int, dashboards: int) -> str:
    # Round LOC to nearest 500
    loc_rounded = round(loc / 500) * 500
    return f"~{loc_rounded:,} LOC | {files} Python files | {databases} databases | {tests} tests | {dashboards} dashboards | 5 Telegram bots | 3 platforms"


def update_file(path: Path, stats_line: str) -> bool:
    """Update the stats line in a file. Returns True if changed."""
    text = path.read_text()

    if path.name == "CLAUDE.md":
        # Match the line under ## Stats
        pattern = r"(~[\d,]+ LOC \|.+platforms)"
        replacement = stats_line
    elif path.name == "README.md":
        # Match the bold stats line
        pattern = r"(\*\*~[\d,]+ LOC\*\*.+platforms\*\*)"
        bold_line = " | ".join(f"**{s.strip()}**" for s in stats_line.split("|"))
        replacement = bold_line
    else:
        return False

    new_text, count = re.subn(pattern, replacement, text, count=1)
    if count == 0:
        print(f"  WARNING: Stats pattern not found in {path.name}")
        return False

    if new_text == text:
        return False

    path.write_text(new_text)
    return True


def main():
    check_only = "--check" in sys.argv

    print("Counting stats...")
    loc = count_loc()
    files = count_python_files()
    tests = count_tests()
    databases = count_databases()
    dashboards = count_dashboards()

    stats_line = build_stats_line(loc, files, tests, databases, dashboards)
    print(f"  {stats_line}")

    changed = []
    for path in [CLAUDE_MD, README_MD]:
        if path.exists():
            if update_file(path, stats_line):
                changed.append(path.name)
                print(f"  Updated {path.name}")
            else:
                print(f"  {path.name} already up to date")

    if check_only and changed:
        print(f"\nStats are stale in: {', '.join(changed)}")
        sys.exit(1)

    if changed:
        print(f"\nUpdated: {', '.join(changed)}")
    else:
        print("\nAll stats up to date.")


if __name__ == "__main__":
    main()
