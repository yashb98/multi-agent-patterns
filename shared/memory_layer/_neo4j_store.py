"""Neo4j graph store for the memory layer.

Handles relationship traversal and signal queries used by the ForgettingEngine's
6-signal decay formula. All methods are no-ops when unavailable (graceful degradation).

Node schema (Memory label):
  memory_id, tier, domain, content_preview, score, confidence,
  decay_score, lifecycle, created_at

Edge types mirror EdgeType enum:
  SIMILAR_TO, PRODUCED, TAUGHT, EXTRACTED_FROM, CONTRADICTS,
  REINFORCES, SUPERSEDES, RELATED_TO, APPLIES_TO
"""

from __future__ import annotations

import os
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

_NEO4J_URI_ENV = "NEO4J_URI"
_NEO4J_USER_ENV = "NEO4J_USER"
_NEO4J_PASSWORD_ENV = "NEO4J_PASSWORD"

_DEFAULT_URI = "bolt://localhost:7687"
_DEFAULT_USER = "neo4j"


class Neo4jStore:
    """Graph store backed by Neo4j.

    Parameters
    ----------
    uri:
        Bolt URI (e.g. "bolt://localhost:7687"). Defaults to NEO4J_URI env var
        or "bolt://localhost:7687".
    user:
        Neo4j username. Defaults to NEO4J_USER env var or "neo4j".
    password:
        Neo4j password. Defaults to NEO4J_PASSWORD env var.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._uri = uri or os.environ.get(_NEO4J_URI_ENV, _DEFAULT_URI)
        self._user = user or os.environ.get(_NEO4J_USER_ENV, _DEFAULT_USER)
        self._password = password or os.environ.get(_NEO4J_PASSWORD_ENV, "")
        self._driver = None
        self._available: bool = False

    # ------------------------------------------------------------------
    # Driver lifecycle
    # ------------------------------------------------------------------

    def _get_driver(self):
        """Lazy driver initialization. Returns None if neo4j package is absent."""
        if self._driver is not None:
            return self._driver
        try:
            from neo4j import GraphDatabase  # type: ignore
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            return self._driver
        except Exception as exc:
            logger.warning("Neo4j driver init failed: %s", exc)
            return None

    def verify(self) -> bool:
        """Check connectivity, create uniqueness constraint, set _available flag.

        Returns True if Neo4j is reachable and ready.
        """
        driver = self._get_driver()
        if driver is None:
            self._available = False
            return False
        try:
            with driver.session() as session:
                session.run(
                    "CREATE CONSTRAINT IF NOT EXISTS FOR (m:Memory) "
                    "REQUIRE m.memory_id IS UNIQUE"
                )
            self._available = True
            logger.info("Neo4j connected and constraint verified at %s", self._uri)
            return True
        except Exception as exc:
            logger.warning("Neo4j verify failed: %s", exc)
            self._available = False
            return False

    def close(self) -> None:
        """Close the driver connection."""
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception as exc:
                logger.debug("Neo4j driver close error: %s", exc)
            finally:
                self._driver = None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_node(
        self,
        memory_id: str,
        tier: str,
        domain: str,
        content_preview: str,
        score: float,
        confidence: float,
        decay_score: float,
        lifecycle: str,
        created_at: str,
    ) -> None:
        """MERGE a Memory node (idempotent)."""
        if not self._available:
            return
        driver = self._get_driver()
        if driver is None:
            return
        try:
            with driver.session() as session:
                session.run(
                    """
                    MERGE (m:Memory {memory_id: $memory_id})
                    SET m.tier = $tier,
                        m.domain = $domain,
                        m.content_preview = $content_preview,
                        m.score = $score,
                        m.confidence = $confidence,
                        m.decay_score = $decay_score,
                        m.lifecycle = $lifecycle,
                        m.created_at = $created_at
                    """,
                    memory_id=memory_id,
                    tier=tier,
                    domain=domain,
                    content_preview=content_preview,
                    score=score,
                    confidence=confidence,
                    decay_score=decay_score,
                    lifecycle=lifecycle,
                    created_at=created_at,
                )
        except Exception as exc:
            logger.warning("Neo4j create_node failed for %s: %s", memory_id, exc)

    def get_node(self, memory_id: str) -> Optional[dict]:
        """Retrieve a Memory node by ID, or None if not found."""
        if not self._available:
            return None
        driver = self._get_driver()
        if driver is None:
            return None
        try:
            with driver.session() as session:
                result = session.run(
                    "MATCH (m:Memory {memory_id: $memory_id}) RETURN m",
                    memory_id=memory_id,
                )
                record = result.single()
                if record is None:
                    return None
                return dict(record["m"])
        except Exception as exc:
            logger.warning("Neo4j get_node failed for %s: %s", memory_id, exc)
            return None

    def create_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: Optional[dict] = None,
    ) -> None:
        """MERGE an edge between two Memory nodes (idempotent).

        Uses dynamic relationship type via APOC or string interpolation in a
        controlled context — edge_type is validated against the EdgeType enum
        values to prevent injection.
        """
        if not self._available:
            return
        driver = self._get_driver()
        if driver is None:
            return
        # Allowlist edge types to prevent Cypher injection
        _ALLOWED_EDGE_TYPES = {
            "SIMILAR_TO", "PRODUCED", "TAUGHT", "EXTRACTED_FROM",
            "CONTRADICTS", "REINFORCES", "SUPERSEDES", "RELATED_TO", "APPLIES_TO",
        }
        if edge_type not in _ALLOWED_EDGE_TYPES:
            logger.warning("Neo4j create_edge: rejected unknown edge type '%s'", edge_type)
            return
        props = properties or {}
        try:
            with driver.session() as session:
                # Dynamic relationship type requires string formatting here;
                # edge_type is allowlisted above so injection is not possible.
                cypher = (
                    f"MATCH (a:Memory {{memory_id: $source_id}}), "
                    f"(b:Memory {{memory_id: $target_id}}) "
                    f"MERGE (a)-[r:{edge_type}]->(b) "
                    f"SET r += $props"
                )
                session.run(cypher, source_id=source_id, target_id=target_id, props=props)
        except Exception as exc:
            logger.warning(
                "Neo4j create_edge failed %s->%s (%s): %s",
                source_id, target_id, edge_type, exc,
            )

    def mark_label(self, memory_id: str, label: str) -> None:
        """Update the lifecycle property on a Memory node."""
        if not self._available:
            return
        driver = self._get_driver()
        if driver is None:
            return
        try:
            with driver.session() as session:
                session.run(
                    "MATCH (m:Memory {memory_id: $memory_id}) SET m.lifecycle = $label",
                    memory_id=memory_id,
                    label=label.lower(),
                )
        except Exception as exc:
            logger.warning("Neo4j mark_label failed for %s: %s", memory_id, exc)

    def batch_create_edges(
        self, edges: list[tuple[str, str, str, dict]]
    ) -> int:
        """Create multiple edges in one session. Returns count of edges created.

        Each item in `edges` is (source_id, target_id, edge_type, properties).
        Skips unknown edge types. Not truly atomic — individual MERGEs run
        sequentially in one session.
        """
        if not self._available:
            return 0
        driver = self._get_driver()
        if driver is None:
            return 0
        count = 0
        _ALLOWED_EDGE_TYPES = {
            "SIMILAR_TO", "PRODUCED", "TAUGHT", "EXTRACTED_FROM",
            "CONTRADICTS", "REINFORCES", "SUPERSEDES", "RELATED_TO", "APPLIES_TO",
        }
        try:
            with driver.session() as session:
                for source_id, target_id, edge_type, props in edges:
                    if edge_type not in _ALLOWED_EDGE_TYPES:
                        logger.warning(
                            "Neo4j batch_create_edges: skipping unknown edge type '%s'",
                            edge_type,
                        )
                        continue
                    cypher = (
                        f"MATCH (a:Memory {{memory_id: $source_id}}), "
                        f"(b:Memory {{memory_id: $target_id}}) "
                        f"MERGE (a)-[r:{edge_type}]->(b) "
                        f"SET r += $props"
                    )
                    session.run(cypher, source_id=source_id, target_id=target_id, props=props or {})
                    count += 1
        except Exception as exc:
            logger.warning("Neo4j batch_create_edges failed: %s", exc)
        return count

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def expand(
        self,
        memory_ids: list[str],
        depth: int = 1,
        exclude_labels: Optional[list[str]] = None,
    ) -> list[str]:
        """BFS expansion from seed nodes up to `depth` hops.

        Uses APOC subgraphNodes when available; falls back to a Cypher
        variable-length path query. Nodes whose lifecycle is in
        `exclude_labels` are pruned along with their subtrees.

        Returns all reachable memory_ids (including seeds).
        """
        if not self._available:
            return list(memory_ids)
        driver = self._get_driver()
        if driver is None:
            return list(memory_ids)
        exclude = set(lbl.lower() for lbl in (exclude_labels or []))
        try:
            with driver.session() as session:
                # Variable-length path query — works without APOC plugin
                result = session.run(
                    """
                    MATCH (seed:Memory)
                    WHERE seed.memory_id IN $seeds
                    MATCH path = (seed)-[*0..$depth]-(neighbor:Memory)
                    WHERE NOT neighbor.lifecycle IN $exclude
                    RETURN DISTINCT neighbor.memory_id AS mid
                    """,
                    seeds=list(memory_ids),
                    depth=depth,
                    exclude=list(exclude),
                )
                return [record["mid"] for record in result]
        except Exception as exc:
            logger.warning("Neo4j expand failed: %s", exc)
            return list(memory_ids)

    def domain_neighbors(self, domain: str, limit: int = 20) -> list[str]:
        """Return memory IDs in the same domain, excluding archived nodes."""
        if not self._available:
            return []
        driver = self._get_driver()
        if driver is None:
            return []
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Memory {domain: $domain})
                    WHERE m.lifecycle <> 'archived'
                    RETURN m.memory_id AS mid
                    LIMIT $limit
                    """,
                    domain=domain,
                    limit=limit,
                )
                return [record["mid"] for record in result]
        except Exception as exc:
            logger.warning("Neo4j domain_neighbors failed for domain '%s': %s", domain, exc)
            return []

    # ------------------------------------------------------------------
    # Signal queries (ForgettingEngine inputs)
    # ------------------------------------------------------------------

    def degree(self, memory_id: str) -> int:
        """Return the total number of relationships (in + out) for a node."""
        if not self._available:
            return 0
        driver = self._get_driver()
        if driver is None:
            return 0
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Memory {memory_id: $memory_id})-[r]-()
                    RETURN count(r) AS deg
                    """,
                    memory_id=memory_id,
                )
                record = result.single()
                return record["deg"] if record else 0
        except Exception as exc:
            logger.warning("Neo4j degree failed for %s: %s", memory_id, exc)
            return 0

    def avg_downstream_score(self, memory_id: str) -> float:
        """Average score of all directly downstream (outgoing edge) nodes."""
        if not self._available:
            return 0.0
        driver = self._get_driver()
        if driver is None:
            return 0.0
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Memory {memory_id: $memory_id})-[]->(n:Memory)
                    RETURN avg(n.score) AS avg_score
                    """,
                    memory_id=memory_id,
                )
                record = result.single()
                if record is None or record["avg_score"] is None:
                    return 0.0
                return float(record["avg_score"])
        except Exception as exc:
            logger.warning("Neo4j avg_downstream_score failed for %s: %s", memory_id, exc)
            return 0.0

    def count_similar(self, memory_id: str) -> int:
        """Count SIMILAR_TO edges (in or out) for a node."""
        if not self._available:
            return 0
        driver = self._get_driver()
        if driver is None:
            return 0
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Memory {memory_id: $memory_id})-[r:SIMILAR_TO]-()
                    RETURN count(r) AS cnt
                    """,
                    memory_id=memory_id,
                )
                record = result.single()
                return record["cnt"] if record else 0
        except Exception as exc:
            logger.warning("Neo4j count_similar failed for %s: %s", memory_id, exc)
            return 0
