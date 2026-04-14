"""
Pattern 5: Plan-and-Execute
============================

Decomposes complex queries into a multi-step plan, executes steps sequentially,
evaluates after each step, and replans if needed.

Topology: planner → [step_executor → evaluator → replanner?]* → synthesizer

Convergence: max 7 steps, max 3 replans, 7-minute total timeout.
"""

import os
import sys
import time
from typing import TypedDict, Annotated, Optional
import operator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from langgraph.graph import StateGraph, START, END

from shared.agents import get_llm, smart_llm_call
from shared.state import prune_state
from shared.experiential_learning import Experience, get_shared_experience_memory
from shared.logging_config import get_logger, generate_run_id, set_run_id

logger = get_logger(__name__)

_experience_memory = get_shared_experience_memory()

# ── Constants ──
MAX_STEPS = 7
MAX_REPLANS = 3
STEP_TIMEOUT_S = 60
TOTAL_TIMEOUT_S = 420  # 7 minutes
QUALITY_THRESHOLD = 8.0
ACCURACY_THRESHOLD = 9.5


class Step(TypedDict):
    goal: str
    expected_output: str
    dependencies: list[int]
    delegate_to: str | None


class StepResult(TypedDict):
    step_index: int
    output: str
    success: bool


class PlanExecuteState(TypedDict):
    topic: str
    plan: list[Step]
    completed_steps: list[StepResult]
    current_step_index: int
    replan_count: int
    research_notes: Annotated[list[str], operator.add]
    final_output: str
    quality_score: float
    accuracy_score: float
    token_usage: Annotated[list[dict], operator.add]
    agent_history: Annotated[list[str], operator.add]
    start_time: float


def create_initial_state(topic: str) -> PlanExecuteState:
    return PlanExecuteState(
        topic=topic,
        plan=[],
        completed_steps=[],
        current_step_index=0,
        replan_count=0,
        research_notes=[],
        final_output="",
        quality_score=0.0,
        accuracy_score=0.0,
        token_usage=[],
        agent_history=[],
        start_time=time.time(),
    )
