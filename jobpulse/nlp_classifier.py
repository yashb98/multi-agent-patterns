"""Semantic NLP Intent Classifier — Tier 2 in the 3-tier classification pipeline.

Uses sentence-transformers to embed user messages and compare against
pre-computed intent examples via cosine similarity. Runs locally, no API cost.

Lifecycle:
  1. On first import: loads model + embeds all examples from intent_examples.json
  2. On classify: embeds the query (5ms), finds closest intent
  3. On learn: adds new example from LLM resolution, re-embeds periodically
"""

import json
import numpy as np
from pathlib import Path
from shared.logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
EXAMPLES_FILE = DATA_DIR / "intent_examples.json"
EMBEDDINGS_CACHE = DATA_DIR / "intent_embeddings.npz"
LEARNED_FILE = DATA_DIR / "intent_learned.json"

# Confidence thresholds
HIGH_CONFIDENCE = 0.85
GOOD_CONFIDENCE = 0.72
LOW_CONFIDENCE = 0.60

# How many new examples before re-embedding
REEMBED_EVERY = 50

_model = None
_intent_names = []        # list of intent names matching embedding rows
_intent_embeddings = None  # numpy array of all example embeddings
_loaded = False
_learned_count = 0


def _load_model():
    """Load the sentence-transformer model (once, lazy)."""
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded NLP model: all-MiniLM-L6-v2")
    except Exception as e:
        logger.warning("Failed to load NLP model: %s", e)
        _model = None
    return _model


def _load_examples() -> dict:
    """Load intent examples from JSON + any learned examples."""
    examples = {}
    if EXAMPLES_FILE.exists():
        examples = json.loads(EXAMPLES_FILE.read_text())

    # Merge learned examples
    if LEARNED_FILE.exists():
        try:
            learned = json.loads(LEARNED_FILE.read_text())
            for intent, phrases in learned.items():
                if intent in examples:
                    examples[intent].extend(phrases)
                else:
                    examples[intent] = phrases
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to load learned examples: %s", e)

    return examples


def _build_embeddings():
    """Embed all examples and cache. Called on first classify or after learning."""
    global _intent_names, _intent_embeddings, _loaded

    model = _load_model()
    if model is None:
        _loaded = False
        return

    examples = _load_examples()
    if not examples:
        logger.warning("No intent examples found")
        _loaded = False
        return

    all_texts = []
    all_names = []
    for intent, phrases in examples.items():
        for phrase in phrases:
            all_texts.append(phrase)
            all_names.append(intent)

    logger.info("Embedding %d examples across %d intents...", len(all_texts), len(examples))
    embeddings = model.encode(all_texts, show_progress_bar=False, normalize_embeddings=True)

    _intent_names = all_names
    _intent_embeddings = np.array(embeddings)
    _loaded = True

    # Cache to disk
    try:
        np.savez_compressed(
            str(EMBEDDINGS_CACHE),
            embeddings=_intent_embeddings,
            names=np.array(_intent_names),
        )
        logger.info("Cached %d embeddings to disk", len(all_texts))
    except Exception as e:
        logger.debug("Failed to cache embeddings: %s", e)


def _ensure_loaded():
    """Ensure embeddings are loaded (from cache or fresh build)."""
    global _intent_names, _intent_embeddings, _loaded

    if _loaded:
        return

    # Try loading from cache first
    if EMBEDDINGS_CACHE.exists():
        try:
            model = _load_model()
            if model is not None:
                data = np.load(str(EMBEDDINGS_CACHE), allow_pickle=True)
                _intent_embeddings = data["embeddings"]
                _intent_names = list(data["names"])
                _loaded = True
                logger.info("Loaded %d cached embeddings", len(_intent_names))
                return
        except (OSError, ValueError, KeyError) as e:
            logger.debug("Embeddings cache invalid, rebuilding: %s", e)

    # Build fresh
    _build_embeddings()


def classify_semantic(text: str) -> tuple[str, float]:
    """Classify text by semantic similarity to intent examples.

    Returns (intent_name, confidence_score).
    Score range: 0.0 - 1.0. Higher = more confident.
    """
    _ensure_loaded()

    if not _loaded or _intent_embeddings is None:
        return ("unknown", 0.0)

    model = _load_model()
    if model is None:
        return ("unknown", 0.0)

    # Embed the query
    query_embedding = model.encode([text], normalize_embeddings=True)

    # Cosine similarity (embeddings are normalized, so dot product = cosine)
    similarities = np.dot(_intent_embeddings, query_embedding.T).flatten()

    # Find best match
    best_idx = np.argmax(similarities)
    best_score = float(similarities[best_idx])
    best_intent = _intent_names[best_idx]

    # Also get the top-3 for logging
    top_indices = np.argsort(similarities)[-3:][::-1]
    top_matches = [(float(similarities[i]), _intent_names[i]) for i in top_indices]

    logger.debug("Semantic: '%s' → %s (%.3f) | top3: %s",
                 text[:50], best_intent, best_score,
                 [(f"{s:.2f}", n) for s, n in top_matches])

    return (best_intent, best_score)


def add_learned_example(intent: str, text: str):
    """Add a new training example from LLM resolution. Persists to disk."""
    global _learned_count

    # Load existing learned examples
    learned = {}
    if LEARNED_FILE.exists():
        try:
            learned = json.loads(LEARNED_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # Corrupted file — start fresh

    if intent not in learned:
        learned[intent] = []

    # Don't add duplicates
    if text.lower() in [ex.lower() for ex in learned[intent]]:
        return

    learned[intent].append(text)
    _learned_count += 1

    # Save
    try:
        LEARNED_FILE.write_text(json.dumps(learned, indent=2))
    except Exception as e:
        logger.debug("Failed to save learned example: %s", e)

    logger.info("Learned: '%s' → %s (total learned: %d)", text[:50], intent, _learned_count)

    # Re-embed periodically
    if _learned_count % REEMBED_EVERY == 0:
        logger.info("Re-embedding after %d new examples", _learned_count)
        _build_embeddings()


def get_stats() -> dict:
    """Get classifier statistics."""
    _ensure_loaded()
    examples = _load_examples()
    learned = {}
    if LEARNED_FILE.exists():
        try:
            learned = json.loads(LEARNED_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # Corrupted file — start fresh

    return {
        "model": "all-MiniLM-L6-v2" if _model else "not loaded",
        "total_examples": len(_intent_names) if _loaded else 0,
        "intents": len(examples),
        "learned_examples": sum(len(v) for v in learned.values()),
        "loaded": _loaded,
    }
