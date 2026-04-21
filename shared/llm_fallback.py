"""Multi-provider LLM fallback chain.

Tries providers in order. If primary fails with retryable error,
falls back to next provider. Records failure in circuit breaker.
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
    """Try providers in order until one succeeds."""

    def __init__(self, providers: list[str] | None = None):
        self.providers = providers or ["openai", "anthropic", "ollama"]

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

    def _call_openai(self, prompt: str, **kwargs) -> str:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=kwargs.get("model", "gpt-5-mini"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return resp.choices[0].message.content

    def _call_anthropic(self, prompt: str, **kwargs) -> str:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=kwargs.get("model", "claude-sonnet-4-6"),
            max_tokens=kwargs.get("max_tokens", 4096),
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    def _call_ollama(self, prompt: str, **kwargs) -> str:
        import httpx
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.getenv("LOCAL_LLM_MODEL", "gemma4:31b")
        resp = httpx.post(f"{base}/api/generate", json={"model": model, "prompt": prompt}, timeout=60)
        resp.raise_for_status()
        return resp.json()["response"]
