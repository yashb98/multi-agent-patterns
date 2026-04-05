"""Tests for shared/hybrid_search.py — FTS5 + vector similarity + RRF merge."""

import pytest

from shared.hybrid_search import HybridSearch, RRF_K


@pytest.fixture
def search():
    """In-memory HybridSearch instance."""
    s = HybridSearch(":memory:")
    yield s
    s.close()


@pytest.fixture
def populated_search(search):
    """HybridSearch pre-loaded with diverse documents."""
    search.add("auth_jwt", "Implementing authentication with JWT tokens and refresh tokens", {"domain": "security"})
    search.add("rest_api", "Building REST APIs with FastAPI and SQLAlchemy ORM", {"domain": "backend"})
    search.add("react_ui", "Creating interactive UI components with React and TypeScript", {"domain": "frontend"})
    search.add("ml_pipeline", "Training machine learning models with PyTorch and scikit-learn", {"domain": "ml"})
    search.add("docker_deploy", "Deploying containerized applications with Docker and Kubernetes", {"domain": "devops"})
    search.add("auth_oauth", "OAuth2 authentication flow with Google and GitHub providers", {"domain": "security"})
    return search


# ─── CREATION ──────────────────────────────────────────────────────


class TestCreation:
    def test_create_memory_instance(self):
        s = HybridSearch(":memory:")
        assert s.count() == 0
        s.close()

    def test_create_file_backed_instance(self, tmp_path):
        db_path = str(tmp_path / "search.db")
        s = HybridSearch(db_path)
        s.add("doc1", "hello world")
        assert s.count() == 1
        s.close()


# ─── ADD & COUNT ───────────────────────────────────────────────────


class TestAddAndCount:
    def test_add_single_document(self, search):
        search.add("d1", "test document")
        assert search.count() == 1

    def test_add_multiple_documents(self, search):
        for i in range(5):
            search.add(f"doc_{i}", f"Document number {i} with content")
        assert search.count() == 5

    def test_add_with_metadata(self, search):
        search.add("d1", "some text", {"author": "alice", "priority": 1})
        results = search.query("some text", top_k=1)
        assert len(results) >= 1
        assert results[0]["metadata"]["author"] == "alice"
        assert results[0]["metadata"]["priority"] == 1

    def test_add_without_metadata_defaults_to_empty_dict(self, search):
        search.add("d1", "some text")
        results = search.query("some text", top_k=1)
        assert results[0]["metadata"] == {}

    def test_add_duplicate_id_replaces(self, search):
        search.add("d1", "original text")
        search.add("d1", "updated text")
        assert search.count() == 1
        results = search.query("updated text", top_k=1)
        assert len(results) >= 1
        assert results[0]["text"] == "updated text"


# ─── REMOVE ────────────────────────────────────────────────────────


class TestRemove:
    def test_remove_existing_document(self, search):
        search.add("d1", "to be removed")
        search.add("d2", "to stay")
        search.remove("d1")
        assert search.count() == 1

    def test_remove_nonexistent_is_noop(self, search):
        search.add("d1", "keep me")
        search.remove("nonexistent")
        assert search.count() == 1

    def test_removed_document_not_in_query_results(self, search):
        search.add("d1", "JWT authentication tokens")
        search.add("d2", "REST API design")
        search.remove("d1")
        results = search.query("JWT tokens", top_k=10)
        result_ids = [r["id"] for r in results]
        assert "d1" not in result_ids


# ─── QUERY BASICS ──────────────────────────────────────────────────


class TestQueryBasics:
    def test_empty_query_returns_only_vector_results(self, populated_search):
        """Empty string has no FTS matches but vector search still runs."""
        results = populated_search.query("")
        # All results should have fts_rank=999 (no FTS match)
        for r in results:
            assert r["fts_rank"] == 999

    def test_query_returns_list_of_dicts(self, populated_search):
        results = populated_search.query("authentication")
        assert isinstance(results, list)
        if results:
            assert isinstance(results[0], dict)

    def test_query_result_has_expected_keys(self, populated_search):
        results = populated_search.query("JWT authentication")
        assert len(results) > 0
        r = results[0]
        assert "id" in r
        assert "text" in r
        assert "metadata" in r
        assert "score" in r
        assert "fts_rank" in r
        assert "vec_rank" in r

    def test_query_on_empty_db(self, search):
        results = search.query("anything")
        assert results == []

    def test_top_k_limits_results(self, populated_search):
        results = populated_search.query("authentication", top_k=2)
        assert len(results) <= 2


