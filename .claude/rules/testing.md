---
paths: ["tests/**/*.py"]
description: "Testing conventions — isolation, no production DB access"
---

# Testing Conventions

## Database Isolation

- Tests MUST NEVER operate on production databases
- Any test that writes to SQLite MUST patch DB_PATH to a tmp_path fixture
- NEVER add `clear_all()` or `DELETE` to any test without verifying the DB path is temporary

## Test Structure

- Use pytest fixtures for setup/teardown
- Each test file should have an `autouse` fixture for DB isolation if it touches SQLite
- Mock external APIs (Telegram, Gmail, GitHub, Notion) — never make real API calls in tests

## Assertions

- Test both success and error paths
- Verify structured error responses include `errorCategory` and `isRetryable`
- Test that partial results are preserved on failure
