import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field


@dataclass
class MockProceduralEntry:
    procedure_id: str = "proc_001"
    domain: str = "test_domain"
    strategy: str = "Test strategy"
    context: str = "When testing"
    success_rate: float = 0.9
    times_used: int = 5
    avg_score_when_used: float = 8.5
    source: str = "reflexion"
    created_at: str = "2026-04-21T10:00:00"


@dataclass
class MockEpisodicEntry:
    run_id: str = "ep_001"
    topic: str = "test_task"
    timestamp: str = "2026-04-21T10:00:00"
    final_score: float = 3.0
    iterations: int = 1
    pattern_used: str = "reflexion"
    agents_used: list = field(default_factory=lambda: ["test_agent"])
    strengths: list = field(default_factory=list)
    weaknesses: list = field(default_factory=lambda: ["Failed on edge case"])
    output_summary: str = "Bad output"
    duration_seconds: float = 1.0
    total_llm_calls: int = 1
    domain: str = "test_domain"


class MockMemoryManager:
    """Mock MemoryManager that simulates Pillar 1 memory operations."""

    def __init__(self):
        self._procedural: list[MockProceduralEntry] = []
        self._episodic: list[MockEpisodicEntry] = []
        self._semantic: list[dict] = []
        self.store_calls: list[dict] = []
        self.learn_procedure_calls: list[dict] = []
        self.learn_fact_calls: list[dict] = []

    def get_context_for_agent(self, agent_name: str, topic: str, domain: str = "") -> str:
        sections = []
        procs = [p for p in self._procedural if p.domain == domain or not domain]
        if procs:
            lines = [f"- {p.strategy} (success: {p.success_rate:.0%})" for p in procs[:3]]
            sections.append("Proven strategies:\n" + "\n".join(lines))
        return "\n\n".join(sections)

    def learn_procedure(self, domain: str, strategy: str, context: str = "",
                        score: float = 7.0, source: str = "runtime"):
        self.learn_procedure_calls.append({
            "domain": domain, "strategy": strategy, "context": context,
            "score": score, "source": source,
        })
        self._procedural.append(MockProceduralEntry(
            domain=domain, strategy=strategy, context=context,
            avg_score_when_used=score, source=source,
        ))

    def record_episode(self, topic: str, final_score: float, iterations: int,
                       pattern_used: str, agents_used: list, strengths: list,
                       weaknesses: list, output_summary: str, **kwargs):
        self._episodic.append(MockEpisodicEntry(
            topic=topic, final_score=final_score, iterations=iterations,
            pattern_used=pattern_used, agents_used=agents_used,
            strengths=strengths, weaknesses=weaknesses,
            output_summary=output_summary, domain=kwargs.get("domain", ""),
        ))

    def learn_fact(self, domain: str, fact: str, run_id: str = "manual"):
        self.learn_fact_calls.append({"domain": domain, "fact": fact})

    def get_procedural_entries(self, domain: str) -> list[MockProceduralEntry]:
        return [p for p in self._procedural if p.domain == domain]

    def get_episodic_entries(self, domain: str) -> list[MockEpisodicEntry]:
        return [p for p in self._episodic if p.domain == domain]

    def search_patterns(self, topic: str, domain: str = ""):
        return None, 0.0


@pytest.fixture
def mock_memory():
    return MockMemoryManager()


@pytest.fixture
def mock_scorer():
    """Returns a scorer function that returns configurable scores."""
    scores = []

    def scorer(output: str) -> float:
        if scores:
            return scores.pop(0)
        return 8.0

    scorer.set_scores = lambda s: scores.extend(s)
    return scorer


@pytest.fixture
def mock_llm_response():
    """Returns a mock LLM response with .content attribute."""
    def make(content: str = "mock answer", model: str = "gpt-4.1-nano"):
        resp = MagicMock()
        resp.content = content
        resp.response_metadata = {
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model_name": model,
        }
        return resp
    return make
