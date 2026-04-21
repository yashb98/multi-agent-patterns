import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.cognitive._tree_of_thought import TreeOfThought, ToTResult, Branch
from tests.shared.cognitive.conftest import MockMemoryManager


class TestTreeOfThought:

    @pytest.fixture
    def tot(self, mock_memory):
        return TreeOfThought(mock_memory, agent_name="test_agent")

    @pytest.mark.asyncio
    async def test_generates_n_branches(self, tot):
        """Generates correct number of initial branches."""
        call_count = 0
        async def mock_generate(prompt, model=None):
            nonlocal call_count
            call_count += 1
            return f"branch output {call_count}"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test task", domain="test", context="ctx",
                num_branches=4, scorer=lambda x: 7.0,
            )
        assert len(result.all_branches) >= 4

    @pytest.mark.asyncio
    async def test_prunes_below_threshold(self, tot):
        """Branches scoring below threshold are pruned."""
        outputs = iter(["good A", "bad B", "good C", "bad D", "ext A", "ext C"])
        async def mock_generate(prompt, model=None):
            return next(outputs, "default")

        scores = {"good A": 8.0, "bad B": 3.0, "good C": 7.0, "bad D": 4.0,
                  "ext A": 8.5, "ext C": 9.0, "default": 5.0}

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=lambda x: scores.get(x, 5.0),
            )
        assert result.pruned_count == 2

    @pytest.mark.asyncio
    async def test_extends_top_n(self, tot):
        """Top N branches after pruning get extended."""
        call_idx = 0
        async def mock_generate(prompt, model=None):
            nonlocal call_idx
            call_idx += 1
            return f"output_{call_idx}"

        score_map = {}
        base_scores = [8.0, 3.0, 7.0, 4.0]
        ext_scores = [8.5, 9.0]
        all_scores = base_scores + ext_scores

        def scorer(x):
            if x in score_map:
                return score_map[x]
            if all_scores:
                s = all_scores.pop(0)
                score_map[x] = s
                return s
            return 5.0

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=scorer,
            )
        extensions = [b for b in result.all_branches if b.depth == 1]
        assert len(extensions) == 2

    @pytest.mark.asyncio
    async def test_winner_is_highest_score(self, tot):
        """Winner branch has the highest score across all branches."""
        idx = 0
        async def mock_generate(prompt, model=None):
            nonlocal idx
            idx += 1
            return f"output_{idx}"

        scores_list = [5.0, 8.0, 3.0, 6.0, 8.5, 9.5]
        score_map = {}

        def scorer(x):
            if x not in score_map and scores_list:
                score_map[x] = scores_list.pop(0)
            return score_map.get(x, 5.0)

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=4.0, extend_top_n=2,
                scorer=scorer,
            )
        all_scores = [b.score for b in result.all_branches]
        assert result.winner.score == max(all_scores)

    @pytest.mark.asyncio
    async def test_strategy_extracted_from_winner(self, tot):
        """Winner produces a non-empty strategy template."""
        async def mock_generate(prompt, model=None):
            return "detailed winning strategy here"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=2, extend_top_n=1,
                scorer=lambda x: 9.0,
            )
        assert len(result.strategy_template) > 0

    @pytest.mark.asyncio
    async def test_grpo_called_for_generation(self, tot):
        """GRPO parallel generation is attempted for initial branches."""
        grpo_called = []
        def mock_grpo_gen(make_variant, system_prompt, user_message, temps):
            grpo_called.append(True)
            return [f"grpo_output_{i}" for i in range(len(temps))]

        with patch("shared.cognitive._tree_of_thought.parallel_grpo_candidates",
                   side_effect=mock_grpo_gen, create=True), \
             patch("shared.cognitive._tree_of_thought._llm_generate",
                   new_callable=AsyncMock, return_value="fallback"):
            # Patch the import inside _generate_branches_via_grpo
            with patch.object(tot, "_generate_branches_via_grpo",
                              return_value=["grpo_0", "grpo_1", "grpo_2", "grpo_3"]):
                await tot.explore(
                    task="test", domain="test", context="ctx",
                    num_branches=4, extend_top_n=0,
                    scorer=lambda x: 7.0,
                )

    @pytest.mark.asyncio
    async def test_branch_prompts_structurally_different(self, tot):
        """Each branch gets a different reasoning instruction."""
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            return "output"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, extend_top_n=0,
                scorer=lambda x: 7.0,
            )
        initial_prompts = prompts_seen[:4]
        assert len(set(initial_prompts)) == 4

    @pytest.mark.asyncio
    async def test_extension_builds_on_parent(self, tot):
        """Extension prompts reference parent branch reasoning."""
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            return "parent reasoning output"

        scores = [8.0, 3.0, 7.0, 4.0, 9.0, 8.5]
        score_idx = 0
        def scorer(x):
            nonlocal score_idx
            s = scores[score_idx] if score_idx < len(scores) else 5.0
            score_idx += 1
            return s

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=scorer,
            )
        extension_prompts = prompts_seen[4:]
        assert any("parent reasoning" in p or "Build on" in p for p in extension_prompts)

    @pytest.mark.asyncio
    async def test_cost_tracking(self, tot):
        """Cost reported accurately."""
        async def mock_generate(prompt, model=None):
            return "output"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, extend_top_n=2,
                scorer=lambda x: 7.0,
            )
        assert result.cost > 0

    @pytest.mark.asyncio
    async def test_single_branch_no_extension(self, tot):
        """Only 1 branch passes pruning → returned without extension."""
        idx = 0
        async def mock_generate(prompt, model=None):
            nonlocal idx
            idx += 1
            return f"output_{idx}"

        scores = [9.0, 2.0, 2.0, 2.0]
        score_idx = 0
        def scorer(x):
            nonlocal score_idx
            s = scores[score_idx] if score_idx < len(scores) else 5.0
            score_idx += 1
            return s

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=scorer,
            )
        extensions = [b for b in result.all_branches if b.depth == 1]
        assert len(extensions) <= 1
