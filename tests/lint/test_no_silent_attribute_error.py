"""Lint guard: forbid the silent-AttributeError shape in audited-clean modules.

The S11 B-1 incident: `MemoryManager.run_forgetting_sweep` called
`self._forgetting.sweep(...)` for two months. `sweep` was never defined.
The wrapper had `except Exception as exc: logger.warning(...); return None`
— so the AttributeError was swallowed at debug/warning level and the hourly
forgetting sweep was a silent no-op.

Pattern (`pipeline-bugs.md` Section 2 grep):

    except (AttributeError | Exception) ...:
        logger.debug(...) | logger.warning(...)
        return None | {} | []

This shape is *sometimes* legitimate (graceful degradation when an optional
service is unavailable) but it has masked real bugs whenever the wrapped
call is supposed to invoke a method that exists. The audit promoted
known-clean modules to a tripwire allowlist: any new occurrence of the
shape in those files fails the test.

Future sessions extend `CLEAN_FILES` as more modules are audited and
cleaned. Removing a file from the list requires explicit justification in
the PR.

Narrow `except (TypeError, ValueError, KeyError, ...): pass` patterns are
deliberately NOT caught — those are safe parse-guards, not bug shapes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


# Modules audited clean of the silent-attribute-error shape. Anchored on
# fix commits in `git log` — see `docs/audits/audit-{subsystem}.md`.
CLEAN_FILES: list[str] = [
    # post-S11 audit (sweep wired correctly, no silent swallow)
    "shared/memory_layer/_manager.py",
    "shared/memory_layer/_forgetting.py",
    "shared/memory_layer/_linker.py",
    # post-S6 audit (cognitive subsystem)
    "shared/cognitive/_engine.py",
    "shared/cognitive/_classifier.py",
    "shared/cognitive/_strategy.py",
    "shared/cognitive/_reflexion.py",
    "shared/cognitive/_tree_of_thought.py",
    "shared/cognitive/_budget.py",
    # post-S10 audit (optimization subsystem; _engine.py still has 1 known
    # legacy hit at line 26 — exempted until S17 cleanup)
    "shared/optimization/_aggregator.py",
    "shared/optimization/_tracker.py",
    # post-S5 audit (post-apply chain)
    "jobpulse/post_apply_hook.py",
    "jobpulse/correction_capture.py",
    "jobpulse/agent_rules.py",
    "jobpulse/trajectory_store.py",
    # post-S12 audit (ats_adapters unification)
    "jobpulse/ats_adapters/__init__.py",
    "jobpulse/ats_adapters/strategy.py",
    "jobpulse/ats_adapters/base.py",
]


# Compile once: matches the audit's grep shape with multiline support.
_PATTERN = re.compile(
    r"except\s+(?:\(?[\w\s,]*?(?:AttributeError|Exception)[\w\s,]*?\)?)"
    r"\s*(?:as\s+\w+\s*)?:\s*\n"
    r"\s*logger\.(?:debug|warning)\([^)]*\)[^\n]*\n"
    r"\s*return\s+(?:None|\{\}|\[\])",
    re.MULTILINE,
)


@pytest.mark.parametrize("rel", CLEAN_FILES)
def test_clean_file_has_no_silent_attribute_error(rel: str) -> None:
    text = (_root() / rel).read_text(encoding="utf-8")
    matches = list(_PATTERN.finditer(text))
    if not matches:
        return
    locations = [text[: m.start()].count("\n") + 1 for m in matches]
    raise AssertionError(
        f"\n{rel} contains the silent-AttributeError shape at line(s): "
        f"{locations}\n"
        f"\nShape: `except (AttributeError|Exception): logger.debug|warning; "
        f"return None|{{}}|[]`\n"
        f"\nThis pattern hid a missing-method bug for two months "
        f"(see `pipeline-bugs.md` S11 B-1: ForgettingEngine.sweep). "
        f"Either narrow the exception type to the specific class you expect "
        f"(e.g. KeyError, ValueError, json.JSONDecodeError), OR re-raise "
        f"after logging at `logger.error(...)` so the failure surfaces.\n"
    )
