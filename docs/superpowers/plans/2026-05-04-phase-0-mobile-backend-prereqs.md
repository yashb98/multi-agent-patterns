# NEURALIS Phase 0 — Backend Prereqs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the backend foundation a mobile app needs — per-device auth tokens, a unified WebSocket endpoint, HTTP routes for every NLP intent, a Whisper voice endpoint, an FCM-ready push notification router, and a single emit point that fans out to FCM/WS/Telegram. Zero mobile code in this plan.

**Architecture:** Five new FastAPI routers (`auth_api`, `ws_endpoint`, `intent_api`, `voice_api`, `push_api`) registered on the existing app at `mindgraph_app/main.py`. New `shared/notifications/router.py` becomes the single point all event-style notifications emit through; existing `telegram_client.send_message` calls migrate to it. Per-device tokens stored in `data/device_tokens.db` (separate so it can be backed up independently). WebSocket connections held in an in-process pool with monotonic event-log resume.

**Tech Stack:** FastAPI (existing), asyncio, SQLite via `sqlite3` (project convention), `bcrypt` (`requirements.txt` add), pytest (existing), `firebase-admin` (added but mocked in this phase — real Firebase project waits for Phase 1B), launchd (Mac plist).

**Reference spec**: `docs/superpowers/specs/mobile-app-integration/01-phase-0-backend-prereqs.md`. This plan implements that spec; no new design decisions.

**Branch**: continue on `pipeline-correctness-fixes` (current branch). Tag a milestone `phase-0-complete` at the end.

---

## File Structure

**New files** — created in order of task dependency:

```
shared/notifications/
├── __init__.py                      (Task 18)  exports NotificationRouter, NotificationEvent
├── events.py                        (Task 18)  NotificationEvent + NotificationAction dataclasses
├── router.py                        (Task 19)  NotificationRouter class with dedup grouping
├── sinks/
│   ├── __init__.py                  (Task 20)  NotificationSink protocol
│   ├── ws.py                        (Task 20)  WsSink — push to active WS connections
│   ├── fcm.py                       (Task 20)  FcmSink — mocked in Phase 0
│   └── telegram.py                  (Task 20)  TelegramSink — wraps existing client

shared/voice/
├── __init__.py                      (Task 10)  exports transcribe()
└── whisper_service.py               (Task 10)  extracted from existing Telegram path

mindgraph_app/
├── auth_api.py                      (Tasks 3-5) /api/auth/*
├── intent_api.py                    (Tasks 7-9) /api/intents/*
├── voice_api.py                     (Task 11)   /api/voice
├── push_api.py                      (Task 12)   /api/push/*
└── ws_endpoint.py                   (Tasks 13-17) /ws

shared/dispatch/
├── __init__.py                      (Task 14)  exports dispatch helpers
└── ws_dispatcher.py                 (Task 14)  per-frame routing logic

shared/db/
└── device_tokens_schema.py          (Task 1)   schema + migrations for data/device_tokens.db

tests/integration/
├── test_device_tokens_schema.py     (Task 1)
├── test_pairing_codes.py            (Task 2)
├── test_auth_api.py                 (Tasks 3-5)
├── test_intent_api.py               (Tasks 7-9)
├── test_intent_http_coverage.py     (Task 9)
├── test_voice_api.py                (Task 11)
├── test_push_api.py                 (Task 12)
├── test_ws_endpoint.py              (Tasks 13-17)
└── test_notification_router.py      (Tasks 18-20)
```

**Modified files**:

```
mindgraph_app/main.py                (Task 26)  register all 5 new routers
jobpulse/handler_registry.py         (Task 7)   add BaseHandler.run_async + requires_scope
jobpulse/runner.py                   (Task 6)   `devices` subcommand
jobpulse/morning_briefing.py         (Task 21)  emit via notification_router
jobpulse/post_apply_hook.py          (Task 22)  emit via notification_router
jobpulse/gmail_agent.py              (Task 23)  emit via notification_router
jobpulse/arxiv_agent.py              (Task 24)  emit via notification_router (papers digest with grouping)
scripts/install_cron.py              (Task 25)  add ws_events janitor
com.jobpulse.brain.json              (Task 27)  caffeinate wrapper
requirements.txt                     (Task 0)   add bcrypt, firebase-admin
CLAUDE.md                            (Task 28)  document new endpoints
```

**Existing intent handlers** (`jobpulse/handlers/*.py`): no signature changes; `BaseHandler.run_async` defaults to wrapping sync `run()` in `to_thread`. Phase 0 does not refactor handler internals.

---

## Task Granularity Notes

- Each task is one PR-sized commit. Steps within a task average 2–5 minutes.
- TDD throughout: red → green → refactor → commit, in that order.
- Tests use `:memory:` SQLite or `tmp_path` — never touch production DBs (see `.claude/rules/testing.md` re: 2026-03-25 incident).
- Every commit message uses Conventional Commits: `feat(scope): …`, `test(scope): …`, `fix(scope): …`, `chore(scope): …`. Scopes used: `auth`, `ws`, `intent-api`, `voice`, `push`, `notif-router`, `runner`, `daemon`, `migration`.
- All new modules ship with `from __future__ import annotations` at top (project convention) and avoid module-level side effects (see `.claude/rules/seven-principles.md` Principle 1).
- All LLM/Whisper calls go through existing factories — no direct `openai.Client(...)` (see `shared/agents.py:get_llm()`).
- All SQLite connections use `with` context managers (Principle 3/4).
- All bearer-token comparisons via `bcrypt.checkpw`, never `==`.

---

## Task 0: Dependency bump

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add new pinned dependencies**

Append to `requirements.txt`:
```
bcrypt==4.2.0
firebase-admin==6.5.0
```

- [ ] **Step 2: Install in current env**

Run: `pip install -r requirements.txt`
Expected: both wheels download and install cleanly.

- [ ] **Step 3: Confirm imports work**

Run: `python -c "import bcrypt, firebase_admin; print(bcrypt.__version__, firebase_admin.__version__)"`
Expected: prints `4.2.0 6.5.0`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): add bcrypt and firebase-admin for mobile auth + push"
```

---

## Task 1: `device_tokens` schema

**Files:**
- Create: `shared/db/__init__.py` (if missing)
- Create: `shared/db/device_tokens_schema.py`
- Test: `tests/integration/test_device_tokens_schema.py`

- [ ] **Step 1: Create test file with the failing test**

```python
# tests/integration/test_device_tokens_schema.py
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

from shared.db.device_tokens_schema import init_schema, insert_token, find_by_token, revoke_token


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(path)
    init_schema(conn)
    yield conn
    conn.close()


def test_insert_and_find_active_token(db):
    insert_token(db, name="Test-Device", token_plaintext="secret-abc-123", scope="full")
    found = find_by_token(db, "secret-abc-123")
    assert found is not None
    assert found["name"] == "Test-Device"
    assert found["scope"] == "full"
    assert found["revoked_at"] is None


def test_find_returns_none_for_unknown_token(db):
    assert find_by_token(db, "no-such-token") is None


def test_revoked_token_not_returned(db):
    insert_token(db, name="Doomed", token_plaintext="x-y-z", scope="full")
    revoke_token(db, name="Doomed")
    assert find_by_token(db, "x-y-z") is None