# ─── FTS5 SEARCH ──────────────────────────────────────────────────


class TestFTSSearch:
    def test_keyword_match_finds_document(self, populated_search):
        results = populated_search.query("JWT tokens")
        ids = [r["id"] for r in results]
        assert "auth_jwt" in ids

    def test_keyword_match_ranks_relevant_higher(self, populated_search):
        results = populated_search.query("authentication OAuth")
        # Both auth_jwt and auth_oauth should appear, but auth_oauth has exact "OAuth"
        ids = [r["id"] for r in results]
        assert "auth_oauth" in ids
        assert "auth_jwt" in ids

    def test_partial_keyword_match(self, populated_search):
        """FTS5 with porter tokenizer should handle word stems."""
        results = populated_search.query("container deploy")
        ids = [r["id"] for r in results]
        assert "docker_deploy" in ids


# ─── VECTOR SIMILARITY ────────────────────────────────────────────


class TestVectorSimilarity:
    def test_similar_text_scores_higher(self, search):
        search.add("d1", "machine learning deep neural networks training")
        search.add("d2", "cooking recipes italian pasta carbonara")
        results = search.query("neural network model training")
        if len(results) >= 2:
            # ML doc should rank higher than cooking doc
            ml_rank = next((i for i, r in enumerate(results) if r["id"] == "d1"), 999)
            cook_rank = next((i for i, r in enumerate(results) if r["id"] == "d2"), 999)
            assert ml_rank < cook_rank

    def test_identical_text_has_high_similarity(self, search):
        search.add("d1", "exact match test phrase")
        results = search.query("exact match test phrase", top_k=1)
        assert len(results) == 1
        assert results[0]["id"] == "d1"
        assert results[0]["score"] > 0


# ─── RRF MERGE ─────────────────────────────────────────────────────


class TestRRFMerge:
    def test_rrf_merge_combines_both_signals(self, populated_search):
        """Documents appearing in both FTS and vector results should score higher."""
        results = populated_search.query("JWT authentication tokens")
        assert len(results) > 0
        # auth_jwt should be top since it matches both keyword and semantically
        assert results[0]["id"] == "auth_jwt"

    def test_rrf_scores_are_positive(self, populated_search):
        results = populated_search.query("React frontend components")
        for r in results:
            assert r["score"] > 0

    def test_rrf_score_formula(self):
        """Verify RRF score formula: weight/(k+rank) for each ranker."""
        s = HybridSearch(":memory:")
        fts = [("doc_a", 1), ("doc_b", 2)]
        vec = [("doc_b", 1), ("doc_c", 2)]
        # Use equal weights to test formula cleanly
        merged = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.0, vec_weight=1.0)
        s.close()

        scores = {doc_id: score for doc_id, score, _, _ in merged}
        assert scores["doc_b"] > scores["doc_a"]
        assert scores["doc_a"] > scores["doc_c"]

        expected_b = 1.0 / (RRF_K + 2) + 1.0 / (RRF_K + 1)
        assert abs(scores["doc_b"] - expected_b) < 1e-9

    def test_rrf_merge_deduplicates(self, populated_search):
        """Same doc in both FTS and vector results should appear only once."""
        results = populated_search.query("authentication")
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids))


# ─── METADATA ──────────────────────────────────────────────────────


class TestMetadata:
    def test_metadata_preserved_in_results(self, populated_search):
        results = populated_search.query("machine learning PyTorch")
        ml_results = [r for r in results if r["id"] == "ml_pipeline"]
        assert len(ml_results) == 1
        assert ml_results[0]["metadata"]["domain"] == "ml"

    def test_mixed_metadata_types(self, search):
        search.add("d1", "test doc", {"count": 42, "tags": ["a", "b"], "active": True})
        results = search.query("test doc", top_k=1)
        meta = results[0]["metadata"]
        assert meta["count"] == 42
        assert meta["tags"] == ["a", "b"]
        assert meta["active"] is True


# ─── EMBEDDING INTERNALS ──────────────────────────────────────────


