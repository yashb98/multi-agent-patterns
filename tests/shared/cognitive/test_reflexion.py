import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from shared.cognitive._reflexion import ReflexionLoop, ReflexionResult
from shared.cognitive._budget import ThinkLevel
from tests.shared.cognitive.conftest import MockMemoryManager, MockEpisodicEntry


class TestReflexionLoop:

    @pytest.fixture
    def reflexion(self, mock_memory):
        return ReflexionLoop(mock_memory, agent_name="test_agent")

    @pytest.mark.asyncio
    async def test_passes_first_attempt(self, reflexion):
        """Score above threshold on first try → no retry."""
        with patch("shared.cognitive._reflexion._llm_generate",
                   new_callable=AsyncMock, return_value="good answer"):
            result = await reflexion.run(
                task="test task", domain="test",
                initial_prompt="Be helpful.", score_threshold=7.0,
                scorer=lambda x: 8.5,
            )
        assert result.attempts == 1
        assert result.score == 8.5
        assert len(result.critiques) == 0

    @pytest.mark.asyncio
    async def test_retries_on_low_score(self, reflexion):
        """Low first score → critique → retry → succeeds."""
        call_count = 0
        async def mock_generate(prompt, model=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return "bad answer"
            if "MISTAKE:" in prompt or "critique" in prompt.lower():
                return "MISTAKE: Too vague\nFIX: Be specific"
            return "good answer"

        scores = [4.0, 8.0]
        scorer = lambda x: scores.pop(0) if scores else 8.0

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test task", domain="test",
                initial_prompt="Be helpful.", score_threshold=7.0,
                scorer=scorer,
            )
        assert result.attempts == 2
        assert result.score == 8.0
        assert len(result.critiques) >= 1

    @pytest.mark.asyncio
    async def test_max_3_attempts(self, reflexion):
        """Always fails → capped at 3 attempts."""
        async def mock_generate(prompt, model=None):
            if "MISTAKE:" in prompt or "critique" in prompt.lower():
                return "MISTAKE: Still bad\nFIX: Try harder"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test task", domain="test",
                initial_prompt="Be helpful.", score_threshold=7.0,
                scorer=lambda x: 3.0,
            )
        assert result.attempts == 3
        assert result.score == 3.0

    @pytest.mark.asyncio
    async def test_returns_best_attempt(self, reflexion):
        """Returns the highest-scoring attempt, not necessarily the last."""
        attempt = 0
        async def mock_generate(prompt, model=None):
            nonlocal attempt
            if "What specifically went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            attempt += 1
            return f"attempt {attempt}"

        scores = [3.0, 7.5, 6.0]
        def scorer(x):
            return scores.pop(0) if scores else 5.0

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test", domain="test",
                initial_prompt="prompt", score_threshold=8.0,
                scorer=scorer,
            )
        assert result.score == 7.5
        assert "attempt 2" in result.answer

    @pytest.mark.asyncio
    async def test_critique_prompt_includes_output(self, reflexion):
        """Critique LLM call receives the previous attempt's output."""
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            if "MISTAKE:" in prompt or "What specifically went wrong" in prompt:
                return "MISTAKE: Wrong\nFIX: Fix"
            return "my specific output text"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test",
                initial_prompt="prompt", score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        critique_prompts = [p for p in prompts_seen if "went wrong" in p]
        assert any("my specific output text" in p for p in critique_prompts)

    @pytest.mark.asyncio
    async def test_failure_memory_retrieved(self, mock_memory):
        """Past failure patterns injected into retry prompt."""
        mock_memory._episodic.append(MockEpisodicEntry(
            domain="test", final_score=2.0,
            weaknesses=["Past failure: forgot to validate input"],
        ))
        reflexion = ReflexionLoop(mock_memory, agent_name="test_agent")
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "answer"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test",
                initial_prompt="prompt", score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        retry_prompts = [p for p in prompts_seen
                         if "Past failure" in p or "validate input" in p]
        assert len(retry_prompts) >= 1 or \
            any("validate input" in p for p in prompts_seen)

    @pytest.mark.asyncio
    async def test_stores_success_template(self, reflexion, mock_memory):
        """Successful reflexion stores a PROCEDURAL template."""
        async def mock_generate(prompt, model=None):
            return "good answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="email", initial_prompt="prompt",
                score_threshold=7.0, scorer=lambda x: 8.5,
            )
        assert len(mock_memory.learn_procedure_calls) >= 1
        stored = mock_memory.learn_procedure_calls[-1]
        assert stored["domain"] == "email"
        assert stored["source"] == "reflexion"

    @pytest.mark.asyncio
    async def test_stores_failure_pattern(self, mock_memory):
        """All attempts below threshold → EPISODIC failure entry stored."""
        reflexion = ReflexionLoop(mock_memory, agent_name="test_agent")
        async def mock_generate(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Critical error\nFIX: Redo everything"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0, scorer=lambda x: 3.0,
            )
        eps = [e for e in mock_memory._episodic if e.final_score < 5.0]
        assert len(eps) >= 1

    @pytest.mark.asyncio
    async def test_failure_has_critique(self, mock_memory):
        """Failure entry has critique in weaknesses."""
        reflexion = ReflexionLoop(mock_memory, agent_name="test_agent")
        async def mock_generate(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Critical error\nFIX: Redo everything"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0, scorer=lambda x: 3.0,
            )
        eps = [e for e in mock_memory._episodic if e.final_score < 5.0]
        assert len(eps) >= 1
        assert any("Critical error" in w for w in eps[0].weaknesses)

    @pytest.mark.asyncio
    async def test_custom_scorer(self, reflexion):
        """Custom scorer function is called instead of LLM scorer."""
        call_log = []
        def custom_scorer(output):
            call_log.append(output)
            return 9.0

        async def mock_generate(prompt, model=None):
            return "answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0, scorer=custom_scorer,
            )
        assert len(call_log) >= 1
        assert result.score == 9.0

    @pytest.mark.asyncio
    async def test_cost_tracking(self, reflexion):
        """Cost reported accurately."""
        async def mock_generate(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "answer"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        # 2 generation calls + 1 critique = should have some cost
        assert result.cost > 0

    @pytest.mark.asyncio
    async def test_critique_uses_nano(self, reflexion):
        """Critique calls use gpt-4.1-nano model."""
        models_used = []
        async def mock_generate(prompt, model=None):
            models_used.append(model)
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "answer"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        assert "gpt-4.1-nano" in models_used
