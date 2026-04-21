import pytest
from unittest.mock import MagicMock

from shared.cognitive._classifier import EscalationClassifier, STAKES_REGISTRY
from shared.cognitive._budget import ThinkLevel, CognitiveBudget, BudgetTracker
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestEscalationClassifier:

    def _make_classifier(self, memory=None, budget=None):
        mem = memory or MockMemoryManager()
        bgt = budget or BudgetTracker(CognitiveBudget())
        return EscalationClassifier(mem, bgt)

    def test_l0_when_strong_templates_exist(self, mock_memory):
        """Memory hit with high confidence → L0."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email_classification",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5, source="reflexion",
            ))
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("classify this email", "email_classification", "medium")
        assert level == ThinkLevel.L0_MEMORY

    def test_l1_when_weak_templates_exist(self, mock_memory):
        """Low-confidence templates → L1."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="email_classification", strategy="Weak strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("classify this email", "email_classification", "medium")
        assert level == ThinkLevel.L1_SINGLE

    def test_l1_when_episodic_but_no_procedural(self, mock_memory):
        """Has episodic memory but no strategy templates → L1."""
        from tests.shared.cognitive.conftest import MockEpisodicEntry
        mock_memory._episodic.append(MockEpisodicEntry(
            domain="calendar", final_score=7.0,
        ))
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("schedule meeting", "calendar", "medium")
        assert level == ThinkLevel.L1_SINGLE

    def test_l2_novel_medium_stakes(self, mock_memory):
        """No memory + medium stakes → L2."""
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("classify email", "email_classification", "medium")
        assert level == ThinkLevel.L2_REFLEXION

    def test_l3_novel_high_stakes(self, mock_memory):
        """No memory + high stakes → L3."""
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("submit application", "job_application", "high")
        assert level == ThinkLevel.L3_TREE_OF_THOUGHT

    def test_l1_novel_low_stakes(self, mock_memory):
        """No memory + low stakes → L1."""
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("summarize briefing", "briefing_synthesis", "low")
        assert level == ThinkLevel.L1_SINGLE

    def test_auto_escalation_on_low_score(self):
        """Post-execution: L0 scored poorly → should escalate."""
        classifier = self._make_classifier()
        should, next_level = classifier.should_escalate(
            current_level=ThinkLevel.L0_MEMORY, score=4.0,
            task="classify email", domain="email",
        )
        assert should is True
        assert next_level == ThinkLevel.L1_SINGLE

    def test_budget_clamps_level(self, mock_memory):
        """Budget exhausted → L3 clamped to L2."""
        budget = CognitiveBudget(max_l3_per_hour=0)
        tracker = BudgetTracker(budget)
        classifier = self._make_classifier(mock_memory, tracker)
        level = classifier.classify("submit app", "job_application", "high")
        # Classifier wants L3, but budget clamps it
        assert level <= ThinkLevel.L2_REFLEXION

    def test_self_improving_skips_check(self, mock_memory):
        """Classifier memory says domain is easy → always L0."""
        classifier = self._make_classifier(mock_memory)
        classifier._domain_stats["email_classification"] = {
            "l0_success_rate": 0.98, "sample_size": 200,
        }
        # Even without templates, classifier memory overrides
        level = classifier.classify("classify email", "email_classification", "medium")
        assert level == ThinkLevel.L0_MEMORY

    def test_self_improving_starts_higher(self, mock_memory):
        """Classifier memory says domain is hard → start at L2."""
        classifier = self._make_classifier(mock_memory)
        classifier._domain_stats["tricky_domain"] = {
            "l1_escalation_rate": 0.6, "sample_size": 15,
        }
        mock_memory._procedural.append(MockProceduralEntry(
            domain="tricky_domain", success_rate=0.5,
            times_used=2, avg_score_when_used=5.0,
        ))
        level = classifier.classify("do something", "tricky_domain", "medium")
        assert level == ThinkLevel.L2_REFLEXION