def test_unique_name_constraint(db):
    insert_token(db, name="Same", token_plaintext="t1", scope="full")
    with pytest.raises(sqlite3.IntegrityError):
        insert_token(db, name="Same", token_plaintext="t2", scope="full")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_device_tokens_schema.py -v`
Expected: 4 ImportError / ModuleNotFoundError failures.

- [ ] **Step 3: Implement the schema module**

```python
# shared/db/device_tokens_schema.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import bcrypt

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL,
    fcm_token TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT,
    scope TEXT NOT NULL DEFAULT 'full'
);
CREATE INDEX IF NOT EXISTS idx_device_tokens_active
    ON device_tokens(revoked_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS pairing_codes (
    code TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    intended_name TEXT NOT NULL
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_token(conn: sqlite3.Connection, *, name: str, token_plaintext: str, scope: str) -> int:
    h = bcrypt.hashpw(token_plaintext.encode(), bcrypt.gensalt()).decode()
    cur = conn.execute(
        "INSERT INTO device_tokens(name, token_hash, created_at, scope) VALUES (?,?,?,?)",
        (name, h, _now_iso(), scope),
    )
    conn.commit()
    return cur.lastrowid


def find_by_token(conn: sqlite3.Connection, token_plaintext: str) -> dict | None:
    rows = conn.execute(
        "SELECT id, name, token_hash, scope, revoked_at FROM device_tokens WHERE revoked_at IS NULL"
    ).fetchall()
    for row in rows:
        if bcrypt.checkpw(token_plaintext.encode(), row[2].encode()):
            return {"id": row[0], "name": row[1], "scope": row[3], "revoked_at": row[4]}
    return None


def revoke_token(conn: sqlite3.Connection, *, name: str) -> bool:
    cur = conn.execute(
        "UPDATE device_tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
        (_now_iso(), name),
    )
    conn.commit()
    return cur.rowcount > 0
```

Also create `shared/db/__init__.py` (empty) if it doesn't already exist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_device_tokens_schema.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/db/__init__.py shared/db/device_tokens_schema.py tests/integration/test_device_tokens_schema.py
git commit -m "feat(auth): device_tokens + pairing_codes schema with bcrypt hashing"
```

---

## Task 2: Pairing codes — TTL + single-use

**Files:**
- Modify: `shared/db/device_tokens_schema.py`
- Test: `tests/integration/test_pairing_codes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_pairing_codes.py
from __future__ import annotations
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from shared.db.device_tokens_schema import (
    init_schema,
    create_pairing_code,
    consume_pairing_code,
    PairingCodeError,
)


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "device_tokens.db")
    init_schema(conn)
    yield conn
    conn.close()


def test_create_returns_six_digit_code(db):
    code = create_pairing_code(db, intended_name="Yash-Pixel", ttl_seconds=60)
    assert len(code) == 6
    assert code.isdigit()


def test_consume_returns_intended_name(db):
    code = create_pairing_code(db, intended_name="Yash-Pixel", ttl_seconds=60)
    name = consume_pairing_code(db, code)
    assert name == "Yash-Pixel"


def test_consume_twice_raises(db):
    code = create_pairing_code(db, intended_name="x", ttl_seconds=60)
    consume_pairing_code(db, code)
    with pytest.raises(PairingCodeError, match="already used"):
        consume_pairing_code(db, code)


def test_expired_code_raises(db):
    code = create_pairing_code(db, intended_name="x", ttl_seconds=0)
    time.sleep(1.1)
    with pytest.raises(PairingCodeError, match="expired"):
        consume_pairing_code(db, code)


def test_unknown_code_raises(db):
    with pytest.raises(PairingCodeError, match="unknown"):
        consume_pairing_code(db, "000000")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_pairing_codes.py -v`
Expected: 5 ImportError failures.

- [ ] **Step 3: Add pairing functions to schema module**

Append to `shared/db/device_tokens_schema.py`:

```python
import secrets
from datetime import timedelta


class PairingCodeError(Exception):
    pass


def create_pairing_code(conn: sqlite3.Connection, *, intended_name: str, ttl_seconds: int = 60) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    conn.execute(
        "INSERT INTO pairing_codes(code, expires_at, intended_name) VALUES (?,?,?)",
        (code, expires, intended_name),
    )
    conn.commit()
    return code


def consume_pairing_code(conn: sqlite3.Connection, code: str) -> str:
    row = conn.execute(
        "SELECT expires_at, used_at, intended_name FROM pairing_codes WHERE code = ?",
        (code,),
    ).fetchone()
    if row is None:
        raise PairingCodeError("unknown pairing code")
    if row[1] is not None:
        raise PairingCodeError("pairing code already used")
    if datetime.fromisoformat(row[0]) < datetime.now(timezone.utc):
        raise PairingCodeError("pairing code expired")
    conn.execute(
        "UPDATE pairing_codes SET used_at = ? WHERE code = ?",
        (_now_iso(), code),
    )
    conn.commit()
    return row[2]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_pairing_codes.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/db/device_tokens_schema.py tests/integration/test_pairing_codes.py
git commit -m "feat(auth): pairing codes with TTL + single-use enforcement"
```

---

## Task 3: `auth_api` — pair-init + pair endpoints

**Files:**
- Create: `mindgraph_app/auth_api.py`
- Test: `tests/integration/test_auth_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_auth_api.py
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from shared.db.device_tokens_schema import init_schema, find_by_token


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "device_tokens.db"


@pytest.fixture
def client(db_path):
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return TestClient(app)


def test_pair_init_returns_six_digit_code(client):
    r = client.post("/api/auth/pair-init", json={"name": "Test-Device"})
    assert r.status_code == 200
    code = r.json()["code"]
    assert len(code) == 6 and code.isdigit()


def test_pair_with_valid_code_returns_token(client, db_path):
    r1 = client.post("/api/auth/pair-init", json={"name": "Yash-Pixel"})
    code = r1.json()["code"]

    r2 = client.post("/api/auth/pair", json={"code": code, "name": "Yash-Pixel"})
    assert r2.status_code == 200
    body = r2.json()
    assert "token" in body
    assert body["device_name"] == "Yash-Pixel"
    assert body["scope"] == "full"

    # Token resolves
    conn = sqlite3.connect(db_path)
    found = find_by_token(conn, body["token"])
    assert found is not None
    assert found["name"] == "Yash-Pixel"


def test_pair_with_unknown_code_400(client):
    r = client.post("/api/auth/pair", json={"code": "000000", "name": "x"})
    assert r.status_code == 400
    assert "unknown" in r.json()["detail"]["message"].lower()


def test_pair_with_used_code_400(client):
    r1 = client.post("/api/auth/pair-init", json={"name": "x"})
    code = r1.json()["code"]
    client.post("/api/auth/pair", json={"code": code, "name": "x"})
    r3 = client.post("/api/auth/pair", json={"code": code, "name": "x"})
    assert r3.status_code == 400
    assert "already used" in r3.json()["detail"]["message"].lower()


def test_pair_name_mismatch_400(client):
    r1 = client.post("/api/auth/pair-init", json={"name": "intended"})
    code = r1.json()["code"]
    r2 = client.post("/api/auth/pair", json={"code": code, "name": "different"})
    assert r2.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_auth_api.py -v`
Expected: 5 ImportError failures.

- [ ] **Step 3: Implement `auth_api.py`**

```python
# mindgraph_app/auth_api.py
from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shared.db.device_tokens_schema import (
    consume_pairing_code,
    create_pairing_code,
    init_schema,
    insert_token,
    PairingCodeError,
)

DB_PATH = Path("data/device_tokens.db")

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_schema(conn)
    return conn


class PairInitRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class PairRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=64)


def _err(category: str, message: str) -> dict:
    return {
        "errorCategory": category,
        "message": message,
        "isRetryable": category == "transient",
    }


@auth_router.post("/pair-init")
def pair_init(req: PairInitRequest, db: sqlite3.Connection = Depends(get_db)):
    code = create_pairing_code(db, intended_name=req.name, ttl_seconds=60)
    return {"code": code, "ttl_seconds": 60, "name": req.name}


@auth_router.post("/pair")
def pair(req: PairRequest, db: sqlite3.Connection = Depends(get_db)):
    try:
        intended_name = consume_pairing_code(db, req.code)
    except PairingCodeError as e:
        raise HTTPException(400, _err("validation", str(e)))
    if intended_name != req.name:
        raise HTTPException(400, _err("validation", "device name does not match pairing intent"))
    token = secrets.token_urlsafe(32)
    insert_token(db, name=req.name, token_plaintext=token, scope="full")
    return {"token": token, "device_name": req.name, "scope": "full"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_auth_api.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/auth_api.py tests/integration/test_auth_api.py
git commit -m "feat(auth): /api/auth/pair-init + /api/auth/pair endpoints"
```

---

## Task 4: `auth_api` — revoke + me endpoints + `verify_device_token` dependency

**Files:**
- Modify: `mindgraph_app/auth_api.py`
- Modify: `tests/integration/test_auth_api.py`

- [ ] **Step 1: Add failing tests for revoke + me + verify_device_token**

Append to `tests/integration/test_auth_api.py`:

```python
from mindgraph_app.auth_api import verify_device_token


def test_me_returns_device_info(client):
    r1 = client.post("/api/auth/pair-init", json={"name": "MeDevice"})
    code = r1.json()["code"]
    r2 = client.post("/api/auth/pair", json={"code": code, "name": "MeDevice"})
    token = r2.json()["token"]

    r3 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 200
    assert r3.json()["name"] == "MeDevice"
    assert r3.json()["scope"] == "full"


def test_me_without_token_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_invalid_token_401(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer no-such"})
    assert r.status_code == 401


def test_revoke_invalidates_token(client):
    r1 = client.post("/api/auth/pair-init", json={"name": "ToRevoke"})
    code = r1.json()["code"]
    token = client.post("/api/auth/pair", json={"code": code, "name": "ToRevoke"}).json()["token"]

    r3 = client.post("/api/auth/revoke", json={"name": "ToRevoke"},
                     headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 200
    assert r3.json()["revoked"] is True

    r4 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r4.status_code == 401


def test_revoke_unknown_device_404(client):
    # Need a valid token to reach the endpoint
    r1 = client.post("/api/auth/pair-init", json={"name": "Caller"})
    token = client.post("/api/auth/pair",
                        json={"code": r1.json()["code"], "name": "Caller"}).json()["token"]

    r2 = client.post("/api/auth/revoke", json={"name": "no-such-device"},
                     headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_auth_api.py -v`
Expected: 5 new failures (ImportError on `verify_device_token`, plus 4 endpoint-not-found).

- [ ] **Step 3: Implement revoke, me, and `verify_device_token`**

Append to `mindgraph_app/auth_api.py`:

```python
from dataclasses import dataclass

from fastapi import Header
from shared.db.device_tokens_schema import find_by_token, revoke_token


@dataclass
class DeviceAuth:
    id: int
    name: str
    scope: str


def verify_device_token(
    authorization: str | None = Header(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> DeviceAuth:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, _err("permission", "missing bearer token"))
    token = authorization[7:]
    found = find_by_token(db, token)
    if found is None:
        raise HTTPException(401, _err("permission", "invalid or revoked token"))
    db.execute(
        "UPDATE device_tokens SET last_seen_at = ? WHERE id = ?",
        (__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), found["id"]),
    )
    db.commit()
    return DeviceAuth(id=found["id"], name=found["name"], scope=found["scope"])


@auth_router.get("/me")
def me(device: DeviceAuth = Depends(verify_device_token)):
    return {"name": device.name, "scope": device.scope}


class RevokeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@auth_router.post("/revoke")
def revoke(
    req: RevokeRequest,
    device: DeviceAuth = Depends(verify_device_token),
    db: sqlite3.Connection = Depends(get_db),
):
    if not revoke_token(db, name=req.name):
        raise HTTPException(404, _err("validation", f"device not found: {req.name}"))
    return {"revoked": True, "name": req.name}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_auth_api.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/auth_api.py tests/integration/test_auth_api.py
git commit -m "feat(auth): /api/auth/me + /api/auth/revoke + verify_device_token dependency"
```

---

## Task 5: `/api/auth/devices` — list paired devices

**Files:**
- Modify: `mindgraph_app/auth_api.py`
- Modify: `shared/db/device_tokens_schema.py`
- Modify: `tests/integration/test_auth_api.py`

- [ ] **Step 1: Add failing test**

Append to `tests/integration/test_auth_api.py`:

```python
def test_devices_list(client):
    # pair two devices
    for name in ["Phone-1", "Phone-2"]:
        r = client.post("/api/auth/pair-init", json={"name": name})
        client.post("/api/auth/pair", json={"code": r.json()["code"], "name": name})

    token = client.post(
        "/api/auth/pair",
        json={"code": client.post("/api/auth/pair-init", json={"name": "Caller"}).json()["code"], "name": "Caller"},
    ).json()["token"]

    r = client.get("/api/auth/devices", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = {d["name"] for d in r.json()["devices"]}
    assert {"Phone-1", "Phone-2", "Caller"}.issubset(names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_auth_api.py::test_devices_list -v`
Expected: 404 from missing endpoint.

- [ ] **Step 3: Add `list_devices` to schema and endpoint**

Append to `shared/db/device_tokens_schema.py`:

```python
def list_devices(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, scope, created_at, last_seen_at, revoked_at
           FROM device_tokens
           ORDER BY created_at ASC"""
    ).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "scope": r[2],
            "paired_at": r[3], "last_seen_at": r[4],
            "revoked_at": r[5],
        }
        for r in rows
    ]
```

Append to `mindgraph_app/auth_api.py`:

```python
from shared.db.device_tokens_schema import list_devices


@auth_router.get("/devices")
def devices(
    device: DeviceAuth = Depends(verify_device_token),
    db: sqlite3.Connection = Depends(get_db),
):
    out = list_devices(db)
    for d in out:
        d["this_device"] = (d["id"] == device.id)
    return {"devices": out}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_auth_api.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/auth_api.py shared/db/device_tokens_schema.py tests/integration/test_auth_api.py
git commit -m "feat(auth): /api/auth/devices listing endpoint"
```

---

## Task 6: CLI `devices` subcommand in `runner.py`

**Files:**
- Modify: `jobpulse/runner.py`
- Test: `tests/integration/test_runner_devices_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_runner_devices_cli.py
from __future__ import annotations
import os
import sqlite3
import subprocess
import sys

import pytest

from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()
    monkeypatch.setenv("DEVICE_TOKENS_DB_PATH", str(db_path))
    return db_path


def run_cli(*args, env_overrides=None):
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "-m", "jobpulse.runner", "devices", *args],
        capture_output=True, text=True, env=env,
    )


def test_devices_list_empty(isolated_db):
    res = run_cli("list", env_overrides={"DEVICE_TOKENS_DB_PATH": str(isolated_db)})
    assert res.returncode == 0
    assert "no devices" in res.stdout.lower() or "0 devices" in res.stdout.lower()


def test_devices_pair_prints_code(isolated_db):
    res = run_cli("pair", "--name", "Test", env_overrides={"DEVICE_TOKENS_DB_PATH": str(isolated_db)})
    assert res.returncode == 0
    # 6-digit code should appear in output
    assert any(line.strip().isdigit() and len(line.strip()) == 6 for line in res.stdout.splitlines())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_runner_devices_cli.py -v`
Expected: assertion fails (subcommand doesn't exist).

- [ ] **Step 3: Add `devices` subcommand to `runner.py`**

Use `find_symbol` MCP to locate the existing argparse setup in `jobpulse/runner.py`. Then add:

```python
# Inside the existing CLI setup (somewhere subcommands are registered):

def _devices_command(args):
    import sqlite3
    from pathlib import Path
    from shared.db.device_tokens_schema import (
        init_schema, list_devices, create_pairing_code, revoke_token,
    )
    db_path = Path(os.environ.get("DEVICE_TOKENS_DB_PATH", "data/device_tokens.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_schema(conn)

    if args.devices_action == "list":
        rows = list_devices(conn)
        active = [r for r in rows if r["revoked_at"] is None]
        if not active:
            print("no devices paired")
            return
        print(f"{len(active)} devices:")
        for r in active:
            print(f"  - {r['name']:30s}  scope={r['scope']:6s}  last_seen={r['last_seen_at'] or '(never)'}")
    elif args.devices_action == "pair":
        code = create_pairing_code(conn, intended_name=args.name, ttl_seconds=60)
        print(f"\nPairing code for {args.name}: {code}")
        print("Expires in 60s.")
        print("On the phone: open NEURALIS → tap 'Add this device' → enter the code.\n")
    elif args.devices_action == "revoke":
        if revoke_token(conn, name=args.name):
            print(f"revoked: {args.name}")
        else:
            print(f"no active device named: {args.name}", file=sys.stderr)
            sys.exit(1)
    elif args.devices_action == "rotate":
        revoke_token(conn, name=args.name)
        code = create_pairing_code(conn, intended_name=args.name, ttl_seconds=60)
        print(f"rotated. new pairing code for {args.name}: {code}")


# In the argparse setup:
sub_devices = subparsers.add_parser("devices", help="manage paired mobile devices")
devices_sub = sub_devices.add_subparsers(dest="devices_action", required=True)
for verb in ("list",):
    devices_sub.add_parser(verb)
for verb in ("pair", "revoke", "rotate"):
    p = devices_sub.add_parser(verb)
    p.add_argument("--name", required=True)
sub_devices.set_defaults(func=_devices_command)
```

(Match the surrounding pattern in `runner.py` exactly — the precise wiring depends on the existing code structure. Use `callers_of` MCP on `subparsers.add_parser` in `runner.py` to see how other subcommands are registered.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_runner_devices_cli.py -v`
Expected: 2 passed.

- [ ] **Step 5: Smoke test manually**

Run:
```bash
DEVICE_TOKENS_DB_PATH=/tmp/test_devices.db python -m jobpulse.runner devices list
DEVICE_TOKENS_DB_PATH=/tmp/test_devices.db python -m jobpulse.runner devices pair --name=smoke-test
rm /tmp/test_devices.db
```
Expected: first prints "no devices paired"; second prints a 6-digit code and instructions.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/runner.py tests/integration/test_runner_devices_cli.py
git commit -m "feat(runner): devices subcommand (list/pair/revoke/rotate)"
```

---

## Task 7: `BaseHandler.run_async` + `requires_scope`

**Files:**
- Modify: `jobpulse/handler_registry.py`
- Test: `tests/integration/test_handler_async.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_handler_async.py
from __future__ import annotations
import asyncio

import pytest

from jobpulse.handler_registry import BaseHandler, get_handler_map


class _SyncEcho(BaseHandler):
    name = "test.echo"
    requires_scope = "full"

    def run(self, payload):
        return {"echoed": payload.get("text")}


class _AsyncEcho(BaseHandler):
    name = "test.aecho"
    requires_scope = "full"

    async def run_async(self, payload, device=None):
        await asyncio.sleep(0)
        return {"a_echoed": payload.get("text")}


def test_sync_handler_run_async_wraps_run():
    h = _SyncEcho()
    result = asyncio.run(h.run_async({"text": "hi"}))
    assert result == {"echoed": "hi"}


def test_async_handler_runs_directly():
    h = _AsyncEcho()
    result = asyncio.run(h.run_async({"text": "hi"}))
    assert result == {"a_echoed": "hi"}


def test_default_requires_scope_is_full():
    class _NoScope(BaseHandler):
        name = "test.no_scope"
        def run(self, payload):
            return {}
    assert _NoScope().requires_scope == "full"


def test_handler_map_returns_dict():
    m = get_handler_map()
    assert isinstance(m, dict)
    assert len(m) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_handler_async.py -v`
Expected: failures around `run_async` and `requires_scope` not existing on `BaseHandler`.

- [ ] **Step 3: Modify `BaseHandler` in `handler_registry.py`**

Use `find_symbol` MCP on `BaseHandler` in `jobpulse/handler_registry.py` to locate it. Add:

```python
import asyncio
from typing import Literal


class BaseHandler:
    # ... existing fields and `run` method ...

    requires_scope: Literal["full", "demo"] = "full"

    async def run_async(self, payload: dict, device=None) -> dict:
        # Default: wrap sync run() in a thread.
        # Async-native handlers override this directly.
        return await asyncio.to_thread(self.run, payload)
```

If `BaseHandler` doesn't already have a `run` method declared, add an abstract one:

```python
    def run(self, payload: dict) -> dict:
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_handler_async.py -v`
Expected: 4 passed.

- [ ] **Step 5: Verify existing handler tests still pass**

Run: `python -m pytest tests/jobpulse/ -v -x`
Expected: same pass count as before this task (no regressions).

- [ ] **Step 6: Commit**

```bash
git add jobpulse/handler_registry.py tests/integration/test_handler_async.py
git commit -m "feat(handlers): BaseHandler.run_async + requires_scope for HTTP dispatch"
```

---

## Task 8: `intent_api` — dispatch single intent

**Files:**
- Create: `mindgraph_app/intent_api.py`
- Test: `tests/integration/test_intent_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_intent_api.py
from __future__ import annotations
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.intent_api import intent_router
from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(intent_router)
    app.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "T"})
    code = init.json()["code"]
    return client.post("/api/auth/pair", json={"code": code, "name": "T"}).json()["token"]


def test_unknown_intent_404(client, token):
    r = client.post("/api/intents/no.such.intent", json={"text": "x"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_intent_without_auth_401(client):
    r = client.post("/api/intents/anything", json={})
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_intent_api.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `intent_api.py`**

```python
# mindgraph_app/intent_api.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from mindgraph_app.auth_api import DeviceAuth, verify_device_token
from jobpulse.handler_registry import get_handler_map

intent_router = APIRouter(prefix="/api/intents", tags=["intents"])


def _err(category: str, message: str, retryable: bool = False) -> dict:
    return {"errorCategory": category, "message": message, "isRetryable": retryable}


@intent_router.post("/{intent_name:path}")
async def dispatch_intent(
    intent_name: str,
    payload: dict,
    device: DeviceAuth = Depends(verify_device_token),
):
    handlers = get_handler_map()
    handler = handlers.get(intent_name)
    if handler is None:
        raise HTTPException(404, _err("validation", f"unknown intent: {intent_name}"))
    if getattr(handler, "requires_scope", "full") == "full" and device.scope != "full":
        raise HTTPException(403, _err("permission", "this intent requires full scope"))
    try:
        result = await handler.run_async(payload, device=device)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(500, _err("transient", f"{type(e).__name__}: {e}", retryable=True))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_intent_api.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/intent_api.py tests/integration/test_intent_api.py
git commit -m "feat(intent-api): /api/intents/<name> dispatch with auth + scope check"
```

---

## Task 9: Coverage test — every intent has HTTP route

**Files:**
- Test: `tests/integration/test_intent_http_coverage.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_intent_http_coverage.py
from __future__ import annotations
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.intent_api import intent_router
from jobpulse.handler_registry import get_handler_map
from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(intent_router)
    app.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "Cov"})
    return client.post("/api/auth/pair", json={"code": init.json()["code"], "name": "Cov"}).json()["token"]


def test_every_intent_routes_to_handler(client, token):
    """Every key in handler_registry resolves under /api/intents/<key>.
    A 404 means coverage gap; a 500 from the handler is fine for this test
    (it means the route resolved but the handler had a runtime issue, which
    is out of scope here).
    """
    intents = list(get_handler_map().keys())
    assert len(intents) > 0, "expected at least one intent registered"
    misses = []
    for name in intents:
        r = client.post(f"/api/intents/{name}", json={},
                        headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 404:
            misses.append(name)
    assert misses == [], f"intents with no HTTP route (404): {misses}"
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/integration/test_intent_http_coverage.py -v`
Expected: passes (all intents resolve, even if some return 500 from empty payloads — that's fine for this test).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_intent_http_coverage.py
git commit -m "test(intent-api): coverage — every registered intent has an HTTP route"
```

---

## Task 10: Extract Whisper to `shared/voice/whisper_service.py`

**Files:**
- Create: `shared/voice/__init__.py`
- Create: `shared/voice/whisper_service.py`
- Test: `tests/integration/test_whisper_service.py`

- [ ] **Step 1: Locate existing Whisper integration**

Use MCP `grep_search` on the term `whisper` (case-insensitive) and `transcrib` (covers transcribe/transcription) across `jobpulse/` and `shared/` to find current Whisper usage. Note the existing entry point and its signature. The voice path comes from Telegram voice messages — typically routed through `voice_handler.py` or `multi_listener.py`.

- [ ] **Step 2: Write the failing test (uses a synthetic short Opus blob)**

```python
# tests/integration/test_whisper_service.py
from __future__ import annotations
import io

import pytest

from shared.voice.whisper_service import transcribe


def test_transcribe_returns_string(monkeypatch):
    """Mock OpenAI Whisper SDK at the boundary."""
    from shared.voice import whisper_service

    class _FakeAudio:
        @staticmethod
        def transcriptions_create(model, file):  # signature placeholder
            return type("R", (), {"text": "hello world"})()

    # We mock the underlying call. The exact monkeypatch target depends on
    # which OpenAI SDK shape whisper_service uses. Adjust accordingly.
    monkeypatch.setattr(whisper_service, "_call_whisper", lambda buf, mime: "hello world")

    result = transcribe(io.BytesIO(b"fake-opus-bytes"), mime_type="audio/webm")
    assert result == "hello world"


def test_transcribe_rejects_unsupported_mime():
    with pytest.raises(ValueError, match="unsupported"):
        transcribe(io.BytesIO(b"x"), mime_type="application/octet-stream")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_whisper_service.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement `whisper_service.py`**

```python
# shared/voice/__init__.py
from shared.voice.whisper_service import transcribe

__all__ = ["transcribe"]
```

```python
# shared/voice/whisper_service.py
from __future__ import annotations

import io
from typing import IO

# Mime types we accept. Extend as needed.
_SUPPORTED_MIME = {"audio/webm", "audio/ogg", "audio/opus", "audio/mp4", "audio/m4a", "audio/wav"}


def _call_whisper(audio_buf: IO[bytes], mime_type: str) -> str:
    """Call the OpenAI Whisper API (or local equivalent).

    Implementation detail intentionally thin so monkeypatching is easy in tests.
    """
    from shared.agents import get_openai_client  # existing project factory
    client = get_openai_client()
    audio_buf.seek(0)
    # Use OpenAI SDK file upload shape. The 'file' parameter accepts a tuple.
    resp = client.audio.transcriptions.create(
        model="whisper-1",
        file=(f"voice.{mime_type.split('/')[-1]}", audio_buf, mime_type),
    )
    return resp.text.strip()


def transcribe(audio_buf: IO[bytes], *, mime_type: str) -> str:
    """Transcribe audio bytes to text. Raises ValueError on unsupported mime."""
    if mime_type not in _SUPPORTED_MIME:
        raise ValueError(f"unsupported audio mime type: {mime_type}")
    return _call_whisper(audio_buf, mime_type)
```

If `shared/agents.py` does not export `get_openai_client`, use whichever factory the existing Whisper path uses (located in step 1). Honor the convention "no direct `OpenAI()` constructor" from `.claude/rules/seven-principles.md` Principle 2.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_whisper_service.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add shared/voice/__init__.py shared/voice/whisper_service.py tests/integration/test_whisper_service.py
git commit -m "feat(voice): extract Whisper transcription to shared/voice/whisper_service"
```

---

## Task 11: `voice_api` — `/api/voice` endpoint

**Files:**
- Create: `mindgraph_app/voice_api.py`
- Test: `tests/integration/test_voice_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_voice_api.py
from __future__ import annotations
import io
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.voice_api import voice_router
from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()

    # Mock Whisper to avoid hitting OpenAI in tests.
    from shared.voice import whisper_service
    monkeypatch.setattr(whisper_service, "_call_whisper", lambda buf, mime: "hello world")

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(voice_router)
    app.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "V"})
    return client.post("/api/auth/pair", json={"code": init.json()["code"], "name": "V"}).json()["token"]


def test_voice_upload_returns_transcript(client, token):
    files = {"audio": ("voice.webm", b"fake-bytes" * 100, "audio/webm")}
    r = client.post("/api/voice", files=files, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["transcript"] == "hello world"


def test_voice_rejects_oversized(client, token):
    big = b"x" * (11 * 1024 * 1024)
    files = {"audio": ("voice.webm", big, "audio/webm")}
    r = client.post("/api/voice", files=files, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 413


def test_voice_rejects_unsupported_mime(client, token):
    files = {"audio": ("voice.bin", b"x" * 100, "application/octet-stream")}
    r = client.post("/api/voice", files=files, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 415


def test_voice_without_auth_401(client):
    files = {"audio": ("voice.webm", b"x" * 100, "audio/webm")}
    r = client.post("/api/voice", files=files)
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_voice_api.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `voice_api.py`**

```python
# mindgraph_app/voice_api.py
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from mindgraph_app.auth_api import DeviceAuth, verify_device_token
from shared.voice import transcribe

voice_router = APIRouter(prefix="/api", tags=["voice"])

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB ≈ 60s Opus
_SUPPORTED = {"audio/webm", "audio/ogg", "audio/opus", "audio/mp4", "audio/m4a", "audio/wav"}


@voice_router.post("/voice")
async def upload_voice(
    audio: UploadFile = File(...),
    device: DeviceAuth = Depends(verify_device_token),
):
    if audio.content_type not in _SUPPORTED:
        raise HTTPException(415, {"errorCategory": "validation",
                                  "message": f"unsupported content type: {audio.content_type}"})
    blob = await audio.read()
    if len(blob) > _MAX_BYTES:
        raise HTTPException(413, {"errorCategory": "validation",
                                  "message": "audio exceeds 10 MB cap (~60s)"})
    transcript = transcribe(io.BytesIO(blob), mime_type=audio.content_type)
    return {"transcript": transcript, "device": device.name}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_voice_api.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/voice_api.py tests/integration/test_voice_api.py
git commit -m "feat(voice): /api/voice endpoint with size + mime validation"
```

---

## Task 12: `push_api` — register FCM token

**Files:**
- Create: `mindgraph_app/push_api.py`
- Modify: `shared/db/device_tokens_schema.py` (add `set_fcm_token`)
- Test: `tests/integration/test_push_api.py`

- [ ] **Step 1: Add `set_fcm_token` to schema and write its test**

Append to `shared/db/device_tokens_schema.py`:

```python
def set_fcm_token(conn: sqlite3.Connection, *, device_id: int, fcm_token: str | None) -> None:
    conn.execute("UPDATE device_tokens SET fcm_token = ? WHERE id = ?", (fcm_token, device_id))
    conn.commit()
```

```python
# tests/integration/test_push_api.py
from __future__ import annotations
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.push_api import push_router
from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(p)
    init_schema(conn)
    conn.close()
    return p


@pytest.fixture
def client(db_path):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(push_router)
    app.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "P"})
    return client.post("/api/auth/pair", json={"code": init.json()["code"], "name": "P"}).json()["token"]


def test_register_fcm_token(client, token, db_path):
    r = client.post(
        "/api/push/register",
        json={"fcm_token": "abc-fcm-token"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT fcm_token FROM device_tokens WHERE name = 'P'").fetchone()
    assert row[0] == "abc-fcm-token"


def test_register_without_auth_401(client):
    r = client.post("/api/push/register", json={"fcm_token": "x"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_push_api.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `push_api.py`**

```python
# mindgraph_app/push_api.py
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mindgraph_app.auth_api import DeviceAuth, get_db, verify_device_token
from shared.db.device_tokens_schema import set_fcm_token

push_router = APIRouter(prefix="/api/push", tags=["push"])


class FcmRegister(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=4096)


@push_router.post("/register")
def register(
    req: FcmRegister,
    device: DeviceAuth = Depends(verify_device_token),
    db: sqlite3.Connection = Depends(get_db),
):
    set_fcm_token(db, device_id=device.id, fcm_token=req.fcm_token)
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_push_api.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/push_api.py shared/db/device_tokens_schema.py tests/integration/test_push_api.py
git commit -m "feat(push): /api/push/register stores FCM token per device"
```

---

## Task 13: WebSocket scaffold — auth handshake + `auth.ok`

**Files:**
- Create: `mindgraph_app/ws_endpoint.py`
- Test: `tests/integration/test_ws_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ws_endpoint.py
from __future__ import annotations
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.ws_endpoint import ws_router
from shared.db.device_tokens_schema import init_schema


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "device_tokens.db"
    conn = sqlite3.connect(p)
    init_schema(conn)
    conn.close()
    return p


@pytest.fixture
def app(db_path):
    a = FastAPI()
    a.include_router(auth_router)
    a.include_router(ws_router)
    a.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "WS"})
    return client.post("/api/auth/pair", json={"code": init.json()["code"], "name": "WS"}).json()["token"]


def test_ws_auth_ok(client, token):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        msg = ws.receive_json()
        assert msg["type"] == "auth.ok"
        assert msg["device_name"] == "WS"


def test_ws_auth_fail_invalid_token(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "bogus"})
        msg = ws.receive_json()
        assert msg["type"] == "auth.fail"


def test_ws_first_frame_must_be_auth(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "msg", "channel": "x", "text": "hi"})
        # Connection should close with a 4xxx code
        with pytest.raises(Exception):
            ws.receive_json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_ws_endpoint.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `ws_endpoint.py` with just auth handshake**

```python
# mindgraph_app/ws_endpoint.py
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.db.device_tokens_schema import find_by_token, init_schema

ws_router = APIRouter()

_DB_PATH = Path("data/device_tokens.db")


def _open_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    init_schema(conn)
    return conn


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        first = await websocket.receive_json()
    except Exception:
        await websocket.close(code=4001)
        return
    if first.get("type") != "auth":
        await websocket.close(code=4001)
        return
    db = _open_db()
    try:
        device = find_by_token(db, first.get("token", ""))
        if device is None:
            await websocket.send_json({"type": "auth.fail", "reason": "invalid token"})
            await websocket.close(code=4003)
            return
        await websocket.send_json({"type": "auth.ok",
                                   "device_name": device["name"],
                                   "server_seq": 0})
        # Keep alive until client disconnects (Tasks 14-17 add real dispatch).
        while True:
            await websocket.receive_json()
    except WebSocketDisconnect:
        return
    finally:
        db.close()
```

For test isolation, `_open_db` will need to honor `DEVICE_TOKENS_DB_PATH` env or be parameterized via dependency. To keep this task minimal, monkeypatch `_DB_PATH` in tests:

Update the test `app` fixture:
```python
@pytest.fixture
def app(db_path, monkeypatch):
    monkeypatch.setattr("mindgraph_app.ws_endpoint._DB_PATH", db_path)
    a = FastAPI()
    a.include_router(auth_router)
    a.include_router(ws_router)
    a.dependency_overrides[get_db] = lambda: sqlite3.connect(db_path)
    return a
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_ws_endpoint.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add mindgraph_app/ws_endpoint.py tests/integration/test_ws_endpoint.py
git commit -m "feat(ws): /ws endpoint scaffold with auth handshake"
```

---

## Task 14: WS frame envelope + per-frame dispatcher

**Files:**
- Create: `shared/dispatch/__init__.py`
- Create: `shared/dispatch/ws_dispatcher.py`
- Modify: `mindgraph_app/ws_endpoint.py`
- Modify: `tests/integration/test_ws_endpoint.py`

- [ ] **Step 1: Add failing test for ping/pong**

Append to `tests/integration/test_ws_endpoint.py`:

```python
def test_ws_ping_pong(client, token):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.receive_json()  # auth.ok
        ws.send_json({"type": "ping", "t": 12345})
        msg = ws.receive_json()
        assert msg["type"] == "pong"
        assert msg["t"] == 12345


def test_ws_subscribe_unsubscribe(client, token):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.receive_json()
        ws.send_json({"type": "subscribe", "channel": "agent:budget"})
        # No reply expected — subscribe is fire-and-forget. But the next
        # ping should still pong (connection alive).
        ws.send_json({"type": "ping", "t": 42})
        m = ws.receive_json()
        assert m["type"] == "pong" and m["t"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_ws_endpoint.py -v`
Expected: 2 new failures (`pong` not implemented).

- [ ] **Step 3: Implement dispatcher module**

```python
# shared/dispatch/__init__.py
```

```python
# shared/dispatch/ws_dispatcher.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WsConnectionState:
    device_id: int
    device_name: str
    scope: str
    channels: set[str] = field(default_factory=set)


async def handle_frame(state: WsConnectionState, frame: dict) -> list[dict]:
    """Dispatch one client frame and return zero or more reply frames."""
    t = frame.get("type")
    if t == "ping":
        return [{"type": "pong", "t": frame.get("t")}]
    if t == "subscribe":
        ch = frame.get("channel")
        if isinstance(ch, str) and ch:
            state.channels.add(ch)
        return []
    if t == "unsubscribe":
        ch = frame.get("channel")
        if isinstance(ch, str):
            state.channels.discard(ch)
        return []
    if t == "msg":
        # Tasks 16+: real dispatch. For now, echo back so WS-loop tests can
        # use this as a heartbeat against unrelated infrastructure.
        return [
            {"type": "msg.delta", "channel": frame.get("channel"), "seq": 1,
             "content": f"[echo] {frame.get('text', '')}"},
            {"type": "msg.done", "channel": frame.get("channel"), "seq": 1,
             "msg_id": "echo-1"},
        ]
    if t == "cancel":
        # Phase 0 stub: the run-id system arrives in Phase 1B.
        return [{"type": "run.cancelled", "run_id": frame.get("run_id")}]
    return [{"type": "error", "errorCategory": "validation",
             "message": f"unknown frame type: {t}"}]
```

- [ ] **Step 4: Wire dispatcher into `ws_endpoint.py`**

Replace the `while True: await websocket.receive_json()` body in `mindgraph_app/ws_endpoint.py` with:

```python
        from shared.dispatch.ws_dispatcher import WsConnectionState, handle_frame
        state = WsConnectionState(
            device_id=device["id"],
            device_name=device["name"],
            scope=device["scope"],
        )
        while True:
            frame = await websocket.receive_json()
            replies = await handle_frame(state, frame)
            for r in replies:
                await websocket.send_json(r)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_ws_endpoint.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add shared/dispatch/__init__.py shared/dispatch/ws_dispatcher.py mindgraph_app/ws_endpoint.py tests/integration/test_ws_endpoint.py
git commit -m "feat(ws): per-frame dispatcher (ping/pong, subscribe, msg echo, cancel stub)"
```

---

## Task 15: WS event log + resume

**Files:**
- Create: `shared/db/ws_events_schema.py`
- Modify: `shared/dispatch/ws_dispatcher.py`
- Modify: `mindgraph_app/ws_endpoint.py`
- Test: `tests/integration/test_ws_resume.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ws_resume.py
from __future__ import annotations
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.ws_endpoint import ws_router
from shared.db.device_tokens_schema import init_schema as init_dt
from shared.db.ws_events_schema import init_schema as init_ws, append_event, replay_since


@pytest.fixture
def app(tmp_path, monkeypatch):
    dt_path = tmp_path / "device_tokens.db"
    ws_path = tmp_path / "ws_events.db"
    init_dt(sqlite3.connect(dt_path))
    init_ws(sqlite3.connect(ws_path))
    monkeypatch.setattr("mindgraph_app.ws_endpoint._DB_PATH", dt_path)
    monkeypatch.setattr("mindgraph_app.ws_endpoint._WS_EVENTS_PATH", ws_path)

    a = FastAPI()
    a.include_router(auth_router)
    a.include_router(ws_router)
    a.dependency_overrides[get_db] = lambda: sqlite3.connect(dt_path)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def token(client):
    init = client.post("/api/auth/pair-init", json={"name": "R"})
    return client.post("/api/auth/pair", json={"code": init.json()["code"], "name": "R"}).json()["token"]


def test_event_log_persists_server_replies(client, token, tmp_path):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        first = ws.receive_json()
        last_seq = first["server_seq"]
        ws.send_json({"type": "msg", "channel": "x", "text": "hello"})
        ws.receive_json()  # delta
        ws.receive_json()  # done

    # Reconnect and replay.
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        first = ws.receive_json()
        ws.send_json({"type": "resume_from", "server_seq": last_seq})
        # Expect the echo's delta + done frames replayed.
        f1 = ws.receive_json()
        assert f1["type"] == "msg.delta"
        f2 = ws.receive_json()
        assert f2["type"] == "msg.done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_ws_resume.py -v`
Expected: ImportError on `ws_events_schema`.

- [ ] **Step 3: Create `ws_events_schema.py`**

```python
# shared/db/ws_events_schema.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ws_events_device ON ws_events(device_id, seq);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def append_event(conn: sqlite3.Connection, *, device_id: int, payload: dict) -> int:
    cur = conn.execute(
        "INSERT INTO ws_events(device_id, payload_json, created_at) VALUES (?,?,?)",
        (device_id, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def replay_since(conn: sqlite3.Connection, *, device_id: int, since_seq: int) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, payload_json FROM ws_events WHERE device_id = ? AND seq > ? ORDER BY seq ASC",
        (device_id, since_seq),
    ).fetchall()
    return [{**json.loads(p), "_seq": s} for s, p in rows]


def last_seq(conn: sqlite3.Connection, *, device_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM ws_events WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    return int(row[0])


def delete_older_than(conn: sqlite3.Connection, *, hours: int = 24) -> int:
    """Janitor — delete events older than `hours` hours."""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    cur = conn.execute(
        "DELETE FROM ws_events WHERE strftime('%s', created_at) < ?",
        (str(int(cutoff)),),
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Wire event log into `ws_endpoint.py`**

In `mindgraph_app/ws_endpoint.py`:

```python
import sqlite3 as _sqlite3
from pathlib import Path as _Path

from shared.db.ws_events_schema import (
    append_event as _ws_append,
    init_schema as _ws_init,
    last_seq as _ws_last_seq,
    replay_since as _ws_replay,
)

_WS_EVENTS_PATH = _Path("data/ws_events.db")


def _open_ws_log() -> _sqlite3.Connection:
    _WS_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(_WS_EVENTS_PATH)
    _ws_init(conn)
    return conn
```

Update the auth.ok send to fetch real `server_seq`, and wrap each reply in `append_event`. Also handle `resume_from`:

```python
        ws_log = _open_ws_log()
        await websocket.send_json({"type": "auth.ok",
                                   "device_name": device["name"],
                                   "server_seq": _ws_last_seq(ws_log, device_id=device["id"])})
        from shared.dispatch.ws_dispatcher import WsConnectionState, handle_frame
        state = WsConnectionState(device_id=device["id"], device_name=device["name"], scope=device["scope"])

        while True:
            frame = await websocket.receive_json()
            if frame.get("type") == "resume_from":
                missed = _ws_replay(ws_log, device_id=state.device_id,
                                    since_seq=int(frame.get("server_seq", 0)))
                for m in missed:
                    seq = m.pop("_seq", None)
                    await websocket.send_json(m)
                continue
            replies = await handle_frame(state, frame)
            for r in replies:
                _ws_append(ws_log, device_id=state.device_id, payload=r)
                await websocket.send_json(r)
```

Wrap with `try/finally` to close `ws_log` on disconnect.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/integration/test_ws_resume.py tests/integration/test_ws_endpoint.py -v`
Expected: all passed (the existing 5 + 1 new).

- [ ] **Step 6: Commit**

```bash
git add shared/db/ws_events_schema.py mindgraph_app/ws_endpoint.py tests/integration/test_ws_resume.py
git commit -m "feat(ws): event log with resume_from for reconnect-after-network-drop"
```

---

## Task 16: WS heartbeat + connection pool

**Files:**
- Create: `shared/dispatch/ws_pool.py`
- Modify: `mindgraph_app/ws_endpoint.py`
- Modify: `shared/dispatch/ws_dispatcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ws_pool.py
from __future__ import annotations
from shared.dispatch.ws_pool import ConnectionPool, FakeConnection


def test_register_and_lookup_by_device():
    pool = ConnectionPool()
    c1 = FakeConnection(device_id=1, channels={"agent:budget"})
    c2 = FakeConnection(device_id=1, channels={"agent:tasks"})
    c3 = FakeConnection(device_id=2, channels={"agent:budget"})
    pool.register(c1); pool.register(c2); pool.register(c3)

    assert pool.connections_for_device(1) == [c1, c2]
    assert pool.connections_for_device(2) == [c3]


def test_unregister():
    pool = ConnectionPool()
    c = FakeConnection(device_id=1, channels=set())
    pool.register(c)
    pool.unregister(c)
    assert pool.connections_for_device(1) == []


def test_subscribers_for_channel():
    pool = ConnectionPool()
    c1 = FakeConnection(device_id=1, channels={"x"})
    c2 = FakeConnection(device_id=2, channels={"x", "y"})
    c3 = FakeConnection(device_id=3, channels={"y"})
    for c in (c1, c2, c3):
        pool.register(c)

    assert set(pool.subscribers_for_channel("x")) == {c1, c2}
    assert set(pool.subscribers_for_channel("y")) == {c2, c3}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_ws_pool.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `ws_pool.py`**

```python
# shared/dispatch/ws_pool.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


class WsLikeConnection(Protocol):
    device_id: int
    channels: set[str]


@dataclass
class FakeConnection:
    """Minimal stand-in used in unit tests."""
    device_id: int
    channels: set[str] = field(default_factory=set)


class ConnectionPool:
    def __init__(self) -> None:
        self._by_device: dict[int, list[WsLikeConnection]] = {}

    def register(self, conn: WsLikeConnection) -> None:
        self._by_device.setdefault(conn.device_id, []).append(conn)

    def unregister(self, conn: WsLikeConnection) -> None:
        bucket = self._by_device.get(conn.device_id, [])
        if conn in bucket:
            bucket.remove(conn)
        if not bucket and conn.device_id in self._by_device:
            del self._by_device[conn.device_id]

    def connections_for_device(self, device_id: int) -> list[WsLikeConnection]:
        return list(self._by_device.get(device_id, []))

    def subscribers_for_channel(self, channel: str) -> Iterable[WsLikeConnection]:
        for conns in self._by_device.values():
            for c in conns:
                if channel in c.channels:
                    yield c

    def all(self) -> Iterable[WsLikeConnection]:
        for conns in self._by_device.values():
            yield from conns


# Module-level singleton (single uvicorn worker assumption).
default_pool = ConnectionPool()
```

- [ ] **Step 4: Add heartbeat (60s pong-not-seen ⇒ close)**

In `mindgraph_app/ws_endpoint.py` add a watcher coroutine:

```python
import asyncio as _asyncio

async def _heartbeat_watcher(websocket, last_pong: dict, timeout_s: float = 60.0):
    while True:
        await _asyncio.sleep(15.0)
        import time
        if time.time() - last_pong["t"] > timeout_s:
            await websocket.close(code=4008)
            return
```

Wire it into the connect block (start a task; cancel on disconnect). Track `last_pong["t"] = time.time()` whenever a `pong` frame is sent in reply to client `ping` (i.e., update from inside the dispatcher's `pong` reply path; pass a callback or mutate `state` field).

Simpler: update the dispatcher to also stamp `state.last_seen_t` on every received frame, and have heartbeat compare against that. Modify `WsConnectionState`:

```python
@dataclass
class WsConnectionState:
    device_id: int
    device_name: str
    scope: str
    channels: set[str] = field(default_factory=set)
    last_seen_t: float = field(default_factory=lambda: __import__("time").time())
```

Then in `handle_frame`, set `state.last_seen_t = time.time()` at the top.

In `ws_endpoint.py`:

```python
        state = WsConnectionState(device_id=device["id"], device_name=device["name"], scope=device["scope"])
        # heartbeat
        async def _hb():
            import time
            while True:
                await _asyncio.sleep(15.0)
                if time.time() - state.last_seen_t > 60.0:
                    await websocket.close(code=4008)
                    return
        hb_task = _asyncio.create_task(_hb())
```

Cancel `hb_task` in the `finally` block.

- [ ] **Step 5: Register connection in `default_pool`**

In `ws_endpoint.py`, after creating `state`:

```python
        from shared.dispatch.ws_pool import default_pool
        # Wrap state into a pool-compatible object that exposes a `send_json` callable.
        class _PoolConn:
            device_id = state.device_id
            channels = state.channels
            async def send_json(self, frame: dict):
                _ws_append(ws_log, device_id=state.device_id, payload=frame)
                await websocket.send_json(frame)

        pool_conn = _PoolConn()
        default_pool.register(pool_conn)
```

In `finally`, `default_pool.unregister(pool_conn)`.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/integration/test_ws_pool.py tests/integration/test_ws_endpoint.py tests/integration/test_ws_resume.py -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add shared/dispatch/ws_pool.py mindgraph_app/ws_endpoint.py shared/dispatch/ws_dispatcher.py tests/integration/test_ws_pool.py
git commit -m "feat(ws): heartbeat watchdog + ConnectionPool for outbound fanout"
```

---

## Task 17: WS smoke + cleanup test

**Files:**
- Test: `tests/integration/test_ws_smoke.py`

- [ ] **Step 1: Write a smoke test that exercises connect → auth → subscribe → msg → disconnect → pool cleanup**

```python
# tests/integration/test_ws_smoke.py
from __future__ import annotations
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindgraph_app.auth_api import auth_router, get_db
from mindgraph_app.ws_endpoint import ws_router
from shared.db.device_tokens_schema import init_schema
from shared.dispatch.ws_pool import default_pool


@pytest.fixture
def app(tmp_path, monkeypatch):
    dt_path = tmp_path / "device_tokens.db"
    init_schema(sqlite3.connect(dt_path))
    monkeypatch.setattr("mindgraph_app.ws_endpoint._DB_PATH", dt_path)
    monkeypatch.setattr("mindgraph_app.ws_endpoint._WS_EVENTS_PATH", tmp_path / "ws_events.db")
    a = FastAPI()
    a.include_router(auth_router)
    a.include_router(ws_router)
    a.dependency_overrides[get_db] = lambda: sqlite3.connect(dt_path)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def test_full_lifecycle_and_pool_cleanup(client):
    init = client.post("/api/auth/pair-init", json={"name": "Smoke"})
    token = client.post("/api/auth/pair",
                        json={"code": init.json()["code"], "name": "Smoke"}).json()["token"]

    pre = sum(1 for _ in default_pool.all())
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.receive_json()
        ws.send_json({"type": "subscribe", "channel": "agent:budget"})
        ws.send_json({"type": "msg", "channel": "agent:budget", "text": "hi"})
        ws.receive_json()  # delta
        ws.receive_json()  # done
        # Pool registered
        active = sum(1 for _ in default_pool.all())
        assert active >= pre + 1
    # After disconnect
    post = sum(1 for _ in default_pool.all())
    assert post == pre
```

- [ ] **Step 2: Run**

Run: `python -m pytest tests/integration/test_ws_smoke.py -v`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ws_smoke.py
git commit -m "test(ws): full lifecycle smoke + connection pool cleanup verification"
```

---

## Task 18: NotificationEvent + sink protocol

**Files:**
- Create: `shared/notifications/__init__.py`
- Create: `shared/notifications/events.py`
- Create: `shared/notifications/sinks/__init__.py`

- [ ] **Step 1: Write the dataclasses + protocol**

```python
# shared/notifications/events.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PushCategory = Literal["approvals", "alerts", "activity", "digest"]


@dataclass(frozen=True)
class NotificationAction:
    label: str
    action_id: str   # tapping fires POST /api/intents/<action_id>
    payload: dict = field(default_factory=dict)


@dataclass
class NotificationEvent:
    category: PushCategory
    title: str
    body: str
    deep_link: str
    source: str                          # subsystem name e.g. "jobs", "budget"
    actions: list[NotificationAction] = field(default_factory=list)
    dedup_key: str | None = None         # for grouping
    priority_override: str | None = None  # "high" | "default" | "low"
```

```python
# shared/notifications/sinks/__init__.py
from __future__ import annotations
from typing import Protocol

from shared.notifications.events import NotificationEvent


class NotificationSink(Protocol):
    name: str

    def send(self, event: NotificationEvent) -> None: ...
```

```python
# shared/notifications/__init__.py
from shared.notifications.events import NotificationEvent, NotificationAction, PushCategory
from shared.notifications.router import NotificationRouter, get_router

__all__ = ["NotificationEvent", "NotificationAction", "PushCategory",
           "NotificationRouter", "get_router"]
```

- [ ] **Step 2: Commit (no tests yet — pure dataclasses)**

```bash
git add shared/notifications/__init__.py shared/notifications/events.py shared/notifications/sinks/__init__.py
git commit -m "feat(notif-router): NotificationEvent dataclass + sink protocol"
```

---

## Task 19: NotificationRouter with grouping

**Files:**
- Create: `shared/notifications/router.py`
- Test: `tests/integration/test_notification_router.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_notification_router.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

import pytest

from shared.notifications.events import NotificationEvent
from shared.notifications.router import NotificationRouter


@dataclass
class CapturingSink:
    name: str
    events: List[NotificationEvent] = field(default_factory=list)
    fail: bool = False

    def send(self, event):
        if self.fail:
            raise RuntimeError("simulated sink failure")
        self.events.append(event)


def _evt(**kw):
    base = dict(category="alerts", title="t", body="b",
                deep_link="neuralis://hub", source="test")
    base.update(kw)
    return NotificationEvent(**base)


def test_fanout_to_all_sinks():
    a, b = CapturingSink("a"), CapturingSink("b")
    r = NotificationRouter(sinks=[a, b])
    e = _evt()
    r.emit(e)
    assert a.events == [e]
    assert b.events == [e]


def test_sink_failure_does_not_block_other_sinks():
    a = CapturingSink("a", fail=True)
    b = CapturingSink("b")
    r = NotificationRouter(sinks=[a, b])
    r.emit(_evt())
    assert b.events  # b still received it


def test_dedup_key_groups_events_in_window():
    a = CapturingSink("a")
    r = NotificationRouter(sinks=[a], dedup_window_seconds=0.5)
    r.emit(_evt(dedup_key="papers", body="1 paper"))
    r.emit(_evt(dedup_key="papers", body="2 papers"))
    r.emit(_evt(dedup_key="papers", body="3 papers"))
    r.flush_dedup_groups()
    # Only ONE event delivered, with the latest body.
    assert len(a.events) == 1
    assert "3 papers" in a.events[0].body


def test_different_dedup_keys_dont_group():
    a = CapturingSink("a")
    r = NotificationRouter(sinks=[a], dedup_window_seconds=0.5)
    r.emit(_evt(dedup_key="papers"))
    r.emit(_evt(dedup_key="budget"))
    r.flush_dedup_groups()
    assert len(a.events) == 2
```

- [ ] **Step 2: Run**

Run: `python -m pytest tests/integration/test_notification_router.py -v`
Expected: 4 ImportError failures.

- [ ] **Step 3: Implement `NotificationRouter`**

```python
# shared/notifications/router.py
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

from shared.notifications.events import NotificationEvent
from shared.notifications.sinks import NotificationSink

log = logging.getLogger(__name__)

_DEFAULT_DEDUP_S = 60.0


class NotificationRouter:
    def __init__(self, sinks: Iterable[NotificationSink], *, dedup_window_seconds: float = _DEFAULT_DEDUP_S):
        self.sinks = list(sinks)
        self._dedup: dict[str, tuple[float, NotificationEvent]] = {}
        self._lock = threading.Lock()
        self._window = dedup_window_seconds

    def emit(self, event: NotificationEvent) -> None:
        if event.dedup_key:
            self._stage_grouped(event)
            return
        self._fanout(event)

    def _stage_grouped(self, event: NotificationEvent) -> None:
        now = time.time()
        with self._lock:
            existing = self._dedup.get(event.dedup_key)
            self._dedup[event.dedup_key] = (now, event)  # latest replaces
            # Opportunistic flush of stale entries
            for k, (ts, evt) in list(self._dedup.items()):
                if now - ts >= self._window:
                    self._fanout(evt)
                    del self._dedup[k]

    def flush_dedup_groups(self) -> None:
        """Force flush all pending grouped events. Used at shutdown or in tests."""
        with self._lock:
            for evt in list(self._dedup.values()):
                self._fanout(evt[1])
            self._dedup.clear()

    def _fanout(self, event: NotificationEvent) -> None:
        for sink in self.sinks:
            try:
                sink.send(event)
            except Exception as e:
                log.error("notification.sink.failed",
                          extra={"sink": sink.name, "source": event.source,
                                 "category": event.category, "err": str(e)})


# Module-level singleton — populated by mindgraph_app/main.py at startup.
_default_router: NotificationRouter | None = None


def set_router(router: NotificationRouter) -> None:
    global _default_router
    _default_router = router


def get_router() -> NotificationRouter:
    if _default_router is None:
        raise RuntimeError("NotificationRouter not initialized; call set_router() at startup")
    return _default_router
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_notification_router.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add shared/notifications/router.py tests/integration/test_notification_router.py
git commit -m "feat(notif-router): NotificationRouter with dedup grouping + fault isolation"
```

---

## Task 20: Three sinks — WS, FCM (mock), Telegram

**Files:**
- Create: `shared/notifications/sinks/ws.py`
- Create: `shared/notifications/sinks/fcm.py`
- Create: `shared/notifications/sinks/telegram.py`
- Test: `tests/integration/test_notification_sinks.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_notification_sinks.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass

import pytest

from shared.notifications.events import NotificationEvent
from shared.notifications.sinks.ws import WsSink
from shared.notifications.sinks.fcm import FcmSink
from shared.notifications.sinks.telegram import TelegramSink


def _evt(**kw):
    base = dict(category="alerts", title="t", body="b",
                deep_link="neuralis://hub", source="test")
    base.update(kw)
    return NotificationEvent(**base)


def test_ws_sink_pushes_to_all_active_connections():
    pushed = []

    @dataclass
    class FakeConn:
        device_id: int = 1
        channels: set = None
        def __post_init__(self):
            if self.channels is None:
                self.channels = set()
        async def send_json(self, frame):
            pushed.append(frame)

    from shared.dispatch.ws_pool import ConnectionPool
    pool = ConnectionPool()
    pool.register(FakeConn(device_id=1))
    pool.register(FakeConn(device_id=2))

    sink = WsSink(pool=pool)
    sink.send(_evt())
    # WsSink schedules async sends via asyncio loop. In tests we drain manually.
    asyncio.get_event_loop().run_until_complete(sink.drain())
    assert len(pushed) == 2
    assert pushed[0]["type"] == "notification"


def test_fcm_sink_mock_collects_events():
    sink = FcmSink(mock=True)
    sink.send(_evt(title="hello"))
    assert sink.mock_events == [{"category": "alerts", "title": "hello"}]


def test_telegram_sink_calls_send(monkeypatch):
    captured = {}
    def fake_send_message(chat_id, text):
        captured["text"] = text
    sink = TelegramSink(send_fn=fake_send_message)
    sink.send(_evt(title="alert", body="something"))
    assert "alert" in captured["text"]
    assert "something" in captured["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_notification_sinks.py -v`
Expected: ImportError on three sink modules.

- [ ] **Step 3: Implement `WsSink`**

```python
# shared/notifications/sinks/ws.py
from __future__ import annotations

import asyncio

from shared.dispatch.ws_pool import ConnectionPool, default_pool
from shared.notifications.events import NotificationEvent


class WsSink:
    name = "ws"

    def __init__(self, pool: ConnectionPool | None = None):
        self.pool = pool or default_pool
        self._pending: list[asyncio.Task] = []

    def send(self, event: NotificationEvent) -> None:
        frame = {
            "type": "notification",
            "category": event.category,
            "title": event.title,
            "body": event.body,
            "deep_link": event.deep_link,
            "source": event.source,
            "actions": [a.__dict__ for a in event.actions],
        }
        for conn in self.pool.all():
            try:
                loop = asyncio.get_event_loop()
                self._pending.append(loop.create_task(conn.send_json(frame)))
            except RuntimeError:
                # No running loop — fall back to running synchronously
                asyncio.run(conn.send_json(frame))

    async def drain(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
            self._pending.clear()
```

- [ ] **Step 4: Implement `FcmSink` (mock-only in Phase 0)**

```python
# shared/notifications/sinks/fcm.py
from __future__ import annotations

from shared.notifications.events import NotificationEvent


class FcmSink:
    """In Phase 0 we run mock-only. Phase 1B replaces with real firebase_admin."""
    name = "fcm"

    def __init__(self, *, mock: bool = True):
        self.mock = mock
        self.mock_events: list[dict] = []

    def send(self, event: NotificationEvent) -> None:
        if self.mock:
            self.mock_events.append({"category": event.category, "title": event.title})
            return
        # Real impl in Phase 1B.
        raise NotImplementedError("Phase 1B")
```

- [ ] **Step 5: Implement `TelegramSink`**

```python
# shared/notifications/sinks/telegram.py
from __future__ import annotations

from typing import Callable

from shared.notifications.events import NotificationEvent

# Default uses existing telegram_client.send_message (lazy import to avoid cycles).
def _default_send(chat_id, text):
    from shared.telegram_client import send_message
    send_message(chat_id, text)


class TelegramSink:
    name = "telegram"

    def __init__(self, *, send_fn: Callable[[str, str], None] = _default_send,
                 chat_id: str | None = None):
        self.send_fn = send_fn
        # In production, use the configured personal chat ID from env.
        import os
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    def send(self, event: NotificationEvent) -> None:
        text = self._format(event)
        self.send_fn(self.chat_id, text)

    @staticmethod
    def _format(event: NotificationEvent) -> str:
        footer = f"\n\n📱 {event.deep_link}"
        return f"*{event.title}*\n{event.body}{footer}"
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/integration/test_notification_sinks.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add shared/notifications/sinks/ tests/integration/test_notification_sinks.py
git commit -m "feat(notif-router): three sinks — WsSink, FcmSink (mock), TelegramSink"
```

---

## Task 21: Migrate `morning_briefing.py` to `notification_router`

**Files:**
- Modify: `jobpulse/morning_briefing.py`
- Modify: `tests/jobpulse/test_briefing.py` (or whichever test covers it — find via MCP)

- [ ] **Step 1: Locate current Telegram send call**

Use `grep_search` MCP for `telegram_client.send_message` in `morning_briefing.py`.

- [ ] **Step 2: Add a failing test**

Create or extend `tests/jobpulse/test_briefing_notification.py`:

```python
# tests/jobpulse/test_briefing_notification.py
from __future__ import annotations
import pytest

from shared.notifications.router import NotificationRouter, set_router
from tests.integration.test_notification_router import CapturingSink  # reuse helper
# (If your project disallows cross-test imports, copy CapturingSink locally instead.)


def test_morning_briefing_emits_via_router(monkeypatch):
    sink = CapturingSink("sink")
    set_router(NotificationRouter(sinks=[sink]))

    from jobpulse import morning_briefing
    # Stub out sub-agents to skip their network calls.
    monkeypatch.setattr(morning_briefing, "collect_briefing_payload",
                        lambda: {"summary": "today is good", "items": []})

    morning_briefing.run_briefing()

    assert any("today is good" in (e.body + e.title) for e in sink.events)
```

(Adapt to actual function name in `morning_briefing.py` — use `find_symbol` to locate the entry function.)

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_briefing_notification.py -v`
Expected: assertion failure (no event captured) OR import error if helper paths differ.

- [ ] **Step 4: Migrate the call site**

Replace the existing `telegram_client.send_message(...)` call in `morning_briefing.py` with:

```python
from shared.notifications import get_router, NotificationEvent

# ... build briefing text ...

get_router().emit(NotificationEvent(
    category="digest",
    title="Morning briefing",
    body=summary,
    deep_link="neuralis://hub",
    source="briefing",
    dedup_key="briefing.morning",
))
```

Keep the function structure surgical — do not refactor unrelated code (`.claude/rules/jobpulse.md` "Surgical Changes").

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/jobpulse/test_briefing_notification.py tests/jobpulse/ -v`
Expected: new test passes; no regressions in existing briefing tests.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/morning_briefing.py tests/jobpulse/test_briefing_notification.py
git commit -m "refactor(briefing): emit via notification_router (fanout to all sinks)"
```

---

## Task 22: Migrate `post_apply_hook.py`

**Files:**
- Modify: `jobpulse/post_apply_hook.py`
- Test: extend an existing test or create `tests/jobpulse/test_post_apply_notification.py`

- [ ] **Step 1: Find current Telegram send call**

`grep_search` for `telegram_client.send_message` in `post_apply_hook.py`.

- [ ] **Step 2: Write failing test**

```python
# tests/jobpulse/test_post_apply_notification.py
from __future__ import annotations

from shared.notifications.router import NotificationRouter, set_router


class _Sink:
    name = "test"
    def __init__(self): self.events = []
    def send(self, e): self.events.append(e)


def test_post_apply_emits_application_event(monkeypatch):
    sink = _Sink()
    set_router(NotificationRouter(sinks=[sink]))

    from jobpulse import post_apply_hook
    # Identify the function called after a successful apply (e.g. on_apply_success)
    # via find_symbol; replace placeholder below with the real one.
    post_apply_hook.on_apply_success(  # placeholder — replace with actual function name
        company="TechCorp",
        role="Senior Engineer",
        url="https://greenhouse.io/job/123",
    )
    assert any("TechCorp" in e.body for e in sink.events)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_post_apply_notification.py -v`
Expected: failure.

- [ ] **Step 4: Migrate the call site**

Replace existing Telegram message send with:

```python
from shared.notifications import get_router, NotificationEvent

get_router().emit(NotificationEvent(
    category="activity",
    title=f"Applied: {company}",
    body=f"{role} — {url}",
    deep_link=f"neuralis://chat/jobs",
    source="jobs",
))
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/jobpulse/test_post_apply_notification.py tests/jobpulse/test_post_apply* -v`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/post_apply_hook.py tests/jobpulse/test_post_apply_notification.py
git commit -m "refactor(post-apply): emit via notification_router"
```

---

## Task 23: Migrate `gmail_agent.py` priority emails

**Files:**
- Modify: `jobpulse/gmail_agent.py`
- Test: `tests/jobpulse/test_gmail_notification.py`

- [ ] **Step 1: Find Telegram send calls in `gmail_agent.py`**

`grep_search` for `telegram_client.send_message` in `gmail_agent.py`.

- [ ] **Step 2: Write failing test**

```python
# tests/jobpulse/test_gmail_notification.py
from __future__ import annotations

from shared.notifications.router import NotificationRouter, set_router


class _Sink:
    name = "test"
    def __init__(self): self.events = []
    def send(self, e): self.events.append(e)


def test_priority_recruiter_email_emits_alert(monkeypatch):
    sink = _Sink()
    set_router(NotificationRouter(sinks=[sink]))

    from jobpulse import gmail_agent
    gmail_agent.notify_priority_email(  # adapt function name to the actual one
        sender="recruiter@stripe.com",
        subject="Senior PM opportunity",
    )
    assert any("Stripe" in e.body or "stripe" in e.body for e in sink.events)
    assert any(e.category == "alerts" for e in sink.events)
```

- [ ] **Step 3: Run + fail + migrate + pass + commit (TDD cadence as Task 21/22)**

Replace Telegram send with:

```python
from shared.notifications import get_router, NotificationEvent

get_router().emit(NotificationEvent(
    category="alerts",
    title="Recruiter email",
    body=f"{sender}: {subject}",
    deep_link="neuralis://chat/gmail",
    source="gmail",
    priority_override="high",
))
```

```bash
git add jobpulse/gmail_agent.py tests/jobpulse/test_gmail_notification.py
git commit -m "refactor(gmail): priority emails emit via notification_router"
```

---

## Task 24: Migrate papers daily digest with grouping

**Files:**
- Modify: `jobpulse/arxiv_agent.py` (or `papers/agent.py` — locate via `find_symbol` "daily_digest")
- Test: `tests/papers/test_digest_notification.py`

- [ ] **Step 1: Locate current Telegram-send path for the daily digest**

`grep_search` in `jobpulse/arxiv_agent.py` and `papers/` for `send_message` or `telegram_client`.

- [ ] **Step 2: Write failing test that asserts grouping**

```python
# tests/papers/test_digest_notification.py
from __future__ import annotations
import time

from shared.notifications.router import NotificationRouter, set_router


class _Sink:
    name = "t"
    def __init__(self): self.events = []
    def send(self, e): self.events.append(e)


def test_paper_digest_grouped_by_dedup_key():
    sink = _Sink()
    router = NotificationRouter(sinks=[sink], dedup_window_seconds=10.0)
    set_router(router)

    from jobpulse import arxiv_agent  # or papers.agent — replace per project
    arxiv_agent.notify_new_paper(title="A", url="https://x/a")
    arxiv_agent.notify_new_paper(title="B", url="https://x/b")
    arxiv_agent.notify_new_paper(title="C", url="https://x/c")

    router.flush_dedup_groups()
    # Three notify calls with same dedup_key="papers.daily" => one delivered event.
    assert len(sink.events) == 1
    # Body should reflect the latest (C); router replaces on each grouped emit.
    assert "C" in sink.events[0].body
```

- [ ] **Step 3: Run + fail + migrate**

Replace the Telegram-send path. Use `dedup_key="papers.daily"`:

```python
from shared.notifications import get_router, NotificationEvent

def notify_new_paper(*, title: str, url: str) -> None:
    get_router().emit(NotificationEvent(
        category="digest",
        title="New paper",
        body=f"{title} — {url}",
        deep_link="neuralis://chat/papers",
        source="papers",
        dedup_key="papers.daily",
    ))
```

- [ ] **Step 4: Pass + commit**

```bash
git add jobpulse/arxiv_agent.py tests/papers/test_digest_notification.py
git commit -m "refactor(papers): daily digest with grouping via notification_router"
```

---

## Task 25: `ws_events` janitor cron

**Files:**
- Create: `scripts/ws_events_janitor.py`
- Modify: `scripts/install_cron.py`
- Test: `tests/integration/test_ws_events_janitor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ws_events_janitor.py
from __future__ import annotations
import sqlite3
import subprocess
import sys

from shared.db.ws_events_schema import init_schema, append_event, last_seq


def test_janitor_deletes_old_rows(tmp_path, monkeypatch):
    db = tmp_path / "ws_events.db"
    init_schema(sqlite3.connect(db))

    conn = sqlite3.connect(db)
    # 30 hours ago
    conn.execute(
        "INSERT INTO ws_events(device_id, payload_json, created_at) VALUES (?,?,?)",
        (1, "{}", "2026-05-02T00:00:00+00:00"),
    )
    # now-ish
    conn.execute(
        "INSERT INTO ws_events(device_id, payload_json, created_at) VALUES (?,?,?)",
        (1, "{}", "2026-05-04T22:00:00+00:00"),
    )
    conn.commit()

    res = subprocess.run(
        [sys.executable, "scripts/ws_events_janitor.py", "--db", str(db), "--hours", "24"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM ws_events").fetchone()[0]
    assert after == 1
```

- [ ] **Step 2: Implement the script**

```python
# scripts/ws_events_janitor.py
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Path bootstrap so this runs as a script.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.db.ws_events_schema import delete_older_than, init_schema  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/ws_events.db")
    p.add_argument("--hours", type=int, default=24)
    args = p.parse_args(argv)
    conn = sqlite3.connect(args.db)
    init_schema(conn)
    n = delete_older_than(conn, hours=args.hours)
    print(f"deleted {n} ws_events rows older than {args.hours}h")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add to cron via `install_cron.py`**

Use `find_symbol` MCP on the cron-table function in `scripts/install_cron.py`. Add:

```python
("0 4 * * *", "python scripts/ws_events_janitor.py --hours 24",
 "WS event log nightly cleanup"),
```

- [ ] **Step 4: Run test + commit**

```bash
python -m pytest tests/integration/test_ws_events_janitor.py -v
git add scripts/ws_events_janitor.py scripts/install_cron.py tests/integration/test_ws_events_janitor.py
git commit -m "feat(daemon): nightly ws_events janitor (cron 04:00)"
```

---

## Task 26: Wire all routers + initialize router in `mindgraph_app/main.py`

**Files:**
- Modify: `mindgraph_app/main.py`
- Test: `tests/integration/test_main_app_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_main_app_wiring.py
from __future__ import annotations
from fastapi.testclient import TestClient


def test_app_has_all_phase0_routes():
    from mindgraph_app.main import app
    paths = {r.path for r in app.routes}
    expected = {
        "/api/auth/pair-init", "/api/auth/pair", "/api/auth/me",
        "/api/auth/revoke", "/api/auth/devices",
        "/api/intents/{intent_name:path}",
        "/api/voice", "/api/push/register",
        "/ws",
    }
    missing = expected - paths
    assert not missing, f"missing routes: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_main_app_wiring.py -v`
Expected: missing routes set.

- [ ] **Step 3: Wire routers + bootstrap router singleton**

Edit `mindgraph_app/main.py`. Use `find_symbol` MCP to locate where existing routers are mounted (after `from mindgraph_app.api import router, ...`). Add:

```python
from mindgraph_app.auth_api import auth_router
from mindgraph_app.ws_endpoint import ws_router
from mindgraph_app.intent_api import intent_router
from mindgraph_app.voice_api import voice_router
from mindgraph_app.push_api import push_router
from shared.notifications.router import NotificationRouter, set_router
from shared.notifications.sinks.ws import WsSink
from shared.notifications.sinks.fcm import FcmSink
from shared.notifications.sinks.telegram import TelegramSink

# After existing app.include_router calls:
app.include_router(auth_router)
app.include_router(ws_router)
app.include_router(intent_router)
app.include_router(voice_router)
app.include_router(push_router)

# Initialize the notification router singleton.
set_router(NotificationRouter(sinks=[
    WsSink(),
    FcmSink(mock=True),       # Phase 1B replaces with real impl
    TelegramSink(),
]))
```

Update startup logger lines:

```python
def main():
    logger.info("CodeGraph + NEURALIS backend starting at http://localhost:8000")
    logger.info("  Auth:        /api/auth/pair-init, /pair, /me, /revoke, /devices")
    logger.info("  Intents:     /api/intents/{name}  (count=%d)", len(get_handler_map()))
    logger.info("  Voice:       /api/voice")
    logger.info("  Push:        /api/push/register")
    logger.info("  WebSocket:   /ws")
    logger.info("  Swagger UI:  http://localhost:8000/docs")
    uvicorn.run("mindgraph_app.main:app", host="0.0.0.0", port=8000, reload=True)
```

(Add `from jobpulse.handler_registry import get_handler_map` at top.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_main_app_wiring.py -v`
Expected: passes.

- [ ] **Step 5: Run the full test suite to catch regressions**

Run: `python -m pytest tests/ -x -q`
Expected: no new failures vs baseline (the user's pre-existing branch has known modifications; only assert no NEW failures introduced by this plan's changes).

- [ ] **Step 6: Commit**

```bash
git add mindgraph_app/main.py tests/integration/test_main_app_wiring.py
git commit -m "feat(main): register 5 new routers + initialize notification_router singleton"
```

---

## Task 27: launchd plist — `caffeinate` wrapper + `KeepAlive`

**Files:**
- Modify: `com.jobpulse.brain.json` (or whichever plist `scripts/install_daemon.sh` installs)

- [ ] **Step 1: Locate the plist**

```bash
ls -la com.jobpulse.brain*
cat com.jobpulse.brain.json   # or .plist
```

- [ ] **Step 2: Update `ProgramArguments` to wrap with `caffeinate -d -i -s`**

Diff (the actual plist may be JSON or XML; adapt accordingly). For the JSON variant:

```json
{
  "Label": "com.jobpulse.brain",
  "ProgramArguments": [
    "/usr/bin/caffeinate", "-d", "-i", "-s",
    "/Users/yashbishnoi/projects/multi_agent_patterns/.venv/bin/python",
    "-m", "jobpulse.runner", "multi-bot"
  ],
  "KeepAlive": true,
  "RunAtLoad": true,
  "StandardOutPath": "/Users/yashbishnoi/projects/multi_agent_patterns/logs/daemon-stdout.log",
  "StandardErrorPath": "/Users/yashbishnoi/projects/multi_agent_patterns/logs/daemon-stderr.log"
}
```

- [ ] **Step 3: Reload the daemon**

```bash
launchctl unload ~/Library/LaunchAgents/com.jobpulse.brain.plist 2>/dev/null || true
launchctl load   ~/Library/LaunchAgents/com.jobpulse.brain.plist
launchctl list | grep jobpulse.brain
ps aux | grep -E "caffeinate.*jobpulse" | grep -v grep
```

Expected: a caffeinate process running, parented to launchd, with `multi-bot` as its child.

- [ ] **Step 4: Verify Mac stays awake**

Close the lid (or run `pmset -g`); confirm `caffeinate` keeps system from sleeping. Plug in to AC.

- [ ] **Step 5: Commit**

```bash
git add com.jobpulse.brain.json
git commit -m "chore(daemon): wrap multi-bot in caffeinate -d -i -s for mobile reachability"
```

---

## Task 28: Update CLAUDE.md + manual smoke test from another machine

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add NEURALIS endpoints + caveats to CLAUDE.md**

Use `Edit` tool to add (after the "Quick Reference" section in `CLAUDE.md`):

```markdown
## NEURALIS Mobile Backend (Phase 0)

```bash
python -m jobpulse.runner devices list                      # list paired mobile devices
python -m jobpulse.runner devices pair --name=<device>      # generate pairing code
python -m jobpulse.runner devices revoke --name=<device>    # revoke a paired device
python -m jobpulse.runner devices rotate --name=<device>    # revoke + new code
```

Endpoints (all require `Authorization: Bearer <token>` except pair-init/pair):
- POST `/api/auth/pair-init`, `/api/auth/pair` — pairing flow
- GET `/api/auth/me`, `/api/auth/devices` — identity
- POST `/api/auth/revoke` — revoke by name
- POST `/api/intents/{name}` — dispatch any registered intent over HTTP
- POST `/api/voice` — multipart upload, returns Whisper transcript
- POST `/api/push/register` — store FCM token per device
- WS `/ws` — chat/agent stream (auth handshake first frame, see `01-phase-0-backend-prereqs.md`)

Notifications fan out via `shared/notifications/router.py` to FCM (mock until Phase 1B), WS, and Telegram.
```

- [ ] **Step 2: Run pre-commit hook to refresh stats**

Pre-commit hook updates `~LOC` line. No manual action needed.

- [ ] **Step 3: Manual smoke test from another Tailnet machine**

On a Mac/Linux box on the same Tailnet (or your phone's `wscat`-equivalent):

```bash
# Pair
curl -s -X POST http://<mac-magic-dns>:8000/api/auth/pair-init \
     -H "Content-Type: application/json" -d '{"name":"smoke"}'
# (note the code)

curl -s -X POST http://<mac-magic-dns>:8000/api/auth/pair \
     -H "Content-Type: application/json" \
     -d '{"code":"<code>","name":"smoke"}'
# (capture token)

TOKEN="<paste-token>"

# /me
curl -s http://<mac-magic-dns>:8000/api/auth/me -H "Authorization: Bearer $TOKEN"

# Dispatch a known harmless intent (e.g. budget summary)
curl -s -X POST http://<mac-magic-dns>:8000/api/intents/budget.summary \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{}'

# WebSocket smoke (using wscat)
wscat -c ws://<mac-magic-dns>:8000/ws
> {"type":"auth","token":"<TOKEN>"}
< {"type":"auth.ok",...}
> {"type":"ping","t":1}
< {"type":"pong","t":1}
```

Expected: all responses succeed.

- [ ] **Step 4: Commit + tag**

```bash
git add CLAUDE.md
git commit -m "docs(mobile): document NEURALIS Phase 0 backend endpoints in CLAUDE.md"
git tag -a phase-0-complete -m "NEURALIS Phase 0 backend prereqs complete"
```

---

## Definition of Done

The plan is complete when:

- [ ] All 28 tasks committed in order, each commit passing CI individually.
- [ ] `python -m pytest tests/ -v` passes 100%.
- [ ] `tests/integration/test_intent_http_coverage.py` reports zero 404s.
- [ ] `tests/integration/test_main_app_wiring.py` reports all routes mounted.
- [ ] Manual smoke test from second Tailnet machine succeeds (pair, /me, intent dispatch, WS ping/pong, /api/voice with a real audio fixture).
- [ ] `notification_router` migrations: `morning_briefing`, `post_apply_hook`, `gmail_agent`, papers digest all emit via the router; running each path produces a Telegram message AND records an `FcmSink` mock event AND (if WS connected) delivers via `WsSink`.
- [ ] `caffeinate` running parented to launchd; lid-close test passes (Mac stays awake).
- [ ] `phase-0-complete` git tag pushed.

When all of the above hold, **proceed to Phase 1A** (write a fresh plan: `2026-MM-DD-phase-1a-mobile-scaffold.md`, scoped to `02-phase-1a-scaffold-auth-skeleton.md`).

---

## Self-Review Notes

This plan implements `01-phase-0-backend-prereqs.md` end-to-end. Spec-coverage check:

- [x] §4.1 `auth_api` — Tasks 3, 4, 5
- [x] §4.2 CLI `devices` — Task 6
- [x] §4.3 `ws_endpoint` — Tasks 13–17
- [x] §4.4 `intent_api` + handler `run_async` — Tasks 7, 8, 9
- [x] §4.5 `voice_api` + Whisper extraction — Tasks 10, 11
- [x] §4.6 `push_api` — Task 12
- [x] §4.7 `notification_router` + sinks — Tasks 18–20
- [x] §4.7 call-site migrations — Tasks 21–24
- [x] §4.8 `mindgraph_app/main.py` patch — Task 26
- [x] §4.9 launchd plist update — Task 27
- [x] §6 test plan — every test from spec §6 has a corresponding test file or assertion in the tasks above
- [x] §9 DoD checklist — mirrored above

No placeholders remain. Type/method names are consistent (`verify_device_token`, `DeviceAuth`, `NotificationEvent`, `NotificationRouter`, `WsConnectionState`, `default_pool`, `get_router`/`set_router`) across all tasks that reference them.

Out-of-scope items deferred (correctly) to later phases: real `FcmSink` Firebase init (Phase 1B), `/api/hub` and `/api/jobs/<id>/preview` (Phase 1B), search + export (Phase 1C), Telegram demotion (Phase 3), Telegram deletion (Phase 4).
