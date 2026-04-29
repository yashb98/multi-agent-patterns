"""Intent classification pipeline for screening questions.

Embedding-based few-shot classifier that maps free-text questions into
~30 intent categories. Replaces brittle regex as the primary resolution path.

Usage:
    classifier = ScreeningIntentClassifier()
    intent, confidence = classifier.classify("What's your current salary?")
    # -> (ScreeningIntent.SALARY_CURRENT, 0.94)
"""

from __future__ import annotations

import json
import sqlite3
from enum import Enum
from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._embedder import MemoryEmbedder

logger = get_logger(__name__)

_DEFAULT_DB_PATH = None  # resolved lazily


def _default_db_path() -> str:
    from jobpulse.config import DATA_DIR
    return str(DATA_DIR / "screening_intent_prototypes.db")


class ScreeningIntent(str, Enum):
    """Canonical intent categories for job application screening questions."""

    WORK_AUTH_YES_NO = "work_auth_yes_no"
    WORK_AUTH_TYPE = "work_auth_type"
    VISA_STATUS = "visa_status"
    SPONSORSHIP = "sponsorship"
    SALARY_CURRENT = "salary_current"
    SALARY_EXPECTED = "salary_expected"
    NOTICE_PERIOD = "notice_period"
    START_DATE = "start_date"
    CURRENTLY_EMPLOYED = "currently_employed"
    CURRENT_JOB_TITLE = "current_job_title"
    CURRENT_EMPLOYER = "current_employer"
    REASON_LEAVING = "reason_leaving"
    LOCATION_CURRENT = "location_current"
    WILLING_RELOCATE = "willing_relocate"
    COMMUTE = "commute"
    REMOTE = "remote"
    OFFICE = "office"
    HYBRID = "hybrid"
    EXPERIENCE_YEARS = "experience_years"
    EXPERIENCE_SKILL = "experience_skill"
    EDUCATION_LEVEL = "education_level"
    DEGREE_SUBJECT = "degree_subject"
    LANGUAGE_ENGLISH = "language_english"
    LANGUAGES = "languages"
    DRIVING_LICENSE = "driving_license"
    WILLING_TRAVEL = "willing_travel"
    SECURITY_CLEARANCE = "security_clearance"
    BACKGROUND_CHECK = "background_check"
    DIVERSITY_MONITORING = "diversity_monitoring"
    CONSENT_DATA = "consent_data"
    OPEN_ENDED = "open_ended"
    UNKNOWN = "unknown"


