# Screening HITL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable mid-form human-in-the-loop question answering so the AI agent never bails on unknown screening questions. Every human answer is cached so the same question becomes autonomous next time.

**Architecture:** New `question_router` primitive (`jobpulse/question_router.py`) backed by SQLite table `pending_questions` plus an in-process `asyncio.Future` registry keyed by `question_id`. New `notification_router` (`shared/notifications/router.py`) with a single `emit(NotificationEvent)` that fans out to multiple sinks. Telegram is sink #1 today; FCM and WebSocket sinks are no-op stubs that activate when the NEURALIS mobile app ships (Phase 1A and Phase 1B per `docs/superpowers/specs/mobile-app-integration/00-design-overview.md:157-184` and `03-phase-1b-voice-push-offline-agents.md`). The hook lives in `jobpulse/form_engine/field_mapper.py::screen_questions` — already async — at the spot where both `ScreeningPipeline.answer()` AND the legacy LLM batch fall through with empty answers (after line 524). On resolve, every human answer is persisted to all 3 caches: `ScreeningSemanticCache.cache(...)`, `ProfileStore.screening_defaults` row, and `CorrectionCapture.record_corrections(...)` so the existing learning chain fires. Browser keep-alive runs as a sibling `asyncio.create_task` so Workday/iCIMS pages don't time out while the human types.

**Domain event vs notification event:** `AgentQuestionEvent` is a higher-level dataclass with question-specific fields (`qid`, `question_text`, `options`, `field_type`, `source_url`). `NotificationEvent` is the transport contract per the mobile spec. The Telegram sink converts the former to the latter with `category="approvals"` and inline button actions.

**Async/sync boundary:** The hook lives in `screen_questions` (already `async def`) — NOT in `ScreeningPipeline.answer()` (sync). `ScreeningPipeline` continues to return `confidence=0.0, source="no_answer"` for unknowns. The async caller (`screen_questions`) detects empty answers and decides whether to ask the human via `await question_router.ask_batch(...)`. This avoids any `run_coroutine_threadsafe` complexity.

**Tech Stack:** Python 3.11+, asyncio, SQLite (`data/pending_questions.db`, `data/user_profile.db`), Qdrant (existing `screening_semantic_cache.db`), Playwright (existing async API), Telegram Bot API (existing `jobpulse/telegram_agent.py`).

---

### Task 0: File Inventory and Dependency Map

Before tasks 1-15 begin, the engineer should verify these touchpoints exist (they have been verified at plan write time, 2026-05-04 — re-check before coding):

| Path | Lines | Role |
|------|-------|------|
| `jobpulse/screening_pipeline.py` | 165-176 | Existing LLM-fallback no-answer path (returns `confidence=0.0, source="no_answer"`) — NOT modified by this plan |
| `jobpulse/form_engine/field_mapper.py` | 469-531 | Existing `screen_questions()` — HITL hook point at line ~524 (after LLM batch) |
| `jobpulse/native_form_filler.py` | 53, 2730 | Imports + invokes `screen_questions` |
| `jobpulse/telegram_listener.py` | 90-96 | Existing approval-reply branch — new HITL branch added BEFORE this |
| `jobpulse/approval.py` | 173-193 | Reference pattern for `process_reply()` (do NOT copy — new logic in question_router) |
| `jobpulse/telegram_agent.py` | 58-86 | Existing `send_message()` — extended to support `reply_markup` for inline keyboards |
| `jobpulse/screening_semantic_cache.py` | 173-258 | Existing `cache()` — called as-is with `confidence=1.0` |
| `shared/profile_store.py` | 257-260 | Existing `screening_defaults(question_type TEXT PRIMARY KEY, answer TEXT)` table |
| `jobpulse/correction_capture.py` | 58-110 | Existing `record_corrections(domain, platform, agent_mapping, final_mapping, *, source="hitl")` |
| `docs/superpowers/specs/mobile-app-integration/00-design-overview.md` | 157-184 | Spec for `NotificationEvent` + sinks (FCM/WS/Telegram) |

---

### Task 1: pending_questions schema + question_store helpers

**Files:**
- Create: `jobpulse/question_store.py`
- Test: `tests/jobpulse/test_question_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_question_store.py
from datetime import datetime, UTC
import pytest

from jobpulse import question_store


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    db_path = tmp_path / "pending_questions.db"
    monkeypatch.setattr(question_store, "_DB_PATH", str(db_path))
    yield


def test_create_inserts_row_and_returns_qid():
    qid = question_store.create(
        question_text="What is your visa status?",
        options=["Option A", "Option B"],
        field_type="select",
        source_form_url="https://example.com/job/123",
    )
    assert isinstance(qid, str)
    assert len(qid) == 32  # uuid4 hex

    row = question_store.get(qid)
    assert row is not None
    assert row["question_text"] == "What is your visa status?"
    assert row["options"] == ["Option A", "Option B"]
    assert row["field_type"] == "select"
    assert row["status"] == "pending"
    assert row["answer_text"] is None
    assert row["source_form_url"] == "https://example.com/job/123"


def test_mark_answered_updates_row():
    qid = question_store.create(
        question_text="What is your notice period?",
        options=[],
        field_type="text",
        source_form_url="https://example.com/job/456",
    )
    question_store.mark_answered(qid, "1 month")

    row = question_store.get(qid)
    assert row["status"] == "answered"
    assert row["answer_text"] == "1 month"
    assert row["answered_at"] is not None


def test_get_returns_none_for_missing_qid():
    assert question_store.get("nonexistent") is None


def test_list_pending_returns_only_pending():
    q1 = question_store.create("Q1", [], "text", "https://x")
    q2 = question_store.create("Q2", [], "text", "https://x")
    question_store.mark_answered(q1, "answer")

    pending = question_store.list_pending()
    qids = [r["id"] for r in pending]
    assert q2 in qids
    assert q1 not in qids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_question_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.question_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/question_store.py
"""Persistent store for pending HITL screening questions.

Records every question the agent asks the human, along with metadata so the
question can be answered out-of-band (e.g. via Telegram reply or, later,
mobile app push action). The asyncio.Future registry that resolves these
rows lives in jobpulse/question_router.py — this module is pure storage.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

_DB_PATH: str = str(DATA_DIR / "pending_questions.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_questions (
    id TEXT PRIMARY KEY,
    question_text TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    field_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    answered_at TEXT,
    answer_text TEXT,
    source_form_url TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_questions_status
    ON pending_questions (status);
CREATE INDEX IF NOT EXISTS idx_pending_questions_created_at
    ON pending_questions (created_at);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def create(
    question_text: str,
    options: list[str],
    field_type: str,
    source_form_url: str,
) -> str:
    """Insert a new pending question. Returns the question_id (uuid4 hex)."""
    qid = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO pending_questions
               (id, question_text, options_json, field_type, status,
                created_at, source_form_url)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (qid, question_text, json.dumps(options), field_type, now, source_form_url),
        )
    logger.debug("question_store.create qid=%s text=%s", qid, question_text[:60])
    return qid


def get(qid: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_questions WHERE id = ?", (qid,)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["options"] = json.loads(data.pop("options_json") or "[]")
    return data


def mark_answered(qid: str, answer_text: str) -> None:
    now = datetime.now(UTC).isoformat()
    with _conn() as conn:
        conn.execute(
            """UPDATE pending_questions
                  SET status = 'answered',
                      answer_text = ?,
                      answered_at = ?
                WHERE id = ?""",
            (answer_text, now, qid),
        )
    logger.debug("question_store.mark_answered qid=%s answer=%s", qid, answer_text[:60])


def mark_timeout(qid: str) -> None:
    now = datetime.now(UTC).isoformat()
    with _conn() as conn:
        conn.execute(
            """UPDATE pending_questions
                  SET status = 'timeout',
                      answered_at = ?
                WHERE id = ? AND status = 'pending'""",
            (now, qid),
        )


def list_pending(limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM pending_questions
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["options"] = json.loads(data.pop("options_json") or "[]")
        out.append(data)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_question_store.py -v`
Expected: PASS — 4 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/question_store.py tests/jobpulse/test_question_store.py
git commit -m "feat(hitl): add pending_questions store with CRUD helpers"
```

---

### Task 2: AgentQuestionEvent dataclass + question_router skeleton

**Files:**
- Create: `jobpulse/question_router.py`
- Test: `tests/jobpulse/test_question_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_question_router.py
import asyncio
import pytest

from jobpulse import question_store, question_router


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    db_path = tmp_path / "pending_questions.db"
    monkeypatch.setattr(question_store, "_DB_PATH", str(db_path))
    # Reset router state between tests
    question_router._FUTURES.clear()
    yield
    question_router._FUTURES.clear()


