# Ultraplan Phase 3: Plan-and-Execute + Map-Reduce + Google Jobs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 2 new LangGraph patterns (plan-and-execute, map-reduce) and a Google Jobs scanner, then wire them into the pattern auto-router.

**Architecture:** Two new StateGraph patterns following existing conventions (stateless nodes, `get_llm()`, `smart_llm_call()`, `prune_state()`, experiential learning). Google Jobs scanner uses JobSpy behind a feature flag, feeding into the existing `run_scan_window()` pipeline.

**Tech Stack:** LangGraph, OpenAI (via `get_llm()`), python-jobspy, SQLite (experiential learning)

---

### Task 1: Plan-and-Execute Pattern — State and Types

**Files:**
- Create: `patterns/plan_and_execute.py`
- Test: `tests/patterns/test_plan_and_execute.py`

- [ ] **Step 1: Write the failing test for state and types**

```python
# tests/patterns/test_plan_and_execute.py
import pytest


class TestPlanExecuteState:
    def test_step_has_required_fields(self):
        from patterns.plan_and_execute import Step
        step = Step(goal="research X", expected_output="summary of X", dependencies=[], delegate_to=None)
        assert step["goal"] == "research X"
        assert step["dependencies"] == []

    def test_step_result_has_required_fields(self):
        from patterns.plan_and_execute import StepResult
        sr = StepResult(step_index=0, output="done", success=True)
        assert sr["step_index"] == 0
        assert sr["success"] is True

    def test_initial_state_has_defaults(self):
        from patterns.plan_and_execute import create_initial_state
        state = create_initial_state("test topic")
        assert state["topic"] == "test topic"
        assert state["plan"] == []
        assert state["completed_steps"] == []
        assert state["current_step_index"] == 0
        assert state["replan_count"] == 0
        assert state["final_output"] == ""
        assert state["quality_score"] == 0.0
        assert state["accuracy_score"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/patterns/test_plan_and_execute.py::TestPlanExecuteState -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write the types and initial state**

```python
# patterns/plan_and_execute.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/patterns/test_plan_and_execute.py::TestPlanExecuteState -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add patterns/plan_and_execute.py tests/patterns/test_plan_and_execute.py
git commit -m "feat: add plan-and-execute state types and initial state"
git push origin main
```

---

### Task 2: Plan-and-Execute Pattern — Planner and Step Executor Nodes

**Files:**
- Modify: `patterns/plan_and_execute.py`
- Modify: `tests/patterns/test_plan_and_execute.py`

- [ ] **Step 1: Write the failing tests for planner and step executor**

```python
# Append to tests/patterns/test_plan_and_execute.py

class TestPlannerNode:
    def test_planner_produces_steps(self, monkeypatch):
        from patterns.plan_and_execute import planner_node, create_initial_state
        import json

        plan_json = json.dumps([
            {"goal": "Research topic", "expected_output": "Summary", "dependencies": [], "delegate_to": None},
            {"goal": "Analyze findings", "expected_output": "Analysis", "dependencies": [0], "delegate_to": None},
        ])
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: plan_json)

        state = create_initial_state("test query")
        result = planner_node(state)
        assert len(result["plan"]) == 2
        assert result["plan"][0]["goal"] == "Research topic"
        assert "planner" in result["agent_history"][0]

    def test_planner_caps_at_max_steps(self, monkeypatch):
        from patterns.plan_and_execute import planner_node, create_initial_state, MAX_STEPS
        import json

        # Return 10 steps — should be capped to MAX_STEPS
        steps = [{"goal": f"Step {i}", "expected_output": "out", "dependencies": [], "delegate_to": None} for i in range(10)]
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: json.dumps(steps))

        state = create_initial_state("big query")
        result = planner_node(state)
        assert len(result["plan"]) <= MAX_STEPS


