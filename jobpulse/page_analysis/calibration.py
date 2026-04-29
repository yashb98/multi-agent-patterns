"""Tools for calibrating the page type classifier.

Collects labeled examples and produces optimal feature weights via
coordinate-ascent grid search.
"""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR
from jobpulse.form_models import PageSnapshot, PageType
from jobpulse.page_analysis.classifier import DEFAULT_WEIGHTS, PageFeatures, PageTypeClassifier

logger = get_logger(__name__)

_DB_PATH = str(DATA_DIR / "page_classifier_examples.db")

_GRID_VALUES = (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0)


def _ensure_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                features_json TEXT NOT NULL,
                true_label TEXT NOT NULL,
                predicted_label TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_examples_label ON examples(true_label);
            CREATE INDEX IF NOT EXISTS idx_examples_timestamp ON examples(timestamp);
            PRAGMA journal_mode=WAL;
            """
        )
        conn.commit()


class ClassifierCalibration:
    """Collect labeled examples and produce calibrated weights."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DB_PATH
        _ensure_db(self.db_path)

    def record_example(
        self,
        snapshot: PageSnapshot | dict[str, Any],
        true_label: PageType,
        classifier: PageTypeClassifier | None = None,
    ) -> None:
        """Store a labeled example in SQLite."""
        if classifier is None:
            classifier = PageTypeClassifier()

        features = classifier._extract_features(snapshot)
        predicted, confidence = classifier.classify_from_features(features)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO examples
                    (url, features_json, true_label, predicted_label, confidence)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    features.url_path,
                    json.dumps(_features_to_dict(features), sort_keys=True),
                    true_label.value,
                    predicted.value,
                    confidence,
                ),
            )

    def calibrate_weights(self) -> dict[str, Any]:
        """Compute optimal feature weights from stored examples."""
        examples = self._load_examples()
        if not examples:
            logger.warning("No examples to calibrate from")
            return deepcopy(DEFAULT_WEIGHTS)

        best_weights = deepcopy(DEFAULT_WEIGHTS)
        best_accuracy = self._evaluate_weights(best_weights, examples)

        improved = True
        iterations = 0
        max_iterations = 3

        while improved and iterations < max_iterations:
            improved = False
            iterations += 1

            for page_type in PageType:
                pt_key = page_type.value
                if pt_key not in best_weights:
                    best_weights[pt_key] = {}

                current_features = list(best_weights[pt_key].keys())
                for feature in current_features:
                    current_value = best_weights[pt_key][feature]
                    for candidate in _GRID_VALUES:
                        if abs(candidate - current_value) < 0.01:
                            continue
                        test_weights = _deep_copy_weights(best_weights)
                        test_weights[pt_key][feature] = candidate
                        acc = self._evaluate_weights(test_weights, examples)
                        if acc > best_accuracy:
                            best_accuracy = acc
                            best_weights = test_weights
                            improved = True
                            logger.debug(
                                "Improved %s.%s to %.2f (acc=%.3f)",
                                pt_key,
                                feature,
                                candidate,
                                acc,
                            )

        logger.info(
            "Calibration complete after %d iteration(s). Best accuracy: %.3f",
            iterations,
            best_accuracy,
        )
        return best_weights

    def evaluate(self) -> dict[str, float]:
        """Return precision/recall/F1 per class from stored examples."""
        examples = self._load_examples()
        if not examples:
            return {}

        classifier = PageTypeClassifier()
        per_class: dict[str, dict[str, int]] = {}

        for features, true_label in examples:
            pred, _ = classifier.classify_from_features(features)
            true_key = true_label.value
            pred_key = pred.value

            if true_key not in per_class:
                per_class[true_key] = {"tp": 0, "fp": 0, "fn": 0}
            if pred_key not in per_class:
                per_class[pred_key] = {"tp": 0, "fp": 0, "fn": 0}

            if true_key == pred_key:
                per_class[true_key]["tp"] += 1
            else:
                per_class[true_key]["fn"] += 1
                per_class[pred_key]["fp"] += 1

        metrics: dict[str, float] = {}
        for label, counts in per_class.items():
            tp = counts["tp"]
            fp = counts["fp"]
            fn = counts["fn"]
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall) > 0
                else 0.0
            )
            metrics[f"{label}_precision"] = precision
            metrics[f"{label}_recall"] = recall
            metrics[f"{label}_f1"] = f1

        total_tp = sum(c["tp"] for c in per_class.values())
        total = sum(c["tp"] + c["fn"] for c in per_class.values())
        metrics["accuracy"] = total_tp / total if total > 0 else 0.0
        return metrics

    def _load_examples(self) -> list[tuple[PageFeatures, PageType]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT features_json, true_label FROM examples ORDER BY timestamp"
            ).fetchall()

        examples: list[tuple[PageFeatures, PageType]] = []
        for features_json, true_label in rows:
            features_dict = json.loads(features_json)
            features = _dict_to_features(features_dict)
            examples.append((features, PageType(true_label)))
        return examples

    def _evaluate_weights(
        self,
        weights: dict[str, dict[str, float]],
        examples: list[tuple[PageFeatures, PageType]],
    ) -> float:
        classifier = PageTypeClassifier()
        classifier.weights = weights
        correct = 0
        for features, true_label in examples:
            pred, _ = classifier.classify_from_features(features)
            if pred == true_label:
                correct += 1
        return correct / len(examples) if examples else 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _features_to_dict(features: PageFeatures) -> dict[str, Any]:
    return asdict(features)


def _dict_to_features(d: dict[str, Any]) -> PageFeatures:
    return PageFeatures(
        has_application_labels=d.get("has_application_labels", False),
        has_file_inputs=d.get("has_file_inputs", False),
        has_login_button=d.get("has_login_button", False),
        has_signup_button=d.get("has_signup_button", False),
        password_count=d.get("password_count", 0),
        confirmation_signals=d.get("confirmation_signals", []),
        email_verify_signals=d.get("email_verify_signals", []),
        session_expired_signals=d.get("session_expired_signals", []),
        consent_signals=d.get("consent_signals", []),
        dialog_present=d.get("dialog_present", False),
        field_count=d.get("field_count", 0),
        button_count=d.get("button_count", 0),
        url_path=d.get("url_path", ""),
        verification_wall_present=d.get("verification_wall_present", False),
        has_apply_button=d.get("has_apply_button", False),
        has_email_field=d.get("has_email_field", False),
        has_accept_button=d.get("has_accept_button", False),
    )


def _deep_copy_weights(
    weights: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    return {k: dict(v) for k, v in weights.items()}