@pytest.mark.asyncio
async def test_ask_one_creates_row_and_returns_pending_future():
    qid, future = question_router.ask_one(
        question_text="What is your visa status?",
        options=["Option A", "Option B"],
        field_type="select",
        source_form_url="https://example.com/job/1",
    )
    assert isinstance(qid, str)
    assert isinstance(future, asyncio.Future)
    assert not future.done()

    row = question_store.get(qid)
    assert row["status"] == "pending"
    assert row["question_text"] == "What is your visa status?"
    assert qid in question_router._FUTURES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_question_router.py::test_ask_one_creates_row_and_returns_pending_future -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.question_router'`

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/question_router.py
"""HITL question router — ask the human a question and resume on reply.

Pairs a SQLite row (jobpulse.question_store) with an in-process
asyncio.Future. Callers `await` the future; the Telegram listener (or any
other reply path) calls `resolve(qid, answer)` to wake them up.

Reply path is idempotent: calling resolve(qid, ...) twice does not raise.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from jobpulse import question_store
from shared.logging_config import get_logger

logger = get_logger(__name__)

# qid → Future[str]. Populated by ask_one(), drained by resolve()/timeout.
_FUTURES: dict[str, asyncio.Future[str]] = {}


@dataclass
class AgentQuestionEvent:
    """Domain event for a single mid-form question awaiting a human answer.

    Converted to shared.notifications.router.NotificationEvent by each
    sink (Telegram now; FCM + WS later — see mobile-app-integration spec
    Phase 1A/1B).
    """
    qid: str
    question_text: str
    options: list[str] = field(default_factory=list)
    field_type: str = ""
    source_form_url: str = ""
    required: bool = True


def ask_one(
    question_text: str,
    options: list[str],
    field_type: str,
    source_form_url: str,
) -> tuple[str, asyncio.Future[str]]:
    """Insert a pending question, register a Future, return (qid, future).

    The caller is responsible for awaiting the future (with timeout) and for
    cancelling it on the parent task being cancelled.
    """
    qid = question_store.create(
        question_text=question_text,
        options=options,
        field_type=field_type,
        source_form_url=source_form_url,
    )
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()
    _FUTURES[qid] = future
    logger.info("question_router: asked qid=%s text=%s", qid, question_text[:60])
    return qid, future
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_question_router.py -v`
Expected: PASS — 1 test passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/question_router.py tests/jobpulse/test_question_router.py
git commit -m "feat(hitl): add question_router.ask_one + AgentQuestionEvent"
```

---

### Task 3: question_router.resolve (idempotent)

**Files:**
- Modify: `jobpulse/question_router.py` (append)
- Test: `tests/jobpulse/test_question_router.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/jobpulse/test_question_router.py`:

```python
@pytest.mark.asyncio
async def test_resolve_completes_future_and_marks_answered():
    qid, future = question_router.ask_one(
        question_text="What is your notice period?",
        options=[],
        field_type="text",
        source_form_url="https://example.com/job/2",
    )
    ok = question_router.resolve(qid, "1 month")
    assert ok is True
    assert future.done()
    assert future.result() == "1 month"

    row = question_store.get(qid)
    assert row["status"] == "answered"
    assert row["answer_text"] == "1 month"


@pytest.mark.asyncio
async def test_resolve_is_idempotent():
    qid, future = question_router.ask_one(
        "Q", [], "text", "https://x"
    )
    assert question_router.resolve(qid, "first") is True
    # Second call returns False (already resolved) but does NOT raise
    assert question_router.resolve(qid, "second") is False
    assert future.result() == "first"


@pytest.mark.asyncio
async def test_resolve_unknown_qid_returns_false():
    assert question_router.resolve("nonexistent", "answer") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_question_router.py -v`
Expected: FAIL with `AttributeError: module 'jobpulse.question_router' has no attribute 'resolve'`

- [ ] **Step 3: Append implementation to `jobpulse/question_router.py`**

```python
def resolve(qid: str, answer: str) -> bool:
    """Resolve a pending question with the human's answer.

    Returns True if this call resolved the future, False if the qid was
    unknown OR the future was already resolved (idempotent).
    """
    future = _FUTURES.pop(qid, None)
    if future is None:
        logger.debug("question_router.resolve: unknown qid=%s", qid)
        return False
    if future.done():
        logger.debug("question_router.resolve: already done qid=%s", qid)
        return False
    future.set_result(answer)
    question_store.mark_answered(qid, answer)
    logger.info("question_router: resolved qid=%s answer=%s", qid, answer[:60])
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_question_router.py -v`
Expected: PASS — 4 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/question_router.py tests/jobpulse/test_question_router.py
git commit -m "feat(hitl): question_router.resolve with idempotent semantics"
```

---

### Task 4: ask_batch — parallel question fan-out

**Files:**
- Modify: `jobpulse/question_router.py` (append)
- Test: `tests/jobpulse/test_question_router.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/jobpulse/test_question_router.py`:

```python
@pytest.mark.asyncio
async def test_ask_batch_returns_qids_and_gathered_future():
    questions = [
        ("Q1", ["A", "B"], "select", "https://x"),
        ("Q2", [], "text", "https://x"),
        ("Q3", ["Yes", "No"], "radio", "https://x"),
    ]
    qids, gathered = question_router.ask_batch(questions)
    assert len(qids) == 3
    assert all(isinstance(q, str) for q in qids)

    # Resolve out of order
    question_router.resolve(qids[1], "two weeks")
    question_router.resolve(qids[0], "A")
    question_router.resolve(qids[2], "Yes")

    answers = await asyncio.wait_for(gathered, timeout=2.0)
    assert answers == ["A", "two weeks", "Yes"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_question_router.py::test_ask_batch_returns_qids_and_gathered_future -v`
Expected: FAIL with `AttributeError: module 'jobpulse.question_router' has no attribute 'ask_batch'`

- [ ] **Step 3: Append implementation**

```python
def ask_batch(
    questions: list[tuple[str, list[str], str, str]],
) -> tuple[list[str], asyncio.Future[list[str]]]:
    """Ask multiple questions in parallel.

    Args:
        questions: list of (question_text, options, field_type, source_form_url)

    Returns:
        (list_of_qids, gathered_future) — gathered_future resolves to a list
        of answers in the same order as the input.
    """
    qids: list[str] = []
    futures: list[asyncio.Future[str]] = []
    for q_text, opts, ftype, src in questions:
        qid, fut = ask_one(q_text, opts, ftype, src)
        qids.append(qid)
        futures.append(fut)
    gathered = asyncio.gather(*futures)
    return qids, gathered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_question_router.py -v`
Expected: PASS — 5 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/question_router.py tests/jobpulse/test_question_router.py
git commit -m "feat(hitl): question_router.ask_batch for parallel HITL fan-out"
```

---

### Task 5: notification_router with sink registry

**Files:**
- Create: `shared/notifications/__init__.py` (empty)
- Create: `shared/notifications/router.py`
- Test: `tests/shared/test_notifications_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_notifications_router.py
import pytest

from shared.notifications import router as nr


@pytest.fixture(autouse=True)
def _reset_sinks():
    original = nr._SINKS.copy()
    nr._SINKS.clear()
    yield
    nr._SINKS.clear()
    nr._SINKS.extend(original)


def test_emit_calls_every_registered_sink():
    captured: list[nr.NotificationEvent] = []

    def sink_a(event: nr.NotificationEvent) -> None:
        captured.append(("a", event))

    def sink_b(event: nr.NotificationEvent) -> None:
        captured.append(("b", event))

    nr.register_sink(sink_a)
    nr.register_sink(sink_b)

    event = nr.NotificationEvent(
        category="approvals",
        title="Test",
        body="A test event",
        deep_link="neuralis://chat/jobs?msg_id=test",
        source="jobs",
    )
    nr.emit(event)

    assert len(captured) == 2
    assert captured[0][0] == "a"
    assert captured[1][0] == "b"
    assert captured[0][1].title == "Test"


def test_emit_continues_when_one_sink_raises():
    called: list[str] = []

    def bad_sink(event):
        raise RuntimeError("sink down")

    def good_sink(event):
        called.append("good")

    nr.register_sink(bad_sink)
    nr.register_sink(good_sink)

    event = nr.NotificationEvent(
        category="alerts",
        title="X",
        body="Y",
        deep_link="",
        source="test",
    )
    nr.emit(event)  # must not raise
    assert called == ["good"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/test_notifications_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.notifications'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/notifications/__init__.py
```

```python
# shared/notifications/router.py
"""Multi-sink notification router.

Single emit() that fans out to every registered sink. Today: Telegram.
When the NEURALIS mobile app ships (Phase 1A scaffolding, Phase 1B push +
WS — see docs/superpowers/specs/mobile-app-integration/), FCM and
WebSocket sinks register themselves at app startup.

Contract matches docs/superpowers/specs/mobile-app-integration/
00-design-overview.md:157-184.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from shared.logging_config import get_logger

logger = get_logger(__name__)

Category = Literal["approvals", "alerts", "activity", "digest"]


@dataclass
class NotificationAction:
    label: str
    action_id: str        # e.g. "screening_answer:option_a"
    payload: dict         # opaque, sent with action when user taps button


@dataclass
class NotificationEvent:
    category: Category
    title: str
    body: str
    deep_link: str
    actions: list[NotificationAction] = field(default_factory=list)
    dedup_key: str | None = None
    source: str = ""


Sink = Callable[[NotificationEvent], None]
_SINKS: list[Sink] = []


def register_sink(sink: Sink) -> None:
    _SINKS.append(sink)


