"""SQLite storage layer for knowledge graph entities and relations."""

import sqlite3
import hashlib
import uuid
from pathlib import Path
from dataclasses import dataclass

from shared.paths import DATA_DIR as _DATA_DIR
DB_PATH = _DATA_DIR / "mindgraph.db"


_conn: sqlite3.Connection | None = None


def _is_conn_valid(conn: sqlite3.Connection | None) -> bool:
    if conn is None:
        return False
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.ProgrammingError:
        return False


def get_conn() -> sqlite3.Connection:
    global _conn
    if not _is_conn_valid(_conn):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1,
            importance REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            type TEXT NOT NULL,
            context TEXT DEFAULT '',
            FOREIGN KEY (from_id) REFERENCES knowledge_entities(id),
            FOREIGN KEY (to_id) REFERENCES knowledge_entities(id)
        );

        CREATE TABLE IF NOT EXISTS processed_files (
            file_hash TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            entity_count INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_entity_type ON knowledge_entities(entity_type);
        CREATE INDEX IF NOT EXISTS idx_entity_name ON knowledge_entities(name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_rel_from ON knowledge_relations(from_id);
        CREATE INDEX IF NOT EXISTS idx_rel_to ON knowledge_relations(to_id);
    """)
    conn.commit()
    conn.close()


def _entity_id(name: str, entity_type: str) -> str:
    """Deterministic ID from name+type for dedup."""
    key = f"{entity_type}:{name.lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _relation_id(from_id: str, to_id: str, rel_type: str) -> str:
    key = f"{from_id}:{to_id}:{rel_type}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def is_file_processed(file_hash: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM processed_files WHERE file_hash=?", (file_hash,)).fetchone()
    conn.close()
    return row is not None


def mark_file_processed(file_hash: str, filename: str, entity_count: int):
    conn = get_conn()
    from datetime import datetime
    conn.execute(
        "INSERT OR REPLACE INTO processed_files VALUES (?,?,?,?)",
        (file_hash, filename, datetime.now().isoformat(), entity_count)
    )
    conn.commit()
    conn.close()


def upsert_entity(name: str, entity_type: str, description: str = "") -> str:
    """Insert or increment mention_count. Returns entity ID."""
    eid = _entity_id(name, entity_type)
    conn = get_conn()
    existing = conn.execute("SELECT id, mention_count, description FROM knowledge_entities WHERE id=?", (eid,)).fetchone()

    if existing:
        new_count = existing["mention_count"] + 1
        # Keep longer description
        desc = description if len(description) > len(existing["description"] or "") else existing["description"]
        conn.execute(
            "UPDATE knowledge_entities SET mention_count=?, description=? WHERE id=?",
            (new_count, desc, eid)
        )
    else:
        conn.execute(
            "INSERT INTO knowledge_entities (id, name, entity_type, description, mention_count) VALUES (?,?,?,?,1)",
            (eid, name.strip(), entity_type, description)
        )
    conn.commit()
    conn.close()
    return eid


def upsert_relation(from_id: str, to_id: str, rel_type: str, context: str = ""):
    """Insert or update relation. Keeps most descriptive context."""
    rid = _relation_id(from_id, to_id, rel_type)
    conn = get_conn()
    existing = conn.execute("SELECT context FROM knowledge_relations WHERE id=?", (rid,)).fetchone()

    if existing:
        ctx = context if len(context) > len(existing["context"] or "") else existing["context"]
        conn.execute("UPDATE knowledge_relations SET context=? WHERE id=?", (ctx, rid))
    else:
        conn.execute(
            "INSERT INTO knowledge_relations (id, from_id, to_id, type, context) VALUES (?,?,?,?,?)",
            (rid, from_id, to_id, rel_type, context)
        )
    conn.commit()
    conn.close()


def recompute_importance():
    """Recompute importance = mention_count / max_mention_count for all entities."""
    conn = get_conn()
    max_count = conn.execute("SELECT MAX(mention_count) FROM knowledge_entities").fetchone()[0] or 1
    conn.execute("UPDATE knowledge_entities SET importance = CAST(mention_count AS REAL) / ?", (max_count,))
    conn.commit()
    conn.close()


def get_full_graph() -> dict:
    """Return full graph as {nodes: [...], edges: [...]}."""
    conn = get_conn()
    nodes = [dict(r) for r in conn.execute("SELECT * FROM knowledge_entities ORDER BY mention_count DESC").fetchall()]
    edges = [dict(r) for r in conn.execute("SELECT * FROM knowledge_relations").fetchall()]
    conn.close()
    return {"nodes": nodes, "edges": edges}


def search_entities(query: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM knowledge_entities WHERE name LIKE ? ORDER BY mention_count DESC LIMIT 50",
        (f"%{query}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    conn = get_conn()
    total_entities = conn.execute("SELECT COUNT(*) FROM knowledge_entities").fetchone()[0]
    total_relations = conn.execute("SELECT COUNT(*) FROM knowledge_relations").fetchone()[0]
    top_entities = [dict(r) for r in conn.execute(
        "SELECT name, entity_type, mention_count FROM knowledge_entities ORDER BY mention_count DESC LIMIT 5"
    ).fetchall()]
    conn.close()
    return {
        "total_entities": total_entities,
        "total_relations": total_relations,
        "top_entities": top_entities,
    }


def clear_all():
    """Delete all entities, relations, and processed files.

    SAFETY: In test mode (JOBPULSE_TEST_MODE=1), this only works if DB_PATH
    has been patched to a temp path. Prevents tests from wiping production data.
    """
    import os
    if os.getenv("JOBPULSE_TEST_MODE"):
        db_str = str(DB_PATH).lower()
        if "tmp" not in db_str and "test" not in db_str and "temp" not in db_str:
            raise RuntimeError(
                f"clear_all() blocked: tests must not wipe production DB ({DB_PATH}). "
                "Patch storage.DB_PATH to a tmp_path fixture."
            )
    conn = get_conn()
    conn.executescript("""
        DELETE FROM knowledge_relations;
        DELETE FROM knowledge_entities;
        DELETE FROM processed_files;
    """)
    conn.commit()
    conn.close()


# Initialize on import
init_db()