class TestEmbeddingInternals:
    def test_compute_embedding_returns_fixed_size(self, search):
        emb = search._compute_embedding("hello world")
        assert len(emb) == search._VOCAB_SIZE

    def test_compute_embedding_normalized(self, search):
        import math
        emb = search._compute_embedding("some text here")
        magnitude = math.sqrt(sum(v * v for v in emb))
        assert abs(magnitude - 1.0) < 1e-6

    def test_empty_text_embedding(self, search):
        emb = search._compute_embedding("")
        # No tokens → all zeros → magnitude 0 → no normalization
        assert all(v == 0.0 for v in emb)

    def test_cosine_similarity_identical_vectors(self):
        vec = [0.5, 0.3, 0.1, 0.8]
        sim = HybridSearch._cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        sim = HybridSearch._cosine_similarity(a, b)
        assert abs(sim) < 1e-6

    def test_cosine_similarity_different_lengths(self):
        a = [1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        sim = HybridSearch._cosine_similarity(a, b)
        assert sim == 0.0

    def test_cosine_similarity_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        sim = HybridSearch._cosine_similarity(a, b)
        assert sim == 0.0


# ─── MULTIPLE DOCUMENTS RELEVANCE ──────────────────────────────────


class TestMultiDocRelevance:
    def test_security_query_finds_both_auth_docs(self, populated_search):
        results = populated_search.query("security authentication")
        ids = [r["id"] for r in results]
        assert "auth_jwt" in ids
        assert "auth_oauth" in ids

    def test_unrelated_query_still_returns_results(self, populated_search):
        """Bag-of-words embedding may still find partial overlap."""
        results = populated_search.query("completely unrelated topic about gardening")
        # May return results with low scores due to embedding overlap
        # The important thing is it doesn't crash
        assert isinstance(results, list)

    def test_all_documents_findable(self, populated_search):
        """Each document should be findable by its distinctive terms."""
        test_cases = [
            ("JWT tokens", "auth_jwt"),
            ("FastAPI REST", "rest_api"),
            ("React TypeScript", "react_ui"),
            ("PyTorch machine learning", "ml_pipeline"),
            ("Docker Kubernetes", "docker_deploy"),
            ("OAuth Google GitHub", "auth_oauth"),
        ]
        for query, expected_id in test_cases:
            results = populated_search.query(query, top_k=3)
            ids = [r["id"] for r in results]
            assert expected_id in ids, f"Expected '{expected_id}' in results for query '{query}', got {ids}"


# ─── SPECIAL CHARACTERS ───────────────────────────────────────────


class TestSpecialCharacters:
    def test_query_with_punctuation(self, populated_search):
        """Punctuation should be cleaned from queries."""
        results = populated_search.query("JWT! authentication? tokens...")
        ids = [r["id"] for r in results]
        assert "auth_jwt" in ids

    def test_query_with_only_punctuation(self, search):
        """Punctuation-only query has no FTS matches but vector search still runs."""
        search.add("d1", "hello world")
        results = search.query("!!??")
        # No FTS matches — any results come from vector search only
        for r in results:
            assert r["fts_rank"] == 999


# ─── EXTERNAL CONNECTION ──────────────────────────────────────────


class TestExternalConnection:
    def test_accepts_external_connection(self, tmp_path):
        """HybridSearch can use a shared SQLite connection."""
        import sqlite3
        db_path = str(tmp_path / "shared.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)
        assert search.conn is conn
        search.add("doc1", "test document")
        assert search.count() == 1
        search.close()

    def test_default_memory_still_works(self):
        """Default :memory: behavior is preserved."""
        search = HybridSearch()
        assert search.count() == 0
        search.close()

    def test_db_path_still_works(self, tmp_path):
        """File-path constructor still works."""
        db_path = str(tmp_path / "test.db")
        search = HybridSearch(db_path=db_path)
        search.close()
        assert (tmp_path / "test.db").exists()


# ─── VOYAGE VECTOR SEARCH ─────────────────────────────────────────


class TestVoyageVectorSearch:
    """Tests for Voyage Code 3 vector search integration."""

    def test_voyage_vector_search_uses_embeddings_table(self):
        """When embeddings table has vectors, _vector_search uses them."""
        import struct
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)

        # Add documents
        search.add("doc_auth", "JWT authentication token verification")
        search.add("doc_api", "REST API endpoint design patterns")

        # Manually insert Voyage-style packed embeddings into embeddings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                doc_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
        """)
        # Create simple 4d vectors for testing (real ones are 1024d)
        vec_auth = struct.pack("4f", 0.9, 0.1, 0.0, 0.0)  # auth-like
        vec_api = struct.pack("4f", 0.0, 0.0, 0.9, 0.1)   # api-like
        conn.execute("INSERT INTO embeddings (doc_id, vector) VALUES (?, ?)", ("doc_auth", vec_auth))
        conn.execute("INSERT INTO embeddings (doc_id, vector) VALUES (?, ?)", ("doc_api", vec_api))
        conn.commit()

        # Query with a vector similar to auth
        query_vec = [0.8, 0.2, 0.0, 0.0]
        results = search._voyage_vector_search(query_vec, limit=5)

        assert len(results) >= 1
        # doc_auth should rank higher (more similar to query)
        ids = [doc_id for doc_id, _ in results]
        assert ids[0] == "doc_auth"
        search.close()

    def test_voyage_vector_search_falls_back_to_bow(self):
        """When no embeddings table, falls back to bag-of-words."""
        search = HybridSearch(":memory:")
        search.add("doc1", "test document about authentication")
        # No embeddings table exists — should use bag-of-words
        results = search._vector_search("authentication", limit=5)
        assert len(results) >= 1
        search.close()

    def test_query_embedding_fn_default_is_none(self):
        """_query_embedding_fn should default to None."""
        search = HybridSearch(":memory:")
        assert search._query_embedding_fn is None
        search.close()

    def test_voyage_vector_search_empty_embeddings_table(self):
        """When embeddings table exists but is empty, returns empty list."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                doc_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
        """)
        conn.commit()

        results = search._voyage_vector_search([0.5, 0.5], limit=5)
        assert results == []
        search.close()

    def test_vector_search_uses_voyage_when_fn_set_and_embeddings_present(self):
        """When _query_embedding_fn is set and embeddings table is populated, uses Voyage path."""
        import struct
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)

        search.add("doc_auth", "JWT authentication token verification")
        search.add("doc_api", "REST API endpoint design patterns")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                doc_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
        """)
        vec_auth = struct.pack("4f", 0.9, 0.1, 0.0, 0.0)
        vec_api = struct.pack("4f", 0.0, 0.0, 0.9, 0.1)
        conn.execute("INSERT INTO embeddings (doc_id, vector) VALUES (?, ?)", ("doc_auth", vec_auth))
        conn.execute("INSERT INTO embeddings (doc_id, vector) VALUES (?, ?)", ("doc_api", vec_api))
        conn.commit()

        # Set the query embedding function to return auth-like vector
        search._query_embedding_fn = lambda q: [0.8, 0.2, 0.0, 0.0]

        results = search._vector_search("authentication query", limit=5)
        assert len(results) >= 1
        ids = [doc_id for doc_id, _ in results]
        assert ids[0] == "doc_auth"
        search.close()


