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
