#!/usr/bin/env python3
"""Re-embed Qdrant collections from SQLite source-of-truth using BGE-M3.

The codebase migrated from Voyage 3 Large → BGE-M3 (Ollama). Both are
1024-dim, so Qdrant's collection schema is unchanged, but Voyage and
BGE-M3 occupy unrelated semantic spaces — old vectors return random
neighbors when queried with a BGE-M3 vector.

This script walks each affected collection, fetches the source ``content``
text from ``data/agent_memory/memories.db``, batch-embeds it via the local
Ollama tunnel (BGE-M3), and upserts the new vector against the same point
``memory_id``. Idempotent: running it twice on already-BGE-M3 vectors is
a no-op (the new vector replaces an identical one).

Verified pre-flight (audit probe, 2026-05-10):
  screening_questions: BGE-M3 already (cos=1.000)  → SKIP
  procedures:          VOYAGE-era (cos=0.0051)     → 5232 to re-index
  episodic_memories:   VOYAGE-era (cos=-0.0494)    → 58
  semantic_facts:      VOYAGE-era (cos=0.0307)     → 1568
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Force the LLM-provider pin so this script doesn't accidentally re-route
# completion calls to local Ollama (we only use Ollama for embeddings).
os.environ.setdefault("LLM_PROVIDER", "openai")

DB_PATH = PROJECT_ROOT / "data" / "agent_memory" / "memories.db"
QDRANT_BASE = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3:latest")
BATCH = int(os.environ.get("REINDEX_BATCH_SIZE", "8"))

TIERS = {
    "procedures":        "procedural",
    "episodic_memories": "episodic",
    "semantic_facts":    "semantic",
}


def _to_qdrant_id(memory_id: str) -> int:
    """Mirror of ``shared.memory_layer._qdrant_store._to_qdrant_id``.

    Qdrant requires point IDs to be unsigned integers or UUIDs; SQLite
    memory_ids are 12-char hex strings. Hash with MD5 and truncate to
    63 bits so the same memory_id always maps to the same Qdrant point.
    """
    return int(hashlib.md5(memory_id.encode()).hexdigest(), 16) % (2 ** 63)


def embed_batch(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request(
        f"{OLLAMA_BASE.rstrip('/')}/api/embed",
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    embeddings = data.get("embeddings") or []
    if len(embeddings) != len(texts):
        raise RuntimeError(
            f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs"
        )
    return embeddings


def upsert_qdrant(collection: str, points: list[dict]) -> int:
    """Upsert ``points`` into ``collection``. On 500 (transient server load),
    fall back to per-point upserts. Returns the count actually written.
    """
    try:
        req = urllib.request.Request(
            f"{QDRANT_BASE}/collections/{collection}/points?wait=true",
            data=json.dumps({"points": points}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "ok":
            return len(points)
    except urllib.error.HTTPError as exc:
        if exc.code != 500:
            raise

    # Per-point fallback when the batch failed with 500
    written = 0
    for p in points:
        try:
            req = urllib.request.Request(
                f"{QDRANT_BASE}/collections/{collection}/points?wait=true",
                data=json.dumps({"points": [p]}).encode(),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            if data.get("status") == "ok":
                written += 1
        except Exception:
            pass
    return written


def reindex_tier(collection: str, tier: str) -> tuple[int, int, float]:
    """Re-embed one tier. Returns (rows_processed, batches, elapsed_s)."""
    print(f"\n=== {collection} (tier={tier}) ===", flush=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT memory_id, content FROM memories "
        "WHERE tier = ? AND is_tombstoned = 0 AND content != '' "
        "ORDER BY rowid",
        (tier,),
    ).fetchall()
    conn.close()

    total = len(rows)
    print(f"  {total} rows to re-embed (batch={BATCH})", flush=True)
    start = time.time()
    processed = 0
    batches = 0
    for i in range(0, total, BATCH):
        slice_ = rows[i:i + BATCH]
        texts = [r["content"][:8192] for r in slice_]
        try:
            vectors = embed_batch(texts)
        except Exception as exc:
            print(f"  ERR batch {i}-{i+BATCH}: {exc}", flush=True)
            continue
        points = [
            {"id": _to_qdrant_id(r["memory_id"]), "vector": v}
            for r, v in zip(slice_, vectors)
        ]
        try:
            written = upsert_qdrant(collection, points)
        except Exception as exc:
            print(f"  ERR upsert batch {i}-{i+BATCH}: {exc}", flush=True)
            continue
        if written < len(points):
            print(
                f"  partial batch {i}-{i+BATCH}: {written}/{len(points)} written "
                "(rest hit per-point fallback failure)",
                flush=True,
            )
        processed += written
        batches += 1
        if batches % 10 == 0 or processed >= total:
            elapsed = time.time() - start
            rate = processed / elapsed if elapsed else 0
            eta = (total - processed) / rate if rate else 0
            print(
                f"  …{processed}/{total} done  "
                f"({rate:.0f} vec/s, eta {eta:.0f}s)",
                flush=True,
            )
    elapsed = time.time() - start
    print(f"  ✓ {processed}/{total} re-indexed in {elapsed:.1f}s", flush=True)
    return processed, batches, elapsed


def main() -> int:
    print(f"Re-index target: {QDRANT_BASE}")
    print(f"Embedder: {OLLAMA_BASE}/api/embed model={EMBED_MODEL}")
    print(f"Source SQLite: {DB_PATH}")

    # Sanity-check the embedder before doing any writes
    try:
        probe = embed_batch(["sanity check"])
        if not probe or len(probe[0]) != 1024:
            print(f"FATAL: embedder probe returned {len(probe[0]) if probe else 'nothing'} dims, expected 1024")
            return 2
        print(f"  embedder probe: ✓ 1024 dims")
    except Exception as exc:
        print(f"FATAL: embedder unreachable: {exc}")
        return 2

    grand_total = 0
    grand_elapsed = 0.0
    for collection, tier in TIERS.items():
        processed, _, elapsed = reindex_tier(collection, tier)
        grand_total += processed
        grand_elapsed += elapsed

    print(f"\n=== Re-index complete: {grand_total} vectors in {grand_elapsed:.1f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