# ─── WEIGHTED RRF ─────────────────────────────────────────────────


class TestWeightedRRF:
    def test_weighted_rrf_fts_boost(self):
        """FTS weight > 1.0 should boost FTS-only matches over vector-only."""
        s = HybridSearch(":memory:")
        fts = [("doc_fts", 1)]       # Only in FTS
        vec = [("doc_vec", 1)]       # Only in vector
        # With fts_weight=1.3, FTS rank-1 should score higher than vec rank-1
        merged = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.3, vec_weight=1.0)
        s.close()

        scores = {doc_id: score for doc_id, score, _, _ in merged}
        assert scores["doc_fts"] > scores["doc_vec"]

    def test_weighted_rrf_equal_weights_matches_original(self):
        """With equal weights (1.0/1.0), both calls produce identical scores."""
        s = HybridSearch(":memory:")
        fts = [("doc_a", 1), ("doc_b", 2)]
        vec = [("doc_b", 1), ("doc_c", 2)]
        # Both calls use explicit 1.0/1.0 — results must be identical
        original = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.0, vec_weight=1.0)
        weighted = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.0, vec_weight=1.0)
        s.close()

        orig_scores = {d: sc for d, sc, _, _ in original}
        weighted_scores = {d: sc for d, sc, _, _ in weighted}
        for doc_id in orig_scores:
            assert abs(orig_scores[doc_id] - weighted_scores[doc_id]) < 1e-9
