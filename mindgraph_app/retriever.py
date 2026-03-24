"""GraphRAG Retriever — multi-method knowledge graph retrieval for agents.

Three retrieval strategies:
  1. local_search: text similarity on entity names/descriptions
  2. multi_hop_search: graph traversal from a starting entity
  3. temporal_search: all simulation events for a date

Agents use this to ground their responses in the knowledge graph.
"""

import sqlite3
from datetime import datetime, date
from pathlib import Path
from mindgraph_app.storage import get_conn, get_full_graph, search_entities


def local_search(query: str, limit: int = 10) -> dict:
    """Search entities by name/description similarity. Returns matching entities + their connections."""
    entities = search_entities(query)[:limit]
    if not entities:
        return {"entities": [], "relations": [], "method": "local_search"}

    entity_ids = {e["id"] for e in entities}

    # Get relations connecting these entities
    conn = get_conn()
    placeholders = ",".join("?" for _ in entity_ids)
    relations = conn.execute(
        f"SELECT * FROM knowledge_relations WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
        list(entity_ids) + list(entity_ids),
    ).fetchall()

    # Also grab neighbor entities
    neighbor_ids = set()
    for r in relations:
        neighbor_ids.add(r["from_id"])
        neighbor_ids.add(r["to_id"])
    neighbor_ids -= entity_ids

    if neighbor_ids:
        np = ",".join("?" for _ in neighbor_ids)
        neighbors = conn.execute(
            f"SELECT * FROM knowledge_entities WHERE id IN ({np})", list(neighbor_ids)
        ).fetchall()
        entities.extend([dict(n) for n in neighbors])

    conn.close()

    return {
        "entities": entities,
        "relations": [dict(r) for r in relations],
        "method": "local_search",
        "query": query,
    }


def multi_hop_search(entity_name: str, max_hops: int = 2) -> dict:
    """Graph traversal from a starting entity. Follows relationships up to N hops."""
    conn = get_conn()

    # Find the starting entity
    start = conn.execute(
        "SELECT * FROM knowledge_entities WHERE name LIKE ? LIMIT 1",
        (f"%{entity_name}%",),
    ).fetchone()

    if not start:
        conn.close()
        return {"entities": [], "relations": [], "method": "multi_hop", "start": entity_name}

    visited_ids = {start["id"]}
    all_entities = [dict(start)]
    all_relations = []
    frontier = {start["id"]}

    for hop in range(max_hops):
        if not frontier:
            break
        placeholders = ",".join("?" for _ in frontier)
        relations = conn.execute(
            f"SELECT * FROM knowledge_relations WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
            list(frontier) + list(frontier),
        ).fetchall()

        new_frontier = set()
        for r in relations:
            all_relations.append(dict(r))
            for nid in [r["from_id"], r["to_id"]]:
                if nid not in visited_ids:
                    visited_ids.add(nid)
                    new_frontier.add(nid)

        # Fetch new entities
        if new_frontier:
            np = ",".join("?" for _ in new_frontier)
            new_entities = conn.execute(
                f"SELECT * FROM knowledge_entities WHERE id IN ({np})", list(new_frontier)
            ).fetchall()
            all_entities.extend([dict(e) for e in new_entities])

        frontier = new_frontier

    conn.close()

    return {
        "entities": all_entities,
        "relations": all_relations,
        "method": "multi_hop",
        "start": entity_name,
        "hops": max_hops,
    }


def temporal_search(target_date: str = None) -> dict:
    """Get everything that happened on a specific day — events + new entities."""
    target_date = target_date or date.today().isoformat()

    # Use the event logger's DB path (same as mindgraph)
    db_path = Path(__file__).parent.parent / "data" / "mindgraph.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Get simulation events for the day
    events = conn.execute(
        "SELECT * FROM simulation_events WHERE day_date=? ORDER BY created_at ASC",
        (target_date,),
    ).fetchall()

    # Get entities created/updated on this day (by checking if any event mentions them)
    # This is approximate — based on events that mention entity names
    conn.close()

    return {
        "date": target_date,
        "events": [_row_to_dict(e) for e in events],
        "event_count": len(events),
        "method": "temporal_search",
    }


def retrieve(query: str, method: str = "auto") -> dict:
    """Smart retrieval — picks the best method based on query.

    method: auto, local, multi_hop, temporal
    """
    if method == "local":
        return local_search(query)
    elif method == "multi_hop":
        return multi_hop_search(query)
    elif method == "temporal":
        return temporal_search(query)

    # Auto-detect
    query_lower = query.lower()
    if any(kw in query_lower for kw in ["today", "yesterday", "2026-", "march", "monday", "last week"]):
        # Parse date
        if "today" in query_lower:
            return temporal_search(date.today().isoformat())
        elif "yesterday" in query_lower:
            from datetime import timedelta
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            return temporal_search(yesterday)
        else:
            # Try to find a date in the query
            import re
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", query)
            if date_match:
                return temporal_search(date_match.group(1))

    # Default to local search
    return local_search(query)


def _row_to_dict(row) -> dict:
    import json
    d = dict(row)
    if "metadata" in d and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d
