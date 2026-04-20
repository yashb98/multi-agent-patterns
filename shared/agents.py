"""
Agent Node Functions
====================

Each function here is a "node" in the LangGraph graph.
A node is simply: state in → LLM call → partial state update out.

KEY PATTERN:
    def agent_node(state: AgentState) -> dict:
        # 1. READ what you need from state
        # 2. BUILD your prompt (system + user message)
        # 3. CALL the LLM
        # 4. RETURN only the fields you're updating

These functions are PURE — they don't know about the graph topology.
They don't know if they're in a hierarchy, a debate, or a swarm.
The orchestration pattern imports these and wires them differently.

WHY THIS SEPARATION MATTERS:
- Same agent logic, three different architectures
- Easy to test agents in isolation
- Easy to swap an agent (e.g., upgrade Writer) without touching wiring
"""

import json
import os
import re
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

# Multi-provider imports — graceful degrade if packages not installed
try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None  # type: ignore[misc,assignment]

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None  # type: ignore[misc,assignment]

from shared.state import AgentState
from shared.prompts import RESEARCHER_PROMPT, WRITER_PROMPT, REVIEWER_PROMPT
from shared.logging_config import get_logger
from shared.prompt_defense import sanitize_user_input

# Re-export from split modules for backward compatibility
from shared.cost_tracker import (  # noqa: F401
    MODEL_COSTS,
    estimate_cost,
    track_llm_usage,
    compute_cost_summary,
)
from shared.context_compression import (  # noqa: F401
    MAX_RESEARCH_CHARS,
    compress_research_notes,
    count_tokens,
    truncate_messages_to_fit,
    count_messages_tokens,
)
from shared.agentic_loop import (  # noqa: F401
    AgentError,
    AGENT_TOOLS,
    register_agent_tool,
)
from shared.llm_retry import resilient_llm_call  # noqa: F401
from shared.streaming import smart_llm_call  # noqa: F401

logger = get_logger(__name__)


# ─── LLM PROVIDER CONFIG ───────────────────────────────────────
#
# LLM_PROVIDER=auto    → Probe Ollama; use local if reachable, else cloud (DEFAULT)
# LLM_PROVIDER=local   → Force Ollama (gemma4:31b via OpenAI-compatible API)
# LLM_PROVIDER=openai  → Force OpenAI API
#
# When provider resolves to "local", get_llm() and get_openai_client()
# point at Ollama's OpenAI-compatible endpoint (http://localhost:11434/v1).
# When falling back to cloud, older/cheaper models are used automatically
# (gpt-4o-mini instead of gpt-4.1-mini) to minimise cost.

_OLLAMA_HOST = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_BASE_URL = _OLLAMA_HOST.rstrip("/") + "/v1"
_LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "gemma4:31b")

# Cloud fallback model map: current → older/cheaper equivalent
_FALLBACK_MODELS = {
    "gpt-5-mini": "gpt-4o-mini",
    "gpt-4.1-mini": "gpt-4o-mini",
    "gpt-4.1": "gpt-4o",
    "gpt-4.1-nano": "gpt-4o-mini",
}

# Multi-provider model defaults
_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

# Provider fallback chain (when LLM_PROVIDER=auto and cloud)
# Order: openai → anthropic → gemini
_PROVIDER_FALLBACK_CHAIN = ["openai", "anthropic", "gemini"]


