"""Tests for NLP semantic intent classifier."""

import pytest
import json
from pathlib import Path


class TestClassifySemantic:
    """Test that the semantic classifier returns correct intents for natural language."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from jobpulse.nlp_classifier import _ensure_loaded
        _ensure_loaded()

    def test_natural_hours_logging(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("I put in 6 hours at work today")
        assert intent == "log_hours"
        assert score >= 0.70

    def test_natural_budget_query(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("how much have I spent this week")
        assert intent == "show_budget"
        assert score >= 0.70

    def test_natural_email_check(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("did any recruiter reply to me")
        assert intent == "gmail"
        assert score >= 0.70

    def test_natural_savings(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("chuck 50 quid into savings")
        assert intent == "log_savings"
        assert score >= 0.70

    def test_natural_complete_task(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("knock off the dentist task")
        assert intent == "complete_task"
        assert score >= 0.70

    def test_natural_calendar(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("any meetings tomorrow")
        assert intent == "calendar"
        assert score >= 0.70

    def test_natural_conversation(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("hey how are you doing")
        assert intent == "conversation"
        assert score >= 0.70

    def test_natural_github(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("what code did I write yesterday")
        assert intent == "github"
        assert score >= 0.70

    def test_natural_briefing(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("give me the morning report")
        assert intent == "briefing"
        assert score >= 0.70

    def test_natural_spending(self):
        from jobpulse.nlp_classifier import classify_semantic
        intent, score = classify_semantic("I spent 45 at the supermarket")
        assert intent == "log_spend"
        assert score >= 0.70

    def test_unknown_returns_low_confidence(self):
        from jobpulse.nlp_classifier import classify_semantic
        _, score = classify_semantic("quantum entanglement in photosynthesis")
        # Should have low confidence for gibberish
        assert score < 0.85


class TestContinuousLearning:
    """Test that learned examples persist and integrate."""

    def test_add_learned_example(self, tmp_path):
        from jobpulse.nlp_classifier import add_learned_example, LEARNED_FILE
        import jobpulse.nlp_classifier as nlp

        # Point to temp file
        original = nlp.LEARNED_FILE
        nlp.LEARNED_FILE = tmp_path / "test_learned.json"

        add_learned_example("log_hours", "I clocked in for five hours")
        add_learned_example("show_budget", "whats left on my grocery budget")

        data = json.loads(nlp.LEARNED_FILE.read_text())
        assert "log_hours" in data
        assert "I clocked in for five hours" in data["log_hours"]
        assert len(data["show_budget"]) == 1

        # Restore
        nlp.LEARNED_FILE = original

    def test_no_duplicates(self, tmp_path):
        from jobpulse.nlp_classifier import add_learned_example
        import jobpulse.nlp_classifier as nlp

        original = nlp.LEARNED_FILE
        nlp.LEARNED_FILE = tmp_path / "test_learned2.json"

        add_learned_example("gmail", "check my inbox")
        add_learned_example("gmail", "check my inbox")  # duplicate

        data = json.loads(nlp.LEARNED_FILE.read_text())
        assert len(data["gmail"]) == 1  # not 2

        nlp.LEARNED_FILE = original


class TestGetStats:
    """Test classifier stats."""

    def test_stats_loaded(self):
        from jobpulse.nlp_classifier import get_stats
        stats = get_stats()
        assert stats["loaded"] is True
        assert stats["total_examples"] > 200
        assert stats["intents"] >= 30
        assert "MiniLM" in stats["model"]


class TestThreeTierClassify:
    """Test the full 3-tier pipeline via command_router.classify()."""

    def test_regex_tier_catches_exact(self):
        from jobpulse.command_router import classify, Intent
        cmd = classify("budget")
        assert cmd.intent == Intent.SHOW_BUDGET

    def test_regex_tier_catches_undo(self):
        from jobpulse.command_router import classify, Intent
        cmd = classify("undo")
        assert cmd.intent == Intent.UNDO_BUDGET

    def test_nlp_tier_catches_natural(self):
        from jobpulse.command_router import classify, Intent
        cmd = classify("I put in 6 hours at work today")
        assert cmd.intent == Intent.LOG_HOURS

    def test_nlp_tier_catches_slang(self):
        from jobpulse.command_router import classify, Intent
        cmd = classify("chuck 50 quid into savings")
        assert cmd.intent == Intent.LOG_SAVINGS

    def test_unknown_falls_to_conversation(self):
        from jobpulse.command_router import classify, Intent
        cmd = classify("asdfjkl random gibberish xyz")
        assert cmd.intent == Intent.CONVERSATION


class TestIntentExamplesFile:
    """Test that the examples file is valid and complete."""

    def test_file_exists(self):
        assert Path("data/intent_examples.json").exists()

    def test_valid_json(self):
        data = json.loads(Path("data/intent_examples.json").read_text())
        assert isinstance(data, dict)

    def test_all_intents_have_examples(self):
        from jobpulse.command_router import Intent
        data = json.loads(Path("data/intent_examples.json").read_text())

        skip = {"unknown", "create_event"}  # create_event is a stub
        for intent in Intent:
            if intent.value in skip:
                continue
            assert intent.value in data, f"Missing examples for {intent.value}"
            assert len(data[intent.value]) >= 4, f"Too few examples for {intent.value}: {len(data[intent.value])}"

    def test_each_example_is_nonempty_string(self):
        data = json.loads(Path("data/intent_examples.json").read_text())
        for intent, examples in data.items():
            for ex in examples:
                assert isinstance(ex, str) and len(ex) > 3, f"Bad example in {intent}: {ex!r}"
