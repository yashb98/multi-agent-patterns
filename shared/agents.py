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
from shared.governance._output_sanitizer import sanitize_agent_output

# Re-export from split modules for backward compatibility
from shared.cost_tracker import (  # noqa: F401
    MODEL_COSTS,
    estimate_cost,
    record_llm_usage,
    record_openai_usage,
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
_LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen3.6:35b")

# ── Kimi (Moonshot) — mandatory cloud provider ─────────────────────
# OpenAI key is exhausted (per the 2026-05-09 plan); every cloud LLM
# call goes through Kimi via its OpenAI-compatible endpoint. When
# ``KimiAI_API_KEY`` is set in the env we wire it into both
# ``get_openai_client`` (raw SDK) and ``_make_openai_llm`` (LangChain
# ChatOpenAI) so existing callers don't need to change. Caller-supplied
# ``model="gpt-5-mini"`` arguments are remapped to Moonshot's default
# (``moonshot-v1-auto``) — gpt-5 is not a Kimi model and would 4xx.
_KIMI_BASE_URL = os.environ.get(
    "KIMI_BASE_URL", "https://api.moonshot.ai/v1",
)
_KIMI_DEFAULT_MODEL = os.environ.get(
    "KIMI_DEFAULT_MODEL", "moonshot-v1-auto",
)


def _kimi_api_key() -> str:
    """Return the configured Kimi key (KimiAI_API_KEY) or empty string.

    Read on every call so tests that monkeypatch the env via
    ``monkeypatch.setenv`` see the change without reloading the module.
    """
    return os.environ.get("KimiAI_API_KEY", "").strip()


def _use_kimi() -> bool:
    """When True, the Kimi key short-circuits any OpenAI fallback —
    Kimi is mandatory."""
    return bool(_kimi_api_key())


def _kimi_remap_model(model: str | None) -> str:
    """Remap an OpenAI-style model name (gpt-5-mini, gpt-4o, …) to a
    Kimi-compatible default. If the model is already a Moonshot/Kimi
    name, leave it alone."""
    if not model:
        return _KIMI_DEFAULT_MODEL
    m = model.lower()
    if m.startswith(("kimi", "moonshot", "k2", "k3")):
        return model
    return _KIMI_DEFAULT_MODEL

# Hard model override — when set, overrides every caller's model= argument
# regardless of LLM_PROVIDER. Lets the user point the existing OpenAI client
# at any OpenAI-compatible provider (Kimi/Moonshot, Together, Anyscale, etc.)
# without code changes: just set OPENAI_BASE_URL + OPENAI_API_KEY +
# LLM_MODEL_OVERRIDE in the env. None when the var is empty/unset, so the
# original caller-supplied model wins.
_MODEL_OVERRIDE = os.environ.get("LLM_MODEL_OVERRIDE", "").strip() or None


# Per-domain model registry — used by ``cognitive_llm_call`` so reasoning
# domains (page-reasoning, field-type analysis, recovery decisions) get
# a reasoning model while content domains (cv_tailor, cover-letter,
# screening-answer text) get a fast non-reasoning model. Saves cost +
# latency on the bulk of calls while keeping reasoning where it pays
# off.
#
# Resolution order (first match wins):
#   1. LLM_MODEL_OVERRIDE  (global, applies to ALL domains)
#   2. LLM_MODEL_FOR_<DOMAIN_UPPERCASE>  (env, per-domain)
#   3. _DOMAIN_MODEL_REGISTRY  (built-in defaults below)
#   4. get_model_name()  (provider-default fallback)
#
# Defaults below assume Kimi/Moonshot (the audit's S8 step-3 verifier).
# OpenAI / Together users can override via the env vars; they'll see
# their provider's models slotted in via _MODEL_OVERRIDE or
# get_model_name's existing fallback chain.
_DOMAIN_MODEL_REGISTRY = {
    # Decision / reasoning domains — chain-of-thought matters.
    # Multi-step / planning calls land here.
    "page_reasoning":         "kimi-k2.6",
    "form_recovery":          "kimi-k2.6",
    "form_navigation":        "kimi-k2.6",
    "cv_scrutiny":            "kimi-k2.6",
    # Bulk-classification with closed enums — non-reasoning is faster and
    # the few-shot examples in the prompt give the model deterministic
    # rules to follow. Tried K2.6 reasoning in cache-llm Step C live test:
    # 44-field input timed out at 180 s because each field's reasoning
    # chain compounds. v1-auto handles 44 fields in ~3 s with the same
    # few-shot prompt scaffolding.
    "field_type_analysis":    "moonshot-v1-auto",
    # Content domains — speed wins over reasoning.
    "cv_tailoring":           "moonshot-v1-auto",
    "cover_letter":           "moonshot-v1-auto",
    "screening_answers":      "moonshot-v1-auto",
    "screening_decomposition": "moonshot-v1-auto",
    "email_classification":   "moonshot-v1-auto",
    "skill_extraction":       "moonshot-v1-auto",
    "strategy_reflection":    "moonshot-v1-auto",
    "form_field_mapping":     "moonshot-v1-auto",
}


def _model_for_domain(domain: str | None) -> str | None:
    """Resolve the model for a cognitive-domain call.

    Honours ``LLM_MODEL_OVERRIDE`` first (global), then
    ``LLM_MODEL_FOR_<DOMAIN>`` env vars, then the built-in registry,
    then None (caller falls back to ``get_model_name()``).
    """
    if _MODEL_OVERRIDE:
        return _MODEL_OVERRIDE
    if not domain:
        return None
    env_key = f"LLM_MODEL_FOR_{domain.upper().replace('-', '_')}"
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return env_val
    return _DOMAIN_MODEL_REGISTRY.get(domain)

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
    """Check if Ollama is reachable AND the target model is loaded."""
    import json as _json
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            _OLLAMA_HOST.rstrip("/") + "/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = _json.loads(resp.read())
        models = [m.get("name", "") for m in data.get("models", [])]
        if not models:
            logger.info("Ollama reachable but no models loaded")
            return False
        target = _LOCAL_MODEL.split(":")[0]
        if not any(target in m for m in models):
            logger.info("Ollama reachable but model %s not found (available: %s)", _LOCAL_MODEL, models[:5])
            return False
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _resolve_provider() -> str:
    """Resolve LLM_PROVIDER, auto-detecting Ollama when set to 'auto'."""
    explicit = os.environ.get("LLM_PROVIDER", "auto").lower()
    if explicit in ("local", "openai", "together"):
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

    Resolution order (first match wins):
    1. ``LLM_MODEL_OVERRIDE`` env var — when set, overrides every other
       rule. Used to point any OpenAI-compatible provider (Kimi/Moonshot,
       Together, Anyscale, …) at its own model without editing every
       caller's hardcoded model= argument.
    2. ``LLM_PROVIDER=local`` → returns ``LOCAL_LLM_MODEL`` (e.g. qwen3:32b).
    3. ``LLM_PROVIDER=together`` → returns ``TOGETHER_MODEL`` env or default.
    4. ``LLM_PROVIDER=auto`` falling back to cloud → maps to older/cheaper
       OpenAI models (e.g. gpt-5-mini → gpt-4o-mini).
    5. ``LLM_PROVIDER=openai`` (explicit) → returns the caller default.
    """
    if _MODEL_OVERRIDE:
        return _MODEL_OVERRIDE
    _ensure_provider()
    if _is_local:
        return _LOCAL_MODEL
    if _LLM_PROVIDER == "together":
        return os.environ.get("TOGETHER_MODEL", default if default != "gpt-5-mini" else "Qwen/Qwen3-30B-A3B-Instruct")
    # Kimi (mandatory cloud provider) — remap any OpenAI-style default
    # to the Moonshot equivalent so callers passing model="gpt-5-mini"
    # don't 4xx against the Moonshot endpoint.
    if _use_kimi():
        return _kimi_remap_model(default)
    if _use_fallback_models:
        return _FALLBACK_MODELS.get(default, default)
    return default


def _needs_max_completion_tokens(model: str) -> bool:
    """Return True if model requires max_completion_tokens instead of max_tokens."""
    m = model.lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


def _requires_fixed_temperature(model: str) -> bool:
    """Some models reject any temperature ≠ 1 with a 400. Detect them so
    callers passing temperature=0.4 / 0.7 / 0.2 don't blow up.

    Currently: gpt-5 family (rejects ≠ 1) and Kimi K2.6 / K2.5 (return
    ``invalid temperature: only 1 is allowed for this model``).
    """
    m = model.lower()
    if m.startswith("gpt-5"):
        return True
    if m.startswith("kimi-k2") or m.startswith("kimi-k3"):
        return True
    return False


def _is_reasoning_model(model: str) -> bool:
    """Reasoning models burn tokens on chain-of-thought BEFORE producing
    final content. Detect them so the caller can budget more tokens —
    otherwise the reasoning_content blows the cap and the visible
    ``content`` field comes back empty.

    Currently: o1 / o3 / o4 (OpenAI reasoning), gpt-5 family,
    Kimi K2.* / K3.* (Moonshot reasoning), DeepSeek-R1.
    """
    m = model.lower()
    return (
        m.startswith(("o1", "o3", "o4", "gpt-5"))
        or m.startswith(("kimi-k2", "kimi-k3"))
        or m.startswith("deepseek-r1")
    )


def _max_tokens_for_model(model: str, requested: int) -> int:
    """Bump max_tokens for reasoning models so the chain-of-thought
    can complete AND still leave room for the final content.

    Empirically (probe in S8 step 3): Kimi K2.6 used 675 reasoning
    tokens on a tiny "return JSON" prompt; cv_tailor's full prompts
    push this past the default 2000 budget, leaving content empty.
    16k handles the cv_tailor / page_reasoner working set. Non-reasoning
    callers keep the requested cap so cost stays bounded.
    """
    if _is_reasoning_model(model):
        return max(requested, 16000)
    return requested


def _token_limit_kwargs(model: str, max_tokens: int) -> dict:
    """Return the correct token limit kwarg for the model."""
    if _needs_max_completion_tokens(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _make_openai_llm(temperature: float, model: str, timeout: float, max_tokens: int) -> ChatOpenAI:
    """Build an OpenAI LLM instance.

    When ``LLM_MODEL_OVERRIDE`` is set (Kimi / Together / Anyscale …),
    that wins over every caller-supplied ``model`` and the fallback map.
    Otherwise, when ``KimiAI_API_KEY`` is set, the model is remapped to
    a Kimi-compatible default (``moonshot-v1-auto``) and the client is
    pointed at Moonshot's endpoint — Kimi is mandatory in production.
    Temperature heuristic still defaults to 1 for gpt-5 family — non-OpenAI
    models with a literal ``gpt-5`` prefix would be misclassified, but
    the override path's typical models (kimi-*, together's Qwen, …) don't
    use that prefix so the heuristic is harmless.
    """
    if _MODEL_OVERRIDE:
        effective_model = _MODEL_OVERRIDE
    elif _use_kimi():
        effective_model = _kimi_remap_model(model)
    elif _use_fallback_models:
        effective_model = _FALLBACK_MODELS.get(model, model)
    else:
        effective_model = model
    effective_temp = 1 if _requires_fixed_temperature(effective_model) else temperature
    effective_max_tokens = _max_tokens_for_model(effective_model, max_tokens)
    if _use_kimi() and not _MODEL_OVERRIDE:
        return ChatOpenAI(
            model=effective_model,
            temperature=effective_temp,
            request_timeout=timeout,
            openai_api_base=_KIMI_BASE_URL,
            openai_api_key=_kimi_api_key(),
            **_token_limit_kwargs(effective_model, effective_max_tokens),
        )
    return ChatOpenAI(
        model=effective_model,
        temperature=effective_temp,
        request_timeout=timeout,
        **_token_limit_kwargs(effective_model, effective_max_tokens),
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


def _make_together_llm(temperature: float, model: str, timeout: float, max_tokens: int) -> ChatOpenAI:
    """Build a Together AI LLM via OpenAI-compatible endpoint."""
    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        raise RuntimeError("TOGETHER_API_KEY not set; cannot use LLM_PROVIDER=together")
    effective_model = os.environ.get("TOGETHER_MODEL", model) if model == "gpt-5-mini" else model
    return ChatOpenAI(
        model=effective_model,
        temperature=temperature,
        request_timeout=timeout,
        max_tokens=max_tokens,
        openai_api_base="https://api.together.xyz/v1",
        openai_api_key=api_key,
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
                        extra={
                            "failed_provider": name,
                            "fallback_provider": self._provider_names[i + 1],
                            "error_type": type(e).__name__,
                        },
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


class _InstrumentedLLM:
    """Thin proxy that records LLM usage with run/trajectory context."""

    def __init__(self, llm, model_hint: str | None = None, agent_name: str = "unknown"):
        self._llm = llm
        self._model_hint = model_hint
        self._agent_name = agent_name

    def invoke(self, messages, **kwargs):
        response = self._llm.invoke(messages, **kwargs)
        try:
            record_llm_usage(
                response,
                agent_name=self._agent_name,
                messages=messages,
                model_hint=self._model_hint,
                operation="invoke",
            )
        except Exception as exc:
            logger.debug("LLM usage telemetry skipped: %s", exc)
        return response

    def stream(self, messages, **kwargs):
        return self._llm.stream(messages, **kwargs)

    def bind(self, **kwargs):
        if hasattr(self._llm, "bind"):
            return _InstrumentedLLM(self._llm.bind(**kwargs), model_hint=self._model_hint, agent_name=self._agent_name)
        return self

    def __getattr__(self, name):
        return getattr(self._llm, name)


_LOCAL_TIMEOUT_MULTIPLIER = float(os.environ.get("LOCAL_TIMEOUT_MULTIPLIER", "3.0"))


def get_llm(temperature: float = 0.7, model: str = "gpt-5-mini",
            timeout: float = 30.0, max_tokens: int = 4096,
            agent_name: str = "unknown", force_cloud: bool = False):
    """
    Factory function for LLM instances with multi-provider fallback.

    WHY a factory? Because different agents may need different configs:
    - Researcher: low temperature (0.3) for factual accuracy
    - Writer: medium temperature (0.7) for creative prose
    - Reviewer: low temperature (0.2) for consistent scoring

    When LLM_PROVIDER=local, routes to Ollama's OpenAI-compatible API.
    The ``model`` parameter is overridden by LOCAL_LLM_MODEL unless the
    caller explicitly passes a model name that doesn't match the default.

    When LLM_PROVIDER=together, routes to Together AI's OpenAI-compatible API
    using TOGETHER_MODEL (defaults to Qwen/Qwen3-30B-A3B-Instruct).

    When falling back to cloud via auto-detection, builds a provider chain:
      OpenAI → Anthropic → Gemini (whichever have API keys configured).

    Pass ``force_cloud=True`` to bypass both ``local`` and ``together``
    providers and route directly to the OpenAI provider chain — used by
    callers implementing non-cloud→cloud failover after a provider error.

    timeout: seconds before the HTTP request is aborted (default 30s for
    cloud, auto-scaled by LOCAL_TIMEOUT_MULTIPLIER for local Ollama).
    """
    _ensure_provider()
    if _is_local and not force_cloud:
        local_timeout = timeout * _LOCAL_TIMEOUT_MULTIPLIER
        return _InstrumentedLLM(
            _make_local_llm(temperature, model, local_timeout, max_tokens),
            model_hint=model,
            agent_name=agent_name,
        )

    if _LLM_PROVIDER == "together" and not force_cloud:
        return _InstrumentedLLM(
            _make_together_llm(temperature, model, timeout, max_tokens),
            model_hint=model,
            agent_name=agent_name,
        )

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
        return _InstrumentedLLM(chain[0], model_hint=model, agent_name=agent_name)
    return _InstrumentedLLM(_MultiProviderLLM(chain), model_hint=model, agent_name=agent_name)


def get_openai_client(timeout: float = 180.0) -> OpenAI:
    """Factory for raw OpenAI SDK client instances.

    Centralizes all direct ``OpenAI()`` calls (previously 27 scattered copies).
    When LLM_PROVIDER=local, points at Ollama's OpenAI-compatible endpoint.

    **Cloud provider — Kimi (mandatory).** When ``KimiAI_API_KEY`` is
    set (it is, in production), the client uses Moonshot's
    OpenAI-compatible endpoint and the Kimi key. The OpenAI fallback is
    only reachable when the Kimi key is unset — and we raise if neither
    is present so calls never silently fail on a stale key.

    Default 180s tolerates 32b local models — qwen3:32b (the
    cache-or-llm-audit.md §2.3 Step-0 model) takes 30–60 s on real
    cv_tailor / page-reasoner sized prompts, which the previous 30 s
    default cut off. OpenAI cloud calls finish in <10 s so a 180 s
    timeout still trips on real hangs.
    """
    _ensure_provider()
    if _LLM_PROVIDER == "local":
        return OpenAI(
            api_key="ollama",
            base_url=_OLLAMA_BASE_URL,
            timeout=timeout,
        )
    if _use_kimi():
        return OpenAI(
            api_key=_kimi_api_key(),
            base_url=_KIMI_BASE_URL,
            timeout=timeout,
        )
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise RuntimeError(
            "No LLM credentials configured: set KimiAI_API_KEY (preferred) "
            "or OPENAI_API_KEY in the env."
        )
    return OpenAI(api_key=openai_key, timeout=timeout)


def get_openai_vision_client(timeout: float = 60.0) -> "OpenAI | None":
    """Return an OpenAI client pinned to api.openai.com/v1 for vision tasks.

    Audit 2026-05-10 / Slice S11 / TP-21. Vision recovery in
    `form_engine/field_mapper.py` was using `get_openai_client()` which
    routes to Moonshot under the Kimi mandate, then calling
    `client.responses.create()` — Moonshot doesn't implement
    OpenAI's `/v1/responses` endpoint, producing a 404 on every engagement.

    The Kimi mandate covers chat completions only; vision is OpenAI-only
    in this codebase (cost_tracker doesn't price Moonshot vision models).
    This client pins to `api.openai.com/v1` regardless of `OPENAI_BASE_URL`
    so the call lands on a real vision endpoint.

    Returns ``None`` when ``OPENAI_API_KEY`` is not set so callers can
    skip vision recovery cleanly instead of 404-ing under Kimi-only configs.
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        return None
    return OpenAI(
        api_key=openai_key,
        base_url="https://api.openai.com/v1",
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

    llm = get_llm(temperature=0.3, agent_name="researcher")
    response = smart_llm_call(llm, [
        SystemMessage(content=RESEARCHER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    research = response.content
    usage = track_llm_usage(response, "researcher")
    logger.info("Research produced: %d characters ($%.4f)", len(research), usage["cost_usd"])

    return {
        "research_notes": [sanitize_agent_output(research, "researcher")],
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

    llm = get_llm(temperature=0.7, agent_name="writer")
    response = smart_llm_call(llm, [
        SystemMessage(content=WRITER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    draft = response.content
    usage = track_llm_usage(response, "writer")
    logger.info("Draft produced: %d characters, ~%d words ($%.4f)", len(draft), len(draft.split()), usage["cost_usd"])

    return {
        "draft": sanitize_agent_output(draft, "writer"),
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
        agent_name="reviewer",
    )
    response = smart_llm_call(llm, [
        SystemMessage(content=REVIEWER_PROMPT),
        HumanMessage(content=user_msg)
    ])

    usage = track_llm_usage(response, "reviewer")
    raw = response.content.strip()

    try:
        review = json.loads(raw)
        from shared.governance._score_validator import validate_review
        validated = validate_review(review)
        score = validated.overall_score
        accuracy = validated.accuracy_score
        passed = review.get("passed", False)
        feedback_text = json.dumps(review, indent=2)

        if validated.anomalies:
            logger.warning(
                "Review anomalies: %s",
                validated.anomalies,
                extra={"anomaly_count": len(validated.anomalies), "agent_name": "reviewer"},
            )
        logger.info("Score: %s/10 (accuracy: %s) | Passed: %s", score, accuracy, passed)
        if not passed:
            improvements = review.get("improvements_needed", [])
            for imp in improvements[:3]:
                logger.info("   -> %s", imp)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "Could not parse review JSON: %s — raw: %s",
            e,
            raw[:200],
            extra={"agent_name": "reviewer", "error_type": type(e).__name__},
        )
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
        logger.warning(
            "Risk analysis failed, falling back to standard review: %s",
            e,
            extra={"agent_name": "risk_aware_reviewer", "error_type": type(e).__name__},
        )
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


# ---------------------------------------------------------------------------
# Cognitive Engine Default-On Helper
# ---------------------------------------------------------------------------


def cognitive_llm_call(
    task: str,
    *,
    domain: str,
    stakes: str = "medium",
    scorer=None,
    fallback_llm=None,
    fallback_messages=None,
    response_format: dict | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """Route LLM calls through CognitiveEngine when available (default-on).

    This is the preferred way to invoke LLMs for all medium+ stakes tasks.
    It automatically uses the CognitiveEngine's multi-level reasoning
    (reflexion, tree-of-thought) when COGNITIVE_ENABLED is not explicitly
    set to false.

    Args:
        task: The task prompt string.
        domain: Cognitive domain (e.g., "cv_scrutiny", "skill_extraction").
        stakes: "low", "medium", or "high". Defaults to "medium".
        scorer: Optional scoring function for cognitive self-improvement.
        fallback_llm: Optional LangChain LLM for direct fallback.
        fallback_messages: Optional messages list for direct fallback.
        response_format: Optional OpenAI response_format constraint, e.g.
            ``{"type": "json_object"}``. When set, bypasses the cognitive
            engine (whose multi-step strategies — reflexion, tree-of-thought —
            don't all return well-formed JSON) and goes straight to a single
            OpenAI call with the constraint applied. Per
            ``.claude/rules/orchestration-agents.md``: prefer this over
            markdown stripping for any task that expects JSON.

    Returns:
        The generated text answer, or None if all fallbacks fail.
    """
    import os

    # JSON mode bypasses cognitive engine: L2 reflexion / L3 tree-of-thought
    # produce intermediate text that isn't necessarily a JSON object, so
    # response_format constraints aren't compatible with them. Single-call
    # OpenAI with the constraint is both simpler and the canonical pattern.
    if response_format is not None:
        return _direct_llm_call(
            task, fallback_llm, fallback_messages,
            response_format=response_format, domain=domain,
            max_tokens=max_tokens,
        )

    if os.getenv("COGNITIVE_ENABLED", "true").lower() == "false":
        return _direct_llm_call(
            task, fallback_llm, fallback_messages, domain=domain,
            max_tokens=max_tokens,
        )

    try:
        from shared.cognitive import get_cognitive_engine
        engine = get_cognitive_engine(agent_name=domain)
        result = engine.think_sync(task=task, domain=domain, stakes=stakes, scorer=scorer)
        engine.flush_sync()
        return result.answer.strip()
    except Exception as exc:
        logger.debug("Cognitive engine failed for %s, falling back to direct LLM: %s", domain, exc)
        return _direct_llm_call(
            task, fallback_llm, fallback_messages, domain=domain,
            max_tokens=max_tokens,
        )


def _direct_llm_call(
    task: str,
    fallback_llm=None,
    fallback_messages=None,
    response_format: dict | None = None,
    domain: str | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """Direct LLM fallback when cognitive engine is unavailable.

    When ``response_format`` is set (e.g. ``{"type": "json_object"}``), the
    raw OpenAI path is preferred because LangChain's ``llm.invoke`` doesn't
    surface ``response_format`` cleanly across all LLM providers — going
    direct keeps the constraint applied.

    ``domain`` (when provided by ``cognitive_llm_call``) routes to a
    domain-specific model via ``_model_for_domain``: reasoning domains
    use a reasoning model, content domains use a fast non-reasoning
    model. Falls back to ``get_model_name()`` when no domain registry
    entry exists.
    """
    # Skip the LangChain fallback when caller wants JSON mode — a raw OpenAI
    # call with response_format gives a stronger guarantee than retrying the
    # bound LangChain LLM and stripping markdown afterwards.
    if not response_format and fallback_llm and fallback_messages:
        try:
            from shared.llm_retry import resilient_llm_call
            response = resilient_llm_call(fallback_llm, fallback_messages)
            return response.content.strip() if hasattr(response, "content") else str(response).strip()
        except Exception:
            pass

    # Raw OpenAI fallback — model picked via per-domain registry first,
    # falling back to the provider default. cv_tailoring / cover_letter
    # / screening_answers / etc. land on a fast non-reasoning model;
    # page_reasoning / form_recovery / field_type_analysis land on a
    # reasoning model. ``max_tokens`` lets a caller (e.g. field_analyzer
    # with 40+ fields to enrich) request a larger budget; default 2000
    # keeps cost bounded for the bulk of calls.
    try:
        client = get_openai_client()
        model = _model_for_domain(domain) or get_model_name()
        requested = max_tokens if max_tokens is not None else 2000
        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": task}],
            "temperature": 1 if _requires_fixed_temperature(model) else 0.4,
            **_token_limit_kwargs(model, _max_tokens_for_model(model, requested)),
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Direct LLM fallback failed: %s", exc)
        return None