def _probe_ollama() -> bool:
    """Check if Ollama is reachable (fast 2s timeout)."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            _OLLAMA_HOST.rstrip("/") + "/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _resolve_provider() -> str:
    """Resolve LLM_PROVIDER, auto-detecting Ollama when set to 'auto'."""
    explicit = os.environ.get("LLM_PROVIDER", "auto").lower()
    if explicit in ("local", "openai"):
        return explicit
    # auto: probe Ollama
    if _probe_ollama():
        logger.info("Ollama detected at %s — using local LLM (%s)", _OLLAMA_HOST, _LOCAL_MODEL)
        return "local"
    logger.info("Ollama not reachable — falling back to cloud (older models)")
    return "openai"


_LLM_PROVIDER: str | None = None
_is_local: bool | None = None
_use_fallback_models: bool | None = None


def _ensure_provider():
    global _LLM_PROVIDER, _is_local, _use_fallback_models
    if _LLM_PROVIDER is None:
        _LLM_PROVIDER = _resolve_provider()
        _is_local = _LLM_PROVIDER == "local"
        _use_fallback_models = not _is_local and os.environ.get("LLM_PROVIDER", "auto").lower() == "auto"


def is_local_llm() -> bool:
    """True when LLM_PROVIDER=local (Ollama). Use for conditional limits."""
    _ensure_provider()
    return _is_local


def get_model_name(default: str = "gpt-5-mini") -> str:
    """Return the effective model name for raw OpenAI SDK calls.

    When LLM_PROVIDER=local, returns LOCAL_LLM_MODEL (e.g. gemma4:31b).
    When falling back to cloud via auto-detection, maps to older/cheaper
    models (e.g. gpt-5-mini → gpt-4o-mini).
    When LLM_PROVIDER=openai (explicit), returns the provided default as-is.
    """
    _ensure_provider()
    if _is_local:
        return _LOCAL_MODEL
    if _use_fallback_models:
        return _FALLBACK_MODELS.get(default, default)
    return default


def _make_openai_llm(temperature: float, model: str, timeout: float, max_tokens: int) -> ChatOpenAI:
    """Build an OpenAI LLM instance."""
    effective_model = _FALLBACK_MODELS.get(model, model) if _use_fallback_models else model
    effective_temp = temperature if not effective_model.startswith("gpt-5") else 1
    return ChatOpenAI(
        model=effective_model,
        temperature=effective_temp,
        request_timeout=timeout,
        max_tokens=max_tokens,
    )


def _make_local_llm(temperature: float, model: str, timeout: float, max_tokens: int) -> ChatOpenAI:
    """Build an Ollama (OpenAI-compatible) LLM instance."""
    effective_model = _LOCAL_MODEL if model == "gpt-5-mini" else model
    return ChatOpenAI(
        model=effective_model,
        temperature=temperature,
        request_timeout=timeout,
        max_tokens=max_tokens,
        openai_api_base=_OLLAMA_BASE_URL,
        openai_api_key="ollama",
    )


def _make_anthropic_llm(temperature: float, timeout: float, max_tokens: int):
    """Build an Anthropic LLM instance if API key is available."""
    if ChatAnthropic is None:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    return ChatAnthropic(
        model=_ANTHROPIC_MODEL,
        temperature=temperature,
        timeout=int(timeout),
        max_tokens=max_tokens,
        api_key=api_key,
    )


def _make_gemini_llm(temperature: float, timeout: float, max_tokens: int):
    """Build a Gemini LLM instance if API key is available."""
    if ChatGoogleGenerativeAI is None:
        return None
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return None
    return ChatGoogleGenerativeAI(
        model=_GEMINI_MODEL,
        temperature=temperature,
        timeout=int(timeout),
        max_output_tokens=max_tokens,
        google_api_key=api_key,
    )


class _MultiProviderLLM:
    """LangChain-compatible LLM wrapper that transparently tries providers in order.

    When the primary provider fails, automatically falls back to the next
    provider in the chain. All providers must be LangChain BaseChatModel instances.
    """

    def __init__(self, providers: list):
        self._providers = providers
        self._provider_names = [getattr(p, "model", getattr(p, "model_name", str(p.__class__.__name__))) for p in providers]

    def _try_providers(self, method_name: str, messages, **kwargs):
        errors = []
        for i, (llm, name) in enumerate(zip(self._providers, self._provider_names)):
            try:
                method = getattr(llm, method_name)
                return method(messages, **kwargs)
            except Exception as e:
                errors.append((name, str(e)[:120]))
                if i < len(self._providers) - 1:
                    logger.warning(
                        "LLM provider %s failed (%s). Falling back to %s...",
                        name, str(e)[:80], self._provider_names[i + 1],
                    )
        # All failed
        error_msg = "; ".join(f"{name}: {err}" for name, err in errors)
        raise RuntimeError(f"All LLM providers failed: {error_msg}")

    def invoke(self, messages, **kwargs):
        return self._try_providers("invoke", messages, **kwargs)

    def stream(self, messages, **kwargs):
        return self._try_providers("stream", messages, **kwargs)

    def bind(self, **kwargs):
        """Return a new _MultiProviderLLM with bound kwargs on each provider."""
        bound_providers = []
        for p in self._providers:
            if hasattr(p, "bind"):
                bound_providers.append(p.bind(**kwargs))
            else:
                bound_providers.append(p)
        return _MultiProviderLLM(bound_providers)


def get_llm(temperature: float = 0.7, model: str = "gpt-5-mini",
            timeout: float = 30.0, max_tokens: int = 4096):
    """
    Factory function for LLM instances with multi-provider fallback.

    WHY a factory? Because different agents may need different configs:
    - Researcher: low temperature (0.3) for factual accuracy
    - Writer: medium temperature (0.7) for creative prose
    - Reviewer: low temperature (0.2) for consistent scoring

    When LLM_PROVIDER=local, routes to Ollama's OpenAI-compatible API.
    The ``model`` parameter is overridden by LOCAL_LLM_MODEL unless the
    caller explicitly passes a model name that doesn't match the default.

    When falling back to cloud via auto-detection, builds a provider chain:
      OpenAI → Anthropic → Gemini (whichever have API keys configured).

    timeout: seconds before the HTTP request is aborted (default 30s).
    """
    _ensure_provider()
    if _is_local:
        return _make_local_llm(temperature, model, timeout, max_tokens)

    # Build provider chain
    chain: list = []
    chain.append(_make_openai_llm(temperature, model, timeout, max_tokens))

    for provider_name in _PROVIDER_FALLBACK_CHAIN[1:]:
        if provider_name == "anthropic":
            llm = _make_anthropic_llm(temperature, timeout, max_tokens)
        elif provider_name == "gemini":
            llm = _make_gemini_llm(temperature, timeout, max_tokens)
        else:
            continue
        if llm is not None:
            chain.append(llm)

    if len(chain) == 1:
        return chain[0]
    return _MultiProviderLLM(chain)


def get_openai_client(timeout: float = 30.0) -> OpenAI:
    """Factory for raw OpenAI SDK client instances.

    Centralizes all direct ``OpenAI()`` calls (previously 27 scattered copies).
    When LLM_PROVIDER=local, points at Ollama's OpenAI-compatible endpoint.
    """
    _ensure_provider()
    if _LLM_PROVIDER == "local":
        return OpenAI(
            api_key="ollama",
            base_url=_OLLAMA_BASE_URL,
            timeout=timeout,
        )
    return OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        timeout=timeout,
    )


# ─── AGENT NODE: RESEARCHER ─────────────────────────────────────

def researcher_node(state: AgentState) -> dict:
    """
    The Researcher agent gathers information on the topic.

    READS: topic, review_feedback (if revision cycle)
    WRITES: research_notes (appended), agent_history (appended)
    """
    logger.info("=" * 50)
    logger.info("RESEARCHER AGENT - Iteration %d", state.get('iteration', 0))
    logger.info("=" * 50)

    topic = state["topic"]
    safe_topic = sanitize_user_input(topic, source="topic")
    feedback = state.get("review_feedback", "")

    if feedback and state.get("iteration", 0) > 0:
        user_msg = f"""Topic: {safe_topic}

