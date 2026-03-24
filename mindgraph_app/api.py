"""FastAPI routes for MindGraph."""

import httpx
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional
from mindgraph_app import storage, extractor

router = APIRouter(prefix="/api/mindgraph")


class IngestText(BaseModel):
    text: Optional[str] = None
    url: Optional[str] = None


@router.post("/ingest")
async def ingest(text: str = Form(None), url: str = Form(None), file: UploadFile = File(None)):
    """Ingest text, file, or URL content into the knowledge graph.
    Accepts both form data and JSON body."""
    content_text = None
    filename = "paste"

    if file and file.filename:
        raw = await file.read()
        content_text = raw.decode("utf-8", errors="replace")
        filename = file.filename
    elif text:
        content_text = text
    elif url:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_text = resp.text
                filename = url
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    if not content_text or len(content_text.strip()) < 20:
        raise HTTPException(status_code=400, detail="No text provided or text too short")

    text = content_text

    result = extractor.extract_from_text(text, filename)
    return result


@router.post("/ingest/json")
async def ingest_json(body: IngestText):
    """JSON body alternative for ingest."""
    if body.url:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(body.url)
                resp.raise_for_status()
                text = resp.text
                filename = body.url
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")
    elif body.text:
        text = body.text
        filename = "paste"
    else:
        raise HTTPException(status_code=400, detail="Provide text or url")

    if len(text.strip()) < 20:
        raise HTTPException(status_code=400, detail="Text too short")

    result = extractor.extract_from_text(text, filename)
    return result


@router.get("/graph")
def get_graph(filter: str = None):
    """Get full graph or filtered by entity name."""
    graph = storage.get_full_graph()
    if filter:
        # Filter nodes matching the query
        matching_ids = set()
        filtered_nodes = []
        for node in graph["nodes"]:
            if filter.lower() in node["name"].lower():
                filtered_nodes.append(node)
                matching_ids.add(node["id"])

        # Include neighbors
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


@router.get("/stats")
def get_stats():
    return storage.get_stats()


@router.get("/search")
def search(q: str = ""):
    if len(q) < 1:
        return {"results": []}
    return {"results": storage.search_entities(q)}


@router.delete("/clear")
def clear():
    storage.clear_all()
    return {"status": "cleared"}
