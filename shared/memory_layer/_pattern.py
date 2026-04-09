"""Pattern Memory — stores and retrieves successful execution patterns.

Uses hybrid search (FTS5 + vector similarity + RRF) for pattern retrieval
alongside word-overlap scoring.

OPERATIONAL PRINCIPLE #1: Memory before action.
Before any task, call search(). If score > 0.7, reuse the pattern.

OPERATIONAL PRINCIPLE #4: Learn after success.
After any run with score >= 7.0, call store() to save the pattern.
"""

import json
import os
import hashlib
from typing import Optional
from datetime import datetime

from shared.logging_config import get_logger
from shared.memory_layer._entries import PatternEntry

logger = get_logger(__name__)


class PatternMemory:
    """
    Stores and retrieves successful execution patterns.

    Uses hybrid search (FTS5 + vector similarity + RRF) for pattern retrieval
    alongside the existing word-overlap scoring. The hybrid search catches
    both exact keyword matches AND semantic similarity.

    OPERATIONAL PRINCIPLE #1: Memory before action.
    Before any task, call search(). If score > 0.7, reuse the pattern.

    OPERATIONAL PRINCIPLE #4: Learn after success.
    After any run with score >= 7.0, call store() to save the pattern.
    """

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or "/tmp/agent_memory/patterns.json"
        self.patterns: list[PatternEntry] = []
        self._hybrid_search = None  # Lazy init
        self._load()
        self._rebuild_search_index()

    def _get_hybrid_search(self):
        """Lazy-init hybrid search index."""
        if self._hybrid_search is None:
            try:
                from shared.hybrid_search import HybridSearch
                self._hybrid_search = HybridSearch(":memory:")
            except ImportError:
                logger.debug("hybrid_search not available, using word overlap only")
        return self._hybrid_search

    def _rebuild_search_index(self):
        """Rebuild the FTS5 + vector index from current patterns."""
        hs = self._get_hybrid_search()
        if not hs:
            return
        for p in self.patterns:
            search_text = f"{p.topic} {p.domain} {' '.join(p.strengths)} {' '.join(p.agents_used)}"
            hs.add(p.pattern_id, search_text, {"topic": p.topic, "score": p.final_score})

    def search(self, topic: str, domain: str = "") -> tuple[Optional[PatternEntry], float]:
        """
        Search for a reusable pattern using hybrid search (FTS5 + vector + word overlap).

        Returns (best_pattern, score).
        If score > 0.7, the caller MUST reuse this pattern.
        If score <= 0.7, returns (None, score).
        """
        if not self.patterns:
            logger.info("No patterns stored yet — building from scratch")
            return None, 0.0

        # Primary: word-overlap scoring (existing approach)
        scored = [
            (p, p.relevance_score(topic, domain))
            for p in self.patterns
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Secondary: hybrid search boost (FTS5 + vector similarity via RRF)
        hs = self._get_hybrid_search()
        if hs and hs.count() > 0:
            query_text = f"{topic} {domain}".strip()
            hybrid_results = hs.query(query_text, top_k=5)
            hybrid_ids = {r["id"]: r["score"] for r in hybrid_results}

            # Boost word-overlap scores with hybrid search signal
            boosted = []
            for pattern, word_score in scored:
                hybrid_boost = hybrid_ids.get(pattern.pattern_id, 0.0)
                # Blend: 70% word overlap + 30% hybrid search
                combined = word_score * 0.7 + hybrid_boost * 100.0 * 0.3
                boosted.append((pattern, combined))
            boosted.sort(key=lambda x: x[1], reverse=True)
            scored = boosted

        best_pattern, best_score = scored[0]

        if best_score > 0.7:
            logger.info("REUSE pattern from '%s' (score: %.2f, original score: %s/10)",
                        best_pattern.topic, best_score, best_pattern.final_score)
            return best_pattern, best_score
        elif best_score > 0.4:
            logger.info("PARTIAL match from '%s' (score: %.2f) — use as starting point",
                        best_pattern.topic, best_score)
            return best_pattern, best_score
        else:
            logger.info("No good match (best: %.2f) — building from scratch", best_score)
            return None, best_score

    def store(self, topic: str, domain: str, agents_used: list[str],
              routing_decisions: list[str], final_score: float,
              iterations: int, strengths: list[str], output_summary: str):
        """
        Store a successful pattern. Only call when final_score >= 7.0.
        """
        if final_score < 7.0:
            logger.info("Score %s < 7.0 — not storing", final_score)
            return

        pattern = PatternEntry(
            pattern_id=hashlib.md5(
                f"{topic}{datetime.now().isoformat()}".encode()
            ).hexdigest()[:10],
            topic=topic,
            domain=domain,
            agents_used=agents_used,
            routing_decisions=routing_decisions,
            final_score=final_score,
            iterations=iterations,
            strengths=strengths,
            output_summary=output_summary[:500],
            timestamp=datetime.now().isoformat(),
        )
        self.patterns.append(pattern)
        # Keep top 50 by score
        if len(self.patterns) > 50:
            self.patterns.sort(key=lambda p: p.final_score, reverse=True)
            self.patterns = self.patterns[:50]
        self._save()

        # Index in hybrid search
        hs = self._get_hybrid_search()
        if hs:
            search_text = f"{topic} {domain} {' '.join(strengths)} {' '.join(agents_used)}"
            hs.add(pattern.pattern_id, search_text, {"topic": topic, "score": final_score})

        logger.info("Stored pattern: '%s' (score: %s/10)", topic, final_score)

    def _save(self):
        try:
            data = [
                {
                    "pattern_id": p.pattern_id, "topic": p.topic,
                    "domain": p.domain, "agents_used": p.agents_used,
                    "routing_decisions": p.routing_decisions,
                    "final_score": p.final_score, "iterations": p.iterations,
                    "strengths": p.strengths, "output_summary": p.output_summary,
                    "timestamp": p.timestamp,
                }
                for p in self.patterns
            ]
            os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("PatternMemory save failed: %s", e)

    def _load(self):
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                self.patterns = [PatternEntry(**d) for d in data]
        except Exception as e:
            logger.debug("Failed to load pattern memory: %s", e)
            self.patterns = []
