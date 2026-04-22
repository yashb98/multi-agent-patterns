"""Unified dispatch entry point — resolves a DispatchStrategy and delegates.

Before this module, every caller (telegram_listener, webhook_server,
multi_bot_listener, slack_adapter, discord_adapter) had to do:

    USE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in (...)
    if USE_SWARM:
        from jobpulse.swarm_dispatcher import dispatch
    else:
        from jobpulse.dispatcher import dispatch

That duplication meant adding a new strategy (e.g. a shadow dispatcher for
A/B testing) required touching every caller. It also hid the dispatch
contract behind an env var read that looked like config but was really a
strategy selector.

This module makes the choice explicit:

    from jobpulse.dispatch import dispatch, DispatchStrategy

    # Default strategy (reads JOBPULSE_SWARM)
    reply = dispatch(cmd)

    # Force a specific strategy (tests, benchmarks, A/B)
    reply = dispatch(cmd, strategy=DispatchStrategy.FLAT)

Both underlying modules (`jobpulse.dispatcher` and
`jobpulse.swarm_dispatcher`) continue to own their implementations — this
file is purely a selector. That way the handler functions, GRPO scorer,
experience memory, and swarm task analyser don't need to move.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from jobpulse.command_router import ParsedCommand

logger = get_logger(__name__)


class DispatchStrategy(str, Enum):
    """Which dispatcher implementation to use for a given ParsedCommand."""

    FLAT = "flat"
    """Direct intent→handler map with a single ProcessTrail step.

    Cheap, predictable, no task decomposition. Best for single-intent
    commands where the swarm overhead (LLM scorer, experience store,
    RLM synthesis) is pure latency. Enabled via JOBPULSE_SWARM=false."""

    SWARM = "swarm"
    """Enhanced Swarm — decompose the intent into one or more sub-tasks,
    run each with GRPO candidate scoring, optionally synthesise with RLM,
    and store the experience for future runs. Default."""


def default_strategy() -> DispatchStrategy:
    """Read the JOBPULSE_SWARM env var and return the matching strategy.

    Tolerates all the common truthy/falsy spellings so ops scripts can set
    ``JOBPULSE_SWARM=0`` or ``JOBPULSE_SWARM=false`` interchangeably.
    """
    raw = os.environ.get("JOBPULSE_SWARM", "true").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return DispatchStrategy.SWARM
    return DispatchStrategy.FLAT


def dispatch(
    cmd: "ParsedCommand",
    strategy: DispatchStrategy | None = None,
) -> str:
    """Route a parsed command to the selected dispatcher implementation.

    Args:
        cmd: Classified Telegram/Slack/Discord/API command.
        strategy: Explicit strategy override. When None (the common case),
            resolves via :func:`default_strategy` so the caller doesn't
            have to read env vars.

    Returns:
        The reply string that should be sent back to the user.
    """
    if strategy is None:
        strategy = default_strategy()

    if strategy is DispatchStrategy.SWARM:
        from jobpulse.swarm_dispatcher import dispatch as _swarm_dispatch
        return _swarm_dispatch(cmd)

    from jobpulse.dispatcher import dispatch as _flat_dispatch
    return _flat_dispatch(cmd)
