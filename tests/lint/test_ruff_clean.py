"""Run `ruff check` on `jobpulse/` and `shared/` and assert clean.

The selected rules in `ruff.toml` are intentionally narrow — every
enabled rule had zero hits at the time of `pipeline-bugs-S2`. This test
makes ruff a CI tripwire so a single new violation fails the suite,
keeping pace with the pattern guards under `tests/lint/`.

Skipped if ruff isn't installed, so contributors without the tool can
still run the rest of the suite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_ruff_check_clean() -> None:
    if shutil.which("ruff") is None:
        pytest.skip("ruff not installed in this environment")

    root = _root()
    proc = subprocess.run(
        ["ruff", "check", "jobpulse/", "shared/"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "\nruff check failed. New violations under the rules selected in "
        "ruff.toml:\n\n"
        f"{proc.stdout}\n"
        f"{proc.stderr}\n"
        "Either fix the violation, or — if the rule was added in error and "
        "the cleanup is out of scope — open a follow-up issue and remove "
        "the rule from ruff.toml's `select` list. Do NOT add per-line "
        "`# noqa` ignores without justification."
    )