PREVIOUS REVIEW FEEDBACK (address these gaps):
{feedback}

Conduct ADDITIONAL research specifically targeting the gaps identified above.
Focus on finding information that was missing from the previous research."""
    else:
        user_msg = f"""Topic: {safe_topic}

Conduct comprehensive research on this topic. Gather facts, technical
details, current trends, and notable perspectives."""

    llm = get_llm(temperature=0.3)
    response = smart_llm_call(llm, [
        SystemMessage(content=RESEARCHER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    research = response.content
    usage = track_llm_usage(response, "researcher")
    logger.info("Research produced: %d characters ($%.4f)", len(research), usage["cost_usd"])

    return {
        "research_notes": [research],
        "current_agent": "researcher",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Researcher completed"],
        "token_usage": [usage],
    }


# ─── AGENT NODE: WRITER ─────────────────────────────────────────

def writer_node(state: AgentState) -> dict:
    """
    The Writer agent drafts or revises the blog article.

    READS: topic, research_notes, review_feedback (if revision), draft
    WRITES: draft (replaced), iteration (incremented), agent_history
    """
    logger.info("=" * 50)
    logger.info("WRITER AGENT - Iteration %d", state.get('iteration', 0))
    logger.info("=" * 50)

    topic = state["topic"]
    safe_topic = sanitize_user_input(topic, source="topic")
    raw_notes = state.get("research_notes", [])
    compressed_notes = compress_research_notes(raw_notes)
    research = "\n\n---\n\n".join(compressed_notes)
    feedback = state.get("review_feedback", "")
    current_draft = state.get("draft", "")
    iteration = state.get("iteration", 0)

    fact_notes = state.get("fact_revision_notes")
    if fact_notes:
        feedback = f"{feedback}\n\n{fact_notes}" if feedback else fact_notes

    if feedback and current_draft:
        user_msg = f"""Topic: {safe_topic}

RESEARCH NOTES:
{research}

YOUR PREVIOUS DRAFT:
{current_draft}

REVIEWER FEEDBACK TO ADDRESS:
{feedback}

