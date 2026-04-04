#!/usr/bin/env python3
"""PostToolUse hook — reindexes a file after Write/Edit.

Called by Claude Code after every Write/Edit tool use.
Target: <200ms. Silent output (no stdout = zero token cost).
Fallback if MCP server file watcher isn't running.
"""

import json
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

# Parse tool input from environment/stdin
tool_input = os.environ.get("TOOL_INPUT", "")
if not tool_input:
    try:
        tool_input = sys.stdin.read()
    except Exception:
        sys.exit(0)

# Extract file path
file_path = None
try:
    data = json.loads(tool_input)
    file_path = data.get("file_path") or data.get("path")
except (json.JSONDecodeError, TypeError):
    file_path = tool_input.strip()

if not file_path:
    sys.exit(0)

# Make relative to project root
try:
    rel_path = os.path.relpath(file_path, project_root)
except ValueError:
    sys.exit(0)

DB_PATH = os.environ.get("CI_DB_PATH", os.path.join(project_root, "data", "code_intelligence.db"))

# Only reindex if DB exists
if not os.path.exists(DB_PATH):
    sys.exit(0)

try:
    from shared.code_intelligence import CodeIntelligence
    ci = CodeIntelligence(db_path=DB_PATH)
    ci.reindex_file(rel_path, project_root)
    ci.close()
except Exception:
    sys.exit(0)
