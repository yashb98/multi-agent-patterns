"""Tests for :func:`get_shared_memory_manager` auto-wiring behavior.

Phase 1, item 1. The factory used to always build a JSON-only MemoryManager
(all three new engines = None). The new implementation probes SQLite + Qdrant
+ Neo4j + embedder via env vars and wires whichever are actually available.

These tests pin down:

- The default (no env) auto-wires SQLite in the given storage_dir.
- ``MEMORY_3_ENGINE=0`` disables everything (legacy JSON-only mode).
- Qdrant URL that can't be reached leaves ``_qdrant = None`` (fail-open).
- Neo4j URI that can't be reached leaves ``_neo4j = None`` (fail-open).
- Singleton behaviour survives the new factory (same instance on repeat calls).
- ``storage_dir=tmp_path`` keeps production data untouched.
"""

from __future__ import annotations

import pytest

from shared.memory_layer._manager import (
    MemoryManager,
    _build_three_engine_kit,
    get_shared_memory_manager,
    reset_shared_memory_manager,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Each test starts with a clean singleton and no memory env vars leaking
    in from the developer's shell."""
    for var in (
        "MEMORY_3_ENGINE",
        "MEMORY_SQLITE_PATH",
        "MEMORY_QDRANT_URL",
        "MEMORY_NEO4J_URI",
        "MEMORY_EMBED_PRIMARY",
        "MEMORY_EMBED_FALLBACK",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_shared_memory_manager()
    yield
    reset_shared_memory_manager()


# ─── Default path: SQLite wired, Qdrant/Neo4j skipped ──

def test_default_auto_wires_sqlite_only(tmp_path):
    kit = _build_three_engine_kit(str(tmp_path))
    assert kit["sqlite_store"] is not None
    assert kit["qdrant"] is None
    assert kit["neo4j"] is None
    # Embedder is created (it's lazy, no network call on __init__)
    assert kit["embedder"] is not None


def test_default_sqlite_file_lives_inside_storage_dir(tmp_path):
    _build_three_engine_kit(str(tmp_path))
    assert (tmp_path / "memories.db").exists()


# ─── Kill-switch ──

def test_memory_3_engine_zero_returns_empty_kit(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_3_ENGINE", "0")
    kit = _build_three_engine_kit(str(tmp_path))
    assert kit == {
        "sqlite_store": None,
        "qdrant": None,
        "neo4j": None,
        "embedder": None,
    }


# ─── Graceful degradation: unreachable backends → None, not crash ──

def test_unreachable_qdrant_leaves_qdrant_none(tmp_path, monkeypatch):
    # Port 65500 is almost never open; the client will fail on ensure_collections.
    monkeypatch.setenv("MEMORY_QDRANT_URL", "http://127.0.0.1:65500")
    kit = _build_three_engine_kit(str(tmp_path))
    assert kit["qdrant"] is None
    assert kit["sqlite_store"] is not None  # SQLite still fine


def test_unreachable_neo4j_leaves_neo4j_none(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_NEO4J_URI", "bolt://127.0.0.1:65500")
    kit = _build_three_engine_kit(str(tmp_path))
    assert kit["neo4j"] is None
    assert kit["sqlite_store"] is not None


# ─── Singleton wiring via the factory ──

def test_singleton_returns_same_instance(tmp_path):
    mm1 = get_shared_memory_manager(storage_dir=str(tmp_path))
    mm2 = get_shared_memory_manager(storage_dir=str(tmp_path))
    assert mm1 is mm2
    assert isinstance(mm1, MemoryManager)


def test_singleton_has_sqlite_by_default(tmp_path):
    mm = get_shared_memory_manager(storage_dir=str(tmp_path))
    assert mm._sqlite is not None
    # Sync + linker + forgetting are instantiated once SQLite exists
    assert mm._sync is not None
    assert mm._linker is not None
    assert mm._forgetting is not None


def test_singleton_with_kill_switch_matches_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_3_ENGINE", "0")
    mm = get_shared_memory_manager(storage_dir=str(tmp_path))
    assert mm._sqlite is None
    assert mm._qdrant is None
    assert mm._neo4j is None
    assert mm._embedder is None
    # Legacy JSON stores still present
    assert mm.episodic is not None
    assert mm.semantic is not None


def test_health_report_reflects_auto_wired_state(tmp_path):
    mm = get_shared_memory_manager(storage_dir=str(tmp_path))
    report = mm.health()
    assert report["sqlite"] == "active"
    assert report["qdrant"] == "not configured"
    assert report["neo4j"] == "not configured"
    assert report["sqlite_count"] == 0