# Seed prototypes — 3-5 example questions per intent
# These are embedded at init time and serve as the few-shot anchors.
_SEED_PROTOTYPES: dict[ScreeningIntent, list[str]] = {
    ScreeningIntent.WORK_AUTH_YES_NO: [
        "Are you authorized to work in the UK?",
        "Do you have the right to work in this country?",
        "Are you legally allowed to work here?",
        "Do you have unrestricted right to work?",
    ],
    ScreeningIntent.WORK_AUTH_TYPE: [
        "What is your right to work type?",
        "What type of visa do you currently hold?",
        "Please select your work authorization type",
        "What is your immigration status?",
    ],
    ScreeningIntent.VISA_STATUS: [
        "What is your current visa status?",
        "What visa do you currently hold?",
        "When does your visa expire?",
    ],
    ScreeningIntent.SPONSORSHIP: [
        "Do you require visa sponsorship?",
        "Will you need sponsorship to work in the UK?",
        "Do you need a visa sponsor?",
    ],
    ScreeningIntent.SALARY_CURRENT: [
        "What is your current salary?",
        "What is your present compensation?",
        "Current base salary?",
        "What do you currently earn?",
    ],
    ScreeningIntent.SALARY_EXPECTED: [
        "What is your expected salary?",
        "What are your salary expectations?",
        "Desired compensation?",
        "What is your target salary range?",
        "Minimum salary requirement?",
    ],
    ScreeningIntent.NOTICE_PERIOD: [
        "What is your notice period?",
        "When can you start?",
        "How soon can you join?",
        "Earliest start date?",
    ],
    ScreeningIntent.START_DATE: [
        "When are you available to start?",
        "Preferred start date?",
        "When can you begin?",
    ],
    ScreeningIntent.CURRENTLY_EMPLOYED: [
        "Are you currently employed?",
        "What is your current employment status?",
        "Do you currently have a job?",
    ],
    ScreeningIntent.CURRENT_JOB_TITLE: [
        "What is your current job title?",
        "What is your present role?",
        "Current position?",
    ],
    ScreeningIntent.CURRENT_EMPLOYER: [
        "Who is your current employer?",
        "What company do you work for?",
        "Current company?",
    ],
    ScreeningIntent.REASON_LEAVING: [
        "Why are you leaving your current job?",
        "Reason for leaving?",
        "Why are you seeking a new position?",
    ],
    ScreeningIntent.LOCATION_CURRENT: [
        "What is your current location?",
        "Where are you based?",
        "Which city do you live in?",
        "Your location?",
    ],
    ScreeningIntent.WILLING_RELOCATE: [
        "Are you willing to relocate?",
        "Open to relocation?",
        "Would you move for this role?",
    ],
    ScreeningIntent.COMMUTE: [
        "Can you commute to this location?",
        "Are you within commuting distance?",
        "How far are you from the office?",
    ],
    ScreeningIntent.REMOTE: [
        "Are you comfortable working remotely?",
        "Do you want to work from home?",
        "Are you open to fully remote work?",
    ],
    ScreeningIntent.OFFICE: [
        "Are you willing to work on-site?",
        "Can you work in the office?",
        "Are you comfortable working in-person?",
    ],
    ScreeningIntent.HYBRID: [
        "Are you open to a hybrid arrangement?",
        "How many days per week in the office?",
        "Comfortable with hybrid work?",
    ],
    ScreeningIntent.EXPERIENCE_YEARS: [
        "How many years of experience do you have?",
        "Total years of experience?",
        "Years in this field?",
    ],
    ScreeningIntent.EXPERIENCE_SKILL: [
        "Do you have experience with Python?",
        "How familiar are you with SQL?",
        "Have you worked with machine learning?",
        "Proficiency in React?",
    ],
    ScreeningIntent.EDUCATION_LEVEL: [
        "What is your highest level of education?",
        "Highest qualification?",
        "What degree do you hold?",
    ],
    ScreeningIntent.DEGREE_SUBJECT: [
        "What was your degree subject?",
        "Field of study?",
        "What did you major in?",
    ],
    ScreeningIntent.LANGUAGE_ENGLISH: [
        "What is your English proficiency?",
        "Are you fluent in English?",
        "English language level?",
    ],
    ScreeningIntent.LANGUAGES: [
        "What languages do you speak?",
        "Language skills?",
        "Other languages?",
    ],
    ScreeningIntent.DRIVING_LICENSE: [
        "Do you have a driving license?",
        "Valid driver's licence?",
        "Do you drive?",
    ],
    ScreeningIntent.WILLING_TRAVEL: [
        "Are you willing to travel?",
        "Comfortable with business travel?",
        "Can you travel for work?",
    ],
    ScreeningIntent.SECURITY_CLEARANCE: [
        "Do you hold security clearance?",
        "What level of clearance do you have?",
        "SC clearance?",
    ],
    ScreeningIntent.BACKGROUND_CHECK: [
        "Are you willing to undergo a background check?",
        "Do you consent to a DBS check?",
        "Pre-employment screening?",
    ],
    ScreeningIntent.DIVERSITY_MONITORING: [
        "What is your gender?",
        "Ethnicity?",
        "Do you have a disability?",
        "Sexual orientation?",
        "Age group?",
    ],
    ScreeningIntent.CONSENT_DATA: [
        "Do you consent to data processing?",
        "Agree to privacy policy?",
        "Consent to GDPR?",
        "Is this information accurate?",
    ],
    ScreeningIntent.OPEN_ENDED: [
        "Why do you want this role?",
        "Tell us about yourself",
        "What excites you about this company?",
        "Describe a challenging project",
    ],
}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class ScreeningIntentClassifier:
    """Embedding-based few-shot intent classifier for screening questions."""

    def __init__(
        self,
        db_path: str | None = None,
        embedder: MemoryEmbedder | None = None,
        confidence_threshold: float = 0.80,
    ) -> None:
        self._db_path = db_path or _default_db_path()
        self._embedder = embedder
        self._threshold = confidence_threshold
        self._prototypes: dict[ScreeningIntent, list[list[float]]] = {}
        self._loaded = False

        # Lazy-load embedder
        if self._embedder is None:
            try:
                self._embedder = MemoryEmbedder()
            except Exception as exc:
                logger.warning("IntentClassifier: Embedder unavailable (%s)", exc)

        self._init_db()
        self._load_prototypes()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intent_prototypes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent TEXT NOT NULL,
                    question_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_prototypes_intent
                ON intent_prototypes(intent)
            """)

    def _load_prototypes(self) -> None:
        """Load all prototypes from DB + seed, embed them, cache vectors."""
        if self._embedder is None:
            return

        # Merge seed + DB examples per intent
        all_examples: dict[ScreeningIntent, list[str]] = {}
        for intent, seeds in _SEED_PROTOTYPES.items():
            all_examples[intent] = list(seeds)

        # Load learned examples from DB
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT intent, question_text FROM intent_prototypes"
                ).fetchall()
            for row in rows:
                intent = ScreeningIntent(row["intent"])
                if intent in all_examples:
                    all_examples[intent].append(row["question_text"])
                else:
                    all_examples[intent] = [row["question_text"]]
        except Exception as exc:
            logger.debug("Could not load intent prototypes from DB: %s", exc)

        # Embed all examples (batched for efficiency)
        self._prototypes = {}
        for intent, questions in all_examples.items():
            try:
                vectors = self._embedder.embed_batch(questions)
                self._prototypes[intent] = vectors
            except Exception as exc:
                logger.debug("Failed to embed prototypes for %s: %s", intent.value, exc)

        self._loaded = True
        total = sum(len(v) for v in self._prototypes.values())
        logger.info("IntentClassifier: loaded %d prototypes across %d intents", total, len(self._prototypes))

    def classify(self, question: str) -> tuple[ScreeningIntent, float]:
        """Classify a question into an intent. Returns (intent, confidence)."""
        if not question or not question.strip():
            return ScreeningIntent.UNKNOWN, 0.0

        if self._embedder is None or not self._loaded:
            return ScreeningIntent.UNKNOWN, 0.0

        try:
            query_vec = self._embedder.embed(question.strip())
        except Exception as exc:
            logger.debug("Intent classification embed failed: %s", exc)
            return ScreeningIntent.UNKNOWN, 0.0

        best_intent: ScreeningIntent = ScreeningIntent.UNKNOWN
        best_score = 0.0

        for intent, vectors in self._prototypes.items():
            # Max similarity against any prototype for this intent
            scores = [_cosine_similarity(query_vec, v) for v in vectors]
            max_score = max(scores) if scores else 0.0
            if max_score > best_score:
                best_score = max_score
                best_intent = intent

        if best_score >= self._threshold:
            return best_intent, best_score
        return ScreeningIntent.UNKNOWN, best_score

    def add_intent_example(self, intent: ScreeningIntent, question: str) -> None:
        """Learn a new example question for an intent. Persists to DB."""
        if not question or not question.strip():
            return

        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO intent_prototypes (intent, question_text, created_at) VALUES (?, ?, ?)",
                (intent.value, question.strip(), now),
            )

        # Update in-memory prototypes
        if self._embedder is not None:
            try:
                vec = self._embedder.embed(question.strip())
                self._prototypes.setdefault(intent, []).append(vec)
            except Exception as exc:
                logger.debug("Failed to embed new prototype: %s", exc)

        logger.info("IntentClassifier: added prototype for %s: '%s...'", intent.value, question[:50])

    def get_intent_stats(self) -> dict:
        """Return prototype counts per intent."""
        return {intent.value: len(vectors) for intent, vectors in self._prototypes.items()}


# ------------------------------------------------------------------
# Singleton factory
# ------------------------------------------------------------------

_classifier_instance: ScreeningIntentClassifier | None = None


def get_intent_classifier() -> ScreeningIntentClassifier:
    """Return shared singleton."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = ScreeningIntentClassifier()
    return _classifier_instance
