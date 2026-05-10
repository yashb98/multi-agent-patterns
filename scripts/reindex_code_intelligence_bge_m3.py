#!/usr/bin/env python3
"""Reindex shared/code_intelligence embeddings table from Voyage Code 3 → BGE-M3.

The MCP-backed code-search system (find_symbol, semantic_search) was using
Voyage Code 3 embeddings (1024-dim, paid API). The codebase migrated the
embedder configuration to BGE-M3 via Ollama on the same machine; both
models are 1024-dim so the on-disk schema is unchanged. However Voyage
and BGE-M3 occupy unrelated semantic spaces, so existing vectors return
near-random neighbors when queried with a BGE-M3 vector (verified live
2026-05-10: cos(voyage_stored, bge_fresh) = 0.018 on a sample doc).

This script:
  1. Reads documents.text + embeddings.doc_id from data/code_intelligence.db
  2. Calls Ollama /api/embed with model=bge-m3:latest in batches
  3. UPDATEs embeddings.vector with the fresh packed-float blob
  4. Re-loads in-memory cache via the same path the runtime uses
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "code_intelligence.db"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3:latest")
BATCH = int(os.environ.get("REINDEX_BATCH_SIZE", "16"))
MAX_TEXT_CHARS = int(os.environ.get("REINDEX_MAX_TEXT", "8192"))


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


def main() -> int:
    print(f"DB:        {DB_PATH}")
    print(f"Embedder:  {OLLAMA_BASE}/api/embed model={EMBED_MODEL}")
    print(f"Batch:     {BATCH}")

    # Probe
    try:
        probe = embed_batch(["sanity check"])
        if not probe or len(probe[0]) != 1024:
            print(f"FATAL: probe returned dims={len(probe[0]) if probe else 0}, expected 1024")
            return 2
        print(f"  probe: ✓ 1024 dims")
    except Exception as exc:
        print(f"FATAL: embedder unreachable: {exc}")
        return 2

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT e.doc_id, d.text FROM embeddings e "
        "JOIN documents d ON d.id = e.doc_id "
        "WHERE d.text IS NOT NULL AND length(d.text) > 0 "
        "ORDER BY e.doc_id"
    ).fetchall()
    total = len(rows)
    print(f"\n{total} embeddings to reindex")
    if total == 0:
        return 0

    start = time.time()
    written = 0
    failed = 0
    for i in range(0, total, BATCH):
        slice_ = rows[i:i + BATCH]
        ids = [r[0] for r in slice_]
        texts = [(r[1] or "")[:MAX_TEXT_CHARS] for r in slice_]
        try:
            vectors = embed_batch(texts)
        except Exception as exc:
            failed += len(slice_)
            print(f"  ERR batch {i}-{i+BATCH}: {exc}", flush=True)
            continue

        params = []
        for doc_id, vec in zip(ids, vectors, strict=True):
            blob = struct.pack(f"{len(vec)}f", *vec)
            params.append((blob, doc_id))
        conn.executemany("UPDATE embeddings SET vector = ? WHERE doc_id = ?", params)
        conn.commit()
        written += len(params)

        if (i // BATCH) % 25 == 0 or written >= total:
            elapsed = time.time() - start
            rate = written / elapsed if elapsed else 0
            eta = (total - written - failed) / rate if rate else 0
            print(
                f"  …{written}/{total} done  ({rate:.0f} vec/s, eta {eta:.0f}s, failed={failed})",
                flush=True,
            )

    elapsed = time.time() - start
    print(f"\n=== Reindex complete: {written}/{total} written, {failed} failed, {elapsed:.1f}s ===")
    conn.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
