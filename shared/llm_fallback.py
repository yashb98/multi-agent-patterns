"""Multi-provider LLM fallback chain — Kimi-mandatory edition.

Tries providers in order: Kimi (cloud, Moonshot's OpenAI-compatible
endpoint) → Ollama (local). On retryable failure, falls back to the
next provider and records the failure in the circuit breaker.

Pre-2026-05-12 this had an ``anthropic`` provider in the chain that
returned 401 in production because ``ANTHROPIC_API_KEY`` isn't set,
which then propagated as ``All LLM providers failed`` and aborted
``apply_job`` at the page-reasoner stage (live run 5 evidence).

Per ``.claude/rules/jobs.md`` + ``shared/CLAUDE.md``, Kimi (Moonshot)
is the only authorised cloud vendor. Ollama is the only authorised
local fallback. Anthropic / Gemini / OpenAI-direct paths were removed.
"""

import os
from shared.logging_config import get_logger
from shared.circuit_breaker import get_breaker
from shared.alerting import AlertManager, AlertLevel

logger = get_logger(__name__)
_alert_mgr = AlertManager()


class ProviderError(Exception):
    """Raised when an LLM provider fails."""
    pass


class FallbackLLM:
    """Try providers in order until one succeeds. Kimi → Ollama only."""

    def __init__(self, providers: list[str] | None = None):
        self.providers = providers or ["kimi", "ollama"]

    def invoke(self, prompt: str, **kwargs) -> str:
        last_error = None
        for provider in self.providers:
            breaker = get_breaker(provider)
            if breaker and not breaker.allow_request():
                logger.warning("Skipping %s (circuit breaker OPEN)", provider)
                continue
            try:
                call_fn = getattr(self, f"_call_{provider}")
                result = call_fn(prompt, **kwargs)
                if breaker:
                    breaker.record_success()
                return result
            except Exception as e:
                last_error = ProviderError(f"{provider}: {e}")
                if breaker:
                    breaker.record_failure()
                logger.warning("Provider %s failed: %s", provider, e)
                _alert_mgr.alert(AlertLevel.ERROR, f"Provider {provider} failed", source="llm_fallback")
        raise last_error or ProviderError("No providers available")

    def _call_kimi(self, prompt: str, **kwargs) -> str:
        """Call Kimi via Moonshot's OpenAI-compatible endpoint."""
        from openai import OpenAI
        api_key = os.getenv("KimiAI_API_KEY") or os.getenv("KIMI_API_KEY", "")
        if not api_key:
            raise RuntimeError("KIMI_API_KEY not set; Kimi is the mandatory cloud vendor")
        base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=kwargs.get("model", "moonshot-v1-auto"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return resp.choices[0].message.content

    def _call_ollama(self, prompt: str, **kwargs) -> str:
        """Call local Ollama (next-tier fallback when Kimi errors)."""
        import httpx
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen3:32b")
        resp = httpx.post(
            f"{base}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["response"]
