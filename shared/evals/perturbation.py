"""Environment perturbation for adversarial form evaluation.

Generates variants of real page snapshots to stress-test semantic matching
and field mapping. NOT for training — eval/stress-testing only.

5 strategies:
1. reorder_fields — shuffle field order
2. rename_labels — paraphrase/synonym label text
3. add_noise_fields — inject irrelevant distractor fields
4. change_option_text — replace dropdown/radio options with synonyms
5. shuffle_options — reorder options within dropdowns/radios
"""
from __future__ import annotations

import copy
import random
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

_LABEL_SYNONYMS: dict[str, list[str]] = {
    "first name": ["given name", "forename", "your first name", "name (first)"],
    "last name": ["surname", "family name", "your last name", "name (last)"],
    "email": ["email address", "e-mail", "your email", "contact email"],
    "phone": ["phone number", "telephone", "mobile number", "contact number"],
    "resume": ["cv", "curriculum vitae", "upload resume", "attach cv"],
    "cover letter": ["covering letter", "motivation letter", "letter of application"],
    "gender": ["sex", "gender identity", "what is your gender"],
    "experience": ["years of experience", "work experience", "professional experience"],
    "salary": ["expected salary", "salary expectation", "desired compensation"],
    "location": ["city", "your location", "current city", "where are you based"],
    "notice period": ["notice", "availability", "when can you start"],
}

_OPTION_SYNONYMS: dict[str, list[str]] = {
    "male": ["man", "m", "he/him"],
    "female": ["woman", "f", "she/her"],
    "other": ["non-binary", "prefer not to say", "self-describe"],
    "yes": ["true", "i do", "affirmative", "i am"],
    "no": ["false", "i do not", "negative", "i am not"],
}

_NOISE_LABELS = [
    "Internal Reference Code", "Tracking ID", "How did you hear about us?",
    "Preferred start date", "Additional comments", "Referral source",
    "Department preference", "Shift preference", "T-shirt size",
    "Dietary requirements", "Parking permit needed?",
]

_NOISE_TYPES = ["text", "select", "radio", "checkbox"]


def reorder_fields(fields: list[dict], *, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    result = copy.deepcopy(fields)
    rng.shuffle(result)
    return result


def rename_labels(fields: list[dict], *, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    result = copy.deepcopy(fields)
    for f in result:
        label_lower = f["label"].lower().strip()
        synonyms = _LABEL_SYNONYMS.get(label_lower, [])
        if synonyms:
            f["label"] = rng.choice(synonyms)
    return result


def add_noise_fields(
    fields: list[dict], *, n_noise: int = 3, seed: int = 0,
) -> list[dict]:
    rng = random.Random(seed)
    result = copy.deepcopy(fields)
    chosen_labels = rng.sample(_NOISE_LABELS, min(n_noise, len(_NOISE_LABELS)))
    for label in chosen_labels:
        noise_type = rng.choice(_NOISE_TYPES)
        noise_field: dict[str, Any] = {
            "label": label,
            "type": noise_type,
            "options": [],
            "value": "",
        }
        if noise_type in ("select", "radio"):
            noise_field["options"] = ["Option A", "Option B", "Option C"]
        pos = rng.randint(0, len(result))
        result.insert(pos, noise_field)
    return result


def change_option_text(fields: list[dict], *, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    result = copy.deepcopy(fields)
    for f in result:
        if not f.get("options"):
            continue
        new_options = []
        for opt in f["options"]:
            synonyms = _OPTION_SYNONYMS.get(opt.lower(), [])
            if synonyms:
                new_options.append(rng.choice(synonyms))
            else:
                new_options.append(opt)
        f["options"] = new_options
    return result


def shuffle_options(fields: list[dict], *, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    result = copy.deepcopy(fields)
    for f in result:
        if f.get("options") and len(f["options"]) > 1:
            rng.shuffle(f["options"])
    return result


_STRATEGIES = [
    ("reorder_fields", reorder_fields),
    ("rename_labels", rename_labels),
    ("add_noise_fields", add_noise_fields),
    ("change_option_text", change_option_text),
    ("shuffle_options", shuffle_options),
]


class PerturbationEngine:
    def generate_variants(
        self,
        fields: list[dict],
        *,
        n_variants: int = 5,
        base_seed: int = 42,
    ) -> list[dict]:
        variants = []
        for i, (name, fn) in enumerate(_STRATEGIES[:n_variants]):
            kwargs: dict[str, Any] = {"seed": base_seed + i}
            if name == "add_noise_fields":
                kwargs["n_noise"] = 3
            perturbed = fn(fields, **kwargs)
            variants.append({
                "strategy": name,
                "fields": perturbed,
                "seed": base_seed + i,
            })
        return variants
