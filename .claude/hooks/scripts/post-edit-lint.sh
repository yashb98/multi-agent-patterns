#!/usr/bin/env bash
# PostToolUse hook: auto-format Python files after every Claude edit
FILE_PATH="$1"
if [[ "$FILE_PATH" == *.py ]]; then
    ruff check "$FILE_PATH" --fix --quiet 2>/dev/null
    ruff format "$FILE_PATH" --quiet 2>/dev/null
fi
exit 0
