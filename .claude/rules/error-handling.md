---
paths: ["jobpulse/**/*.py", "shared/**/*.py"]
description: "Structured error handling for all agent code"
---

# Error Handling Conventions

All errors in agent code MUST return structured error context, not generic strings.

## Required Error Structure

```python
{
    "status": "error",
    "errorCategory": "transient" | "validation" | "permission" | "business",
    "message": "human-readable description",
    "isRetryable": True | False,
    "partialResults": "any data collected before failure" | None,
    "agentName": "which agent failed",
    "attemptedAction": "what was being done"
}
```

## Rules

- NEVER use bare `except: pass` — always log the error with context
- NEVER return generic strings like `f"Error: {e}"` — use DispatchError or AgentError
- Classify errors: timeouts/rate-limits = transient (retryable), auth = permission, bad input = validation
- Propagate partial results when available — don't discard work done before the failure
- Distinguish access failures (needing retry) from valid empty results (successful query, no matches)
