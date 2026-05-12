"""Tests for shared/agents.py — real Ollama + real LLM calls, no mocks."""

import httpx
import pytest


def _ollama_available():
    try:
        return httpx.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ollama_available(), reason="Ollama not running")


class TestOllamaDetection:
    def test_probe_ollama_returns_true(self):
        from shared.agents import _probe_ollama

        assert _probe_ollama() is True

    def test_resolve_provider_auto_finds_ollama(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        import shared.agents as mod

        mod._LLM_PROVIDER = None
        mod._is_local = None
        mod._use_fallback_models = None
        result = mod._resolve_provider()
        assert result == "local"

    def test_is_local_llm_true_when_ollama_running(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        import shared.agents as mod

        mod._LLM_PROVIDER = None
        mod._is_local = None
        mod._use_fallback_models = None
        assert mod.is_local_llm() is True


class TestGetLlm:
    def test_returns_invocable_llm(self):
        from shared.agents import get_llm

        llm = get_llm(temperature=0.0)
        assert llm is not None
        assert hasattr(llm, "invoke")

    @pytest.mark.slow
    def test_llm_generates_response(self):
        from shared.agents import get_llm

        llm = get_llm(temperature=0.0)
        try:
            result = llm.invoke("Say exactly: hello")
        except Exception as e:
            if "not found" in str(e).lower():
                pytest.skip(f"Ollama model not available: {e}")
            raise
        assert len(result.content) > 0

    def test_get_model_name_returns_local_model(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        import shared.agents as mod

        mod._LLM_PROVIDER = None
        mod._is_local = None
        mod._use_fallback_models = None
        name = mod.get_model_name()
        assert "gpt" not in name.lower()

    def test_get_openai_client_connects(self):
        from shared.agents import get_openai_client

        client = get_openai_client()
        assert client is not None


class TestCreateInitialState:
    def test_creates_valid_state(self):
        from shared.agents import create_initial_state

        state = create_initial_state("test topic")
        assert state["topic"] == "test topic"
        assert state["research_notes"] == []
        assert state["draft"] == ""
        assert state["review_score"] == 0.0
        assert state["iteration"] == 0
        assert state["review_passed"] is False

    def test_state_includes_agent_history(self):
        from shared.agents import create_initial_state

        state = create_initial_state("another topic")
        assert len(state["agent_history"]) == 1
        assert "another topic" in state["agent_history"][0]


class TestExtractCodeBlocks:
    def test_extracts_python_block(self):
        from shared.agents import _extract_code_blocks

        text = "Here is code:\n```python\nprint('hello')\n```\nDone."
        blocks = _extract_code_blocks(text)
        assert len(blocks) >= 1
        assert "print" in blocks[0][1]

    def test_extracts_named_file(self):
        from shared.agents import _extract_code_blocks

        text = "```app.py\nx = 1\n```"
        blocks = _extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0][0] == "app.py"

    def test_no_blocks_returns_empty(self):
        from shared.agents import _extract_code_blocks

        assert _extract_code_blocks("no code here") == []

    def test_empty_block_skipped(self):
        from shared.agents import _extract_code_blocks

        text = "```python\n   \n```"
        assert _extract_code_blocks(text) == []


class TestTokenLimitKwargs:
    def test_o1_model_uses_max_completion_tokens(self):
        from shared.agents import _token_limit_kwargs

        result = _token_limit_kwargs("o1-preview", 4096)
        assert "max_completion_tokens" in result
        assert "max_tokens" not in result

    def test_gpt5_uses_max_completion_tokens(self):
        from shared.agents import _token_limit_kwargs

        result = _token_limit_kwargs("gpt-5-mini", 4096)
        assert "max_completion_tokens" in result

    def test_gpt4o_uses_max_tokens(self):
        from shared.agents import _token_limit_kwargs

        result = _token_limit_kwargs("gpt-4o-mini", 4096)
        assert "max_tokens" in result
        assert "max_completion_tokens" not in result
