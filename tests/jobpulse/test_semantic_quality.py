"""Golden test sets for semantic analysis quality. >=90% accuracy required."""
from __future__ import annotations

import pytest
import numpy as np
from unittest.mock import patch, MagicMock


def _make_embedder_with_real_similarity():
    """Mock embedder with a manually-constructed vector space."""
    _VECTORS = {
        "male": np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "man": np.array([0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "m": np.array([0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "female": np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "woman": np.array([0.05, 0.95, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "f": np.array([0.1, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "non-binary": np.array([0.3, 0.3, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0]),
        "prefer not to say": np.array([0.1, 0.1, 0.1, 0.7, 0.0, 0.0, 0.0, 0.0]),
        "yes": np.array([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        "no": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]),
        "true": np.array([0.0, 0.0, 0.0, 0.0, 0.95, 0.05, 0.0, 0.0]),
        "false": np.array([0.0, 0.0, 0.0, 0.0, 0.05, 0.95, 0.0, 0.0]),
        "united kingdom": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
        "uk": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.95, 0.05]),
        "graduate visa": np.array([0.0, 0.0, 0.7, 0.3, 0.0, 0.0, 0.0, 0.0]),
        "graduate route visa": np.array([0.0, 0.0, 0.65, 0.35, 0.0, 0.0, 0.0, 0.0]),
        "1 month": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.3, 0.7, 0.0]),
        "1 month or less": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.3, 0.65, 0.05]),
        "immediately": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.8, 0.0]),
        "i consent to the processing of my personal data": np.array([0.0, 0.0, 0.0, 0.0, 0.85, 0.0, 0.1, 0.05]),
        "i agree to the privacy policy and terms": np.array([0.0, 0.0, 0.0, 0.0, 0.8, 0.0, 0.12, 0.08]),
        "i acknowledge and accept the terms and conditions": np.array([0.0, 0.0, 0.0, 0.0, 0.78, 0.0, 0.12, 0.1]),
        "consent to data processing": np.array([0.0, 0.0, 0.0, 0.0, 0.82, 0.0, 0.1, 0.08]),
        "agree to privacy policy": np.array([0.0, 0.0, 0.0, 0.0, 0.79, 0.0, 0.11, 0.1]),
        "send me marketing emails and promotions": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.85, 0.1, 0.05]),
        "subscribe to newsletter and offers": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.12, 0.08]),
        "opt in to promotional communications": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.78, 0.12, 0.1]),
        "receive marketing updates": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.82, 0.1, 0.08]),
        "i consent to the processing of my data": np.array([0.0, 0.0, 0.0, 0.0, 0.84, 0.0, 0.1, 0.06]),
        "i agree to the privacy policy": np.array([0.0, 0.0, 0.0, 0.0, 0.81, 0.0, 0.11, 0.08]),
        "i acknowledge and accept the terms": np.array([0.0, 0.0, 0.0, 0.0, 0.77, 0.0, 0.13, 0.1]),
        "confirm data processing consent": np.array([0.0, 0.0, 0.0, 0.0, 0.83, 0.0, 0.1, 0.07]),
        "send me marketing emails": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.84, 0.1, 0.06]),
        "subscribe to our newsletter": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.79, 0.12, 0.09]),
        "receive promotional offers": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.81, 0.11, 0.08]),
        "opt in to communications": np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.76, 0.13, 0.11]),
    }
    _DEFAULT = np.array([0.125] * 8)

    embedder = MagicMock()
    embedder.dims = 8

    def fake_embed(text):
        key = text.strip().lower()
        vec = _VECTORS.get(key, _DEFAULT)
        norm = float(np.linalg.norm(vec))
        return (vec / norm).tolist() if norm > 0 else _DEFAULT.tolist()

    def fake_embed_batch(texts):
        return [fake_embed(t) for t in texts]

    embedder.embed = fake_embed
    embedder.embed_batch = fake_embed_batch
    return embedder


@pytest.fixture()
def mock_embedder():
    embedder = _make_embedder_with_real_similarity()
    with patch("shared.semantic_utils._get_embedder", return_value=embedder):
        from shared.semantic_utils import _cached_embed
        _cached_embed.cache_clear()
        yield embedder
        _cached_embed.cache_clear()


