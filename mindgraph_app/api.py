"""FastAPI routes for MindGraph — knowledge graph + simulation events + GraphRAG + process trails."""

import ipaddress
import httpx
from urllib.parse import urlparse
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional
from mindgraph_app import storage, extractor
from mindgraph_app import retriever as graphrag
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _validate_url(url: str) -> None:
    """Reject URLs that could cause SSRF (private IPs, non-HTTPS schemes)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail=f"Unsupported URL scheme: {parsed.scheme}")
    hostname = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise HTTPException(status_code=400, detail="URLs targeting private/internal networks are not allowed")
    except ValueError:
        if hostname in ("localhost", "metadata.google.internal"):
            raise HTTPException(status_code=400, detail="URLs targeting internal hosts are not allowed")

router = APIRouter(prefix="/api/mindgraph")
process_router = APIRouter(prefix="/api/process")


class IngestText(BaseModel):
    text: Optional[str] = None
    url: Optional[str] = None


class RetrieveQuery(BaseModel):
    query: str
    method: str = "auto"  # auto, local, multi_hop, temporal


# ── Knowledge Graph Endpoints ──

@router.post("/ingest")
async def ingest(text: str = Form(None), url: str = Form(None), file: UploadFile = File(None)):
    """Ingest text, file, or URL content into the knowledge graph."""
    content_text = None
    filename = "paste"

    if file and file.filename:
        raw = await file.read()
        content_text = raw.decode("utf-8", errors="replace")
        filename = file.filename
    elif text:
        content_text = text
    elif url:
        _validate_url(url)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_text = resp.text
                filename = url
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    if not content_text or len(content_text.strip()) < 20:
        raise HTTPException(status_code=400, detail="No text provided or text too short")

    result = extractor.extract_from_text(content_text, filename)
    return result


@router.post("/ingest/json")
async def ingest_json(body: IngestText):
    """JSON body alternative for ingest."""
    if body.url:
        _validate_url(body.url)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(body.url)
                resp.raise_for_status()
                text = resp.text
                filename = body.url
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")
    elif body.text:
        text = body.text
        filename = "paste"
    else:
        raise HTTPException(status_code=400, detail="Provide text or url")

    if len(text.strip()) < 20:
        raise HTTPException(status_code=400, detail="Text too short")

    return extractor.extract_from_text(text, filename)


@router.get("/graph")
def get_graph(filter: str = None, date: str = None):
    """Get full graph, optionally filtered by entity name or date."""
    graph = storage.get_full_graph()

    if filter:
        matching_ids = set()
        filtered_nodes = []
        for node in graph["nodes"]:
            if filter.lower() in node["name"].lower() or filter.upper() == node.get("entity_type", ""):
                filtered_nodes.append(node)
                matching_ids.add(node["id"])

        neighbor_ids = set()
        for edge in graph["edges"]:
            if edge["from_id"] in matching_ids:
                neighbor_ids.add(edge["to_id"])
            if edge["to_id"] in matching_ids:
                neighbor_ids.add(edge["from_id"])

        for node in graph["nodes"]:
            if node["id"] in neighbor_ids and node["id"] not in matching_ids:
                filtered_nodes.append(node)

        all_ids = matching_ids | neighbor_ids
        filtered_edges = [e for e in graph["edges"]
                          if e["from_id"] in all_ids and e["to_id"] in all_ids]

        return {"nodes": filtered_nodes, "edges": filtered_edges}

    return graph


@router.get("/entity/{entity_id}")
def get_entity(entity_id: str):
    """Get a single entity with all its connections and related events."""
    conn = storage.get_conn()

    entity = conn.execute(
        "SELECT * FROM knowledge_entities WHERE id=?", (entity_id,)
    ).fetchone()
    if not entity:
        conn.close()
        raise HTTPException(status_code=404, detail="Entity not found")

    entity = dict(entity)

    # Get all relations
    relations = conn.execute(
        "SELECT * FROM knowledge_relations WHERE from_id=? OR to_id=?",
        (entity_id, entity_id),
    ).fetchall()

    # Get connected entity IDs
    connected_ids = set()
    for r in relations:
        connected_ids.add(r["from_id"])
        connected_ids.add(r["to_id"])
    connected_ids.discard(entity_id)

    # Fetch connected entities
    connections = []
    if connected_ids:
        placeholders = ",".join("?" for _ in connected_ids)
        connected_entities = conn.execute(
            f"SELECT id, name, entity_type FROM knowledge_entities WHERE id IN ({placeholders})",
            list(connected_ids),
        ).fetchall()
        entity_map = {e["id"]: dict(e) for e in connected_entities}

        for r in relations:
            r = dict(r)
            if r["from_id"] == entity_id:
                target = entity_map.get(r["to_id"], {})
                connections.append({
                    "direction": "outgoing",
                    "type": r["type"],
                    "context": r["context"],
                    "entity": target,
                })
            else:
                source = entity_map.get(r["from_id"], {})
                connections.append({
                    "direction": "incoming",
                    "type": r["type"],
                    "context": r["context"],
                    "entity": source,
                })

    conn.close()

    # Get related simulation events
    try:
        from jobpulse.event_logger import get_events_mentioning
        events = get_events_mentioning(entity["name"], limit=10)
    except Exception as e:
        logger.debug("Failed to get events mentioning entity: %s", e)
        events = []

    return {
        "entity": entity,
        "connections": connections,
        "recent_events": events,
    }


@router.get("/stats")
def get_stats():
    """Combined stats: knowledge graph + simulation events."""
    kg_stats = storage.get_stats()
    try:
        from jobpulse.event_logger import get_event_stats
        event_stats = get_event_stats()
    except Exception as e:
        logger.debug("Failed to get event stats: %s", e)
        event_stats = {"total_events": 0, "today_events": 0, "by_type": {}}

    return {**kg_stats, "simulation": event_stats}


@router.get("/search")
def search(q: str = ""):
    if len(q) < 1:
        return {"results": []}
    return {"results": storage.search_entities(q)}


@router.delete("/clear")
def clear(confirm: str = ""):
    if confirm != "yes-delete-all":
        raise HTTPException(status_code=400, detail="Pass ?confirm=yes-delete-all to confirm destructive operation")
    storage.clear_all()
    return {"status": "cleared"}


# ── Simulation Event Endpoints ──

@router.get("/simulation/events")
def get_simulation_events(date: str = None, agent: str = None, entity: str = None):
    """Get simulation events filtered by date, agent, or entity mention."""
    try:
        from jobpulse.event_logger import get_events_for_day, get_events_for_agent, get_events_mentioning
        from datetime import date as date_cls

        if entity:
            return {"events": get_events_mentioning(entity)}
        elif agent:
            return {"events": get_events_for_agent(agent)}
        elif date:
            return {"events": get_events_for_day(date)}
        else:
            return {"events": get_events_for_day(date_cls.today().isoformat())}
    except Exception as e:
        logger.error("Failed to get simulation events: %s", e)
        return {"events": [], "error": "Internal error retrieving events"}


@router.get("/simulation/timeline")
def get_timeline():
    """Day-by-day summary for the timeline bar."""
    try:
        from jobpulse.event_logger import get_timeline_summary
        return {"timeline": get_timeline_summary()}
    except Exception as e:
        logger.error("Failed to get timeline: %s", e)
        return {"timeline": [], "error": "Internal error retrieving timeline"}


# ── GraphRAG Retrieval Endpoint ──

@router.post("/retrieve")
def retrieve_knowledge(body: RetrieveQuery):
    """Smart retrieval from knowledge graph — used by agents and frontend."""
    return graphrag.retrieve(body.query, body.method)


class DeepQueryBody(BaseModel):
    query: str


@router.post("/deep-query")
def deep_query_endpoint(body: DeepQueryBody):
    """Complex query using RLM over the knowledge graph."""
    result = graphrag.deep_query(body.query)
    return {"query": body.query, "answer": result, "method": "rlm"}


# ── Process Trail Endpoints ──

@process_router.get("/runs")
def get_process_runs(agent: str = None, date: str = None, limit: int = 20):
    """List recent agent runs with summaries."""
    from jobpulse.process_logger import get_recent_runs, get_runs_for_day
    if date:
        return {"runs": get_runs_for_day(date)}
    return {"runs": get_recent_runs(agent, limit)}


@process_router.get("/trail/{run_id}")
def get_process_trail(run_id: str):
    """Get full step-by-step trail for one agent run."""
    from jobpulse.process_logger import get_trail
    steps = get_trail(run_id)
    if not steps:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "run_id": run_id,
        "agent_name": steps[0]["agent_name"],
        "task_trigger": steps[0]["task_trigger"],
        "started_at": steps[0]["created_at"],
        "total_steps": len(steps),
        "steps": steps,
    }


@process_router.get("/agents")
def get_process_agent_stats():
    """Get stats for each agent: run count, success rate, avg duration."""
    from jobpulse.process_logger import get_agent_stats
    return {"agents": get_agent_stats()}


# ── Rate Limit Monitoring Endpoints ──

rate_router = APIRouter(prefix="/api/rate-limits")


@rate_router.get("")
def get_rate_limits():
    """Get current rate limit status for all tracked APIs."""
    from shared.rate_monitor import get_current_limits
    return {"limits": get_current_limits()}


@rate_router.get("/{api_name}")
def get_rate_limit_history(api_name: str, limit: int = 50):
    """Get rate limit history for a specific API."""
    from shared.rate_monitor import get_history
    return {"api": api_name, "history": get_history(api_name, limit)}
