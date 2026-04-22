"""LLM Cost Tracking — dated pricing snapshots, token counting, and SQLite telemetry.

Tracks token usage and estimated USD cost for every LLM call.
Used by agent nodes and pattern finish nodes for cost visibility.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.context_compression import count_tokens
from shared.db import get_pooled_db_conn
from shared.logging_config import get_logger, get_run_id, get_trajectory_id
from shared.paths import DATA_DIR

logger = get_logger(__name__)

_MODEL_COSTS_DIR = Path(__file__).with_name("model_costs")
_LEDGER_LOCK = threading.Lock()
_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trajectory_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_trajectory ON llm_calls(trajectory_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_agent ON llm_calls(agent_name);
CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model);
"""

_FALLBACK_MODEL_COSTS = {
    "gpt-5-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4": (0.80, 4.00),
    "voyage-3": (0.06, 0.0),
    "voyage-3-lite": (0.02, 0.0),
    "voyage-code-3": (0.06, 0.0),
    "gemma": (0.0, 0.0),
    "llama": (0.0, 0.0),
    "mistral": (0.0, 0.0),
}


def _latest_model_cost_snapshot() -> Path | None:
    if not _MODEL_COSTS_DIR.exists():
        return None
    snapshots = sorted(_MODEL_COSTS_DIR.glob("*.json"))
    return snapshots[-1] if snapshots else None


def get_model_cost_snapshot_path() -> str | None:
    snapshot = _latest_model_cost_snapshot()
    return str(snapshot) if snapshot else None


def load_model_costs() -> dict[str, tuple[float, float]]:
    snapshot = _latest_model_cost_snapshot()
    if snapshot is None:
        return dict(_FALLBACK_MODEL_COSTS)
    try:
        raw = json.loads(snapshot.read_text(encoding="utf-8"))
        loaded = {
            model: (
                float(values["input_per_1m"]),
                float(values["output_per_1m"]),
            )
            for model, values in raw.items()
        }
        logger.info(
            "Loaded %d model prices from snapshot",
            len(loaded),
            extra={"snapshot_path": str(snapshot)},
        )
        return loaded
    except Exception as exc:
        logger.warning(
            "Failed to load model cost snapshot, using fallback table",
            extra={"snapshot_path": str(snapshot), "error": str(exc)},
        )
        return dict(_FALLBACK_MODEL_COSTS)


