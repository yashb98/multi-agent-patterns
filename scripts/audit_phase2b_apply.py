"""Phase 2B audit invocation — call apply_job directly to exercise the
form-fill / screening wired touchpoints regardless of ATS-score routing.

Audit 2026-05-10 / Phase 2B.

Why not job-process-url? `applicator.classify_action` requires
ats_score >= 95 to call apply_job; URLs scoring 85-94 route to
queued_for_review and skip the form-fill path. For audit purposes we
want the wired touchpoints (screening_pipeline, OptionAligner,
ScreeningIntentClassifier) to fire and write rows into
data/semantic_decisions.db. Calling apply_job directly bypasses the
score gate without modifying production routing.

Usage:
    python scripts/audit_phase2b_apply.py <URL> [ats_platform]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/audit_phase2b_apply.py <URL> [ats_platform]")
        return 1

    url = sys.argv[1]
    ats_platform = sys.argv[2] if len(sys.argv) > 2 else "greenhouse"

    if os.environ.get("JOB_AUTOPILOT_AUTO_SUBMIT", "false").lower() == "true":
        print("FAIL: JOB_AUTOPILOT_AUTO_SUBMIT=true — refusing to dry-run a real submit.")
        return 1

    # Reuse a previously-generated CV if it exists; the dry-run apply only needs
    # a real PDF path to satisfy the form-fill upload step.
    company_dir = Path("data/applications")
    candidate_cvs = list(company_dir.rglob("Yash_Bishnoi_*.pdf"))
    if not candidate_cvs:
        print("FAIL: no CV PDF found under data/applications/ — run job-process-url first.")
        return 1
    cv_path = candidate_cvs[0]
    print(f"--- 1. Using CV: {cv_path}")

    print(f"--- 2. Calling apply_job(url={url[:80]}..., ats_platform={ats_platform}, dry_run=True)")
    from jobpulse.applicator import apply_job
    result = apply_job(
        url=url,
        ats_platform=ats_platform,
        cv_path=str(cv_path),
        cover_letter_path=None,
        dry_run=True,
    )

    print("--- 3. Result:")
    summary = {k: str(v)[:200] for k, v in result.items() if not k.startswith("_")}
    print(json.dumps(summary, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