Revise the draft to address EACH piece of feedback. Maintain what was
good, fix what was flagged. Produce the COMPLETE revised article."""
    else:
        user_msg = f"""Topic: {safe_topic}

RESEARCH NOTES:
{research}

Write a complete, polished technical blog article based on these research notes."""

    llm = get_llm(temperature=0.7)
    response = smart_llm_call(llm, [
        SystemMessage(content=WRITER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    draft = response.content
    usage = track_llm_usage(response, "writer")
    logger.info("Draft produced: %d characters, ~%d words ($%.4f)", len(draft), len(draft.split()), usage["cost_usd"])

    return {
        "draft": draft,
        "iteration": iteration + 1,
        "current_agent": "writer",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Writer completed (iteration {iteration + 1})"],
        "token_usage": [usage],
    }


# ─── AGENT NODE: REVIEWER ───────────────────────────────────────

def reviewer_node(state: AgentState) -> dict:
    """
    The Reviewer agent evaluates the draft and produces structured feedback.

    READS: draft, topic, research_notes
    WRITES: review_feedback, review_score, review_passed, agent_history
    """
    logger.info("=" * 50)
    logger.info("REVIEWER AGENT - Evaluating draft")
    logger.info("=" * 50)

    draft = state.get("draft", "")
    topic = state["topic"]
    safe_topic = sanitize_user_input(topic, source="topic")
    research = "\n\n".join(state.get("research_notes", []))

    # Token-aware truncation instead of hardcoded char limit
    research_tokens = count_tokens(research)
    if research_tokens > 1500:
        # Truncate to ~1500 tokens worth of research context
        encoder = None
        try:
            from shared.context_compression import get_token_encoder
            encoder = get_token_encoder("gpt-4.1-mini")
        except (ImportError, Exception) as e:
            logger.debug("Token encoder unavailable, skipping truncation: %s", e)
        if encoder:
            tokens = encoder.encode(research)
            research = encoder.decode(tokens[:1500]) + "\n\n[...truncated for context budget]"
        else:
            research = research[:6000] + "\n\n[...truncated for context budget]"

    user_msg = f"""Evaluate this blog article draft.

ORIGINAL TOPIC: {safe_topic}

RESEARCH NOTES (for accuracy checking):
{research}

ARTICLE DRAFT TO REVIEW:
{draft}

