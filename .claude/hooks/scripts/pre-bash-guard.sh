#!/usr/bin/env bash
# PreToolUse hook: block dangerous commands + production DB operations
COMMAND="$1"

# Block destructive system commands
for pattern in "rm -rf /" "rm -rf ~" "rm -rf \$HOME" "mkfs" "dd if=" "chmod -R 777 /"; do
    if echo "$COMMAND" | grep -qE "$pattern"; then
        echo "BLOCKED: Dangerous command pattern: $pattern" >&2
        exit 1
    fi
done

# Block direct destructive operations on production databases
if echo "$COMMAND" | grep -qE "(rm|sqlite3.*DROP|DELETE FROM).*(data/.*\.db)"; then
    echo "BLOCKED: Direct operation on production database. Use tmp_path in tests." >&2
    exit 1
fi

exit 0
