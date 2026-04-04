#!/usr/bin/env python3
"""SessionStart hook — prints codebase fingerprint to stdout.

Output is injected into the Claude Code conversation as context (~400 tokens).
If DB doesn't exist, runs full index first (~3-5s one-time cost).
"""

import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

DB_PATH = os.environ.get("CI_DB_PATH", os.path.join(project_root, "data", "code_intelligence.db"))

try:
    from shared.code_intelligence import CodeIntelligence

    ci = CodeIntelligence(db_path=DB_PATH)

    # Check if DB needs indexing
    stats = ci._graph.get_stats()
    if stats["nodes"] == 0:
        ci.index_directory(project_root)

    print(ci.get_primer())
    ci.close()
except Exception as e:
    # Hook must never fail — exit silently
    print(f"[Code Intelligence unavailable: {e}]", file=sys.stderr)
    sys.exit(0)
