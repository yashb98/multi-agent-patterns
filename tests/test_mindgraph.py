"""Tests for knowledge extraction with mock LLM responses."""

import json
import pytest
from unittest.mock import patch, MagicMock


MOCK_EXTRACTION = {
    "entities": [
        {"name": "LangGraph", "type": "TECHNOLOGY", "description": "Graph-based agent orchestration framework"},
        {"name": "Multi-Agent Systems", "type": "CONCEPT", "description": "Multiple AI agents collaborating on tasks"},
        {"name": "Yash", "type": "PERSON", "description": "Developer building agent systems"},
    ],
    "relationships": [
        {"from": "Yash", "to": "LangGraph", "type": "USES", "context": "Building orchestration patterns"},
        {"from": "LangGraph", "to": "Multi-Agent Systems", "type": "PART_OF", "context": "Framework for multi-agent coordination"},
    ]
}


def test_chunk_text():
    from mindgraph_app.extractor import chunk_text
    # Short text: single chunk
    short = "Hello world " * 10
    assert len(chunk_text(short)) == 1

    # Long text: multiple chunks
    long_text = "word " * 5000
    chunks = chunk_text(long_text, max_tokens=3000, overlap=200)
    assert len(chunks) > 1
    # Verify overlap: end of chunk N overlaps with start of chunk N+1
    for i in range(len(chunks) - 1):
        words_end = chunks[i].split()[-50:]
        words_start = chunks[i + 1].split()[:50]
        # Some overlap should exist
        assert len(set(words_end) & set(words_start)) > 0


def test_deduplicate_entities():
    from mindgraph_app.extractor import deduplicate_entities
    entities = [
        {"name": "LangGraph", "type": "TECHNOLOGY", "description": "Short"},
        {"name": "langgraph", "type": "TECHNOLOGY", "description": "A longer description of LangGraph"},
        {"name": "Python", "type": "TECHNOLOGY", "description": "Programming language"},
    ]
    result = deduplicate_entities(entities)
    assert len(result) == 2
    # Should keep longer description
    lg = [e for e in result if e["name"].lower() == "langgraph"][0]
    assert "longer" in lg["description"]


@patch("mindgraph_app.extractor.completion")
def test_extract_from_chunk(mock_completion):
    from mindgraph_app.extractor import extract_from_chunk

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(MOCK_EXTRACTION)
    mock_completion.return_value = mock_response

    result = extract_from_chunk("Some text about LangGraph and multi-agent systems.")
    assert len(result["entities"]) == 3
    assert len(result["relationships"]) == 2
    assert result["entities"][0]["name"] == "LangGraph"


@patch("mindgraph_app.extractor.completion")
def test_extract_from_text_full_pipeline(mock_completion):
    from mindgraph_app.extractor import extract_from_text
    from mindgraph_app import storage

    # Clear any existing data
    storage.clear_all()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(MOCK_EXTRACTION)
    mock_completion.return_value = mock_response

    result = extract_from_text("Test text about LangGraph.", "test.txt")
    assert result["status"] == "ok"
    assert result["entities_extracted"] == 3
    assert result["relations_extracted"] == 2

    # Verify stored in DB
    graph = storage.get_full_graph()
    assert len(graph["nodes"]) >= 3
    assert len(graph["edges"]) >= 2

    # Verify dedup: same text again should be no-op
    result2 = extract_from_text("Test text about LangGraph.", "test.txt")
    assert result2["status"] == "already_processed"

    storage.clear_all()


def test_storage_crud():
    from mindgraph_app import storage

    storage.clear_all()

    # Create entities
    eid1 = storage.upsert_entity("Python", "TECHNOLOGY", "Programming language")
    eid2 = storage.upsert_entity("FastAPI", "TECHNOLOGY", "Web framework")
    assert eid1 != eid2

    # Increment mention
    storage.upsert_entity("Python", "TECHNOLOGY", "")
    graph = storage.get_full_graph()
    python_node = [n for n in graph["nodes"] if n["name"] == "Python"][0]
    assert python_node["mention_count"] == 2

    # Create relation
    storage.upsert_relation(eid1, eid2, "USES", "FastAPI uses Python")
    graph = storage.get_full_graph()
    assert len(graph["edges"]) == 1

    # Stats
    stats = storage.get_stats()
    assert stats["total_entities"] == 2
    assert stats["total_relations"] == 1

    # Search
    results = storage.search_entities("pyth")
    assert len(results) == 1
    assert results[0]["name"] == "Python"

    storage.clear_all()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