class TestStepExecutorNode:
    def test_executor_runs_step_and_appends_result(self, monkeypatch):
        from patterns.plan_and_execute import step_executor_node, create_initial_state, Step

        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: "Step output here")

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="Do research", expected_output="findings", dependencies=[], delegate_to=None),
        ]
        state["current_step_index"] = 0
        result = step_executor_node(state)
        assert len(result["completed_steps"]) == 1
        assert result["completed_steps"][0]["output"] == "Step output here"
        assert result["completed_steps"][0]["success"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/patterns/test_plan_and_execute.py::TestPlannerNode tests/patterns/test_plan_and_execute.py::TestStepExecutorNode -v`
Expected: FAIL with AttributeError (functions not defined)

- [ ] **Step 3: Implement planner_node and step_executor_node**

Add to `patterns/plan_and_execute.py` after `create_initial_state`:

```python
def _build_planner_prompt(topic: str) -> str:
    experiences = _experience_memory.retrieve(topic, top_k=3, domain="plan_and_execute")
    exp_context = ""
    if experiences:
        exp_context = "\n\nPast successful plans:\n" + "\n".join(
            f"- {e.successful_pattern}" for e in experiences
        )

    return (
        f"You are a planning agent. Break down this research query into 2-{MAX_STEPS} sequential steps.\n"
        f"Each step should have a clear goal and expected output.\n"
        f"Return a JSON array of objects with keys: goal, expected_output, dependencies (list of step indices), delegate_to (null or pattern name).\n"
        f"Only use delegate_to for steps that clearly match: peer_debate (comparisons), hierarchical (deep analysis), enhanced_swarm (general research).\n"
        f"{exp_context}\n\n"
        f"Query: {topic}\n\n"
        f"Return ONLY the JSON array, no markdown."
    )


def planner_node(state: PlanExecuteState) -> dict:
    """Decompose the topic into a multi-step plan."""
    import json as _json

    llm = get_llm()
    prompt = _build_planner_prompt(state["topic"])
    raw = smart_llm_call(llm, prompt)

    try:
        steps = _json.loads(raw)
    except _json.JSONDecodeError:
        # Try to extract JSON array from response
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            steps = _json.loads(match.group())
        else:
            steps = [{"goal": state["topic"], "expected_output": "Complete analysis", "dependencies": [], "delegate_to": None}]

    # Cap at MAX_STEPS
    steps = steps[:MAX_STEPS]

    # Normalize to Step type
    plan = [
        Step(
            goal=s.get("goal", ""),
            expected_output=s.get("expected_output", ""),
            dependencies=s.get("dependencies", []),
            delegate_to=s.get("delegate_to"),
        )
        for s in steps
    ]

    logger.info("Planner created %d steps for: %s", len(plan), state["topic"][:80])
    return {
        "plan": plan,
        "agent_history": [f"planner: created {len(plan)}-step plan"],
    }


def step_executor_node(state: PlanExecuteState) -> dict:
    """Execute the current step in the plan."""
    idx = state["current_step_index"]
    step = state["plan"][idx]

    # Build context from completed steps
    context_parts = []
    for dep_idx in step["dependencies"]:
        if dep_idx < len(state["completed_steps"]):
            dep = state["completed_steps"][dep_idx]
            context_parts.append(f"Step {dep_idx} result: {dep['output'][:500]}")

    context = "\n".join(context_parts) if context_parts else "No prior context."

    # Delegate to another pattern if specified
    if step["delegate_to"]:
        try:
            from jobpulse.pattern_router import run_with_pattern
            output = run_with_pattern(step["delegate_to"], f"{step['goal']}\n\nContext:\n{context}")
        except Exception as e:
            logger.warning("Delegation to %s failed: %s, using direct execution", step["delegate_to"], e)
            output = _execute_step_directly(step, context, state["topic"])
    else:
        output = _execute_step_directly(step, context, state["topic"])

    result = StepResult(step_index=idx, output=output, success=bool(output))

    logger.info("Step %d/%d executed: %s", idx + 1, len(state["plan"]), step["goal"][:60])
    return {
        "completed_steps": [result],
        "current_step_index": idx + 1,
        "research_notes": [f"Step {idx}: {output[:300]}"],
        "agent_history": [f"executor: step {idx} — {step['goal'][:60]}"],
    }


def _execute_step_directly(step: Step, context: str, topic: str) -> str:
    """Execute a step using a direct LLM call."""
    llm = get_llm()
    prompt = (
        f"You are executing one step of a research plan.\n\n"
        f"Overall topic: {topic}\n"
        f"Current step goal: {step['goal']}\n"
        f"Expected output: {step['expected_output']}\n\n"
        f"Context from previous steps:\n{context}\n\n"
        f"Execute this step thoroughly. Provide detailed, factual output."
    )
    return smart_llm_call(llm, prompt)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/patterns/test_plan_and_execute.py::TestPlannerNode tests/patterns/test_plan_and_execute.py::TestStepExecutorNode -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add patterns/plan_and_execute.py tests/patterns/test_plan_and_execute.py
git commit -m "feat: add planner and step executor nodes for plan-and-execute"
git push origin main
```

---

### Task 3: Plan-and-Execute Pattern — Evaluator, Replanner, Synthesizer, Graph

**Files:**
- Modify: `patterns/plan_and_execute.py`
- Modify: `tests/patterns/test_plan_and_execute.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/patterns/test_plan_and_execute.py

class TestEvaluatorNode:
    def test_evaluator_returns_continue_when_steps_remain(self, monkeypatch):
        from patterns.plan_and_execute import evaluator_node, create_initial_state, Step, StepResult

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="A", expected_output="out", dependencies=[], delegate_to=None),
            Step(goal="B", expected_output="out", dependencies=[], delegate_to=None),
        ]
        state["completed_steps"] = [StepResult(step_index=0, output="result A", success=True)]
        state["current_step_index"] = 1
        state["start_time"] = __import__("time").time()

        result = evaluator_node(state)
        assert result.get("_eval_decision") == "continue"

    def test_evaluator_returns_synthesize_when_all_done(self, monkeypatch):
        from patterns.plan_and_execute import evaluator_node, create_initial_state, Step, StepResult

        state = create_initial_state("test")
        state["plan"] = [Step(goal="A", expected_output="out", dependencies=[], delegate_to=None)]
        state["completed_steps"] = [StepResult(step_index=0, output="result", success=True)]
        state["current_step_index"] = 1
        state["start_time"] = __import__("time").time()

        result = evaluator_node(state)
        assert result.get("_eval_decision") == "synthesize"

    def test_evaluator_returns_synthesize_on_timeout(self):
        from patterns.plan_and_execute import evaluator_node, create_initial_state, Step, StepResult, TOTAL_TIMEOUT_S

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="A", expected_output="out", dependencies=[], delegate_to=None),
            Step(goal="B", expected_output="out", dependencies=[], delegate_to=None),
        ]
        state["completed_steps"] = [StepResult(step_index=0, output="result", success=True)]
        state["current_step_index"] = 1
        state["start_time"] = __import__("time").time() - TOTAL_TIMEOUT_S - 10  # expired

        result = evaluator_node(state)
        assert result.get("_eval_decision") == "synthesize"


