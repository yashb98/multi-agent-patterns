"""Ralph Loop — Self-healing job application engine.

Try → screenshot → diagnose → fix → retry → save pattern.
Learned fixes persist to SQLite so cron runs succeed on first try.
"""

from jobpulse.ralph_loop.pattern_store import (
    PatternStore,
    FixPattern,
    compute_error_signature,
)

__all__ = [
    "PatternStore",
    "FixPattern",
    "compute_error_signature",
]