MODEL_COSTS = load_model_costs()


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single LLM call based on token counts."""
    costs = MODEL_COSTS.get(model)
    if not costs:
        for prefix, c in MODEL_COSTS.items():
            if model.startswith(prefix):
                costs = c
                break
    if not costs:
        costs = (0.15, 0.60)

    return (prompt_tokens * costs[0] + completion_tokens * costs[1]) / 1_000_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _llm_usage_db_path() -> Path:
    return Path(os.getenv("LLM_USAGE_DB", str(DATA_DIR / "llm_usage.db")))


def _usage_conn():
    conn = get_pooled_db_conn(_llm_usage_db_path())
    conn.executescript(_USAGE_SCHEMA)
    conn.commit()
    return conn


def _message_content(message: Any) -> str:
    if isinstance(message, str):
        return message
    if hasattr(message, "content"):
        return str(message.content)
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=True)
    return str(message)


def _extract_usage_metadata(response) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = getattr(response, "response_metadata", {}) or {}
    usage = metadata.get("token_usage", {}) or {}
    if not usage:
        usage = getattr(response, "usage_metadata", {}) or {}
    return metadata, usage


def _normalise_usage_counts(
    response,
    messages: list[Any] | None,
) -> tuple[int, int]:
    metadata, usage = _extract_usage_metadata(response)
    prompt_tokens = usage.get("prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("input_tokens")
    completion_tokens = usage.get("completion_tokens")
    if completion_tokens is None:
        completion_tokens = usage.get("output_tokens")

    if prompt_tokens is not None and completion_tokens is not None:
        return int(prompt_tokens), int(completion_tokens)

    prompt_tokens = 0
    if messages:
        prompt_tokens = sum(count_tokens(_message_content(message)) for message in messages)

    completion_text = getattr(response, "content", "")
    completion_tokens = count_tokens(str(completion_text)) if completion_text else 0

    if not metadata and not usage:
        logger.debug(
            "LLM response missing provider token metadata — using tiktoken fallback",
            extra={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )
    return int(prompt_tokens), int(completion_tokens)


def _record_usage_row(
    *,
    agent_name: str,
    operation: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> int:
    with _LEDGER_LOCK:
        conn = _usage_conn()
        cursor = conn.execute(
            """
            INSERT INTO llm_calls
                (timestamp, trajectory_id, run_id, agent_name, operation, model,
                 prompt_tokens, completion_tokens, total_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_now(),
                get_trajectory_id(),
                get_run_id(),
                agent_name,
                operation,
                model,
                prompt_tokens,
                completion_tokens,
                prompt_tokens + completion_tokens,
                cost_usd,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _promote_recorded_agent_name(row_id: int | None, agent_name: str) -> None:
    if row_id is None or not agent_name or agent_name == "unknown":
        return
    with _LEDGER_LOCK:
        conn = _usage_conn()
        conn.execute(
            """
            UPDATE llm_calls
            SET agent_name = ?
            WHERE id = ? AND agent_name = 'unknown'
            """,
            (agent_name, row_id),
        )
        conn.commit()


def record_llm_usage(
    response,
    *,
    agent_name: str = "unknown",
    messages: list[Any] | None = None,
    model_hint: str | None = None,
    operation: str = "invoke",
) -> dict:
    """Persist a single LLM call to SQLite and return its usage dict."""
    if hasattr(response, "_jobpulse_usage"):
        usage = dict(getattr(response, "_jobpulse_usage"))
        usage["agent"] = agent_name or usage.get("agent", "unknown")
        row_id = getattr(response, "_jobpulse_usage_id", None)
        _promote_recorded_agent_name(row_id, usage["agent"])
        setattr(response, "_jobpulse_usage", usage)
        return usage

    metadata, _ = _extract_usage_metadata(response)
    prompt_tokens, completion_tokens = _normalise_usage_counts(response, messages)
    model = (
        metadata.get("model_name")
        or getattr(response, "model_name", None)
        or model_hint
        or "gpt-4.1-mini"
    )
    cost = estimate_cost(model, prompt_tokens, completion_tokens)
    usage = {
        "agent": agent_name or "unknown",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model": model,
        "cost_usd": cost,
        "trajectory_id": get_trajectory_id(),
        "run_id": get_run_id(),
        "operation": operation,
    }
    row_id = _record_usage_row(
        agent_name=usage["agent"],
        operation=operation,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
    )
    setattr(response, "_jobpulse_usage", usage)
    setattr(response, "_jobpulse_usage_id", row_id)
    return usage


def track_llm_usage(response, agent_name: str) -> dict:
    """Extract token usage from a response and return a tracking dict."""
    return record_llm_usage(response, agent_name=agent_name)


def compute_cost_summary(token_usage: list[dict]) -> dict:
    """Compute aggregate cost summary from accumulated token_usage entries."""
    total_prompt = sum(u.get("prompt_tokens", 0) for u in token_usage)
    total_completion = sum(u.get("completion_tokens", 0) for u in token_usage)
    total_cost = sum(u.get("cost_usd", 0) for u in token_usage)
    per_agent = {}
    for u in token_usage:
        agent = u.get("agent", "unknown")
        per_agent.setdefault(agent, 0.0)
        per_agent[agent] += u.get("cost_usd", 0)
    return {
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_cost_usd": total_cost,
        "calls": len(token_usage),
        "cost_per_agent": per_agent,
    }


class BudgetExceededError(Exception):
    """Raised when LLM spending exceeds the configured budget cap."""
    def __init__(self, spent: float, cap: float, estimated: float):
        self.spent = spent
        self.cap = cap
        self.estimated = estimated
        super().__init__(
            f"Budget exceeded: ${spent:.4f} spent + ${estimated:.4f} estimated > ${cap:.2f} cap"
        )


def check_budget_from_state(state: dict, estimated_next_cost: float = 0.05) -> None:
    """Check accumulated cost in state against the budget cap.

    Raises BudgetExceededError if continuing would exceed the cap.
    Call in pattern control nodes before deciding to continue another iteration.

    Args:
        state: LangGraph state dict (must contain token_usage list).
        estimated_next_cost: Estimated cost of the next operation in USD.
    """
    cap = float(os.environ.get("LLM_BUDGET_CAP_USD", "10.00"))
    if cap <= 0:
        return
    token_usage = state.get("token_usage", [])
    total_cost = sum(u.get("cost_usd", 0) for u in token_usage)
    if total_cost + estimated_next_cost > cap:
        raise BudgetExceededError(total_cost, cap, estimated_next_cost)


class CostEnforcer:
    """Thread-safe budget cap for LLM spending.

    Set LLM_BUDGET_CAP_USD env var or pass max_budget_usd. 0 = unlimited.
    """
    def __init__(self, max_budget_usd: float | None = None):
        if max_budget_usd is not None:
            self.max_budget_usd = max_budget_usd
        else:
            self.max_budget_usd = float(os.getenv("LLM_BUDGET_CAP_USD", "10.00"))
        self.total_spent = 0.0
        self._lock = threading.Lock()

    def record(self, cost_usd: float):
        with self._lock:
            self.total_spent += cost_usd

    def check_budget(self, estimated_cost: float = 0.0):
        if self.max_budget_usd <= 0:
            return
        with self._lock:
            if self.total_spent + estimated_cost > self.max_budget_usd:
                raise BudgetExceededError(self.total_spent, self.max_budget_usd, estimated_cost)

    def remaining(self) -> float:
        with self._lock:
            return max(0, self.max_budget_usd - self.total_spent)

    def reset(self):
        with self._lock:
            self.total_spent = 0.0
