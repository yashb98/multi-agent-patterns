"""Parallel Agent Executor — run multiple LLM calls concurrently.

Provides async wrappers around agent nodes for parallel execution.
Used by patterns that can benefit from concurrent LLM calls:
- Multiple research workers on different sub-topics
- Parallel GRPO candidate generation
- Concurrent debate rounds

Usage:
    from shared.parallel_executor import run_parallel, run_llm_async

    # Run multiple agent nodes in parallel
    results = await run_parallel([
        (researcher_node, state_1),
        (researcher_node, state_2),
    ])

    # Run multiple LLM calls in parallel
    responses = await run_llm_async([
        (llm, messages_1),
        (llm, messages_2),
    ])
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Shared thread pool for running sync LLM calls concurrently
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="llm_worker")


async def run_in_thread(fn: Callable, *args, **kwargs):
    """Run a synchronous function in the thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


async def run_parallel(tasks: list[tuple[Callable, dict]]) -> list[dict]:
    """Run multiple agent nodes in parallel.

    Args:
        tasks: List of (agent_fn, state) tuples

    Returns:
        List of result dicts from each agent node
    """
    logger.info("Parallel executor: launching %d tasks", len(tasks))

    async def _run_one(fn, state, idx):
        try:
            result = await run_in_thread(fn, state)
            logger.info("Parallel task %d completed", idx)
            return result
        except Exception as e:
            logger.warning("Parallel task %d failed: %s", idx, e)
            return {"agent_history": [f"Parallel task {idx} failed: {e}"]}

    results = await asyncio.gather(
        *[_run_one(fn, state, i) for i, (fn, state) in enumerate(tasks)]
    )

    logger.info("Parallel executor: %d/%d tasks completed", len(results), len(tasks))
    return list(results)


async def run_llm_async(calls: list[tuple]) -> list:
    """Run multiple LLM invoke() calls in parallel via thread pool.

    Args:
        calls: List of (llm, messages) or (llm, messages, kwargs) tuples

    Returns:
        List of LLM responses
    """
    logger.info("Parallel LLM: launching %d calls", len(calls))

    async def _invoke(llm, messages, kwargs, idx):
        try:
            result = await run_in_thread(llm.invoke, messages, **kwargs)
            logger.debug("LLM call %d completed", idx)
            return result
        except Exception as e:
            logger.warning("LLM call %d failed: %s", idx, e)
            return None

    tasks = []
    for i, call in enumerate(calls):
        if len(call) == 2:
            llm, messages = call
            kwargs = {}
        else:
            llm, messages, kwargs = call
        tasks.append(_invoke(llm, messages, kwargs, i))

    results = await asyncio.gather(*tasks)
    successes = sum(1 for r in results if r is not None)
    logger.info("Parallel LLM: %d/%d calls succeeded", successes, len(calls))
    return list(results)


def run_parallel_sync(tasks: list[tuple[Callable, dict]]) -> list[dict]:
    """Synchronous wrapper for run_parallel. Use from non-async contexts.

    Creates or uses an existing event loop to run parallel tasks.
    """
    try:
        loop = asyncio.get_running_loop()
        # Already in async context — can't nest, fall back to sequential
        logger.debug("Already in async context, running sequentially")
        return [fn(state) for fn, state in tasks]
    except RuntimeError:
        pass

    return asyncio.run(run_parallel(tasks))


def parallel_grpo_candidates(
    llm_factory: Callable,
    system_prompt: str,
    user_message: str,
    temperatures: list[float],
) -> list[str]:
    """Generate GRPO candidates in parallel at different temperatures.

    Args:
        llm_factory: Function(temperature) -> LLM instance
        system_prompt: System prompt for all candidates
        user_message: User message for all candidates
        temperatures: List of temperatures to sample at

    Returns:
        List of generated text strings
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    calls = []
    for temp in temperatures:
        llm = llm_factory(temp)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        calls.append((llm, messages))

    try:
        loop = asyncio.get_running_loop()
        # Sequential fallback in async context
        results = []
        for llm, messages in calls:
            results.append(llm.invoke(messages))
        return [r.content if r else "" for r in results]
    except RuntimeError:
        pass

    responses = asyncio.run(run_llm_async(calls))
    return [r.content if r else "" for r in responses]