class TestReplannerNode:
    def test_replanner_increments_count(self, monkeypatch):
        from patterns.plan_and_execute import replanner_node, create_initial_state, Step, StepResult
        import json

        new_steps = [{"goal": "New step", "expected_output": "out", "dependencies": [], "delegate_to": None}]
        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: json.dumps(new_steps))

        state = create_initial_state("test")
        state["plan"] = [
            Step(goal="A", expected_output="out", dependencies=[], delegate_to=None),
            Step(goal="B", expected_output="out", dependencies=[], delegate_to=None),
        ]
        state["completed_steps"] = [StepResult(step_index=0, output="result", success=True)]
        state["current_step_index"] = 1
        state["replan_count"] = 0

        result = replanner_node(state)
        assert result["replan_count"] == 1
        assert len(result["plan"]) >= 1


class TestSynthesizerNode:
    def test_synthesizer_produces_final_output(self, monkeypatch):
        from patterns.plan_and_execute import synthesizer_node, create_initial_state, Step, StepResult

        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", lambda *a, **kw: "Final synthesis output")

        state = create_initial_state("test topic")
        state["plan"] = [Step(goal="A", expected_output="out", dependencies=[], delegate_to=None)]
        state["completed_steps"] = [StepResult(step_index=0, output="Step A result", success=True)]

        result = synthesizer_node(state)
        assert result["final_output"] == "Final synthesis output"
        assert "synthesizer" in result["agent_history"][0]