Evaluate against all criteria and respond with ONLY the JSON structure
specified in your instructions."""

    llm = get_llm(
        model="gpt-5-mini",
        temperature=0.2,
        timeout=30.0,
    )
    response = smart_llm_call(llm, [
        SystemMessage(content=REVIEWER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    usage = track_llm_usage(response, "reviewer")
    raw = response.content.strip()

    try:
        review = json.loads(raw)
        score = float(review.get("overall_score", 0))
        passed = review.get("passed", False)
        feedback_text = json.dumps(review, indent=2)

        logger.info("Score: %s/10 | Passed: %s", score, passed)
        if not passed:
            improvements = review.get("improvements_needed", [])
            for imp in improvements[:3]:
                logger.info("   -> %s", imp)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Could not parse review JSON: %s — raw: %s", e, raw[:200])
        score = 5.0
        passed = False
        feedback_text = json.dumps({
            "overall_score": 5.0,
            "passed": False,
            "parse_error": str(e),
            "raw_response": raw[:500],
            "improvements_needed": ["Review JSON could not be parsed — re-review needed"],
            "summary": "Automated review failed to produce valid JSON. Manual review recommended."
        }, indent=2)

    return {
        "review_feedback": feedback_text,
        "review_score": score,
        "review_passed": passed,
        "current_agent": "reviewer",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Reviewer: score={score}, passed={passed}"],
        "token_usage": [usage],
    }


# ─── AGENT NODE: RISK-AWARE REVIEWER ───────────────────────────────

def risk_aware_reviewer_node(state: AgentState) -> dict:
    """
    Reviewer that uses code_graph risk scoring to prioritize inspection.

    If the draft contains code blocks, parses them through CodeGraph,
    computes risk scores, and prepends a risk-prioritized checklist to
    the standard review prompt. Falls back to standard reviewer_node
    when no code blocks are found.
    """
    draft = state.get("draft", "")

    code_blocks = _extract_code_blocks(draft)
    if not code_blocks:
        return reviewer_node(state)

    logger.info("RISK-AWARE REVIEWER - Analysing %d code blocks", len(code_blocks))

    try:
        from shared.code_graph import CodeGraph
        import tempfile
        import os
        graph = CodeGraph(":memory:")

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, (filename, code) in enumerate(code_blocks):
                fpath = os.path.join(tmpdir, filename or f"block_{i}.py")
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "w") as f:
                    f.write(code)
            graph.index_directory(tmpdir)

        risk_report = graph.risk_report(top_n=10)
        graph.close()
    except Exception as e:
        logger.warning("Risk analysis failed, falling back to standard review: %s", e)
        return reviewer_node(state)

    if not risk_report:
        return reviewer_node(state)

    risk_lines = ["HIGH-PRIORITY REVIEW TARGETS (risk-scored by code analysis):"]
    for item in risk_report:
        risk_lines.append(
            f"  - {item['name']} ({item['file_path']}:{item['line_start']}) "
            f"risk={item['risk_score']:.2f}"
        )
    risk_context = "\n".join(risk_lines)

    logger.info("Risk report: %d high-risk functions identified", len(risk_report))

    augmented_state = dict(state)
    augmented_state["draft"] = f"[CODE RISK ANALYSIS]\n{risk_context}\n\n{draft}"
    result = reviewer_node(augmented_state)

    result["agent_history"] = [
        f"Risk-aware review: {len(risk_report)} high-risk functions flagged"
    ] + result.get("agent_history", [])

    return result


def _extract_code_blocks(text: str) -> list[tuple]:
    """Extract (filename, code) tuples from markdown code blocks."""
    blocks = []
    pattern = re.compile(r"```(?:python|(\S+\.py))?\s*\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        filename = match.group(1) or "code.py"
        code = match.group(2)
        if code.strip():
            blocks.append((filename, code))
    return blocks


# ─── AGENT NODE: FACT CHECKER ──────────────────────────────────────

def fact_check_node(state: AgentState) -> dict:
    """
    The Fact Checker agent extracts claims and verifies them against sources.

    READS: draft, topic, research_notes
    WRITES: extracted_claims, claim_verifications, accuracy_score, accuracy_passed, fact_revision_notes, agent_history
    """
    from shared.fact_checker import (
        extract_claims, verify_claims, compute_accuracy_score, generate_revision_notes
    )

    logger.info("=" * 50)
    logger.info("FACT CHECKER AGENT - Verifying claims")
    logger.info("=" * 50)

    draft = state.get("draft", "")
    topic = state["topic"]
    safe_topic = sanitize_user_input(topic, source="topic")
    research = state.get("research_notes", [])

    claims = extract_claims(draft, safe_topic)
    logger.info("Extracted %d claims from draft", len(claims))

    verifications = verify_claims(claims, research, web_search=True)
    logger.info("Verified %d claims", len(verifications))

    score = compute_accuracy_score(verifications)
    passed = score >= 9.5
    logger.info("Accuracy score: %.1f/10 | Passed (>=9.5): %s", score, passed)

    # ── learn_fact: store verified facts in semantic memory ──
    try:
        from shared.memory_layer import get_shared_memory_manager
        _mem = get_shared_memory_manager()
        domain = topic.split()[0].lower() if topic else "general"
        for v in verifications:
            status = v.get("status", "") if isinstance(v, dict) else getattr(v, "status", "")
            claim_text = v.get("claim", "") if isinstance(v, dict) else getattr(v, "claim", "")
            if status == "VERIFIED" and claim_text:
                _mem.learn_fact(domain, claim_text[:300])
    except Exception as _e:
        logger.debug("learn_fact skipped: %s", _e)

    revision_notes = generate_revision_notes(verifications) if not passed else None

    return {
        "extracted_claims": claims,
        "claim_verifications": verifications,
        "accuracy_score": score,
        "accuracy_passed": passed,
        "fact_revision_notes": revision_notes,
        "current_agent": "fact_checker",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] Fact Checker: accuracy={score:.1f}, passed={passed}"]
    }


# ─── UTILITY: STATE INITIALISER ─────────────────────────────────

def create_initial_state(topic: str) -> AgentState:
    """Creates a clean initial state for any pattern."""
    return {
        "topic": topic,
        "research_notes": [],
        "draft": "",
        "review_feedback": "",
        "review_score": 0.0,
        "review_passed": False,
        "iteration": 0,
        "current_agent": "",
        "agent_history": [f"[{datetime.now().strftime('%H:%M:%S')}] System initialised with topic: {topic}"],
        "pending_tasks": [],
        "final_output": "",
        "extracted_claims": [],
        "claim_verifications": [],
        "accuracy_score": 0.0,
        "accuracy_passed": False,
        "fact_revision_notes": None,
        "token_usage": [],
        "total_cost_usd": 0.0,
    }
