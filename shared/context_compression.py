"""Context Compression — proactive token counting and research note compression.

Prevents context window overflow via:
1. tiktoken-based token counting before LLM calls
2. Research note compression across iterations
3. Message list truncation when approaching limits
"""

from shared.logging_config import get_logger

logger = get_logger(__name__)

MAX_RESEARCH_CHARS = 8000  # Total budget for research_notes context

# Model context window limits (tokens)
MODEL_CONTEXT_LIMITS = {
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1": 1_000_000,
    "gpt-5o-mini": 128_000,
    "o3-mini": 128_000,
}

# Reserve tokens for the response
RESPONSE_RESERVE = 4_096

# Cache the encoder to avoid repeated loading
_encoder_cache = {}


def get_token_encoder(model: str = "gpt-5o-mini"):
    """Get a tiktoken encoder for the given model. Cached per model."""
    if model not in _encoder_cache:
        try:
            import tiktoken
            try:
                _encoder_cache[model] = tiktoken.encoding_for_model(model)
            except KeyError:
                # Unknown model — fall back to cl100k_base (GPT-4 family)
                _encoder_cache[model] = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.debug("tiktoken not installed — using char/4 estimate")
            return None
    return _encoder_cache[model]


def count_tokens(text: str, model: str = "gpt-5o-mini") -> int:
    """Count tokens in a string using tiktoken. Falls back to char/4 estimate."""
    encoder = get_token_encoder(model)
    if encoder is None:
        return len(text) // 4  # Rough estimate: ~4 chars per token
    return len(encoder.encode(text))


def count_messages_tokens(messages: list[dict], model: str = "gpt-5o-mini") -> int:
    """Count total tokens across a list of chat messages.

    Accounts for message overhead (~4 tokens per message for role/formatting).
    """
    total = 0
    for msg in messages:
        total += 4  # Message overhead (role, separators)
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            # Multi-part content (vision, etc.)
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += count_tokens(part["text"], model)
    total += 2  # Priming tokens
    return total


def get_context_limit(model: str = "gpt-5o-mini") -> int:
    """Get the context window limit for a model in tokens."""
    for prefix, limit in MODEL_CONTEXT_LIMITS.items():
        if model.startswith(prefix):
            return limit
    return 128_000  # Conservative default


def truncate_messages_to_fit(
    messages: list[dict],
    model: str = "gpt-5o-mini",
    reserve: int = RESPONSE_RESERVE,
) -> list[dict]:
    """Truncate older messages if total tokens would exceed context limit.

    Strategy:
    - Always keep: system message (first) + latest user message (last)
    - Trim from the middle: oldest assistant/tool messages first
    - Adds a [CONTEXT TRUNCATED] marker where messages were removed

    Returns the (possibly truncated) message list.
    """
    limit = get_context_limit(model) - reserve
    current_tokens = count_messages_tokens(messages, model)

    if current_tokens <= limit:
        return messages

    logger.warning(
        "Context overflow: %d tokens > %d limit. Truncating messages.",
        current_tokens, limit,
    )

    # Keep system (index 0) and last message, trim from middle
    if len(messages) <= 2:
        # Can't trim further — truncate the content of the last message
        last = messages[-1].copy()
        content = last.get("content", "")
        if isinstance(content, str):
            # Binary search for the right truncation point
            encoder = get_token_encoder(model)
            if encoder:
                tokens = encoder.encode(content)
                available = limit - count_messages_tokens(messages[:-1], model) - 4
                if available > 100:
                    last["content"] = encoder.decode(tokens[:available]) + "\n\n[TRUNCATED]"
            else:
                available_chars = (limit - count_messages_tokens(messages[:-1], model)) * 4
                last["content"] = content[:max(400, available_chars)] + "\n\n[TRUNCATED]"
        return messages[:-1] + [last]

    # Progressive removal from middle
    system = messages[0]
    latest = messages[-1]
    middle = list(messages[1:-1])

    # Remove oldest middle messages until we fit
    while middle and count_messages_tokens([system] + middle + [latest], model) > limit:
        removed = middle.pop(0)
        logger.debug("Truncated message: role=%s, %d chars",
                      removed.get("role", "?"), len(str(removed.get("content", ""))))

    # Insert truncation marker
    if len(middle) < len(messages) - 2:
        removed_count = len(messages) - 2 - len(middle)
        marker = {"role": "system", "content": f"[{removed_count} earlier messages truncated to fit context window]"}
        result = [system, marker] + middle + [latest]
    else:
        result = [system] + middle + [latest]

    new_tokens = count_messages_tokens(result, model)
    logger.info("Truncated: %d → %d tokens (%d messages removed)",
                current_tokens, new_tokens, len(messages) - len(result))

    return result


def check_context_budget(
    messages: list[dict],
    model: str = "gpt-5o-mini",
    reserve: int = RESPONSE_RESERVE,
) -> dict:
    """Check context budget before sending to LLM.

    Returns:
        tokens_used: int
        tokens_limit: int
        tokens_available: int
        over_budget: bool
        utilization_pct: float (0-100)
    """
    limit = get_context_limit(model)
    used = count_messages_tokens(messages, model)
    available = limit - reserve - used

    return {
        "tokens_used": used,
        "tokens_limit": limit,
        "tokens_available": max(0, available),
        "over_budget": available < 0,
        "utilization_pct": round(used / limit * 100, 1),
    }


def compress_research_notes(notes: list[str]) -> list[str]:
    """Compress older research notes if total size exceeds budget.

    Strategy:
    - Keep the latest note verbatim (most relevant)
    - Summarise older notes into condensed bullet points
    - Total stays under MAX_RESEARCH_CHARS
    """
    if not notes:
        return notes

    total_chars = sum(len(n) for n in notes)
    if total_chars <= MAX_RESEARCH_CHARS:
        return notes

    if len(notes) <= 1:
        return [notes[0][:MAX_RESEARCH_CHARS] + "\n\n[TRUNCATED — original was longer]"]

    latest = notes[-1]
    older = notes[:-1]

    older_budget = max(500, MAX_RESEARCH_CHARS - len(latest))

    per_note_budget = older_budget // len(older)
    compressed = []
    for note in older:
        if len(note) <= per_note_budget:
            compressed.append(note)
        else:
            paragraphs = [p.strip() for p in note.split("\n\n") if p.strip()]
            if len(paragraphs) <= 2:
                compressed.append(note[:per_note_budget] + "...")
            else:
                summary = paragraphs[0] + "\n\n[...compressed...]\n\n" + paragraphs[-1]
                compressed.append(summary[:per_note_budget])

    logger.info("Compressed research notes: %d chars → %d chars (%d notes)",
                total_chars, sum(len(n) for n in compressed) + len(latest), len(notes))

    return compressed + [latest]