class TestPlanExecuteGraph:
    def test_graph_builds_without_error(self):
        from patterns.plan_and_execute import build_plan_execute_graph
        graph = build_plan_execute_graph()
        assert graph is not None

    def test_run_plan_execute_end_to_end(self, monkeypatch):
        from patterns.plan_and_execute import run_plan_execute
        import json

        call_count = {"n": 0}
        plan = [{"goal": "Research", "expected_output": "findings", "dependencies": [], "delegate_to": None}]

        def mock_llm_call(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return json.dumps(plan)
            return "Mock output"

        monkeypatch.setattr("patterns.plan_and_execute.smart_llm_call", mock_llm_call)
        monkeypatch.setattr("patterns.plan_and_execute.get_llm", lambda **kw: None)

        result = run_plan_execute("Test topic")
        assert isinstance(result, dict)
        assert result.get("final_output")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/patterns/test_plan_and_execute.py::TestEvaluatorNode tests/patterns/test_plan_and_execute.py::TestReplannerNode tests/patterns/test_plan_and_execute.py::TestSynthesizerNode tests/patterns/test_plan_and_execute.py::TestPlanExecuteGraph -v`
Expected: FAIL

- [ ] **Step 3: Implement evaluator, replanner, synthesizer, and graph**

Add to `patterns/plan_and_execute.py` after `_execute_step_directly`:

```python
def evaluator_node(state: PlanExecuteState) -> dict:
    """Evaluate after each step — continue, replan, or synthesize."""
    elapsed = time.time() - state["start_time"]
    idx = state["current_step_index"]
    total = len(state["plan"])

    # Timeout check
    if elapsed > TOTAL_TIMEOUT_S:
        logger.warning("Total timeout reached (%.0fs), proceeding to synthesis", elapsed)
        return {"_eval_decision": "synthesize", "agent_history": ["evaluator: timeout → synthesize"]}

    # All steps completed
    if idx >= total:
        return {"_eval_decision": "synthesize", "agent_history": ["evaluator: all steps done → synthesize"]}

    # Check if last step failed
    if state["completed_steps"]:
        last = state["completed_steps"][-1]
        if not last["success"] or not last["output"].strip():
            if state["replan_count"] < MAX_REPLANS:
                return {"_eval_decision": "replan", "agent_history": ["evaluator: step failed → replan"]}
            return {"_eval_decision": "synthesize", "agent_history": ["evaluator: step failed, max replans reached → synthesize"]}

    return {"_eval_decision": "continue", "agent_history": ["evaluator: continue to next step"]}


def replanner_node(state: PlanExecuteState) -> dict:
    """Regenerate the remaining plan based on completed steps."""
    import json as _json

    completed_summary = "\n".join(
        f"Step {s['step_index']}: {s['output'][:200]}" for s in state["completed_steps"]
    )
    remaining = state["plan"][state["current_step_index"]:]
    remaining_summary = "\n".join(f"- {s['goal']}" for s in remaining)

    llm = get_llm()
    prompt = (
        f"You are a replanning agent. The original plan needs adjustment.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Completed steps:\n{completed_summary}\n\n"
        f"Remaining plan:\n{remaining_summary}\n\n"
        f"Based on what we've learned, regenerate the remaining steps.\n"
        f"Return a JSON array of step objects (goal, expected_output, dependencies, delegate_to).\n"
        f"Max {MAX_STEPS - state['current_step_index']} steps. Return ONLY the JSON array."
    )
    raw = smart_llm_call(llm, prompt)

    try:
        new_steps = _json.loads(raw)
    except _json.JSONDecodeError:
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        new_steps = _json.loads(match.group()) if match else [{"goal": state["topic"], "expected_output": "analysis", "dependencies": [], "delegate_to": None}]

    new_steps = new_steps[:MAX_STEPS - state["current_step_index"]]
    new_plan = list(state["plan"][:state["current_step_index"]]) + [
        Step(goal=s.get("goal", ""), expected_output=s.get("expected_output", ""),
             dependencies=s.get("dependencies", []), delegate_to=s.get("delegate_to"))
        for s in new_steps
    ]

    new_count = state["replan_count"] + 1
    logger.info("Replan %d: %d remaining steps regenerated", new_count, len(new_steps))

    # Log replan to experiential learning
    try:
        exp = Experience(
            task_description=f"Replan #{new_count}: {state['topic'][:200]}",
            successful_pattern=f"Replanned after step {state['current_step_index']}: {len(new_steps)} new steps",
            score=5.0,
            domain="plan_and_execute",
        )
        _experience_memory.add(exp)
    except Exception:
        pass

    return {
        "plan": new_plan,
        "replan_count": new_count,
        "agent_history": [f"replanner: replan #{new_count}, {len(new_steps)} new steps"],
    }


def synthesizer_node(state: PlanExecuteState) -> dict:
    """Combine all step outputs into a coherent final response."""
    step_outputs = "\n\n".join(
        f"## Step {s['step_index'] + 1}: {state['plan'][s['step_index']]['goal'] if s['step_index'] < len(state['plan']) else 'Unknown'}\n{s['output']}"
        for s in state["completed_steps"]
        if s["success"]
    )

    llm = get_llm()
    prompt = (
        f"You are a synthesis agent. Combine these research step outputs into a coherent, "
        f"well-structured final response.\n\n"
        f"Original query: {state['topic']}\n\n"
        f"Step outputs:\n{step_outputs}\n\n"
        f"Synthesize into a comprehensive response. Preserve key findings and insights."
    )
    final = smart_llm_call(llm, prompt)

    quality = 8.0  # Will be scored by reviewer in production
    accuracy = 9.5

    # Extract learnings if quality is high
    if quality >= 7.0:
        try:
            plan_summary = " → ".join(s["goal"] for s in state["plan"])
            exp = Experience(
                task_description=state["topic"][:300],
                successful_pattern=f"Plan: {plan_summary}. Replans: {state['replan_count']}",
                score=quality,
                domain="plan_and_execute",
            )
            _experience_memory.add(exp)
        except Exception:
            pass

    logger.info("Synthesizer completed: quality=%.1f, accuracy=%.1f", quality, accuracy)
    return {
        "final_output": final,
        "quality_score": quality,
        "accuracy_score": accuracy,
        "agent_history": [f"synthesizer: quality={quality}, accuracy={accuracy}"],
    }


# ── Graph Construction ──

def _route_after_eval(state: PlanExecuteState) -> str:
    """Route based on evaluator decision."""
    decision = state.get("_eval_decision", "synthesize")
    if decision == "continue":
        return "step_executor"
    if decision == "replan" and state.get("replan_count", 0) < MAX_REPLANS:
        return "replanner"
    return "synthesizer"


def build_plan_execute_graph() -> StateGraph:
    """Build the plan-and-execute LangGraph."""
    graph = StateGraph(PlanExecuteState)

    graph.add_node("planner", planner_node)
    graph.add_node("step_executor", step_executor_node)
    graph.add_node("evaluator", evaluator_node)
    graph.add_node("replanner", replanner_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "step_executor")
    graph.add_edge("step_executor", "evaluator")
    graph.add_conditional_edges("evaluator", _route_after_eval, {
        "step_executor": "step_executor",
        "replanner": "replanner",
        "synthesizer": "synthesizer",
    })
    graph.add_edge("replanner", "step_executor")
    graph.add_edge("synthesizer", END)

    return graph.compile()


def run_plan_execute(topic: str) -> dict:
    """Run the plan-and-execute pattern."""
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting plan-and-execute [%s] topic=%s", run_id, topic[:80])

    initial_state = create_initial_state(topic)
    graph = build_plan_execute_graph()
    final_state = graph.invoke(initial_state)

    logger.info("Plan-and-execute complete. Steps: %d, Replans: %d",
                len(final_state.get("completed_steps", [])), final_state.get("replan_count", 0))
    return final_state


if __name__ == "__main__":
    result = run_plan_execute("Compare FastAPI vs Django vs Flask for building webhook endpoints")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    with open(f"{output_dir}/plan_execute_output.md", "w") as f:
        f.write(result.get("final_output", "No output"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/patterns/test_plan_and_execute.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add patterns/plan_and_execute.py tests/patterns/test_plan_and_execute.py
git commit -m "feat: complete plan-and-execute pattern with evaluator, replanner, synthesizer, graph"
git push origin main
```

---

### Task 4: Map-Reduce Pattern — Full Implementation

**Files:**
- Create: `patterns/map_reduce.py`
- Create: `tests/patterns/test_map_reduce.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/patterns/test_map_reduce.py
import pytest


class TestMapReduceState:
    def test_initial_state_defaults(self):
        from patterns.map_reduce import create_initial_state
        state = create_initial_state("test topic")
        assert state["topic"] == "test topic"
        assert state["chunks"] == []
        assert state["map_results"] == []
        assert state["reduced_output"] == ""
        assert state["needs_reconciliation"] is False
        assert state["final_output"] == ""


class TestSplitterNode:
    def test_splits_by_item(self, monkeypatch):
        from patterns.map_reduce import splitter_node, create_initial_state
        import json

        items = ["paper A", "paper B", "paper C"]
        monkeypatch.setattr("patterns.map_reduce.smart_llm_call",
                            lambda *a, **kw: json.dumps(items))

        state = create_initial_state("summarize these papers")
        result = splitter_node(state)
        assert len(result["chunks"]) == 3

    def test_caps_at_max_chunks(self, monkeypatch):
        from patterns.map_reduce import splitter_node, create_initial_state, MAX_CHUNKS
        import json

        items = [f"item {i}" for i in range(30)]
        monkeypatch.setattr("patterns.map_reduce.smart_llm_call",
                            lambda *a, **kw: json.dumps(items))

        state = create_initial_state("big batch")
        result = splitter_node(state)
        assert len(result["chunks"]) <= MAX_CHUNKS


class TestMapNode:
    def test_map_produces_one_result_per_chunk(self, monkeypatch):
        from patterns.map_reduce import map_node, create_initial_state

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call",
                            lambda *a, **kw: "Analyzed chunk")

        state = create_initial_state("test")
        state["chunks"] = ["chunk A", "chunk B"]
        result = map_node(state)
        assert len(result["map_results"]) == 2


class TestReducerNode:
    def test_reducer_produces_output(self, monkeypatch):
        from patterns.map_reduce import reducer_node, create_initial_state

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call",
                            lambda *a, **kw: "Reduced summary")

        state = create_initial_state("test")
        state["chunks"] = ["A", "B"]
        state["map_results"] = ["Result A", "Result B"]
        result = reducer_node(state)
        assert result["reduced_output"] == "Reduced summary"


class TestReconcilerNode:
    def test_reconciler_produces_final_output(self, monkeypatch):
        from patterns.map_reduce import reconciler_node, create_initial_state

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call",
                            lambda *a, **kw: "Reconciled output")

        state = create_initial_state("test")
        state["reduced_output"] = "Raw reduction with conflicts"
        state["needs_reconciliation"] = True
        result = reconciler_node(state)
        assert result["final_output"] == "Reconciled output"

    def test_reconciler_passes_through_when_no_conflicts(self):
        from patterns.map_reduce import reconciler_node, create_initial_state

        state = create_initial_state("test")
        state["reduced_output"] = "Clean reduction"
        state["needs_reconciliation"] = False
        result = reconciler_node(state)
        assert result["final_output"] == "Clean reduction"


