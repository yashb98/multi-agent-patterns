"""Ralph Loop — Self-healing job application engine.

Try → screenshot → diagnose → fix → retry → save pattern.
Learned fixes persist to SQLite so cron runs succeed on first try.
"""

from jobpulse.ralph_loop.pattern_store import (
    PatternStore,
    FixPattern,
    compute_error_signature,
)
from jobpulse.ralph_loop.loop import ralph_apply_sync, build_overrides_from_fixes

__all__ = [
    "PatternStore",
    "FixPattern",
    "compute_error_signature",
    "ralph_apply_sync",
    "build_overrides_from_fixes",
]
