"""Shared I/O utilities — OpenAI calls, file locking, SQLite atomicity."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. safe_openai_call — timeout + None-safe wrapper
# ---------------------------------------------------------------------------


def safe_openai_call(
    client: Any,
    *,
    model: str = "gpt-5-mini",
    messages: list[dict[str, str]],
    temperature: float = 0.5,
    timeout: float = 60.0,
    caller: str = "",
    **kwargs: Any,
) -> str | None:
    """Call OpenAI chat completions with timeout and None-safety.

    Returns content string on success, None on any failure.
    Never raises — logs the error instead.
    """
    try:
        # gpt-5-mini only supports default temperature (1)
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": timeout,
            **kwargs,
        }
        if not model.startswith("gpt-5"):
            call_kwargs["temperature"] = temperature
        response = client.chat.completions.create(**call_kwargs)
        if not response.choices:
            logger.warning("safe_openai_call(%s): empty choices list", caller)
            return None

        content = response.choices[0].message.content
        if content is None:
            logger.warning("safe_openai_call(%s): response content is None", caller)
            return None

        return content

    except Exception as exc:
        logger.error("safe_openai_call(%s): %s: %s", caller, type(exc).__name__, exc)
        return None


# ---------------------------------------------------------------------------
# 3. locked_json_file — atomic read-modify-write with file locking
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def locked_json_file(
    path: Path,
    default: Any = None,
) -> Generator[Any, None, None]:
    """Read-modify-write a JSON file with file locking.

    - Acquires an exclusive lock before reading.
    - Yields the parsed data for mutation.
    - Writes back atomically (tmp + rename) on clean exit.
    - Does NOT write back if the body raises an exception.
    - Creates the file with `default` if it doesn't exist.
    """
    import fcntl

    if default is None:
        default = []

    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(json.dumps(default), encoding="utf-8")

    with open(path, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            raw = fh.read()
            data = json.loads(raw) if raw.strip() else default

            yield data

            # Write back atomically — only reached if body didn't raise
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.rename(path)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# 4. atomic_sqlite — exclusive transaction with auto-commit/rollback
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def atomic_sqlite(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """SQLite context manager with BEGIN EXCLUSIVE for atomic operations.

    - Acquires exclusive lock on the database.
    - Auto-commits on clean exit.
    - Auto-rolls back on exception.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
