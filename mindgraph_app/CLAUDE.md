# Knowledge MindGraph

Entity/relation extraction → SQLite knowledge graph → GraphRAG retrieval → Three.js 3D visualization.

## Components
- extraction: LLM-based entity/relation extraction (14 types each)
- storage: SQLite knowledge graph (entities, relations, simulation events)
- retrieval: GraphRAG — local search, multi-hop traversal, temporal, RLM deep query
- visualization: FastAPI serving static HTML + Three.js frontend

## Rules
- Storage layer in mindgraph_app/storage.py — all DB access goes through here
- Never import from jobpulse/ or patterns/
- Tests MUST patch DB_PATH to tmp_path (see mistakes.md: 2026-03-25 production DB wipe)
