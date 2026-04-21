import hashlib
import math
import pytest
from datetime import datetime

from shared.memory_layer._entries import (
    MemoryEntry, MemoryTier, Lifecycle, EdgeType, ProtectionLevel,
)


def _deterministic_embedding(text: str, dims: int = 1024) -> list[float]:
    """Hash-based deterministic embedding for reproducible tests."""
    h = hashlib.sha256(text.encode()).digest()
    raw = []
    for i in range(dims):
        byte_idx = i % len(h)
        raw.append((h[byte_idx] + i) % 256 / 255.0 * 2 - 1)
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def make_entry(
    tier: MemoryTier = MemoryTier.EPISODIC,
    domain: str = "test",
    content: str = "test memory content",
    score: float = 7.0,
    confidence: float = 0.7,
    lifecycle: Lifecycle = Lifecycle.STM,
    access_count: int = 0,
    decay_score: float = 1.0,
    payload: dict | None = None,
    is_tombstoned: bool = False,
    embedding: list[float] | None = None,
) -> MemoryEntry:
    entry = MemoryEntry.create(
        tier=tier, domain=domain, content=content,
        score=score, confidence=confidence, payload=payload,
        embedding=embedding or _deterministic_embedding(content),
    )
    entry.lifecycle = lifecycle
    entry.access_count = access_count
    entry.decay_score = decay_score
    entry.is_tombstoned = is_tombstoned
    return entry


@pytest.fixture
def make_memory():
    """Factory fixture for creating test MemoryEntry objects."""
    return make_entry
