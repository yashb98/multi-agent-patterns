"""AutonomousLinker — A-MEM graph linking for the memory layer.

When a new memory is created, the linker discovers relationships to existing
memories via similarity and domain proximity, then creates Neo4j edges.

Relationship classification is rule-based (~80% of cases) with an LLM fallback
path reserved for ambiguous cases.
"""

from __future__ import annotations

from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._entries import EdgeType, MemoryEntry, MemoryTier

logger = get_logger(__name__)


def classify_relationship(
    new_tier: MemoryTier,
    existing_tier: MemoryTier,
    similarity: float,
    same_domain: bool,
    new_score: float = 0.0,
    existing_score: float = 0.0,
) -> Optional[EdgeType]:
    """Rule-based relationship classification between two memory entries.

    Returns the appropriate EdgeType, or None if similarity is too low to
    warrant any relationship.

    Rules (checked in priority order):
    1. Experience → Episodic: EXTRACTED_FROM (any similarity, any domain)
    2. Same tier + similarity > 0.85: SIMILAR_TO
    3. Procedural → Procedural + similarity > 0.80 + new scores higher: SUPERSEDES
    4. Episodic → Semantic + same domain: PRODUCED
    5. Episodic → Procedural + same domain: TAUGHT
    6. Any → Any + similarity > 0.75: RELATED_TO
    7. Otherwise: None
    """
    # Rule 1: Experience always extracts from episodic memories
    if new_tier == MemoryTier.EXPERIENCE and existing_tier == MemoryTier.EPISODIC:
        return EdgeType.EXTRACTED_FROM

    # Rule 2: Same tier, high similarity → SIMILAR_TO
    if new_tier == existing_tier and similarity > 0.85:
        return EdgeType.SIMILAR_TO

    # Rule 3: Procedure supersedes weaker procedure in same tier
    if (
        new_tier == MemoryTier.PROCEDURAL
        and existing_tier == MemoryTier.PROCEDURAL
        and similarity > 0.80
        and new_score > existing_score
    ):
        return EdgeType.SUPERSEDES

    # Rule 4: Episode produces semantic fact in same domain
    if (
        new_tier == MemoryTier.EPISODIC
        and existing_tier == MemoryTier.SEMANTIC
        and same_domain
    ):
        return EdgeType.PRODUCED

    # Rule 5: Episode teaches procedural knowledge in same domain
    if (
        new_tier == MemoryTier.EPISODIC
        and existing_tier == MemoryTier.PROCEDURAL
        and same_domain
    ):
        return EdgeType.TAUGHT

    # Rule 6: Generic high-similarity relationship across tiers
    if similarity > 0.75:
        return EdgeType.RELATED_TO

    # Rule 7: Not similar enough — no relationship
    return None


class AutonomousLinker:
    """Discovers and creates graph relationships for new memory entries.

    Coordinates Qdrant similarity search results with Neo4j edge creation.
    Handles contradiction detection with confidence decay and tombstoning.

    Parameters
    ----------
    neo4j:
        Neo4j store instance (or mock). When None, edges are computed but
        not persisted (useful for testing classification logic in isolation).
    """

    def __init__(self, neo4j=None) -> None:
        self._neo4j = neo4j

    def link_with_neighbors(
        self,
        new_entry: MemoryEntry,
        neighbors: list[tuple[MemoryEntry, float]],
    ) -> list[tuple[str, str, str, dict]]:
        """Classify relationships and create Neo4j edges for a new memory.

        Parameters
        ----------
        new_entry:
            The newly created memory entry.
        neighbors:
            List of (existing_entry, similarity_score) pairs from Qdrant search.

        Returns
        -------
        list of (source_id, target_id, edge_type_str, properties) tuples
        representing the edges that were (or would be) created.
        """
        edges: list[tuple[str, str, str, dict]] = []

        for existing, similarity in neighbors:
            edge_type = classify_relationship(
                new_tier=new_entry.tier,
                existing_tier=existing.tier,
                similarity=similarity,
                same_domain=(new_entry.domain == existing.domain),
                new_score=new_entry.score,
                existing_score=existing.score,
            )
            if edge_type is not None:
                edges.append((
                    new_entry.memory_id,
                    existing.memory_id,
                    edge_type.value,
                    {"similarity": similarity},
                ))

        if edges and self._neo4j is not None:
            self._neo4j.batch_create_edges(edges)

        logger.debug(
            "AutonomousLinker: %d neighbors → %d edges for memory %s",
            len(neighbors), len(edges), new_entry.memory_id,
        )
        return edges

    def handle_contradiction(
        self,
        new_fact: MemoryEntry,
        old_fact: MemoryEntry,
    ) -> dict:
        """Handle a conflict between two semantic facts.

        Computes strength as confidence * score. The weaker memory loses:
        its confidence drops by 0.2. If the resulting confidence falls below
        0.2 the memory is flagged for tombstoning.

        A CONTRADICTS edge is created in Neo4j regardless of which fact wins.

        Parameters
        ----------
        new_fact:
            The newer memory entry.
        old_fact:
            The existing memory entry that conflicts with new_fact.

        Returns
        -------
        dict with keys:
            loser_id (str): memory_id of the weaker fact
            new_confidence (float): post-decay confidence of the loser
            tombstone (bool): True if loser should be tombstoned
        """
        new_strength = new_fact.confidence * new_fact.score
        old_strength = old_fact.confidence * old_fact.score

        loser = old_fact if new_strength >= old_strength else new_fact

        new_confidence = max(0.0, loser.confidence - 0.2)
        should_tombstone = new_confidence < 0.2

        if self._neo4j is not None:
            self._neo4j.create_edge(
                new_fact.memory_id,
                old_fact.memory_id,
                EdgeType.CONTRADICTS.value,
                {"new_strength": new_strength, "old_strength": old_strength},
            )

        logger.debug(
            "Contradiction: new=%s (strength=%.2f) vs old=%s (strength=%.2f) → loser=%s, tombstone=%s",
            new_fact.memory_id, new_strength,
            old_fact.memory_id, old_strength,
            loser.memory_id, should_tombstone,
        )

        return {
            "loser_id": loser.memory_id,
            "new_confidence": new_confidence,
            "tombstone": should_tombstone,
        }