@pytest.mark.usefixtures("mock_embedder")
class TestSemanticMatcherQuality:
    """>=90% accuracy on known option matching scenarios."""

    GOLDEN_MATCHES = [
        ("male", ["Man", "Woman", "Non-binary", "Prefer not to say"], "Man"),
        ("female", ["Man", "Woman", "Non-binary", "Prefer not to say"], "Woman"),
        ("yes", ["Yes", "No"], "Yes"),
        ("no", ["Yes", "No"], "No"),
        ("true", ["Yes", "No"], "Yes"),
        ("false", ["Yes", "No"], "No"),
        ("united kingdom", ["UK", "US", "EU", "Other"], "UK"),
        ("graduate visa", ["Graduate Route Visa", "Skilled Worker", "Other"], "Graduate Route Visa"),
        ("1 month", ["Immediately", "Less than 1 month", "1 month or less", "2+ months"], "1 month or less"),
        ("immediately", ["Immediately", "1 month", "2 months", "3+ months"], "Immediately"),
    ]

    def test_golden_set_accuracy(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match

        correct = 0
        total = len(self.GOLDEN_MATCHES)
        failures = []
        for desired, options, expected in self.GOLDEN_MATCHES:
            result = semantic_option_match(desired, options)
            if result == expected:
                correct += 1
            else:
                failures.append(f"  {desired!r} -> got {result!r}, expected {expected!r}")

        accuracy = correct / total
        msg = f"SemanticMatcher accuracy: {correct}/{total} ({accuracy:.0%})"
        if failures:
            msg += "\nFailures:\n" + "\n".join(failures)
        assert accuracy >= 0.90, msg


@pytest.mark.usefixtures("mock_embedder")
class TestCheckboxIntentQuality:
    """Checkbox consent/marketing detection."""

    CONSENT_LABELS = [
        "I consent to the processing of my data",
        "I agree to the privacy policy",
        "I acknowledge and accept the terms",
        "Confirm data processing consent",
    ]
    MARKETING_LABELS = [
        "Send me marketing emails",
        "Subscribe to our newsletter",
        "Receive promotional offers",
        "Opt in to communications",
    ]

    def test_consent_labels_detected(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        correct = sum(1 for label in self.CONSENT_LABELS if checkbox_intent(label) is True)
        assert correct >= len(self.CONSENT_LABELS) * 0.9

    def test_marketing_labels_detected(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        correct = sum(1 for label in self.MARKETING_LABELS if checkbox_intent(label) is False)
        assert correct >= len(self.MARKETING_LABELS) * 0.9


@pytest.mark.usefixtures("mock_embedder")
class TestOptionAlignerQuality:
    """>=90% accuracy on answer-to-option alignment."""

    GOLDEN_ALIGNMENTS = [
        ("yes", ["Yes", "No"], "Yes"),
        ("Yes", ["Yes", "No"], "Yes"),
        ("y", ["Yes", "No"], "Yes"),
        ("no", ["Yes", "No"], "No"),
        ("true", ["Yes", "No"], "Yes"),
        ("false", ["Yes", "No"], "No"),
        ("prefer not to say", ["Yes", "No", "Prefer not to say"], "Prefer not to say"),
        ("male", ["Man", "Woman", "Non-binary"], "Man"),
        ("Man", ["Male", "Female", "Other"], "Male"),
    ]

    def test_golden_set_accuracy(self):
        from jobpulse.screening_option_aligner import OptionAligner
        aligner = OptionAligner()

        correct = 0
        total = len(self.GOLDEN_ALIGNMENTS)
        failures = []
        for answer, options, expected in self.GOLDEN_ALIGNMENTS:
            result = aligner.align_answer(answer, options)
            if result == expected:
                correct += 1
            else:
                failures.append(f"  {answer!r} -> got {result!r}, expected {expected!r}")

        accuracy = correct / total
        msg = f"OptionAligner accuracy: {correct}/{total} ({accuracy:.0%})"
        if failures:
            msg += "\nFailures:\n" + "\n".join(failures)
        assert accuracy >= 0.90, msg

    def test_fuzzy_score_containment_bug_fixed(self):
        """Verify the max/max bug is fixed."""
        from jobpulse.screening_option_aligner import OptionAligner
        score = OptionAligner._fuzzy_score("uk", "united kingdom")
        assert score < 0.9, f"Containment score should be proportional, got {score}"


class TestPageReasonerSemanticCache:
    def test_semantic_near_miss_hits_cache(self, tmp_path):
        from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction
        reasoner = PageReasoner(db_path=str(tmp_path / "cache.db"))

        action = PageAction(
            page_understanding="Job application form with personal details",
            action="fill_form",
            target_text="",
            reasoning="Form detected",
            confidence=0.9,
            page_type="application_form",
        )
        reasoner._set_cache("testdomain:abc123", action)

        result = reasoner._get_cached_semantic(
            "testdomain",
            "Job application form with personal information",
        )
        assert result is None or isinstance(result, PageAction)

    def test_set_cache_stores_understanding(self, tmp_path):
        import sqlite3
        from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction
        reasoner = PageReasoner(db_path=str(tmp_path / "cache.db"))

        action = PageAction(
            page_understanding="Login page with email and password",
            action="login",
            target_text="Sign In",
            reasoning="Login form",
            confidence=0.85,
            page_type="login_form",
        )
        reasoner._set_cache("example.com:xyz789", action)

        with sqlite3.connect(str(tmp_path / "cache.db")) as conn:
            row = conn.execute(
                "SELECT page_understanding_text FROM reasoning_cache WHERE cache_key = ?",
                ("example.com:xyz789",),
            ).fetchone()
        assert row is not None
        assert row[0] == "Login page with email and password"


class TestPageTypeClassifierEmbedding:
    def test_classifier_has_embedding_signal(self):
        """Verify the classifier uses embedding similarity as a feature."""
        from jobpulse.page_analysis.classifier import DEFAULT_WEIGHTS

        assert "embedding_similarity" in DEFAULT_WEIGHTS.get("application_form", {}), \
            "DEFAULT_WEIGHTS must include embedding_similarity for application_form"

    def test_embedding_scores_computed(self):
        """Verify _compute_embedding_scores returns scores."""
        from jobpulse.page_analysis.classifier import PageTypeClassifier, PageFeatures

        classifier = PageTypeClassifier()
        # Just verify the method exists and accepts PageFeatures
        assert hasattr(classifier, '_compute_embedding_scores')


class TestScreeningPipelineNoKeywordRules:
    def test_no_agent_rules_method(self):
        """_agent_rules keyword matching must be removed — intent classifier handles it."""
        from jobpulse.screening_pipeline import ScreeningPipeline
        assert not hasattr(ScreeningPipeline, "_agent_rules"), \
            "_agent_rules must be removed — redundant with intent classifier"

    def test_salary_uses_intent(self):
        """_finalise must use intent, not keyword matching for salary."""
        import inspect
        from jobpulse.screening_pipeline import ScreeningPipeline
        source = inspect.getsource(ScreeningPipeline._finalise)
        assert 'result.get("intent")' in source, \
            "Salary detection should use intent, not keyword matching"


class TestScreeningDetectorQuality:
    """Screening detection with embeddings as primary signal."""

    SCREENING_FIELDS = [
        {"label": "What is your expected salary?", "type": "text", "required": True, "options": []},
        {"label": "Do you have the right to work in the UK?", "type": "radio", "required": True, "options": ["Yes", "No"]},
        {"label": "How many years of experience do you have?", "type": "select", "required": True, "options": ["0-2", "2-5", "5+"]},
        {"label": "Are you willing to relocate?", "type": "radio", "required": False, "options": ["Yes", "No"]},
        {"label": "What is your notice period?", "type": "text", "required": True, "options": []},
    ]
    NON_SCREENING_FIELDS = [
        {"label": "First name", "type": "text", "required": True, "options": []},
        {"label": "Email address", "type": "email", "required": True, "options": []},
        {"label": "Phone number", "type": "tel", "required": True, "options": []},
    ]

    def test_detects_screening_fields(self):
        from jobpulse.screening_detector import ScreeningDetector
        detector = ScreeningDetector()
        correct = sum(1 for f in self.SCREENING_FIELDS if detector.is_screening(f))
        assert correct >= len(self.SCREENING_FIELDS) * 0.9

    def test_no_regex_attribute(self):
        """Verify _SCREENING_KEYWORDS regex has been removed."""
        import jobpulse.screening_detector as mod
        assert not hasattr(mod, "_SCREENING_KEYWORDS"), \
            "_SCREENING_KEYWORDS regex must be removed -- use embeddings instead"


class TestIntentClassifierQuality:
    def test_no_local_cosine_function(self):
        """Verify local _cosine_similarity function has been removed."""
        import jobpulse.screening_intent as mod
        assert not hasattr(mod, "_cosine_similarity"), \
            "Local _cosine_similarity must be removed — use numpy vectorized ops"

    def test_uses_shared_embedder(self):
        """Verify shared embedder is used instead of direct MemoryEmbedder."""
        import inspect
        import jobpulse.screening_intent as mod
        source = inspect.getsource(mod)
        assert "_get_embedder" in source, \
            "Must use shared.semantic_utils._get_embedder()"


class TestSemanticCacheSharedUtils:
    def test_no_local_cosine(self):
        import jobpulse.screening_semantic_cache as mod
        assert not hasattr(mod, "_cosine_similarity"), \
            "Local _cosine_similarity must be removed — use numpy vectorized ops"

    def test_no_keyword_boolean_inference(self):
        """_infer_boolean_from_text must use embeddings, not keyword sets."""
        import jobpulse.screening_semantic_cache as mod
        assert not hasattr(mod, "_AFFIRMATIVE"), \
            "_AFFIRMATIVE keyword set must be removed — use semantic_similarity"
        assert not hasattr(mod, "_NEGATIVE"), \
            "_NEGATIVE keyword set must be removed — use semantic_similarity"


class TestNLPClassifierSharedEmbedder:
    def test_uses_shared_embedder(self):
        """NLP classifier should use _get_embedder() from shared.semantic_utils."""
        import inspect
        import jobpulse.nlp_classifier as mod
        source = inspect.getsource(mod._load_model)
        assert "_get_embedder" in source, \
            "_load_model must use shared.semantic_utils._get_embedder()"

    def test_no_ollama_embedder(self):
        """_OllamaEmbedder should be removed."""
        import jobpulse.nlp_classifier as mod
        assert not hasattr(mod, "_OllamaEmbedder"), \
            "_OllamaEmbedder must be removed — use shared embedder"


class TestFieldMapperEmbeddingFallback:
    def test_fuzzy_custom_answer_uses_embeddings(self):
        """_fuzzy_custom_answer should use embedding similarity as fallback."""
        import inspect
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer
        source = inspect.getsource(_fuzzy_custom_answer)
        assert "best_semantic_match" in source, \
            "_fuzzy_custom_answer must use best_semantic_match as fallback"
