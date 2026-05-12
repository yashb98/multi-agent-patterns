"""DB-retrieval accuracy instrumentation.

Records every wrapped DB lookup to ``data/db_observability.db`` so we can
spot DBs whose results are silently dropped downstream (e.g. the
``screening_defaults.relocation`` rows that returned ``"Yes, within the
UK"`` against a Yes/No options form — the lookup hit, but the value was
discarded by ``_align_screening_to_options``).

Architecture
------------
1. ``@observe_lookup(db, table)`` wraps a read accessor. On every call it
   invokes ``record_lookup`` which writes a tentative row (status
   ``pending``) and pushes a ``LookupRef`` onto a thread-local correlation
   buffer.

2. The form filler (``native_form_filler``) calls
   ``mark_fill_outcome(field_label, intended, actual, status)`` once per
   field after the readback verify. That drains all buffered lookups
   inside the recency window, correlates them by ``intended`` value (or
   the most-recent lookup if the value isn't in the buffer), and updates
   their rows with ``consumed`` / ``dropped`` + ``drop_reason``.

3. Any lookup left in the buffer when its window expires is flushed as
   ``status='unconsumed'`` — it ran, but no fill outcome ever claimed it.

4. ``JOBPULSE_TEST_MODE=1`` short-circuits the SQLite writer so unit
   tests don't pollute the real ``db_observability.db``. Tests that want
   to exercise the writer pass an explicit ``db_path`` via
   ``set_observability_db_path``.

Invariants
----------
- Decorator NEVER changes the wrapped function's return value or type.
- Buffer is bounded (``_BUFFER_MAX``) — long-running daemons can't leak.
- Latency-only failure mode: if SQLite write raises, log and continue;
  observability must never break the apply pipeline.
"""

from __future__ import annotations

import atexit
import functools
import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ── Configuration ────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_DIR / "data" / "db_observability.db"

# Bound the per-thread buffer so daemons don't leak.
_BUFFER_MAX = 200
# A lookup is considered "claimable" by a fill outcome if it happened
# within this many seconds. Anything older is flushed as unconsumed.
_RECENCY_WINDOW_S = 30.0

# ── Drop reasons (closed enum) ───────────────────────────────────────

DROP_OPTION_MISALIGNMENT = "option_misalignment"
DROP_VALIDATION_FAILED = "validation_failed"
DROP_OVERRIDDEN_BY_LLM = "overridden_by_llm"
DROP_HIT_RETURNED_EMPTY = "hit_returned_empty"
DROP_TYPE_COERCION = "type_coercion"
DROP_UNKNOWN = "unknown"

CONSUMED_STATUSES = frozenset({"consumed"})
DROPPED_STATUSES = frozenset({"dropped"})


# ── Thread-local buffer ──────────────────────────────────────────────


@dataclass
class LookupRef:
    """One unfinished observability row sitting on the thread buffer."""

    row_id: int
    db_name: str
    table: str
    key_hash: str
    hit: bool
    value_repr: str
    ts: float
    consumed_at_ts: float | None = None
    consumed_status: str | None = None
    drop_reason: str | None = None


@dataclass
class _ThreadState:
    buffer: list[LookupRef] = field(default_factory=list)


_thread_local = threading.local()
_db_path: Path = _DEFAULT_DB_PATH
_db_lock = threading.Lock()
_test_mode_override: bool | None = None


def _state() -> _ThreadState:
    s = getattr(_thread_local, "state", None)
    if s is None:
        s = _ThreadState()
        _thread_local.state = s
    return s


def set_observability_db_path(path: str | Path) -> None:
    """Override the SQLite destination (used by tests)."""

    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema()


def set_test_mode(enabled: bool | None) -> None:
    """Programmatically toggle test-mode short-circuit (None = read env)."""

    global _test_mode_override
    _test_mode_override = enabled


def _is_test_mode() -> bool:
    if _test_mode_override is not None:
        return _test_mode_override
    return os.environ.get("JOBPULSE_TEST_MODE") == "1"


# ── SQLite writer ────────────────────────────────────────────────────


