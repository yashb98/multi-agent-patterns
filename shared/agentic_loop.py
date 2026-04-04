"""Agentic Loop — stop_reason-based tool execution loop.

Implements the canonical agentic loop pattern:
1. Send request with tools + system prompt
2. Inspect stop_reason: "tool_use" → execute tools, append results, loop
3. stop_reason "end_turn" → return final content
4. Max iterations is a SAFETY VALVE, not the primary stopping mechanism
"""

import json
import os
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── STRUCTURED ERROR RESPONSE ───────────────────────────────────

class AgentError:
    """Structured error object for agent failures."""

    def __init__(self, error_category: str, message: str,
                 is_retryable: bool = False, partial_results: Any = None,
                 agent_name: str = "", attempted_action: str = ""):
        self.error_category = error_category   # transient | validation | permission | business
        self.message = message
        self.is_retryable = is_retryable
        self.partial_results = partial_results
        self.agent_name = agent_name
        self.attempted_action = attempted_action

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "errorCategory": self.error_category,
            "message": self.message,
            "isRetryable": self.is_retryable,
            "partialResults": self.partial_results,
            "agentName": self.agent_name,
            "attemptedAction": self.attempted_action,
        }

    def __str__(self) -> str:
        retry = " (retryable)" if self.is_retryable else ""
        return f"[{self.error_category}]{retry} {self.agent_name}: {self.message}"


# ─── TOOL REGISTRY ───────────────────────────────────────────────

AGENT_TOOLS = {}


def register_agent_tool(name: str, description: str, func: callable):
    """Register a tool that agents can invoke during agentic loops."""
    AGENT_TOOLS[name] = {
        "name": name,
        "description": description,
        "func": func,
    }


# ─── AGENTIC LOOP ────────────────────────────────────────────────

def run_agentic_loop(
    system_prompt: str,
    user_message: str,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_iterations: int = 10,
    model: str = "gpt-5o-mini",
    timeout: float = 30.0,
) -> dict:
    """
    Run an agentic loop with proper stop_reason handling.

    Returns dict with:
        - content: str (final text output)
        - tool_calls_made: list[dict] (audit trail of tool invocations)
        - iterations: int (how many loop passes)
        - stop_reason: str (why the loop ended: "end_turn" | "max_iterations")
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=timeout)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Build OpenAI-format tool definitions
    openai_tools = None
    tool_map = {}
    if tools:
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            })
            tool_map[t["name"]] = t["func"]

    tool_calls_made = []
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Proactive context truncation — prevent overflow before it crashes
        from shared.context_compression import truncate_messages_to_fit
        messages = truncate_messages_to_fit(messages, model=model)

        kwargs = {"model": model, "messages": messages, "temperature": temperature}
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # ── Check stop_reason (finish_reason in OpenAI) ──
        if choice.finish_reason == "tool_calls":
            # Model wants to call tools — execute them and loop
            assistant_msg = choice.message
            messages.append(assistant_msg.model_dump())

            for tool_call in assistant_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

                logger.info("Agentic loop: tool_call %s(%s)", fn_name, list(fn_args.keys()))

                # Execute the tool
                if fn_name in tool_map:
                    try:
                        result = tool_map[fn_name](**fn_args)
                        result_str = json.dumps(result) if not isinstance(result, str) else result
                    except Exception as e:
                        result_str = json.dumps(AgentError(
                            error_category="transient",
                            message=str(e),
                            is_retryable=True,
                            agent_name=fn_name,
                            attempted_action=f"{fn_name}({fn_args})",
                        ).to_dict())
                else:
                    result_str = json.dumps(AgentError(
                        error_category="validation",
                        message=f"Unknown tool: {fn_name}",
                        is_retryable=False,
                    ).to_dict())

                tool_calls_made.append({
                    "tool": fn_name,
                    "args": fn_args,
                    "result": result_str[:500],
                    "iteration": iteration,
                })

                # Append tool result to conversation for next iteration
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })

        elif choice.finish_reason in ("stop", "end_turn"):
            # Model is done — return the final content
            return {
                "content": choice.message.content or "",
                "tool_calls_made": tool_calls_made,
                "iterations": iteration,
                "stop_reason": "end_turn",
            }
        else:
            # Unexpected finish_reason (length, content_filter, etc.)
            logger.warning("Agentic loop: unexpected finish_reason=%s", choice.finish_reason)
            return {
                "content": choice.message.content or "",
                "tool_calls_made": tool_calls_made,
                "iterations": iteration,
                "stop_reason": choice.finish_reason or "unknown",
            }

    # Safety valve: max iterations reached
    logger.warning("Agentic loop: max_iterations (%d) reached", max_iterations)
    return {
        "content": messages[-1].get("content", "") if isinstance(messages[-1], dict) else "",
        "tool_calls_made": tool_calls_made,
        "iterations": iteration,
        "stop_reason": "max_iterations",
    }
