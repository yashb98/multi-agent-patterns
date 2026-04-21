import pytest
from shared.memory_layer._manager import MemoryManager, get_shared_memory_manager, reset_shared_memory_manager


@pytest.fixture
def manager(tmp_path):
    """Old-style MemoryManager with NO new engines."""
    return MemoryManager(storage_dir=str(tmp_path))


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_shared_memory_manager()
    yield
    reset_shared_memory_manager()


class TestBackwardsCompat:
    def test_old_record_episode_still_works(self, manager):
        manager.record_episode(
            topic="test", final_score=8.0, iterations=3,
            pattern_used="hierarchical", agents_used=["researcher"],
            strengths=["good"], weaknesses=["slow"],
            output_summary="Test output", domain="physics",
        )
        assert len(manager.episodic.episodes) == 1

    def test_old_learn_fact_still_works(self, manager):
        manager.learn_fact("physics", "Quantum advantage proven")
        assert len(manager.semantic.facts) > 0

    def test_old_learn_procedure_still_works(self, manager):
        manager.learn_procedure("physics", "Always verify quantum claims")
        assert len(manager.procedural.procedures) > 0

    def test_old_search_patterns_still_works(self, manager):
        result = manager.search_patterns("test topic", "physics")
        assert isinstance(result, tuple)

    def test_old_get_context_for_agent_same_signature(self, manager):
        result = manager.get_context_for_agent("researcher", "test", "physics")
        assert isinstance(result, str)

    def test_old_get_memory_report_works(self, manager):
        report = manager.get_memory_report()
        assert "Memory System Report" in report

    def test_singleton_factory_works(self, tmp_path):
        mm = get_shared_memory_manager(storage_dir=str(tmp_path))
        assert isinstance(mm, MemoryManager)
        mm2 = get_shared_memory_manager()
        assert mm is mm2