def _ensure_schema() -> None:
    if _is_test_mode():
        # Tests that explicitly call set_observability_db_path before
        # set_test_mode(False) will already have built the schema.
        return
    with _db_lock:
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lookups (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_name       TEXT NOT NULL,
                    table_name    TEXT NOT NULL,
                    key_hash      TEXT NOT NULL,
                    hit           INTEGER NOT NULL,
                    value_repr    TEXT,
                    latency_ms    REAL,
                    ts            REAL NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    drop_reason   TEXT,
                    field_label   TEXT,
                    intended      TEXT,
                    actual        TEXT,
                    consumed_ts   REAL
                );
                CREATE INDEX IF NOT EXISTS idx_lookups_db_table
                    ON lookups(db_name, table_name);
                CREATE INDEX IF NOT EXISTS idx_lookups_status
                    ON lookups(status);
                CREATE INDEX IF NOT EXISTS idx_lookups_ts
                    ON lookups(ts);
                """
            )
            conn.commit()
        finally:
            conn.close()


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(str(_db_path))


def _insert_pending(
    db_name: str,
    table: str,
    key_hash: str,
    hit: bool,
    value_repr: str,
    latency_ms: float,
    ts: float,
) -> int:
    """Insert a tentative row and return its id."""

    if _is_test_mode():
        return -1
    try:
        _ensure_schema()
        with _db_lock:
            conn = _conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO lookups
                        (db_name, table_name, key_hash, hit, value_repr,
                         latency_ms, ts, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (db_name, table, key_hash, 1 if hit else 0, value_repr,
                     latency_ms, ts),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()
    except Exception as exc:  # pragma: no cover — observability never breaks the pipeline
        logger.warning("db_observability: insert failed: %s", exc)
        return -1


def _update_outcome(
    row_id: int,
    status: str,
    drop_reason: str | None,
    field_label: str | None,
    intended: str | None,
    actual: str | None,
    ts: float,
) -> None:
    if _is_test_mode() or row_id <= 0:
        return
    try:
        with _db_lock:
            conn = _conn()
            try:
                conn.execute(
                    """
                    UPDATE lookups
                       SET status = ?, drop_reason = ?, field_label = ?,
                           intended = ?, actual = ?, consumed_ts = ?
                     WHERE id = ?
                    """,
                    (status, drop_reason, field_label, intended, actual,
                     ts, row_id),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:  # pragma: no cover
        logger.warning("db_observability: update failed: %s", exc)


# ── Public API ───────────────────────────────────────────────────────


def _hash_key(key: Any) -> str:
    try:
        if isinstance(key, (str, bytes)):
            payload = key.encode() if isinstance(key, str) else key
        else:
            payload = json.dumps(key, sort_keys=True, default=str).encode()
    except Exception:
        payload = repr(key).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _value_repr(value: Any, *, limit: int = 200) -> str:
    """Human-readable, length-bounded value repr.

    Never includes raw PII bytes — values are passed through ``repr`` so
    a string ``"Yes, within the UK"`` becomes ``"'Yes, within the UK'"``
    in observability rows. The 200-char limit avoids storing entire
    skill-graph blobs.
    """

    try:
        s = repr(value)
    except Exception:
        s = f"<{type(value).__name__}>"
    if len(s) > limit:
        s = s[: limit - 3] + "..."
    return s


def _is_hit(value: Any) -> bool:
    """Heuristic: did the accessor find anything?

    None / empty string / empty list / empty dict / 0 = miss.
    Everything else = hit. Per-call overrides supported via the
    ``hit_predicate`` decorator argument.
    """

    if value is None:
        return False
    if isinstance(value, (str, bytes, list, tuple, set, dict)):
        return len(value) > 0
    if value is False:
        return False
    return True


def record_lookup(
    db_name: str,
    table: str,
    key: Any,
    value: Any,
    *,
    hit: bool | None = None,
    latency_ms: float = 0.0,
) -> LookupRef:
    """Record one DB read. Returns the LookupRef so callers (and the
    decorator) can update later if needed."""

    ts = time.time()
    if hit is None:
        hit = _is_hit(value)
    key_hash = _hash_key(key)
    value_repr = _value_repr(value)
    row_id = _insert_pending(db_name, table, key_hash, hit, value_repr,
                              latency_ms, ts)
    ref = LookupRef(
        row_id=row_id,
        db_name=db_name,
        table=table,
        key_hash=key_hash,
        hit=hit,
        value_repr=value_repr,
        ts=ts,
    )
    state = _state()
    state.buffer.append(ref)
    # Bound the buffer (FIFO drop). Anything we drop is flushed as
    # unconsumed first so it isn't lost from the DB.
    if len(state.buffer) > _BUFFER_MAX:
        overflow = state.buffer[: len(state.buffer) - _BUFFER_MAX]
        state.buffer = state.buffer[len(state.buffer) - _BUFFER_MAX :]
        for r in overflow:
            _flush_unconsumed(r)
    return ref


def _flush_unconsumed(ref: LookupRef) -> None:
    if ref.consumed_status is not None:
        return
    _update_outcome(
        ref.row_id,
        status="unconsumed",
        drop_reason=None,
        field_label=None,
        intended=None,
        actual=None,
        ts=time.time(),
    )
    ref.consumed_status = "unconsumed"


def flush_buffer() -> None:
    """Mark every still-pending lookup older than the window as
    unconsumed. Call at end of a fill loop / process exit."""

    cutoff = time.time() - _RECENCY_WINDOW_S
    state = _state()
    keep: list[LookupRef] = []
    for ref in state.buffer:
        if ref.consumed_status is not None:
            continue
        if ref.ts < cutoff:
            _flush_unconsumed(ref)
        else:
            keep.append(ref)
    state.buffer = keep


def flush_all() -> None:
    """Force-flush every buffered lookup as unconsumed regardless of age.

    Used at apply-pipeline shutdown (``apply_job`` finally block) and in
    tests.
    """

    state = _state()
    for ref in state.buffer:
        if ref.consumed_status is None:
            _flush_unconsumed(ref)
    state.buffer = []


def _atexit_flush() -> None:
    """Final safety net: any pending buffer entries at process exit get
    flushed as ``unconsumed`` so observability rows aren't permanently
    stuck in ``status='pending'`` after a pipeline that never reached
    the form-fill stage (e.g., screening rejected the JD)."""

    try:
        flush_all()
    except Exception:  # pragma: no cover
        pass


atexit.register(_atexit_flush)


def mark_fill_outcome(
    field_label: str,
    intended: Any,
    actual: Any,
    *,
    drop_reason: str | None = None,
) -> int:
    """Tag every recent lookup whose ``value_repr`` matches the
    ``intended`` value with consumed/dropped status.

    Returns the number of lookup rows updated. The status logic:

    - If ``actual`` matches ``intended`` (string-equal, case-insensitive)
      → ``status='consumed'``.
    - Otherwise → ``status='dropped'`` with ``drop_reason`` (defaults to
      ``DROP_OPTION_MISALIGNMENT`` when not provided — that's the most
      common case in the apply pipeline).

    Rows tagged here are also removed from the thread buffer so they
    can't be tagged twice.
    """

    state = _state()
    intended_s = "" if intended is None else str(intended)
    actual_s = "" if actual is None else str(actual)
    is_consumed = (
        intended_s != ""
        and actual_s != ""
        and intended_s.strip().casefold() == actual_s.strip().casefold()
    )
    status = "consumed" if is_consumed else "dropped"
    reason = None if is_consumed else (drop_reason or DROP_OPTION_MISALIGNMENT)
    intended_repr = _value_repr(intended)

    cutoff = time.time() - _RECENCY_WINDOW_S
    matched: list[LookupRef] = []
    fallback: list[LookupRef] = []
    keep: list[LookupRef] = []

    for ref in state.buffer:
        if ref.consumed_status is not None:
            continue
        if ref.ts < cutoff:
            _flush_unconsumed(ref)
            continue
        if intended_repr and intended_repr in ref.value_repr:
            matched.append(ref)
        else:
            fallback.append(ref)
            keep.append(ref)

    targets = matched if matched else fallback[-3:]

    ts = time.time()
    for ref in targets:
        _update_outcome(
            ref.row_id,
            status=status,
            drop_reason=reason,
            field_label=field_label,
            intended=intended_s,
            actual=actual_s,
            ts=ts,
        )
        ref.consumed_status = status
        ref.drop_reason = reason
        ref.consumed_at_ts = ts

    state.buffer = [r for r in keep if r.consumed_status is None]
    return len(targets)


# ── Decorator ────────────────────────────────────────────────────────


def observe_lookup(
    db_name: str,
    table: str,
    *,
    key_arg: int | str | None = 1,
    hit_predicate: Callable[[Any], bool] | None = None,
) -> Callable:
    """Decorator: record a lookup every time the wrapped accessor is
    called.

    Parameters
    ----------
    db_name, table:
        Free-form identifiers for the DB file and the logical table /
        collection inside it. Example: ``observe_lookup("user_profile",
        "screening_defaults")``.
    key_arg:
        Index (after ``self``) or kwarg name of the key argument. Default
        ``1`` matches ``def get(self, key)`` style. Set to ``None`` if
        the function takes no key — the lookup is keyed on its
        positional args.
    hit_predicate:
        Optional callable to override the default hit/miss heuristic.

    The decorator:
    - Times the call.
    - Calls ``record_lookup`` with the result.
    - Returns the original result unchanged.
    - Catches and logs SQLite/observability failures so the wrapped
      accessor never breaks.
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                value = fn(*args, **kwargs)
            except Exception:
                # Don't record errors as lookups — re-raise so the caller
                # sees the real exception.
                raise
            latency_ms = (time.perf_counter() - t0) * 1000.0

            try:
                key = _extract_key(args, kwargs, key_arg)
                hit = hit_predicate(value) if hit_predicate else None
                record_lookup(
                    db_name=db_name,
                    table=table,
                    key=key,
                    value=value,
                    hit=hit,
                    latency_ms=latency_ms,
                )
            except Exception as exc:  # pragma: no cover
                logger.debug("observe_lookup record failed for %s.%s: %s",
                             db_name, table, exc)
            return value

        return wrapper

    return deco


def _extract_key(args: tuple, kwargs: dict, key_arg: int | str | None) -> Any:
    if key_arg is None:
        return {"args": args[1:] if args else (), "kwargs": kwargs}
    if isinstance(key_arg, int):
        idx = key_arg
        if idx < len(args):
            return args[idx]
        return kwargs.get("key", None) or kwargs.get("question_type", None)
    if isinstance(key_arg, str):
        if key_arg in kwargs:
            return kwargs[key_arg]
        # Try positional fallback assuming bound method (self at 0).
        if len(args) > 1:
            return args[1]
    return None
