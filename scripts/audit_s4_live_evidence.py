"""S4 live evidence — option-aligner first-pass drop on EEO fields (TP-7).

Reproduces the live-observed failure pattern from
``logs/live_e2e/run_final_20260510_141251.log``::

    screening answer 'No' did not align to any option for 'Veteran Status'
    — dropping

against the exact EEO option text observed live on Anthropic Greenhouse.
Pre-S4: alignment returns the raw answer ``"No"`` (not in options) →
screening_pipeline drops, form would submit with the EEO field empty.
Post-S4: alignment routes through the new yes/no prefix tier and
returns the correct in-options string.

Run::

    PYTHONPATH=. python scripts/audit_s4_live_evidence.py
"""

from __future__ import annotations

import logging
import sys

from jobpulse.screening_option_aligner import OptionAligner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("audit.s4")


CASES = [
    {
        "label": "Veteran Status",
        "answer": "No",
        "options": [
            "I am not a protected veteran",
            "I identify as one or more of the classifications of a protected veteran",
            "I don't wish to answer",
        ],
        "expected": "I am not a protected veteran",
    },
    {
        "label": "Disability Status",
        "answer": "No",
        "options": [
            "Yes, I have a disability, or have had one in the past",
            "No, I do not have a disability and have not had one in the past",
            "I do not want to answer",
        ],
        "expected": "No, I do not have a disability and have not had one in the past",
    },
    {
        "label": "Hispanic / Latino",
        "answer": "No",
        "options": [
            "Yes, I am Hispanic or Latino",
            "No, I am not Hispanic or Latino",
            "I do not wish to answer",
        ],
        "expected": "No, I am not Hispanic or Latino",
    },
    {
        "label": "Hispanic / Latino (Yes path)",
        "answer": "Yes",
        "options": [
            "Yes, I am Hispanic or Latino",
            "No, I am not Hispanic or Latino",
            "I do not wish to answer",
        ],
        "expected": "Yes, I am Hispanic or Latino",
    },
]


def main() -> int:
    logger.info("=== S4 live evidence (EEO yes/no alignment) ===")
    aligner = OptionAligner()
    failures = []
    for case in CASES:
        result = aligner.align_answer(
            case["answer"], case["options"], field_type="select",
        )
        in_options = result in case["options"]
        ok = result == case["expected"]
        flag = "OK" if ok else "FAIL"
        logger.info(
            "  [%s] %s: answer=%r → %r (in_options=%s expected=%r)",
            flag, case["label"], case["answer"],
            result, in_options, case["expected"],
        )
        if not ok:
            failures.append(case["label"])

    if failures:
        logger.error("FAIL: %d / %d cases mis-aligned: %s",
                     len(failures), len(CASES), failures)
        return 1

    logger.info("=== S4 PASS — %d / %d cases aligned correctly ===",
                len(CASES), len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
