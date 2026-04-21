import pytest
from dataclasses import dataclass, field
from unittest.mock import MagicMock


@dataclass
class MockProceduralEntry:
    procedure_id: str = "proc_001"
    domain: str = "test_domain"
    strategy: str = "Test strategy"
    context: str = ""
    success_rate: float = 0.9
    times_used: int = 5
    avg_score_when_used: float = 8.5
    source: str = "optimization"


class MockMemoryManager:
    """Mock MemoryManager for optimization engine tests."""

    def __init__(self):
        self._stored: list[dict] = []
        self._promoted: list[str] = []
        self._demoted: list[str] = []
        self._revived: list[str] = []
        self._pinned: list[str] = []
        self._contradicted: list[tuple[str, str]] = []
        self._search_results: list[dict] = []
        self._should_fail: bool = False

    def store_memory(self, tier: str, domain: str, content: str,
                     score: float = 7.0, **kwargs):
        self._stored.append({
            "tier": tier, "domain": domain, "content": content,
            "score": score, **kwargs,
        })

    def learn_fact(self, domain: str, fact: str, run_id: str = "optimization"):
        if self._should_fail:
            raise RuntimeError("MockMemoryManager simulated failure")
        mid = f"mem_{len(self._stored)}"
        self._stored.append({"tier": "SEMANTIC", "domain": domain, "content": fact, "id": mid})
        return mid

    def learn_procedure(self, domain: str, strategy: str, context: str = "",
                        score: float = 7.0, source: str = "optimization"):
        self._stored.append({
            "tier": "PROCEDURAL", "domain": domain, "content": strategy,
            "score": score, "source": source,
        })

    def record_episode(self, topic: str, final_score: float, iterations: int,
                       pattern_used: str, agents_used: list, strengths: list,
                       weaknesses: list, output_summary: str, **kwargs):
        self._stored.append({
            "tier": "EPISODIC", "domain": kwargs.get("domain", ""),
            "content": output_summary, "score": final_score,
        })

    def promote(self, memory_id: str):
        self._promoted.append(memory_id)

    def demote(self, memory_id: str):
        self._demoted.append(memory_id)

    def revive(self, memory_id: str):
        self._revived.append(memory_id)

    def pin_memory(self, memory_id: str):
        self._pinned.append(memory_id)

    def pin(self, memory_id: str):
        self._pinned.append(memory_id)

    def contradict(self, old_id: str):
        self._contradicted.append(old_id)

    def query(self, **kwargs):
        return self._search_results

    def search_semantic(self, query: str, domain: str = "", limit: int = 5):
        return self._search_results[:limit]


class MockCognitiveEngine:
    """Mock CognitiveEngine for optimization engine tests."""

    def __init__(self):
        self.think_calls: list[dict] = []
        self.flush_called = False
        self._think_answer = "Take no action"
        self._think_score = 7.0

    async def think(self, task: str, domain: str, stakes: str = "medium",
                    scorer=None, force_level=None):
        self.think_calls.append({
            "task": task, "domain": domain, "stakes": stakes,
        })
        result = MagicMock()
        result.answer = self._think_answer
        result.score = self._think_score
        result.level = 2  # L2
        result.cost = 0.005
        return result

    async def flush(self):
        self.flush_called = True

    def flush_sync(self):
        self.flush_called = True


@pytest.fixture
def mock_memory():
    return MockMemoryManager()


@pytest.fixture
def mock_cognitive():
    return MockCognitiveEngine()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "optimization.db")


@pytest.fixture
def optimization_engine(db_path, mock_memory, mock_cognitive):
    from shared.optimization._engine import OptimizationEngine
    return OptimizationEngine(
        db_path=db_path,
        memory_manager=mock_memory,
        cognitive_engine=mock_cognitive,
    )
