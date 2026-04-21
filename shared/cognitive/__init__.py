"""Cognitive Reasoning Engines — 4-level graduated escalation.

Opt-in toolkit for agents. Any agent adds cognitive abilities via:

    from shared.cognitive import CognitiveEngine

    engine = CognitiveEngine(memory_manager, agent_name="my_agent")
    result = await engine.think(task="...", domain="...", stakes="medium")
    await engine.flush()

Levels:
    L0 Memory Recall  — strategy templates found, zero LLM calls
    L1 Single Shot    — one LLM call with composed prompt
    L2 Reflexion      — try/critique/retry with failure memory
    L3 Tree of Thought — parallel branch exploration via GRPO
"""