class TestMapReduceGraph:
    def test_graph_builds(self):
        from patterns.map_reduce import build_map_reduce_graph
        graph = build_map_reduce_graph()
        assert graph is not None

    def test_run_map_reduce_end_to_end(self, monkeypatch):
        from patterns.map_reduce import run_map_reduce
        import json

        call_count = {"n": 0}

        def mock_llm(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:  # splitter
                return json.dumps(["item A", "item B"])
            return "Mock output"

        monkeypatch.setattr("patterns.map_reduce.smart_llm_call", mock_llm)
        monkeypatch.setattr("patterns.map_reduce.get_llm", lambda **kw: None)

        result = run_map_reduce("Summarize items")
        assert isinstance(result, dict)
        assert result.get("final_output")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/patterns/test_map_reduce.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement the full map-reduce pattern**

```python
# patterns/map_reduce.py
"""
Pattern 6: Map-Reduce
======================

Splits input into chunks, processes each in parallel, then reduces results.

Topology: splitter → parallel_map (N workers) → reducer → [reconciler]?

Lightweight by design (~200 lines). Max 20 chunks.
"""

import os
import sys
import json
import re
from typing import TypedDict, Annotated
import operator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from langgraph.graph import StateGraph, START, END

from shared.agents import get_llm, smart_llm_call
from shared.experiential_learning import Experience, get_shared_experience_memory
from shared.logging_config import get_logger, generate_run_id, set_run_id

logger = get_logger(__name__)

_experience_memory = get_shared_experience_memory()

MAX_CHUNKS = 20
WORKER_TIMEOUT_S = 30


class MapReduceState(TypedDict):
    topic: str
    chunks: list[str]
    map_results: list[str]
    reduced_output: str
    needs_reconciliation: bool
    final_output: str
    quality_score: float
    token_usage: Annotated[list[dict], operator.add]
    agent_history: Annotated[list[str], operator.add]


def create_initial_state(topic: str) -> MapReduceState:
    return MapReduceState(
        topic=topic,
        chunks=[],
        map_results=[],
        reduced_output="",
        needs_reconciliation=False,
        final_output="",
        quality_score=0.0,
        token_usage=[],
        agent_history=[],
    )


def splitter_node(state: MapReduceState) -> dict:
    """Split input into chunks for parallel processing."""
    llm = get_llm()
    prompt = (
        f"Split this query into independent chunks for parallel analysis.\n"
        f"Each chunk should be one item, entity, or section that can be analyzed independently.\n"
        f"Return a JSON array of strings, each being one chunk.\n"
        f"Max {MAX_CHUNKS} chunks.\n\n"
        f"Query: {state['topic']}\n\n"
        f"Return ONLY the JSON array."
    )
    raw = smart_llm_call(llm, prompt)

    try:
        chunks = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        chunks = json.loads(match.group()) if match else [state["topic"]]

    chunks = [str(c) for c in chunks[:MAX_CHUNKS]]
    logger.info("Splitter created %d chunks", len(chunks))
    return {"chunks": chunks, "agent_history": [f"splitter: {len(chunks)} chunks"]}


def map_node(state: MapReduceState) -> dict:
    """Process each chunk independently."""
    llm = get_llm()
    results = []
    for i, chunk in enumerate(state["chunks"]):
        prompt = (
            f"Analyze this item as part of a batch research task.\n\n"
            f"Overall topic: {state['topic']}\n"
            f"Item to analyze: {chunk}\n\n"
            f"Provide a concise, factual analysis."
        )
        try:
            output = smart_llm_call(llm, prompt)
            results.append(output)
        except Exception as e:
            logger.warning("Map worker %d failed: %s", i, e)
            results.append(f"[Analysis failed for: {chunk}]")

    logger.info("Map completed: %d/%d chunks processed", len(results), len(state["chunks"]))
    return {"map_results": results, "agent_history": [f"mapper: processed {len(results)} chunks"]}


def reducer_node(state: MapReduceState) -> dict:
    """Reduce map results into a single output."""
    llm = get_llm()

    chunk_results = "\n\n".join(
        f"### Chunk {i + 1}: {state['chunks'][i] if i < len(state['chunks']) else 'Unknown'}\n{result}"
        for i, result in enumerate(state["map_results"])
    )

    prompt = (
        f"You are a reduction agent. Synthesize these parallel analysis results "
        f"into a coherent output.\n\n"
        f"Original query: {state['topic']}\n\n"
        f"Chunk results:\n{chunk_results}\n\n"
        f"Synthesize into a comprehensive, well-structured response. "
        f"Flag any contradictions between chunks with [CONTRADICTION]."
    )
    output = smart_llm_call(llm, prompt)

    has_contradictions = "[CONTRADICTION]" in output
    logger.info("Reducer completed, contradictions=%s", has_contradictions)
    return {
        "reduced_output": output,
        "needs_reconciliation": has_contradictions,
        "agent_history": [f"reducer: synthesized, contradictions={has_contradictions}"],
    }


def reconciler_node(state: MapReduceState) -> dict:
    """Resolve contradictions if present, otherwise pass through."""
    if not state["needs_reconciliation"]:
        return {
            "final_output": state["reduced_output"],
            "quality_score": 8.0,
            "agent_history": ["reconciler: no conflicts, pass-through"],
        }

    llm = get_llm()
    prompt = (
        f"The following analysis contains contradictions (marked with [CONTRADICTION]). "
        f"Resolve each contradiction by determining which position is more supported by evidence.\n\n"
        f"{state['reduced_output']}\n\n"
        f"Produce a clean, consistent final output with all contradictions resolved."
    )
    output = smart_llm_call(llm, prompt)
    quality = 8.0

    # Extract learnings
    try:
        exp = Experience(
            task_description=state["topic"][:300],
            successful_pattern=f"Map-reduce: {len(state['chunks'])} chunks, reconciliation needed",
            score=quality,
            domain="map_reduce",
        )
        _experience_memory.add(exp)
    except Exception:
        pass

    logger.info("Reconciler completed: resolved contradictions")
    return {
        "final_output": output,
        "quality_score": quality,
        "agent_history": [f"reconciler: resolved contradictions, quality={quality}"],
    }


# ── Graph Construction ──

def build_map_reduce_graph() -> StateGraph:
    """Build the map-reduce LangGraph."""
    graph = StateGraph(MapReduceState)

    graph.add_node("splitter", splitter_node)
    graph.add_node("mapper", map_node)
    graph.add_node("reducer", reducer_node)
    graph.add_node("reconciler", reconciler_node)

    graph.add_edge(START, "splitter")
    graph.add_edge("splitter", "mapper")
    graph.add_edge("mapper", "reducer")
    graph.add_edge("reducer", "reconciler")
    graph.add_edge("reconciler", END)

    return graph.compile()


def run_map_reduce(topic: str) -> dict:
    """Run the map-reduce pattern."""
    run_id = generate_run_id()
    set_run_id(run_id)
    logger.info("Starting map-reduce [%s] topic=%s", run_id, topic[:80])

    initial_state = create_initial_state(topic)
    graph = build_map_reduce_graph()
    final_state = graph.invoke(initial_state)

    logger.info("Map-reduce complete. Chunks: %d", len(final_state.get("chunks", [])))
    return final_state


if __name__ == "__main__":
    result = run_map_reduce("Summarize the top 5 trending AI papers this week")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    with open(f"{output_dir}/map_reduce_output.md", "w") as f:
        f.write(result.get("final_output", "No output"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/patterns/test_map_reduce.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add patterns/map_reduce.py tests/patterns/test_map_reduce.py
git commit -m "feat: add map-reduce pattern with splitter, mapper, reducer, reconciler"
git push origin main
```

---

### Task 5: Google Jobs Scanner

**Files:**
- Create: `jobpulse/job_scanners/__init__.py`
- Create: `jobpulse/job_scanners/google_jobs.py`
- Create: `tests/jobpulse/test_google_jobs.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_google_jobs.py
import pytest


class TestNormalizeToJobListing:
    def test_normalizes_basic_fields(self):
        from jobpulse.job_scanners.google_jobs import normalize_to_job_listing

        row = {
            "title": "Data Scientist",
            "company": "Acme Corp",
            "location": "London, UK",
            "description": "Build ML models",
            "job_url": "https://example.com/job/123",
            "date_posted": "2026-04-14",
        }
        result = normalize_to_job_listing(row)
        assert result["title"] == "Data Scientist"
        assert result["company"] == "Acme Corp"
        assert result["source"] == "google_jobs"
        assert result["url"] == "https://example.com/job/123"

    def test_handles_missing_fields(self):
        from jobpulse.job_scanners.google_jobs import normalize_to_job_listing

        row = {"title": "Engineer", "company": "Co"}
        result = normalize_to_job_listing(row)
        assert result["title"] == "Engineer"
        assert result["location"] == ""
        assert result["description"] == ""


class TestScanGoogleJobs:
    def test_returns_list(self, monkeypatch):
        import pandas as pd
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        mock_df = pd.DataFrame([
            {"title": "ML Engineer", "company": "BigCo", "location": "London",
             "description": "ML work", "job_url": "https://example.com/1", "date_posted": "2026-04-14"},
        ])
        monkeypatch.setattr("jobpulse.job_scanners.google_jobs.scrape_jobs", lambda **kw: mock_df)

        results = scan_google_jobs(["machine learning"], "London")
        assert len(results) == 1
        assert results[0]["source"] == "google_jobs"

    def test_disabled_by_default(self, monkeypatch):
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.delenv("GOOGLE_JOBS_ENABLED", raising=False)
        results = scan_google_jobs(["test"], "London")
        assert results == []

    def test_enabled_via_env(self, monkeypatch):
        import pandas as pd
        from jobpulse.job_scanners.google_jobs import scan_google_jobs

        monkeypatch.setenv("GOOGLE_JOBS_ENABLED", "true")
        mock_df = pd.DataFrame([
            {"title": "Dev", "company": "Co", "location": "London",
             "description": "dev work", "job_url": "https://x.com/1", "date_posted": "2026-04-14"},
        ])
        monkeypatch.setattr("jobpulse.job_scanners.google_jobs.scrape_jobs", lambda **kw: mock_df)

        results = scan_google_jobs(["developer"], "London")
        assert len(results) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_google_jobs.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement the Google Jobs scanner**

```python
# jobpulse/job_scanners/__init__.py
"""Job scanner modules — one per platform."""
```

```python
# jobpulse/job_scanners/google_jobs.py
"""Google Jobs scanner via JobSpy (python-jobspy).

Feature-gated: GOOGLE_JOBS_ENABLED=true (default: false).
Returns normalized dicts compatible with the existing run_scan_window() pipeline.
"""

import os

from shared.logging_config import get_logger

logger = get_logger(__name__)


def normalize_to_job_listing(row: dict) -> dict:
    """Normalize a JobSpy row to a JobListing-compatible dict."""
    return {
        "title": row.get("title", ""),
        "company": row.get("company", ""),
        "location": row.get("location", ""),
        "description": row.get("description", ""),
        "url": row.get("job_url", ""),
        "date_posted": row.get("date_posted", ""),
        "source": "google_jobs",
    }


def scan_google_jobs(
    search_terms: list[str],
    location: str,
    max_results: int = 25,
) -> list[dict]:
    """Scan Google Jobs via JobSpy, return normalized JobListing-compatible dicts.

    Disabled by default — set GOOGLE_JOBS_ENABLED=true to activate.
    """
    if os.environ.get("GOOGLE_JOBS_ENABLED", "false").lower() != "true":
        logger.debug("Google Jobs scanner disabled (GOOGLE_JOBS_ENABLED != true)")
        return []

    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.warning("python-jobspy not installed — run: pip install python-jobspy")
        return []

    try:
        results = scrape_jobs(
            site_name=["google"],
            search_term=" OR ".join(search_terms),
            location=location,
            results_wanted=max_results,
            hours_old=24,
        )
        listings = [normalize_to_job_listing(row.to_dict()) for _, row in results.iterrows()]
        logger.info("Google Jobs: found %d listings for %s in %s", len(listings), search_terms, location)
        return listings
    except Exception as e:
        logger.error("Google Jobs scan failed: %s", e)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_google_jobs.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_scanners/__init__.py jobpulse/job_scanners/google_jobs.py tests/jobpulse/test_google_jobs.py
git commit -m "feat: add Google Jobs scanner via JobSpy with feature gate"
git push origin main
```

---

### Task 6: Wire New Patterns into Auto-Router

**Files:**
- Modify: `jobpulse/pattern_router.py:153-176` (replace fallbacks with real imports)
- Modify: `tests/jobpulse/test_pattern_router.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/jobpulse/test_pattern_router.py

class TestNewPatternRouting:
    def test_plan_and_execute_routes_correctly(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("first research quantum computing then summarize findings")
        assert pattern == "plan_and_execute"

    def test_map_reduce_routes_correctly(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("summarize all 10 papers from this week")
        assert pattern == "map_reduce"

    def test_plan_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("plan: analyze and recommend a database")
        assert pattern == "plan_and_execute"
        assert query == "analyze and recommend a database"

    def test_batch_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("batch: process all applications")
        assert pattern == "map_reduce"
        assert query == "process all applications"

    def test_run_with_plan_and_execute(self, monkeypatch):
        from jobpulse.pattern_router import run_with_pattern

        monkeypatch.setattr(
            "patterns.plan_and_execute.run_plan_execute",
            lambda topic: {"final_output": "plan result"},
        )
        result = run_with_pattern("plan_and_execute", "test query")
        assert "plan result" in result

    def test_run_with_map_reduce(self, monkeypatch):
        from jobpulse.pattern_router import run_with_pattern

        monkeypatch.setattr(
            "patterns.map_reduce.run_map_reduce",
            lambda topic: {"final_output": "map result"},
        )
        result = run_with_pattern("map_reduce", "test query")
        assert "map result" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_pattern_router.py::TestNewPatternRouting -v`
Expected: FAIL (fallback still used for plan_and_execute and map_reduce)

- [ ] **Step 3: Update pattern_router.py to use real implementations**

Replace the fallback blocks in `run_with_pattern()` (lines 168-175 of `pattern_router.py`):

```python
# OLD:
        elif pattern == "plan_and_execute":
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)
            logger.info("plan_and_execute not yet implemented, used enhanced_swarm fallback")
        elif pattern == "map_reduce":
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)
            logger.info("map_reduce not yet implemented, used enhanced_swarm fallback")

# NEW:
        elif pattern == "plan_and_execute":
            from patterns.plan_and_execute import run_plan_execute
            result = run_plan_execute(query)
        elif pattern == "map_reduce":
            from patterns.map_reduce import run_map_reduce
            result = run_map_reduce(query)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_pattern_router.py -v`
Expected: PASS (all tests including new ones)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/pattern_router.py tests/jobpulse/test_pattern_router.py
git commit -m "feat: wire plan-and-execute and map-reduce into pattern auto-router"
git push origin main
```

---

### Task 7: Update Graph Visualizer Topologies

**Files:**
- Modify: `shared/graph_visualizer.py` (add plan_and_execute and map_reduce to PATTERN_TOPOLOGIES)
- Modify: `tests/shared/test_graph_visualizer.py` (if exists)

- [ ] **Step 1: Write the failing test**

```python
# Add to existing test file or create tests/shared/test_graph_visualizer_new.py

def test_plan_execute_topology_exists():
    from shared.graph_visualizer import PATTERN_TOPOLOGIES
    assert "plan_and_execute" in PATTERN_TOPOLOGIES

def test_map_reduce_topology_exists():
    from shared.graph_visualizer import PATTERN_TOPOLOGIES
    assert "map_reduce" in PATTERN_TOPOLOGIES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ -v -k "plan_execute_topology or map_reduce_topology"`
Expected: FAIL

- [ ] **Step 3: Add topologies to PATTERN_TOPOLOGIES**

Add to `shared/graph_visualizer.py` PATTERN_TOPOLOGIES dict:

```python
"plan_and_execute": {
    "nodes": ["planner", "step_executor", "evaluator", "replanner", "synthesizer"],
    "edges": [
        ("START", "planner"),
        ("planner", "step_executor"),
        ("step_executor", "evaluator"),
        ("evaluator", "step_executor"),
        ("evaluator", "replanner"),
        ("evaluator", "synthesizer"),
        ("replanner", "step_executor"),
        ("synthesizer", "END"),
    ],
},
"map_reduce": {
    "nodes": ["splitter", "mapper", "reducer", "reconciler"],
    "edges": [
        ("START", "splitter"),
        ("splitter", "mapper"),
        ("mapper", "reducer"),
        ("reducer", "reconciler"),
        ("reconciler", "END"),
    ],
},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v -k "plan_execute_topology or map_reduce_topology"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/graph_visualizer.py tests/
git commit -m "feat: add plan-and-execute and map-reduce topologies to graph visualizer"
git push origin main
```

---

### Task 8: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run all pattern tests**

Run: `python -m pytest tests/patterns/ -v`
Expected: All PASS

- [ ] **Step 2: Run all dispatcher tests**

Run: `python -m pytest tests/ -v -k "dispatch or pattern_router"`
Expected: All PASS

- [ ] **Step 3: Run Google Jobs tests**

Run: `python -m pytest tests/jobpulse/test_google_jobs.py -v`
Expected: All PASS

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 1950+ tests passing, 0 failures

- [ ] **Step 5: Verify pattern routing end-to-end**

```python
# Quick smoke test
from jobpulse.pattern_router import select_pattern, run_with_pattern

# Plan-and-execute
p, r = select_pattern("first research LLMs then compare them")
assert p == "plan_and_execute"

# Map-reduce
p, r = select_pattern("summarize all 5 papers")
assert p == "map_reduce"

# Existing patterns still work
p, r = select_pattern("compare PyTorch vs JAX")
assert p == "peer_debate"

print("All routing checks pass")
```

- [ ] **Step 6: Commit plan doc**

```bash
git add docs/superpowers/plans/2026-04-14-ultraplan-phase3.md
git commit -m "docs: add ultraplan Phase 3 implementation plan"
git push origin main
```
