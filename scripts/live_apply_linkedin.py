#!/usr/bin/env python3
"""Live apply test harness — Gousto on LinkedIn, dry-run mode.

Bypasses rate limiter. AUTO_SUBMIT hardcoded to false.
Generates a Gousto-tailored CV, runs the full adapter, streams verbose logs,
saves screenshots to data/applications/gousto_test/, pauses on failure.

Usage:
    python scripts/live_apply_linkedin.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Project root on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Safety: force dry-run no matter what .env says
os.environ["JOB_AUTOPILOT_AUTO_SUBMIT"] = "false"

# Verbose logging to stdout
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("live_apply")

from jobpulse.applicator import PROFILE
from jobpulse.ats_adapters.linkedin import LinkedInAdapter
from jobpulse.config import DATA_DIR
from jobpulse.cv_templates.generate_cv import generate_cv_pdf

GOUSTO_URL = "https://www.linkedin.com/jobs/view/4395143521/"
OUTPUT_DIR = DATA_DIR / "applications" / "gousto_test"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DIVIDER = "=" * 62


def _banner(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def _pause_on_failure(result: dict) -> bool:
    """Print failure details and pause for human review.

    Returns True to continue (user pressed Enter), False to abort (Ctrl+C).
    """
    print("\n[ERROR] Application step failed.")
    print(f"  error:      {result.get('error')}")
    print(f"  screenshot: {result.get('screenshot')}")
    print("\nReview the screenshot and tell Claude what you see.")
    print("Claude will diagnose + fix the issue, then re-run.\n")
    try:
        input("Press Enter when ready to continue, or Ctrl+C to abort... ")
        return True
    except KeyboardInterrupt:
        print("\nAborted by user.")
        return False


def main() -> None:
    _banner("LinkedIn Live Apply — Gousto Dry Run")
    print(f"  URL:         {GOUSTO_URL}")
    print(f"  Output:      {OUTPUT_DIR}")
    print(f"  AUTO_SUBMIT: false (hardcoded)")

    # ---- Step 1: Generate Gousto CV ----
    _banner("Step 1: Generating Gousto CV")
    cv_path = generate_cv_pdf(
        company="Gousto",
        location="London, UK",
        output_dir=str(OUTPUT_DIR),
    )
    print(f"  ✅ CV: {cv_path.name}")

    # ---- Step 2: Run adapter ----
    _banner("Step 2: Running LinkedIn adapter")
    print("  Browser will open. Watch it fill the form.\n")

    adapter = LinkedInAdapter()
    result = adapter.fill_and_submit(
        url=GOUSTO_URL,
        cv_path=cv_path,
        cover_letter_path=None,
        profile=PROFILE,
        custom_answers={},
        overrides=None,
    )

    # ---- Step 3: Report ----
    _banner("Result")
    print(f"  success:          {result.get('success')}")
    print(f"  error:            {result.get('error') or 'None'}")
    print(f"  screenshot:       {result.get('screenshot') or 'None'}")
    print(f"  needs_manual:     {result.get('needs_manual_submit', False)}")

    if result.get("needs_manual_submit"):
        print("\n  ✅ Reached Review page — all pages filled!")
        print("  Dry-run complete. When ready to submit for real,")
        print("  set JOB_AUTOPILOT_AUTO_SUBMIT=true and re-run.\n")
    elif not result.get("success"):
        _pause_on_failure(result)
    else:
        print("\n  ✅ Done.\n")

    # Print all screenshots generated
    screenshots = sorted(OUTPUT_DIR.glob("linkedin_*.png"))
    if screenshots:
        print(f"\n  Screenshots saved ({len(screenshots)} total):")
        for s in screenshots:
            print(f"    {s.name}")


if __name__ == "__main__":
    main()
