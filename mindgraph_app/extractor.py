"""LLM-based knowledge extraction — chunks text, extracts entities + relations."""

import json
import hashlib
from litellm import completion
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from mindgraph_app dir or parent multi_agent_patterns dir
load_dotenv(Path(__file__).parent.parent / ".env")

ENTITY_TYPES = [
    "PROJECT", "TECHNOLOGY", "CONCEPT", "DECISION", "PERSON",
    "COMPANY", "METRIC", "SKILL", "PHASE", "RESEARCH_PAPER"
]

RELATION_TYPES = [
    "USES", "CONTAINS", "DECIDED", "DEPENDS_ON", "PART_OF",
    "BUILDS", "TARGETS", "REQUIRES", "IMPROVES", "MEASURED_BY",
    "REFERENCES", "HAS_SKILL", "WORKS_ON", "ALTERNATIVE_TO"
]

EXTRACTION_PROMPT = """Extract knowledge graph entities and relationships from this text.

Entity types: {entity_types}
Relationship types: {relation_types}

Return ONLY valid JSON (no markdown fences):
{{
  "entities": [
    {{"name": "Entity Name", "type": "ENTITY_TYPE", "description": "One sentence description"}}
  ],
  "relationships": [
    {{"from": "Entity A", "to": "Entity B", "type": "RELATION_TYPE", "context": "Brief context"}}
  ]
}}

Rules:
- Entity names should be properly capitalized
- Each entity needs a type from the allowed list
- Each relationship needs from/to matching entity names exactly
- Keep descriptions concise (one sentence)
- Extract ALL meaningful entities and relationships, not just obvious ones

Text to analyze:
{text}"""


def chunk_text(text: str, max_tokens: int = 3000, overlap: int = 200) -> list[str]:
    """Split text into chunks with overlap. Approximates tokens as words * 1.3."""
    words = text.split()
    max_words = int(max_tokens / 1.3)
    overlap_words = int(overlap / 1.3)

    if len(words) <= max_words:
        return [text]

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start = end - overlap_words
        if start >= len(words) - overlap_words:
            break

    return chunks


def extract_from_chunk(text: str, model: str = "gpt-4o-mini") -> dict:
    """Extract entities and relationships from a single text chunk."""
    prompt = EXTRACTION_PROMPT.format(
        entity_types=", ".join(ENTITY_TYPES),
        relation_types=", ".join(RELATION_TYPES),
        text=text
    )

    try:
        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]

        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"[Extractor] Error: {e}")
        return {"entities": [], "relationships": []}


def deduplicate_entities(all_entities: list[dict]) -> list[dict]:
    """Merge duplicate entities (case-insensitive name match)."""
    seen = {}
    for entity in all_entities:
        key = entity["name"].lower().strip()
        if key in seen:
            # Keep entity with longer description
            existing = seen[key]
            if len(entity.get("description", "")) > len(existing.get("description", "")):
                seen[key]["description"] = entity["description"]
            seen[key]["_count"] = seen[key].get("_count", 1) + 1
        else:
            entity["_count"] = 1
            seen[key] = entity
    return list(seen.values())


def extract_from_text(text: str, filename: str = "paste", model: str = "gpt-4o-mini") -> dict:
    """Full extraction pipeline: chunk → extract → deduplicate → return."""
    from mindgraph_app import storage

    # Check if already processed
    file_hash = hashlib.sha256(text.encode()).hexdigest()
    if storage.is_file_processed(file_hash):
        return {"status": "already_processed", "file_hash": file_hash}

    chunks = chunk_text(text)
    all_entities = []
    all_relations = []

    for i, chunk in enumerate(chunks):
        print(f"[Extractor] Processing chunk {i+1}/{len(chunks)}...")
        result = extract_from_chunk(chunk, model)
        all_entities.extend(result.get("entities", []))
        all_relations.extend(result.get("relationships", []))

    # Deduplicate entities
    entities = deduplicate_entities(all_entities)

    # Validate entity types
    valid_entities = []
    for e in entities:
        if e.get("type") in ENTITY_TYPES:
            valid_entities.append(e)
        else:
            # Try to find closest match
            for et in ENTITY_TYPES:
                if et.lower() in e.get("type", "").lower():
                    e["type"] = et
                    valid_entities.append(e)
                    break

    # Store entities
    entity_id_map = {}
    for e in valid_entities:
        eid = storage.upsert_entity(e["name"], e["type"], e.get("description", ""))
        entity_id_map[e["name"].lower().strip()] = eid

    # Validate and store relations
    stored_relations = 0
    for r in all_relations:
        if r.get("type") not in RELATION_TYPES:
            continue
        from_key = r["from"].lower().strip()
        to_key = r["to"].lower().strip()
        from_id = entity_id_map.get(from_key)
        to_id = entity_id_map.get(to_key)
        if from_id and to_id and from_id != to_id:
            storage.upsert_relation(from_id, to_id, r["type"], r.get("context", ""))
            stored_relations += 1

    # Recompute importance scores
    storage.recompute_importance()

    # Mark file as processed
    storage.mark_file_processed(file_hash, filename, len(valid_entities))

    return {
        "status": "ok",
        "chunks": len(chunks),
        "entities_extracted": len(valid_entities),
        "relations_extracted": stored_relations,
        "file_hash": file_hash,
    }