def emit(event: NotificationEvent) -> None:
    """Fan out to every registered sink. Errors in one sink do not block others."""
    for sink in _SINKS:
        try:
            sink(event)
        except Exception as exc:
            logger.warning(
                "notification sink %s failed: %s", getattr(sink, "__name__", sink), exc
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/test_notifications_router.py -v`
Expected: PASS — 2 tests passed

- [ ] **Step 5: Commit**

```bash
git add shared/notifications/__init__.py shared/notifications/router.py tests/shared/test_notifications_router.py
git commit -m "feat(notifications): multi-sink router with NotificationEvent contract"
```

---

### Task 6: Telegram sink + AgentQuestionEvent → NotificationEvent converter

**Files:**
- Create: `jobpulse/notification_sinks/__init__.py` (empty)
- Create: `jobpulse/notification_sinks/telegram_sink.py`
- Modify: `jobpulse/telegram_agent.py` (extend `send_message` with optional `reply_markup`)
- Test: `tests/jobpulse/test_telegram_sink.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_telegram_sink.py
import pytest

from jobpulse.notification_sinks import telegram_sink
from jobpulse.question_router import AgentQuestionEvent
from shared.notifications.router import NotificationEvent, NotificationAction


def test_question_event_to_notification_event_with_inline_buttons():
    qe = AgentQuestionEvent(
        qid="abc123",
        question_text="What is your visa status?",
        options=["Option A", "Option B", "Option C"],
        field_type="select",
        source_form_url="https://example.com/job/1",
    )
    ne = telegram_sink.question_to_notification(qe)
    assert isinstance(ne, NotificationEvent)
    assert ne.category == "approvals"
    assert "What is your visa status?" in ne.body
    assert len(ne.actions) == 3
    assert ne.actions[0].label == "Option A"
    assert ne.actions[0].action_id == "hitl_answer"
    assert ne.actions[0].payload == {"qid": "abc123", "answer": "Option A"}


def test_question_event_with_4plus_options_uses_numbered_list_no_buttons():
    qe = AgentQuestionEvent(
        qid="abc123",
        question_text="Pick one",
        options=["A", "B", "C", "D", "E"],
        field_type="select",
        source_form_url="https://x",
    )
    ne = telegram_sink.question_to_notification(qe)
    assert ne.actions == []
    assert "1. A" in ne.body
    assert "2. B" in ne.body
    assert "5. E" in ne.body


def test_question_event_freetext_no_options():
    qe = AgentQuestionEvent(
        qid="abc123",
        question_text="What is your notice period?",
        options=[],
        field_type="text",
        source_form_url="https://x",
    )
    ne = telegram_sink.question_to_notification(qe)
    assert ne.actions == []
    assert "Reply with your answer" in ne.body


def test_send_to_telegram_invokes_send_message(monkeypatch):
    captured = {}

    def fake_send(text: str, chat_id: str | None = None, reply_markup: dict | None = None):
        captured["text"] = text
        captured["reply_markup"] = reply_markup
        return True

    monkeypatch.setattr(
        "jobpulse.telegram_agent.send_message", fake_send
    )

    event = NotificationEvent(
        category="approvals",
        title="Job form question",
        body="Question body here",
        deep_link="neuralis://hitl/abc123",
        actions=[
            NotificationAction(
                label="Yes",
                action_id="hitl_answer",
                payload={"qid": "abc123", "answer": "Yes"},
            ),
        ],
        source="hitl",
    )
    telegram_sink.send(event)
    assert "Question body here" in captured["text"]
    assert captured["reply_markup"] is not None
    keyboard = captured["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["text"] == "Yes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_telegram_sink.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.notification_sinks'`

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/notification_sinks/__init__.py
```

```python
# jobpulse/notification_sinks/telegram_sink.py
"""Telegram sink for notification_router.

Converts AgentQuestionEvent to a NotificationEvent (category=approvals),
then renders that event into a Telegram message — inline keyboard for
1-3 options, numbered list otherwise.
"""
from __future__ import annotations

from jobpulse import telegram_agent
from jobpulse.question_router import AgentQuestionEvent
from shared.logging_config import get_logger
from shared.notifications.router import NotificationAction, NotificationEvent

logger = get_logger(__name__)

_INLINE_BUTTON_THRESHOLD = 3


def question_to_notification(qe: AgentQuestionEvent) -> NotificationEvent:
    """Build a NotificationEvent for a HITL question."""
    body_lines = [f"Question: {qe.question_text}"]
    actions: list[NotificationAction] = []

    if 0 < len(qe.options) <= _INLINE_BUTTON_THRESHOLD:
        for opt in qe.options:
            actions.append(
                NotificationAction(
                    label=opt,
                    action_id="hitl_answer",
                    payload={"qid": qe.qid, "answer": opt},
                )
            )
        body_lines.append("Tap an option below.")
    elif len(qe.options) > _INLINE_BUTTON_THRESHOLD:
        body_lines.append("Options:")
        for i, opt in enumerate(qe.options, 1):
            body_lines.append(f"{i}. {opt}")
        body_lines.append(f"Reply with the number (1-{len(qe.options)}) or the text.")
    else:
        body_lines.append("Reply with your answer.")

    body_lines.append(f"\n[qid:{qe.qid}]")

    return NotificationEvent(
        category="approvals",
        title="Job form: question waiting",
        body="\n".join(body_lines),
        deep_link=f"neuralis://hitl/{qe.qid}",
        actions=actions,
        dedup_key=f"hitl_{qe.qid}",
        source="hitl",
    )


def _build_inline_keyboard(actions: list[NotificationAction]) -> dict | None:
    if not actions:
        return None
    return {
        "inline_keyboard": [
            [
                {
                    "text": a.label,
                    "callback_data": f"{a.action_id}:{a.payload['qid']}:{a.payload['answer']}"[:64],
                }
            ]
            for a in actions
        ]
    }


def send(event: NotificationEvent) -> None:
    """Render and dispatch a NotificationEvent via Telegram."""
    reply_markup = _build_inline_keyboard(event.actions)
    text = f"{event.title}\n\n{event.body}"
    telegram_agent.send_message(text, reply_markup=reply_markup)
```

Modify `jobpulse/telegram_agent.py` — extend `send_message` to accept `reply_markup`:

In `_send_single` and `send_message`, add optional `reply_markup` param that, when present, is added to the JSON payload sent to Telegram's `sendMessage` API. Locate the existing `send_message` near line 58 and update its body to thread `reply_markup` through `_send_single`:

```python
# jobpulse/telegram_agent.py — replace existing send_message (line 58) with:
def send_message(
    text: str,
    chat_id: str = None,
    reply_markup: dict | None = None,
) -> bool:
    """Send a message to Telegram. Splits at section boundaries if >4096 chars.

    reply_markup: optional Telegram inline_keyboard / reply_keyboard payload,
    e.g. {"inline_keyboard": [[{"text": "Yes", "callback_data": "..."}]]}
    """
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        logger.warning("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    if len(text) <= MAX_MSG_LEN:
        return _send_single(text, cid, reply_markup=reply_markup)

    chunks = []
    current = ""
    for line in text.split("\n"):
        candidate = current + line + "\n" if current else line + "\n"
        if len(candidate) > MAX_MSG_LEN:
            if current:
                chunks.append(current.rstrip("\n"))
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip("\n"))

    success = True
    # reply_markup only attached to LAST chunk so the buttons appear under
    # the final message
    for i, chunk in enumerate(chunks):
        rm = reply_markup if i == len(chunks) - 1 else None
        if not _send_single(chunk, cid, reply_markup=rm):
            success = False
    return success
```

And update `_send_single` (already exists above line 58) to thread the new param into the JSON payload — locate the `payload = json.dumps({"chat_id": cid, "text": text, ...})` line and add `"reply_markup": reply_markup` to the dict when not None.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_telegram_sink.py -v`
Expected: PASS — 4 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/notification_sinks/__init__.py \
        jobpulse/notification_sinks/telegram_sink.py \
        jobpulse/telegram_agent.py \
        tests/jobpulse/test_telegram_sink.py
git commit -m "feat(hitl): Telegram sink with inline keyboards + AgentQuestionEvent conversion"
```

---

### Task 7: Telegram listener integration — resolve question replies

**Files:**
- Modify: `jobpulse/telegram_listener.py` (insert HITL branch BEFORE approval branch at line 90)
- Test: `tests/jobpulse/test_telegram_listener_hitl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_telegram_listener_hitl.py
import asyncio
import pytest

from jobpulse import question_router, question_store
from jobpulse.hitl_reply import process_hitl_reply


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    db_path = tmp_path / "pending_questions.db"
    monkeypatch.setattr(question_store, "_DB_PATH", str(db_path))
    question_router._FUTURES.clear()
    yield
    question_router._FUTURES.clear()


@pytest.mark.asyncio
async def test_text_reply_with_qid_marker_resolves_future():
    qid, fut = question_router.ask_one(
        "Q", ["A", "B"], "select", "https://x"
    )
    text = f"A [qid:{qid}]"
    response = process_hitl_reply(text)
    assert response is not None
    assert "Resolved" in response
    assert fut.done()
    assert fut.result() == "A"


@pytest.mark.asyncio
async def test_numbered_reply_resolves_with_option_text():
    qid, fut = question_router.ask_one(
        "Q", ["Alpha", "Beta", "Gamma", "Delta"], "select", "https://x"
    )
    text = f"3 [qid:{qid}]"
    response = process_hitl_reply(text)
    assert response is not None
    assert fut.done()
    assert fut.result() == "Gamma"


def test_non_hitl_reply_returns_none():
    response = process_hitl_reply("hello, schedule my day")
    assert response is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_telegram_listener_hitl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.hitl_reply'`

- [ ] **Step 3: Create `jobpulse/hitl_reply.py` and wire into listener**

```python
# jobpulse/hitl_reply.py
"""Parse Telegram replies for HITL question answers and resolve futures.

Replies use a [qid:<32-hex>] marker that the Telegram sink injected into
the original question message. This avoids ambiguity vs. other reply
patterns (approval yes/no, email-review, free-form chat).

For numbered replies ("3"), we look up the option list from question_store
and translate to the option text.
"""
from __future__ import annotations

import re

from jobpulse import question_router, question_store
from shared.logging_config import get_logger

logger = get_logger(__name__)

_QID_RE = re.compile(r"\[qid:([a-f0-9]{32})\]")


def process_hitl_reply(text: str) -> str | None:
    """Return a confirmation string if this is a HITL reply, else None."""
    match = _QID_RE.search(text)
    if not match:
        return None
    qid = match.group(1)
    body = _QID_RE.sub("", text).strip()

    row = question_store.get(qid)
    if row is None:
        return f"No pending question with qid={qid[:8]}…"
    if row["status"] != "pending":
        return f"Question {qid[:8]}… already resolved."

    answer = _translate_numbered_reply(body, row.get("options", []))

    if question_router.resolve(qid, answer):
        return f"Resolved question {qid[:8]}… → '{answer[:60]}'"
    return f"Question {qid[:8]}… not active in this process."


def _translate_numbered_reply(body: str, options: list[str]) -> str:
    """If body is just a digit and options exist, return options[N-1]."""
    body = body.strip()
    if options and body.isdigit():
        idx = int(body) - 1
        if 0 <= idx < len(options):
            return options[idx]
    return body
```

Modify `jobpulse/telegram_listener.py` — insert at line 89 (just BEFORE the approval branch at line 90):

```python
# Around line 89 of jobpulse/telegram_listener.py — insert BEFORE the approval block
# (Existing approval block starts: "# Check for pending approval reply")

# Check for HITL question reply
from jobpulse.hitl_reply import process_hitl_reply
hitl_response = process_hitl_reply(text)
if hitl_response:
    telegram_agent.send_message(hitl_response)
    _log(f"HITL: {hitl_response[:80]}")
    continue
```

The `[qid:...]` marker regex is the only regex in this module — it is structural format validation (UUID hex), permitted under `.claude/rules/jobpulse.md` "Regex remains OK for structural format validation".

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_telegram_listener_hitl.py -v`
Expected: PASS — 3 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/hitl_reply.py jobpulse/telegram_listener.py tests/jobpulse/test_telegram_listener_hitl.py
git commit -m "feat(hitl): telegram_listener resolves question replies via [qid:] marker"
```

---

### Task 8: Persist human answers to all 3 caches on resolve

**Files:**
- Create: `jobpulse/hitl_persist.py`
- Modify: `jobpulse/question_router.py` (call `hitl_persist.save_human_answer` from `resolve()`)
- Test: `tests/jobpulse/test_hitl_persist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_hitl_persist.py
import pytest
from unittest.mock import MagicMock, patch

from jobpulse import hitl_persist


def test_save_human_answer_writes_to_three_caches():
    fake_cache = MagicMock()
    fake_profile_store = MagicMock()
    fake_correction = MagicMock()

    with patch("jobpulse.hitl_persist._semantic_cache", return_value=fake_cache), \
         patch("jobpulse.hitl_persist._profile_store", return_value=fake_profile_store), \
         patch("jobpulse.hitl_persist._correction_capture", return_value=fake_correction):

        hitl_persist.save_human_answer(
            question_text="What is your visa status?",
            answer="Option A",
            options=["Option A", "Option B"],
            field_type="select",
            intent="visa_status",
            source_url="https://example.com/job/1",
        )

    fake_cache.cache.assert_called_once()
    cache_kwargs = fake_cache.cache.call_args.kwargs
    assert cache_kwargs["question"] == "What is your visa status?"
    assert cache_kwargs["answer"] == "Option A"
    assert cache_kwargs["confidence"] == 1.0
    assert cache_kwargs["selected_option"] == "Option A"
    assert cache_kwargs["field_type"] == "select"
    assert cache_kwargs["intent"] == "visa_status"

    fake_profile_store.set_screening_default.assert_called_once_with(
        "visa_status", "Option A"
    )

    fake_correction.record_corrections.assert_called_once()
    cc_kwargs = fake_correction.record_corrections.call_args.kwargs
    assert cc_kwargs["source"] == "hitl"
    assert cc_kwargs["agent_name"] == "screening_pipeline"


def test_save_human_answer_skips_profile_store_when_intent_unknown():
    fake_cache = MagicMock()
    fake_profile_store = MagicMock()
    fake_correction = MagicMock()

    with patch("jobpulse.hitl_persist._semantic_cache", return_value=fake_cache), \
         patch("jobpulse.hitl_persist._profile_store", return_value=fake_profile_store), \
         patch("jobpulse.hitl_persist._correction_capture", return_value=fake_correction):

        hitl_persist.save_human_answer(
            question_text="Some custom question",
            answer="custom answer",
            options=[],
            field_type="text",
            intent="",  # unknown intent — only valid intents become profile defaults
            source_url="https://x",
        )

    fake_cache.cache.assert_called_once()
    fake_profile_store.set_screening_default.assert_not_called()
    fake_correction.record_corrections.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_hitl_persist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.hitl_persist'`

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/hitl_persist.py
"""Persist a human-confirmed screening answer to all three learning caches.

Called from question_router.resolve() after the asyncio.Future resolves.
Three sinks fire:

1. ScreeningSemanticCache.cache(...)        — Qdrant + SQLite shadow
2. ProfileStore.set_screening_default(...)  — canonical text answer per intent
3. CorrectionCapture.record_corrections(...) — feeds AgentRulesDB / OptimizationEngine

A failure in any one sink logs and continues; data integrity is not
all-or-nothing — partial caching is better than zero caching.
"""
from __future__ import annotations

from urllib.parse import urlparse

from shared.logging_config import get_logger

logger = get_logger(__name__)


def _semantic_cache():
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    return ScreeningSemanticCache()


def _profile_store():
    from shared.profile_store import get_profile_store
    return get_profile_store()


def _correction_capture():
    from jobpulse.correction_capture import CorrectionCapture
    return CorrectionCapture()


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc or ""
    except Exception:
        return ""


def save_human_answer(
    question_text: str,
    answer: str,
    options: list[str],
    field_type: str,
    intent: str,
    source_url: str,
    platform: str = "",
) -> None:
    """Persist a human-confirmed answer to all 3 caches.

    Args:
        question_text: The original screening question
        answer: The human's answer (already mapped from numbered → option text if applicable)
        options: Field options at the time of the question (for option-aware re-alignment)
        field_type: e.g. "select", "radio", "text"
        intent: ScreeningIntent value if classified, else ""
        source_url: form URL where the question appeared
        platform: ATS platform name if known (greenhouse, workday, …)
    """
    domain = _domain_from_url(source_url)

    # 1. Semantic cache
    try:
        cache = _semantic_cache()
        cache.cache(
            question=question_text,
            intent=intent,
            answer=answer,
            confidence=1.0,
            selected_option=answer if options else "",
            field_type=field_type,
            field_options=options or None,
        )
    except Exception as exc:
        logger.warning("hitl_persist: semantic_cache write failed: %s", exc)

    # 2. ProfileStore screening_defaults (canonical answer per intent)
    if intent:
        try:
            ps = _profile_store()
            ps.set_screening_default(intent, answer)
        except Exception as exc:
            logger.warning("hitl_persist: profile_store write failed: %s", exc)

    # 3. CorrectionCapture (agent had blank, human supplied answer)
    try:
        cc = _correction_capture()
        cc.record_corrections(
            domain=domain or "unknown",
            platform=platform or "unknown",
            agent_mapping={question_text: ""},
            final_mapping={question_text: answer},
            source="hitl",
            agent_name="screening_pipeline",
        )
    except Exception as exc:
        logger.warning("hitl_persist: correction_capture write failed: %s", exc)
```

`ProfileStore.set_screening_default(intent, answer)` already exists in `shared/profile_store.py` (the existing schema has `screening_defaults(question_type TEXT PRIMARY KEY, answer TEXT)` at line 257 — if the setter does not exist, add it as: `INSERT OR REPLACE INTO screening_defaults (question_type, answer) VALUES (?, ?)`).

Now extend `question_router.resolve()` — modify `jobpulse/question_router.py`:

```python
# REPLACE existing resolve() with:
def resolve(
    qid: str,
    answer: str,
    *,
    options: list[str] | None = None,
    field_type: str = "",
    intent: str = "",
    source_url: str = "",
    platform: str = "",
    persist: bool = True,
) -> bool:
    future = _FUTURES.pop(qid, None)
    if future is None:
        logger.debug("question_router.resolve: unknown qid=%s", qid)
        return False
    if future.done():
        logger.debug("question_router.resolve: already done qid=%s", qid)
        return False
    future.set_result(answer)
    question_store.mark_answered(qid, answer)
    logger.info("question_router: resolved qid=%s answer=%s", qid, answer[:60])

    if persist:
        try:
            from jobpulse import hitl_persist
            row = question_store.get(qid) or {}
            hitl_persist.save_human_answer(
                question_text=row.get("question_text", ""),
                answer=answer,
                options=options if options is not None else row.get("options", []),
                field_type=field_type or row.get("field_type", ""),
                intent=intent,
                source_url=source_url or row.get("source_form_url", ""),
                platform=platform,
            )
        except Exception as exc:
            logger.warning("question_router: hitl_persist failed: %s", exc)

    return True
```

The earlier tests pass `persist=False` to avoid touching real caches — update them:

```python
# tests/jobpulse/test_question_router.py — update tests 3 and 4 calls:
# Replace question_router.resolve(qid, "answer") with:
question_router.resolve(qid, "answer", persist=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_hitl_persist.py tests/jobpulse/test_question_router.py -v`
Expected: PASS — all tests pass

- [ ] **Step 5: Commit**

```bash
git add jobpulse/hitl_persist.py jobpulse/question_router.py tests/jobpulse/test_hitl_persist.py tests/jobpulse/test_question_router.py
git commit -m "feat(hitl): persist human answers to semantic cache + profile + corrections"
```

---

### Task 9: ProfileStore.set_screening_default helper

**Files:**
- Modify: `shared/profile_store.py` (add method on `ProfileStore`)
- Test: `tests/shared/test_profile_store_screening_defaults.py`

This task makes Task 8's call to `ps.set_screening_default(...)` real if it does not yet exist. Skip this task if `git grep "def set_screening_default"` already finds an implementation. Otherwise:

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_profile_store_screening_defaults.py
import pytest

from shared.profile_store import ProfileStore


def test_set_screening_default_upserts(tmp_path):
    db = tmp_path / "user_profile.db"
    key = tmp_path / ".profile_key"
    ps = ProfileStore(db_path=db, key_path=key)

    ps.set_screening_default("visa_status", "Option A")
    assert ps.get_screening_default("visa_status") == "Option A"

    # Upsert (overwrite)
    ps.set_screening_default("visa_status", "Option B")
    assert ps.get_screening_default("visa_status") == "Option B"

    # Unrelated key untouched
    assert ps.get_screening_default("notice_period") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/test_profile_store_screening_defaults.py -v`
Expected: FAIL with `AttributeError: 'ProfileStore' object has no attribute 'set_screening_default'`

- [ ] **Step 3: Add methods to `ProfileStore` in `shared/profile_store.py`**

After the existing `_ensure_schema` method (search for `def _ensure_schema`), add:

```python
def set_screening_default(self, question_type: str, answer: str) -> None:
    """Upsert a canonical screening answer keyed by intent / question_type.

    Used by HITL flow (jobpulse/hitl_persist.py) so the same screening
    intent autonomously resolves on next run.
    """
    self._conn.execute(
        """INSERT INTO screening_defaults (question_type, answer)
                VALUES (?, ?)
           ON CONFLICT(question_type) DO UPDATE SET answer = excluded.answer""",
        (question_type, answer),
    )
    self._conn.commit()


def get_screening_default(self, question_type: str) -> str | None:
    row = self._conn.execute(
        "SELECT answer FROM screening_defaults WHERE question_type = ?",
        (question_type,),
    ).fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/test_profile_store_screening_defaults.py -v`
Expected: PASS — 1 test passed

- [ ] **Step 5: Commit**

```bash
git add shared/profile_store.py tests/shared/test_profile_store_screening_defaults.py
git commit -m "feat(profile): set_screening_default upsert for HITL persistence"
```

---

### Task 10: Timeout (15min) + reminder pushes (5min, 10min)

**Files:**
- Modify: `jobpulse/question_router.py` (add `await_with_reminders`)
- Test: `tests/jobpulse/test_question_router_timeout.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_question_router_timeout.py
import asyncio
import pytest

from jobpulse import question_router, question_store


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    db_path = tmp_path / "pending_questions.db"
    monkeypatch.setattr(question_store, "_DB_PATH", str(db_path))
    question_router._FUTURES.clear()
    yield
    question_router._FUTURES.clear()


@pytest.mark.asyncio
async def test_await_with_reminders_resolves_normally():
    qid, fut = question_router.ask_one("Q", [], "text", "https://x")
    reminders: list[float] = []

    async def reminder_cb(elapsed_seconds: float) -> None:
        reminders.append(elapsed_seconds)

    async def resolver():
        await asyncio.sleep(0.05)
        question_router.resolve(qid, "ok", persist=False)

    asyncio.create_task(resolver())
    answer = await question_router.await_with_reminders(
        qid, fut, timeout_seconds=2.0, reminder_seconds=[0.5, 1.0], reminder_cb=reminder_cb,
    )
    assert answer == "ok"
    assert reminders == []  # resolved before any reminder


@pytest.mark.asyncio
async def test_await_with_reminders_fires_reminders_then_times_out():
    qid, fut = question_router.ask_one("Q", [], "text", "https://x")
    reminders: list[float] = []

    async def reminder_cb(elapsed_seconds: float) -> None:
        reminders.append(elapsed_seconds)

    with pytest.raises(asyncio.TimeoutError):
        await question_router.await_with_reminders(
            qid, fut,
            timeout_seconds=0.3,
            reminder_seconds=[0.1, 0.2],
            reminder_cb=reminder_cb,
        )
    assert len(reminders) == 2

    row = question_store.get(qid)
    assert row["status"] == "timeout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_question_router_timeout.py -v`
Expected: FAIL with `AttributeError: module 'jobpulse.question_router' has no attribute 'await_with_reminders'`

- [ ] **Step 3: Append implementation to `jobpulse/question_router.py`**

```python
from typing import Awaitable, Callable

ReminderCB = Callable[[float], Awaitable[None]]


async def await_with_reminders(
    qid: str,
    future: asyncio.Future[str],
    *,
    timeout_seconds: float = 900.0,         # 15 min hard timeout
    reminder_seconds: list[float] | None = None,  # default [300, 600]
    reminder_cb: ReminderCB | None = None,
) -> str:
    """Await `future` with timeout, firing optional reminders along the way.

    On timeout: marks the row in pending_questions.db as 'timeout',
    cancels the future, and raises asyncio.TimeoutError. Caller decides
    what to do (skip job for required, fall back to best-guess for optional).
    """
    if reminder_seconds is None:
        reminder_seconds = [300.0, 600.0]
    schedule = sorted(s for s in reminder_seconds if 0 < s < timeout_seconds)

    try:
        async with asyncio.timeout(timeout_seconds):
            elapsed = 0.0
            for next_at in schedule:
                wait_for = next_at - elapsed
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future), timeout=wait_for
                    )
                except asyncio.TimeoutError:
                    elapsed = next_at
                    if reminder_cb is not None:
                        try:
                            await reminder_cb(elapsed)
                        except Exception as exc:
                            logger.warning("reminder_cb failed: %s", exc)
            return await future
    except (TimeoutError, asyncio.TimeoutError):
        question_store.mark_timeout(qid)
        # Pop and cancel the future — Telegram replies after this point are
        # ignored (resolve() will return False)
        stale = _FUTURES.pop(qid, None)
        if stale is not None and not stale.done():
            stale.cancel()
        raise asyncio.TimeoutError(f"Question {qid} timed out after {timeout_seconds}s")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_question_router_timeout.py -v`
Expected: PASS — 2 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/question_router.py tests/jobpulse/test_question_router_timeout.py
git commit -m "feat(hitl): await_with_reminders — 15min timeout + 5/10min reminders"
```

---

### Task 11: Browser keep-alive for strict-timeout platforms

**Files:**
- Create: `jobpulse/keep_alive.py`
- Test: `tests/jobpulse/test_keep_alive.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_keep_alive.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from jobpulse import keep_alive


def _fake_page() -> MagicMock:
    page = MagicMock()
    page.evaluate = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_keep_alive_workday_dispatches_events_until_done():
    page = _fake_page()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    snapshot_dir = "/tmp/snapshots"

    async def stopper():
        await asyncio.sleep(0.25)
        fut.set_result("ok")

    asyncio.create_task(stopper())
    await keep_alive.keep_alive_until(
        future=fut,
        page=page,
        platform="workday",
        snapshot_dir=snapshot_dir,
        event_interval_s=0.05,
        snapshot_interval_s=10.0,
    )
    # at least one mousemove dispatched
    assert page.evaluate.await_count >= 1


@pytest.mark.asyncio
async def test_keep_alive_greenhouse_returns_immediately():
    page = _fake_page()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    fut.set_result("never awaited")

    await keep_alive.keep_alive_until(
        future=fut, page=page, platform="greenhouse", snapshot_dir="/tmp",
        event_interval_s=0.05, snapshot_interval_s=10.0,
    )
    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_keep_alive_linkedin_returns_immediately_no_fake_activity():
    """Cloudflare-protected platforms must not generate synthetic activity."""
    page = _fake_page()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    async def stopper():
        await asyncio.sleep(0.05)
        fut.set_result("done")

    asyncio.create_task(stopper())
    await keep_alive.keep_alive_until(
        future=fut, page=page, platform="linkedin", snapshot_dir="/tmp",
        event_interval_s=0.01, snapshot_interval_s=10.0,
    )
    page.evaluate.assert_not_awaited()
    page.mouse.wheel.assert_not_awaited()


def test_platform_strategy_classification():
    assert keep_alive._strategy_for("workday") == "active"
    assert keep_alive._strategy_for("icims") == "active"
    assert keep_alive._strategy_for("greenhouse") == "noop"
    assert keep_alive._strategy_for("lever") == "noop"
    assert keep_alive._strategy_for("ashby") == "noop"
    assert keep_alive._strategy_for("linkedin") == "noop"
    assert keep_alive._strategy_for("indeed") == "noop"
    assert keep_alive._strategy_for("reed") == "noop"
    assert keep_alive._strategy_for("unknown") == "noop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_keep_alive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.keep_alive'`

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/keep_alive.py
"""Browser keep-alive for HITL question waits.

Workday and iCIMS pages have strict idle timeouts (5-10 min) that boot
the user back to a login page. While the human types an answer, we
dispatch synthesized mousemove + small wheel jitter every ~60s and
snapshot storage_state every 5min so the session can be re-attached if
the parent process dies.

Greenhouse / Lever / Ashby — client-side until submit, no keep-alive
needed.

LinkedIn / Indeed / Reed — Cloudflare bot-detection. Synthetic activity
*increases* detection risk. We simply wait. If the timeout hits, the
calling code aborts and the user re-applies later.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from shared.logging_config import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from playwright.async_api import Page

Strategy = Literal["active", "noop"]

# Map ATS platform → keep-alive strategy.
# Single source of truth — also referenced by jobs.md after task 15.
_PLATFORM_STRATEGY: dict[str, Strategy] = {
    "workday": "active",
    "icims": "active",
    "greenhouse": "noop",
    "lever": "noop",
    "ashby": "noop",
    "linkedin": "noop",
    "indeed": "noop",
    "reed": "noop",
    "smartrecruiters": "noop",
    "totaljobs": "noop",
    "glassdoor": "noop",
}


def _strategy_for(platform: str) -> Strategy:
    return _PLATFORM_STRATEGY.get((platform or "").lower(), "noop")


async def _dispatch_activity(page: "Page") -> None:
    """Send synthesized mousemove + small wheel jitter."""
    try:
        await page.evaluate(
            """
            () => {
                const ev = new MouseEvent('mousemove', {
                    clientX: Math.random() * 100 + 50,
                    clientY: Math.random() * 100 + 50,
                    bubbles: true,
                });
                document.dispatchEvent(ev);
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Shift', bubbles: true}));
            }
            """
        )
        await page.mouse.wheel(0, 1)
    except Exception as exc:
        logger.debug("keep_alive activity dispatch failed: %s", exc)


async def _snapshot_storage_state(page: "Page", snapshot_dir: str) -> None:
    try:
        Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
        path = os.path.join(snapshot_dir, "keepalive_storage.json")
        ctx = page.context
        await ctx.storage_state(path=path)
        logger.debug("keep_alive snapshot saved → %s", path)
    except Exception as exc:
        logger.debug("keep_alive snapshot failed: %s", exc)


async def keep_alive_until(
    future: asyncio.Future,
    page: "Page",
    platform: str,
    snapshot_dir: str,
    *,
    event_interval_s: float = 60.0,
    snapshot_interval_s: float = 300.0,
) -> None:
    """Keep `page` alive while `future` is pending.

    Returns when future.done() OR when this coroutine is cancelled by the
    caller. NEVER raises — failure to dispatch is logged and the loop
    continues.
    """
    strategy = _strategy_for(platform)
    if strategy == "noop" or future.done():
        return

    last_snapshot = 0.0
    elapsed = 0.0
    try:
        while not future.done():
            await _dispatch_activity(page)
            elapsed += event_interval_s
            if elapsed - last_snapshot >= snapshot_interval_s:
                await _snapshot_storage_state(page, snapshot_dir)
                last_snapshot = elapsed
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=event_interval_s)
                return  # future resolved
            except asyncio.TimeoutError:
                continue  # tick again
    except asyncio.CancelledError:
        return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_keep_alive.py -v`
Expected: PASS — 4 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/keep_alive.py tests/jobpulse/test_keep_alive.py
git commit -m "feat(hitl): browser keep_alive_until for Workday/iCIMS during HITL waits"
```

---

### Task 12: HITL hook in screen_questions — batch + ask + persist

**Files:**
- Modify: `jobpulse/form_engine/field_mapper.py` (after line 524, before return)
- Test: `tests/jobpulse/test_screen_questions_hitl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_screen_questions_hitl.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jobpulse import question_router, question_store
from jobpulse.form_engine import field_mapper


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    db_path = tmp_path / "pending_questions.db"
    monkeypatch.setattr(question_store, "_DB_PATH", str(db_path))
    question_router._FUTURES.clear()
    yield
    question_router._FUTURES.clear()


@pytest.mark.asyncio
async def test_hitl_resolves_required_unknowns_via_human(monkeypatch):
    # Patch ScreeningPipeline → returns blank for both fields
    fake_pipeline = MagicMock()
    fake_pipeline.answer.return_value = {
        "answer": "", "confidence": 0.0, "source": "no_answer", "intent": ""
    }
    monkeypatch.setattr(
        field_mapper, "ScreeningPipeline",
        lambda profile: fake_pipeline, raising=False,
    )
    # Patch LLM batch fallback → also blank
    monkeypatch.setattr(
        field_mapper, "_screen_questions_llm_batch",
        lambda *a, **kw: ({}, 0),
    )
    # Patch Telegram sink so the test does not hit the network
    sent = []
    monkeypatch.setattr(
        "jobpulse.notification_sinks.telegram_sink.send",
        lambda evt: sent.append(evt),
    )

    fields = [
        {"label": "What is your visa status?", "type": "select",
         "options": ["Option A", "Option B"], "required": True},
        {"label": "What is your notice period?", "type": "text",
         "options": [], "required": True},
    ]

    async def simulate_human():
        await asyncio.sleep(0.1)
        pending = question_store.list_pending()
        for row in pending:
            ans = "Option A" if row["options"] else "1 month"
            question_router.resolve(row["id"], ans, persist=False)

    asyncio.create_task(simulate_human())

    answers, _ = await field_mapper.screen_questions(
        unresolved_fields=fields,
        job_context={"url": "https://example.com/job/123", "platform": "greenhouse"},
        profile_store=MagicMock(),
        correction_warning="",
    )

    assert answers["What is your visa status?"] == "Option A"
    assert answers["What is your notice period?"] == "1 month"
    assert len(sent) == 1   # one batched notification, not two


@pytest.mark.asyncio
async def test_hitl_optional_uses_best_guess_on_timeout(monkeypatch):
    fake_pipeline = MagicMock()
    fake_pipeline.answer.return_value = {
        "answer": "", "confidence": 0.0, "source": "no_answer", "intent": ""
    }
    monkeypatch.setattr(
        field_mapper, "ScreeningPipeline",
        lambda profile: fake_pipeline, raising=False,
    )
    monkeypatch.setattr(
        field_mapper, "_screen_questions_llm_batch",
        lambda *a, **kw: ({}, 0),
    )
    monkeypatch.setattr(
        "jobpulse.notification_sinks.telegram_sink.send",
        lambda evt: None,
    )
    monkeypatch.setattr(field_mapper, "_HITL_TIMEOUT_S", 0.1)
    monkeypatch.setattr(field_mapper, "_HITL_REMINDERS_S", [])

    fields = [
        {"label": "Optional question?", "type": "text",
         "options": [], "required": False},
    ]
    answers, _ = await field_mapper.screen_questions(
        unresolved_fields=fields,
        job_context={"url": "https://example.com/job/123"},
        profile_store=MagicMock(),
        correction_warning="",
    )
    # Optional + timeout → field stays blank (caller's "best guess" is empty)
    # — caller flags it for dry-run review, see Task 13.
    assert answers.get("Optional question?", "") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_screen_questions_hitl.py -v`
Expected: FAIL — assertions on missing answers (HITL hook not yet wired)

- [ ] **Step 3: Modify `jobpulse/form_engine/field_mapper.py`**

Add the HITL hook AFTER line 524 (`answers.update(llm_answers)`), BEFORE the existing `else: logger.info(...)` block. Replace the trailing portion of `screen_questions` (from line 525) with:

```python
# Around line 525 of jobpulse/form_engine/field_mapper.py
# After: answers.update(llm_answers)

    unresolved_after_llm = [
        f for f in unresolved_fields if f["label"] not in answers
    ]
    if unresolved_after_llm:
        hitl_answers = await _ask_human_for_unknowns(
            unresolved_after_llm, job_context or {},
        )
        answers.update(hitl_answers)
    elif not unresolved_after_pipeline:
        logger.info(
            "ScreeningPipeline resolved all %d screening fields (0 LLM calls)",
            len(unresolved_fields),
        )

    return answers, llm_calls


# ── HITL fallback ──────────────────────────────────────────────────────
_HITL_TIMEOUT_S = 900.0
_HITL_REMINDERS_S: list[float] = [300.0, 600.0]


async def _ask_human_for_unknowns(
    fields: list[dict],
    job_context: dict[str, Any],
) -> dict[str, str]:
    """Batch unknown fields into ONE notification, await human answers."""
    from jobpulse import question_router
    from jobpulse.notification_sinks import telegram_sink
    from jobpulse.question_router import AgentQuestionEvent

    source_url = (job_context or {}).get("url", "")
    platform = (job_context or {}).get("platform", "")

    questions = [
        (f["label"], f.get("options") or [], f.get("type", ""), source_url)
        for f in fields
    ]
    qids, gathered = question_router.ask_batch(questions)

    # Send ONE batched notification listing all questions.
    # Each pending question carries its own [qid:] marker so the user can
    # answer them out of order via separate Telegram replies.
    for qid, f in zip(qids, fields):
        evt = AgentQuestionEvent(
            qid=qid,
            question_text=f["label"],
            options=f.get("options") or [],
            field_type=f.get("type", ""),
            source_form_url=source_url,
            required=bool(f.get("required", True)),
        )
        telegram_sink.send(telegram_sink.question_to_notification(evt))

    async def reminder_cb(elapsed: float) -> None:
        from jobpulse import telegram_agent
        telegram_agent.send_message(
            f"Reminder: {len(qids)} screening question(s) waiting "
            f"({int(elapsed/60)} min elapsed)."
        )

    answers: dict[str, str] = {}
    try:
        # Single timeout wraps the whole batch — first to all-resolve wins.
        result_list = await question_router.await_with_reminders(
            qid="batch",  # used only for log; per-qid timeout marking happens below
            future=gathered,
            timeout_seconds=_HITL_TIMEOUT_S,
            reminder_seconds=_HITL_REMINDERS_S,
            reminder_cb=reminder_cb,
        )
        for f, ans in zip(fields, result_list):
            answers[f["label"]] = ans
            # Persist each answer through resolve()'s persistence path —
            # await_with_reminders already called resolve() implicitly via
            # the reply path, so nothing to do here.
    except asyncio.TimeoutError:
        # Mark unresolved qids as timeout, classify required vs optional
        for qid, f in zip(qids, fields):
            row = question_store.get(qid)
            if row and row["status"] == "pending":
                question_store.mark_timeout(qid)
            if not f.get("required", True):
                # Optional → leave blank, caller flags for dry-run review
                continue
            # Required + timeout → leave blank, caller will skip job
        logger.warning(
            "HITL: %d question(s) timed out after %ds; required=%d",
            len(qids), _HITL_TIMEOUT_S,
            sum(1 for f in fields if f.get("required", True)),
        )
    return answers
```

Add the import for `question_store` near the top of `field_mapper.py` if not present:

```python
from jobpulse import question_store  # near other jobpulse imports
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_screen_questions_hitl.py -v`
Expected: PASS — 2 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/field_mapper.py tests/jobpulse/test_screen_questions_hitl.py
git commit -m "feat(hitl): batch unknown screening fields → ask human → persist"
```

---

### Task 13: Required-vs-optional skip-or-flag policy at caller

**Files:**
- Modify: `jobpulse/native_form_filler.py` (around line 2730 where `screen_questions` is called)
- Test: `tests/jobpulse/test_native_filler_hitl_skip.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_native_filler_hitl_skip.py
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from jobpulse.native_form_filler import _evaluate_hitl_outcome


def test_required_unknown_returns_skip():
    fields = [
        {"label": "Required", "required": True},
        {"label": "Optional", "required": False},
    ]
    answers = {}  # nothing came back
    decision = _evaluate_hitl_outcome(fields, answers)
    assert decision["action"] == "skip"
    assert "Required" in decision["unresolved_required"]


def test_only_optional_unknown_returns_proceed_with_flag():
    fields = [
        {"label": "Optional1", "required": False},
        {"label": "Optional2", "required": False},
    ]
    answers = {"Optional1": "best-guess"}
    decision = _evaluate_hitl_outcome(fields, answers)
    assert decision["action"] == "proceed"
    assert decision["needs_review"] == ["Optional2"]


def test_all_resolved_returns_clean_proceed():
    fields = [
        {"label": "F1", "required": True},
        {"label": "F2", "required": False},
    ]
    answers = {"F1": "x", "F2": "y"}
    decision = _evaluate_hitl_outcome(fields, answers)
    assert decision["action"] == "proceed"
    assert decision["needs_review"] == []
    assert decision["unresolved_required"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_native_filler_hitl_skip.py -v`
Expected: FAIL with `ImportError: cannot import name '_evaluate_hitl_outcome' from 'jobpulse.native_form_filler'`

- [ ] **Step 3: Add helper to `jobpulse/native_form_filler.py`**

Place the function at module scope (near the existing helpers, e.g. after `emit_form_fill_failures`):

```python
# jobpulse/native_form_filler.py — at module scope
def _evaluate_hitl_outcome(
    fields: list[dict], answers: dict[str, str],
) -> dict[str, Any]:
    """Decide skip vs proceed based on which screening fields are still empty.

    Args:
        fields: the list passed into screen_questions
        answers: what came back

    Returns:
        {"action": "skip" | "proceed",
         "unresolved_required": [labels],
         "needs_review": [optional labels left empty for dry-run flag]}
    """
    unresolved_required: list[str] = []
    needs_review: list[str] = []
    for f in fields:
        label = f["label"]
        if answers.get(label):
            continue
        if f.get("required", True):
            unresolved_required.append(label)
        else:
            needs_review.append(label)
    return {
        "action": "skip" if unresolved_required else "proceed",
        "unresolved_required": unresolved_required,
        "needs_review": needs_review,
    }
```

At the existing call site of `screen_questions` (around line 2730 of `native_form_filler.py`), use the helper. Locate:

```python
screening, s_calls = await screen_questions(
    ...
)
```

Add right after:

```python
hitl_decision = _evaluate_hitl_outcome(unresolved_fields, screening)
if hitl_decision["action"] == "skip":
    logger.warning(
        "HITL skip — required fields unresolved: %s",
        hitl_decision["unresolved_required"],
    )
    from jobpulse import telegram_agent
    telegram_agent.send_message(
        "Skipping job — required screening question(s) timed out: "
        + ", ".join(hitl_decision["unresolved_required"])
    )
    return {"status": "hitl_skip", "unresolved_required": hitl_decision["unresolved_required"]}

if hitl_decision["needs_review"]:
    base.setdefault("hitl_review_flags", []).extend(hitl_decision["needs_review"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_native_filler_hitl_skip.py -v`
Expected: PASS — 3 tests passed

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_filler_hitl_skip.py
git commit -m "feat(hitl): caller-side skip-or-flag policy on HITL outcome"
```

---

### Task 14: End-to-end wiring test (real chain, real DBs)

**Files:**
- Create: `tests/jobpulse/test_hitl_wiring_e2e.py`

This test verifies the FULL chain: `screen_questions` invocation → `question_router.ask_batch` → `notification_sinks.telegram_sink.send` (captured) → simulated Telegram reply → `process_hitl_reply` → `question_router.resolve(persist=True)` → `hitl_persist.save_human_answer` → all 3 caches receive data → answer returned to `screen_questions`. No mocking of internal flow; only Telegram network egress is captured.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_hitl_wiring_e2e.py
"""End-to-end HITL wiring test.

Real DBs (under tmp_path), real question_router, real hitl_persist.
Telegram send is captured (no network). The simulated user replies
through process_hitl_reply, exercising the same code path the listener
calls.
"""
import asyncio
import sqlite3
from pathlib import Path

import pytest

from jobpulse import (
    correction_capture,
    hitl_persist,
    hitl_reply,
    question_router,
    question_store,
    screening_semantic_cache,
)
from jobpulse.form_engine import field_mapper
from jobpulse.notification_sinks import telegram_sink


@pytest.fixture
def isolated_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(question_store, "_DB_PATH", str(tmp_path / "pending.db"))
    monkeypatch.setattr(
        correction_capture, "_DEFAULT_DB", str(tmp_path / "corrections.db")
    )
    monkeypatch.setattr(
        screening_semantic_cache, "_DEFAULT_SQLITE_PATH",
        str(tmp_path / "screening.db"),
    )
    # ProfileStore: redirect to tmp_path
    user_db = tmp_path / "user_profile.db"
    profile_key = tmp_path / ".profile_key"
    from shared import profile_store as ps_mod
    monkeypatch.setattr(ps_mod, "_DEFAULT_DB_PATH", user_db)
    monkeypatch.setattr(ps_mod, "_DEFAULT_KEY_PATH", profile_key)
    ps_mod._shared_store = None
    question_router._FUTURES.clear()
    yield tmp_path


@pytest.mark.asyncio
async def test_full_hitl_chain_from_question_to_three_caches(isolated_dbs, monkeypatch):
    # ── 1. Stub out ScreeningPipeline to force HITL fallback
    class StubPipeline:
        def __init__(self, profile): pass
        def answer(self, question, field, job_context):
            return {"answer": "", "confidence": 0.0, "source": "no_answer", "intent": ""}

    monkeypatch.setattr(field_mapper, "ScreeningPipeline", StubPipeline, raising=False)
    monkeypatch.setattr(field_mapper, "_screen_questions_llm_batch",
                        lambda *a, **kw: ({}, 0))

    # ── 2. Capture Telegram messages instead of sending them
    sent_messages: list[str] = []
    def fake_telegram_send(text, chat_id=None, reply_markup=None):
        sent_messages.append(text)
        return True
    monkeypatch.setattr("jobpulse.telegram_agent.send_message", fake_telegram_send)

    # ── 3. Drive the form filler with two unknown fields
    fields = [
        {"label": "What is your visa status?", "type": "select",
         "options": ["Option A", "Option B"], "required": True},
        {"label": "What is your notice period?", "type": "text",
         "options": [], "required": True},
    ]

    async def simulate_user():
        # Wait for both qid markers to appear in messages
        await asyncio.sleep(0.1)
        pending = question_store.list_pending()
        assert len(pending) == 2

        # Reply via the SAME path the listener uses
        for row in pending:
            qid = row["id"]
            answer = "Option A" if row["options"] else "1 month"
            # Pretend the user typed back: "<answer> [qid:<qid>]"
            confirmation = hitl_reply.process_hitl_reply(f"{answer} [qid:{qid}]")
            assert confirmation is not None and "Resolved" in confirmation

    asyncio.create_task(simulate_user())

    answers, _ = await field_mapper.screen_questions(
        unresolved_fields=fields,
        job_context={
            "url": "https://example.com/jobs/123",
            "platform": "greenhouse",
        },
        profile_store=None,
        correction_warning="",
    )

    # ── 4. Verify the form got the human answers
    assert answers["What is your visa status?"] == "Option A"
    assert answers["What is your notice period?"] == "1 month"

    # ── 5. Verify Telegram got 2 messages with [qid:] markers
    assert len(sent_messages) == 2
    assert all("[qid:" in m for m in sent_messages)

    # ── 6. Verify all 3 caches received the answer
    # 6a. ScreeningSemanticCache (SQLite shadow)
    cache_db = isolated_dbs / "screening.db"
    if cache_db.exists():
        with sqlite3.connect(cache_db) as conn:
            rows = conn.execute(
                "SELECT question_text, answer, confidence FROM screening_semantic_cache"
            ).fetchall()
        questions = {r[0] for r in rows}
        assert "What is your visa status?" in questions
        assert "What is your notice period?" in questions
        for _, ans, conf in rows:
            assert conf == 1.0  # human answers are always confidence=1.0

    # 6b. CorrectionCapture
    with sqlite3.connect(isolated_dbs / "corrections.db") as conn:
        rows = conn.execute(
            "SELECT field_label, agent_value, user_value FROM field_corrections"
        ).fetchall()
    fields_seen = {r[0] for r in rows}
    assert "what is your visa status?" in fields_seen
    assert "what is your notice period?" in fields_seen

    # 6c. ProfileStore.screening_defaults — only writes when intent is set,
    # and our stub pipeline returns intent="", so this table will be empty
    # for this test. To verify the *path* fires, separately call
    # save_human_answer with a non-empty intent:
    hitl_persist.save_human_answer(
        question_text="dummy",
        answer="dummy_ans",
        options=[],
        field_type="text",
        intent="dummy_intent",
        source_url="https://x",
    )
    with sqlite3.connect(isolated_dbs / "user_profile.db") as conn:
        row = conn.execute(
            "SELECT answer FROM screening_defaults WHERE question_type = ?",
            ("dummy_intent",),
        ).fetchone()
    assert row is not None
    assert row[0] == "dummy_ans"
```

- [ ] **Step 2: Run test to verify it fails initially or passes if all earlier tasks shipped**

Run: `python -m pytest tests/jobpulse/test_hitl_wiring_e2e.py -v`
Expected: PASS if Tasks 1-13 are merged. If FAIL, the failure points to a wiring gap — fix in the failing module, do not patch this test.

- [ ] **Step 3: Inspect a failing assertion (only if step 2 fails)**

Re-read the assertion that failed and trace via MCP:

```bash
# Example: if "screening.db doesn't exist" — semantic cache isn't being called
python -c "from jobpulse import hitl_persist; print(hitl_persist.save_human_answer.__doc__)"
```

- [ ] **Step 4: After all assertions pass, run the full HITL test set**

Run: `python -m pytest tests/jobpulse/test_question_store.py tests/jobpulse/test_question_router.py tests/jobpulse/test_question_router_timeout.py tests/jobpulse/test_telegram_sink.py tests/jobpulse/test_telegram_listener_hitl.py tests/jobpulse/test_hitl_persist.py tests/jobpulse/test_keep_alive.py tests/jobpulse/test_screen_questions_hitl.py tests/jobpulse/test_native_filler_hitl_skip.py tests/jobpulse/test_hitl_wiring_e2e.py tests/shared/test_notifications_router.py tests/shared/test_profile_store_screening_defaults.py -v`
Expected: PASS — every test in the HITL slice green.

- [ ] **Step 5: Commit**

```bash
git add tests/jobpulse/test_hitl_wiring_e2e.py
git commit -m "test(hitl): end-to-end wiring — question to all three caches"
```

---

### Task 15: Documentation — jobs.md and jobpulse/CLAUDE.md

**Files:**
- Modify: `.claude/rules/jobs.md` (append new section after "Dry Run → Approve → Learn")
- Modify: `jobpulse/CLAUDE.md` (add HITL flow under existing screening pipeline section)

- [ ] **Step 1: Append HITL section to `.claude/rules/jobs.md`**

Add immediately after the section titled "Dry Run -> Approve -> Learn (MANDATORY)":

```markdown
## Human-in-the-Loop Screening (MANDATORY when agent uncertain)
When ScreeningPipeline + LLM batch both fail to answer a screening question, the agent pauses **in the same browser session** and asks the human via the notification_router.

Pipeline (`jobpulse/form_engine/field_mapper.py::screen_questions`):
1. Pipeline + LLM resolve everything they can (existing behaviour)
2. Remaining unknowns → `question_router.ask_batch(...)` returns futures
3. ONE batched Telegram notification per form page (`jobpulse/notification_sinks/telegram_sink.py`)
4. `await question_router.await_with_reminders(...)` — 15min hard timeout, reminders at 5/10min
5. Workday/iCIMS only — `jobpulse/keep_alive.py::keep_alive_until` runs as sibling task: synthesized mousemove every 60s + storage_state snapshot every 5min. Greenhouse/Lever/Ashby and LinkedIn/Indeed/Reed are no-op.
6. On reply (`jobpulse/hitl_reply.py::process_hitl_reply`): `question_router.resolve(qid, answer)` → `hitl_persist.save_human_answer(...)` writes to ScreeningSemanticCache (confidence=1.0), ProfileStore.screening_defaults (when intent classified), and CorrectionCapture (source="hitl"). Same question is autonomous next run.
7. Timeout policy: required + no answer → skip job + Telegram alert. Optional + no answer → leave blank, flag for dry-run review.

Reply format: user replies with the option text, or a numeric index for >3-option lists, plus a `[qid:<32-hex>]` marker the listener parses. Inline-keyboard buttons available for ≤3 options.

When the NEURALIS mobile app ships (Phase 1A scaffold + 1B push/WS — see `docs/superpowers/specs/mobile-app-integration/`), FCM and WebSocket sinks register themselves at startup; Telegram sink stays for fallback through Phase 4.

DO NOT bypass HITL with hardcoded fallbacks — every blank answer is an opportunity to learn.
```

- [ ] **Step 2: Update `jobpulse/CLAUDE.md` Screening Pipeline section**

Locate the heading "### Screening Pipeline (10 files)" and add at the end of that section:

```markdown
### HITL Add-ons (May 2026)
- `question_store.py` — pending_questions DB CRUD (`data/pending_questions.db`)
- `question_router.py` — asyncio.Future registry + ask_one/ask_batch/resolve/await_with_reminders. AgentQuestionEvent dataclass.
- `notification_sinks/telegram_sink.py` — converts AgentQuestionEvent → NotificationEvent → Telegram with inline keyboards (1-3 opts) or numbered list (4+).
- `hitl_reply.py` — parses `[qid:]` markers from Telegram replies, calls question_router.resolve.
- `hitl_persist.py` — on resolve, persists to all 3 caches: ScreeningSemanticCache (confidence=1.0), ProfileStore.screening_defaults, CorrectionCapture (source="hitl").
- `keep_alive.py` — Workday/iCIMS-only synthesized mouse activity + storage_state snapshots while a question is pending. No-op on Cloudflare-protected aggregators.

`shared/notifications/router.py` — multi-sink fan-out (Telegram now; FCM + WS stubs activate when mobile app ships per `docs/superpowers/specs/mobile-app-integration/`).
```

- [ ] **Step 3: Sanity-check rendering**

Run: `python -c "import pathlib; t=pathlib.Path('.claude/rules/jobs.md').read_text(); assert 'Human-in-the-Loop Screening' in t; print('jobs.md OK')"` and same for `jobpulse/CLAUDE.md`:

```bash
python -c "import pathlib; t=pathlib.Path('jobpulse/CLAUDE.md').read_text(); assert 'HITL Add-ons' in t; print('jobpulse/CLAUDE.md OK')"
```
Expected: `jobs.md OK` and `jobpulse/CLAUDE.md OK`

- [ ] **Step 4: No new tests required for documentation; full suite re-run sanity**

Run: `python -m pytest tests/jobpulse/ tests/shared/ -v -k "hitl or screening or notification or question_router or keep_alive"`
Expected: PASS — all HITL-tagged tests green.

- [ ] **Step 5: Commit**

```bash
git add .claude/rules/jobs.md jobpulse/CLAUDE.md
git commit -m "docs(hitl): document mid-form HITL screening flow + sinks"
```

---

## Self-Review Notes (resolved before this plan was finalized)

1. **Spec coverage** — every locked architectural requirement (1-9 in user spec) maps to:
   - (1) question_router.py + pending_questions table → Tasks 1-4, 10
   - (2) shared/notifications/router.py → Task 5; FCM + WS sinks are NOT created in this plan (stubs activate via mobile spec Phase 1A/1B)
   - (3) hook into LLM-no-answer path → Task 12 (caller-side at `screen_questions`, not in `ScreeningPipeline.answer`; rationale in Architecture header)
   - (4) Telegram listener integration → Task 7
   - (5) Browser keep-alive → Task 11
   - (6) Persistent caching to 3 sinks → Tasks 8 + 9
   - (7) Batched per page → Task 12
   - (8) 15-min timeout + reminders → Task 10
   - (9) Notification UX (inline keyboard / numbered list) → Task 6

2. **Placeholder scan** — no "TBD" / "implement later" / "similar to Task N" left.

3. **Type consistency** — `qid: str` (uuid4 hex) everywhere. `AgentQuestionEvent` and `NotificationEvent` defined once. `ask_one` returns `tuple[str, asyncio.Future[str]]` consistently. `resolve(qid, answer, *, persist=True, …)` signature held across Tasks 3 and 8. `keep_alive_until(future, page, platform, snapshot_dir, *, event_interval_s, snapshot_interval_s)` consistent across declaration and tests.

4. **Future cancellation on timeout** — `await_with_reminders` (Task 10) explicitly cancels the future and pops from `_FUTURES` on timeout. Late `resolve()` calls return False (idempotent). Keep-alive task is gathered/cancelled by the caller in Task 12.

5. **PII discipline** — all examples use generic placeholders ("Option A", "1 month", "What is your visa status?"). No real screening answers.

6. **Real-data wiring (rule)** — Task 14 uses tmp_path for every DB and exercises the real chain end to end with no mocked internals.

---

### Task 16: Session-preserved submit gate (collapses dry-run-stop bail-out)

**Why this is here:** The HITL primitive built in Tasks 1–15 solves field-level
"agent doesn't know an answer." The same primitive solves application-level
"form is filled, ready to submit, awaiting human approval" — and today's
implementation has THREE problems that ship together as one bug:

1. `scan_pipeline.py:903–926` (DRY-RUN STOP) closes the Playwright session and
   returns `RouteResult("queued_for_review", ...)`. The browser tab stays open,
   but the agent's process exits and the page object is dead.
2. `LiveReviewSession.fill_and_request_approval()` always re-runs the full
   ApplicationOrchestrator on resume — re-navigates, re-scans, re-fills 14+
   fields. Total wasted work per submit approval.
3. `_remaining_manual_help_labels()` consults `_failed_fill_labels()` from the
   FRESH fill, not the original dry-run's state. Mismatched verifier between
   dry-run and submit-time creates false-positive manual-help loops (the
   actual blocker on Contentful 2026-05-04).

The Task 1–15 primitive (`question_router.ask_one` + `asyncio.Future` registry +
in-process page hold + multi-sink `notification_router.emit`) is the correct
shape for the submit gate too. Sub-task 16 wires it.

**Files:**
- Create: `jobpulse/submit_gate.py` — new module, ~80 LOC.
- Modify: `jobpulse/scan_pipeline.py:903–926` — replace DRY-RUN STOP with
  in-process pause via `submit_gate.pause_for_approval()`.
- Modify: `jobpulse/live_review_applicator.py:411–475` — `fill_and_request_approval`
  attaches to the existing page when `_active_review.json` already has a live
  session for this URL; skips re-fill, just screenshots + asks.
- Test: `tests/jobpulse/test_submit_gate_session_preserved.py` — new wiring test.

- [ ] **Step 1: Write the failing wiring test**

```python
# tests/jobpulse/test_submit_gate_session_preserved.py
"""Submit gate must hold the page object alive across the human pause.
The pre-fix bug: DRY-RUN STOP closes Playwright, so a "yes" reply triggers
a fresh fill from scratch. After this fix the same page (and all 14 already-
filled fields) is reused — submit click is the ONLY action between approval
and post_apply_hook firing."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from jobpulse.submit_gate import pause_for_approval, _SUBMIT_FUTURES


@pytest.mark.asyncio
async def test_pause_holds_page_alive_until_approve():
    page = MagicMock()
    page.url = "https://job-boards.greenhouse.io/contentful/jobs/7553930"
    page.close = AsyncMock()  # asserted not called during pause
    qid, fut = pause_for_approval(
        page=page,
        company="Contentful",
        title="Fullstack Software Engineer",
        agent_mapping={"Email": "user@example.com"},
    )
    assert qid in _SUBMIT_FUTURES
    # Resolve from a "different sink" (e.g., Telegram listener thread)
    asyncio.get_event_loop().call_later(
        0.05, lambda: _SUBMIT_FUTURES[qid].set_result("approved")
    )
    answer = await asyncio.wait_for(fut, timeout=2.0)
    assert answer == "approved"
    # Critically: page.close was NEVER awaited. Post-fix the page must be alive.
    page.close.assert_not_awaited()
    assert qid not in _SUBMIT_FUTURES, "Future cleaned up after resolve"
```

- [ ] **Step 2: Run test to verify FAIL**

```bash
pytest tests/jobpulse/test_submit_gate_session_preserved.py -xvs
```

Expected: `ImportError: cannot import name 'pause_for_approval' from 'jobpulse.submit_gate'`

- [ ] **Step 3: Implement `submit_gate.py`**

```python
# jobpulse/submit_gate.py
"""In-process submit-approval gate. Holds the live Playwright page across
the human pause so resume = single Submit click, not a full re-fill.

Mirror of question_router (Task 2) at the application level. Same primitive
shape: SQLite row + asyncio.Future registry + idempotent resolve. One
notification fires via shared notification_router (Task 5)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from shared.logging_config import get_logger
from jobpulse.question_store import get_question_store
from shared.notifications.router import emit, AgentQuestionEvent

logger = get_logger(__name__)

_SUBMIT_FUTURES: dict[str, asyncio.Future[str]] = {}


@dataclass
class SubmitApprovalContext:
    """Snapshot stored in pending_questions row so a restart can re-attach."""
    qid: str
    company: str
    title: str
    page_url: str
    agent_mapping: dict[str, str]
    cv_path: str | None = None
    cl_path: str | None = None


def pause_for_approval(
    *,
    page: Any,
    company: str,
    title: str,
    agent_mapping: dict[str, str],
    cv_path: str | None = None,
    cl_path: str | None = None,
) -> tuple[str, asyncio.Future[str]]:
    """Pause the running pipeline, ask the human via notification_router, return
    (question_id, future). Caller awaits the future inside the SAME async task
    that holds the Playwright page; the page lives until the future resolves."""
    qid = uuid.uuid4().hex
    page_url = getattr(page, "url", "") or ""
    store = get_question_store()
    store.create(
        qid=qid,
        question_text=f"Submit application for {title} @ {company}?",
        options_json='["approve","reject"]',
        field_type="approval",
        source_form_url=page_url,
    )
    fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
    _SUBMIT_FUTURES[qid] = fut
    try:
        emit(AgentQuestionEvent(
            qid=qid,
            kind="submit_approval",
            title=f"Submit application for {company}?",
            body=title,
            options=("approve", "reject"),
            deep_link=f"neuralis://approval/{qid}",
            extra={"company": company, "title": title, "page_url": page_url},
        ))
    except Exception as exc:
        logger.warning("submit_gate: notification emit failed: %s", exc)
    return qid, fut


def resolve(qid: str, answer: str, *, persist: bool = True) -> bool:
    """Idempotent. Returns True if a future was resolved, False if missing."""
    fut = _SUBMIT_FUTURES.pop(qid, None)
    if persist:
        try:
            get_question_store().mark_answered(qid, answer)
        except Exception as exc:
            logger.warning("submit_gate: mark_answered failed: %s", exc)
    if fut is None or fut.done():
        return False
    fut.set_result(answer)
    return True
```

- [ ] **Step 4: Wire into scan_pipeline DRY-RUN STOP**

Replace `scan_pipeline.py:903–926` (the dry-run-stop block) with:

```python
else:
    # In-process pause — page stays alive while human approves on
    # any sink (Telegram, FCM, WS). Replaces DRY-RUN STOP bail-out
    # which closed the Playwright session and forced a wasteful
    # re-fill on approval. Single submit click on resume.
    db.save_application(
        job_id=listing.job_id,
        status="Pending Approval",
        ats_score=ats_score,
        match_tier=tier,
        matched_projects=bundle.matched_project_names,
        cv_path=str(bundle.cv_path),
        cover_letter_path=str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
        applied_at=None,
        notion_page_id=notion_page_id,
        follow_up_date=None,
    )
    logger.info(
        "scan_pipeline: PAUSE FOR APPROVAL %s @ %s (ATS %.1f%%) — page held in-process",
        listing.title, listing.company, ats_score,
    )
    from jobpulse.submit_gate import pause_for_approval, resolve
    page = result.get("_page")
    if page is None:
        # Fallback for callers that didn't pass the live page handle.
        # Same outcome as old DRY-RUN STOP — recommend caller pass _page.
        return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)
    qid, fut = pause_for_approval(
        page=page,
        company=listing.company,
        title=listing.title,
        agent_mapping=result.get("agent_mapping") or {},
        cv_path=str(bundle.cv_path),
        cl_path=str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
    )
    try:
        answer = await asyncio.wait_for(fut, timeout=24 * 3600)
    except asyncio.TimeoutError:
        return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)
    if answer != "approve":
        return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)
    # Approved — single submit click; same page, no re-fill
    submit_result = await applicator._click_submit_only(page=page)
    if submit_result.get("success"):
        confirm_application(
            dry_run_result=result,
            url=listing.url,
            cv_path=bundle.cv_path,
            cover_letter_path=bundle.cover_letter_path,
            job_context={
                "job_id": listing.job_id,
                "company": listing.company,
                "title": listing.title,
            },
        )
        return RouteResult("auto_applied", listing.job_id, listing.title, listing.company)
    return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)
```

- [ ] **Step 5: Run test to verify PASS**

```bash
pytest tests/jobpulse/test_submit_gate_session_preserved.py -xvs
```

Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add jobpulse/submit_gate.py jobpulse/scan_pipeline.py tests/jobpulse/test_submit_gate_session_preserved.py
git commit -m "feat(submit-gate): in-process pause replaces DRY-RUN STOP bail-out

Session-preserved submit approval. Same primitive as question_router
(field-level HITL) applied at the application level. Page stays alive
across the human pause; resume = single Submit click, no re-fill."
```

**Acceptance:**
- A dry-run-completed application that's approved 30 minutes later submits
  via the SAME Playwright page (no re-fill, no re-navigate).
- `LiveReviewSession.fill_and_request_approval` is no longer the resume path;
  scan_pipeline owns the gate and resume entirely.
- Telegram and FCM/WS sinks both can resolve the approval — same
  `question_id` is idempotent across all of them.
- `agent_performance.fill_sessions` row reflects the agent submitting (not
  Claude stepping in), because the agent's coroutine is what calls
  `confirm_application` after `pause_for_approval` returns.

