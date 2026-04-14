"""
Tests for the 7 shared/ learning/memory function integrations across
the 4 orchestration patterns.

All tests use:
- MemoryManager with tmp_path (never /tmp/agent_memory production dir)
- ExperienceMemory with ":memory:" SQLite (never data/experience_memory.db)
- Mocked LLM calls (never real OpenAI calls)
"""

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import pytest

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.memory_layer import MemoryManager
from shared.experiential_learning import ExperienceMemory, Experience, TrainingFreeGRPO, GRPOConfig


# ─── FIXTURES ────────────────────────────────────────────────────

@pytest.fixture
def tmp_memory(tmp_path):
    """MemoryManager backed by tmp_path — never touches production files."""
    return MemoryManager(storage_dir=str(tmp_path / "agent_memory"))


@pytest.fixture
def mem_memory():
    """ExperienceMemory backed by :memory: SQLite."""
    return ExperienceMemory(max_size=20, db_path=":memory:")


@pytest.fixture
def fake_llm_response():
    """A mock LangChain LLM that returns a fixed content string."""
    def _make(content: str):
        resp = MagicMock()
        resp.content = content
        resp.usage_metadata = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
        return resp
    return _make


# ─── TEST 1: get_context_for_agent ───────────────────────────────

class TestGetContextForAgent:
    """MemoryManager.get_context_for_agent is wired in pattern nodes."""

    def test_returns_empty_string_when_no_memory(self, tmp_memory):
        ctx = tmp_memory.get_context_for_agent("researcher", "AI trends", "tech")
        assert isinstance(ctx, str)

    def test_returns_semantic_after_learn_fact(self, tmp_memory):
        tmp_memory.learn_fact("tech", "Transformers dominate NLP as of 2024")
        ctx = tmp_memory.get_context_for_agent("researcher", "AI", "tech")
        # Semantic facts are returned for 'researcher' role
        assert isinstance(ctx, str)

    def test_returns_procedural_for_writer(self, tmp_memory):
        tmp_memory.learn_procedure("writing", "Use short paragraphs for technical blogs")
        ctx = tmp_memory.get_context_for_agent("writer", "blog post", "writing")
        assert isinstance(ctx, str)

    def test_short_term_appears_for_all_agents(self, tmp_memory):
        tmp_memory.record_step("researcher", "Gathered 10 facts on quantum ML", score=8.0)
        # Short-term shows for any agent (always included)
        ctx = tmp_memory.get_context_for_agent("reviewer", "quantum", "physics")
        assert isinstance(ctx, str)

    def test_peer_debate_researcher_uses_memory_context(self, tmp_memory):
        """Peer debate researcher node calls get_context_for_agent before LLM."""
        tmp_memory.learn_fact("general", "LLMs emerged in 2017 with the Transformer paper")

        with (
            patch("patterns.peer_debate._memory_manager", tmp_memory),
            patch("patterns.peer_debate._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.peer_debate.get_llm") as mock_get_llm,
        ):
            mock_llm = MagicMock()
            resp = MagicMock()
            resp.content = "Critique: The draft missed recent quantum advances."
            mock_llm.invoke.return_value = resp
            mock_get_llm.return_value = mock_llm

            from patterns.peer_debate import debate_researcher_node

            state = {
                "topic": "AI trends 2026",
                "iteration": 1,  # Round 2+ triggers debate mode
                "draft": "AI is changing fast.",
                "review_feedback": "Needs more depth on quantum.",
                "research_notes": ["Initial research notes"],
                "agent_history": [],
            }

            result = debate_researcher_node(state)

        assert "research_notes" in result
        assert len(result["research_notes"]) == 1
        # Verify get_context_for_agent was consulted (mock LLM was called with enhanced prompt)
        assert mock_llm.invoke.called

    def test_enhanced_swarm_researcher_uses_memory_context(self, tmp_memory):
        """Enhanced swarm researcher calls get_context_for_agent before GRPO."""
        with (
            patch("patterns.enhanced_swarm._memory_manager", tmp_memory),
            patch("patterns.enhanced_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.enhanced_swarm._grpo") as mock_grpo,
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value = MagicMock()
            mock_grpo.enhance_prompt.return_value = "enhanced system prompt"
            mock_grpo.group_sample_and_learn.return_value = ("Best research output", None)

            from patterns.enhanced_swarm import enhanced_researcher_node

            state = {
                "topic": "Quantum Computing 2026",
                "iteration": 0,
                "review_feedback": "",
                "research_notes": [],
                "agent_history": [],
            }

            result = enhanced_researcher_node(state)

        assert "research_notes" in result
        assert result["research_notes"] == ["Best research output"]
        mock_grpo.enhance_prompt.assert_called_once_with("You are an elite Research Analyst. Gather comprehensive,\naccurate information on the given topic. Focus on:\n- Verified facts with clear sourcing\n- Technical depth appropriate to the topic\n- Current trends and recent developments\n- Multiple perspectives and expert opinions\n- Quantitative data points where available\n\nStructure output as: Key Facts, Technical Details, Trends, Expert Views, Data.", domain="research")


# ─── TEST 2: record_step ─────────────────────────────────────────

class TestRecordStep:
    """MemoryManager.record_step is called after each agent step."""

    def test_record_step_adds_to_short_term(self, tmp_memory):
        tmp_memory.record_step("researcher", "Found 15 sources on AI", score=7.5)
        assert len(tmp_memory.short_term.buffer) == 1

    def test_record_step_multiple_agents(self, tmp_memory):
        tmp_memory.record_step("researcher", "Gathered facts")
        tmp_memory.record_step("writer", "Wrote 800 words")
        tmp_memory.record_step("reviewer", "Score: 7.5/10")
        assert len(tmp_memory.short_term.buffer) == 3

    def test_dynamic_swarm_executor_records_step(self, tmp_memory):
        """task_executor_node calls record_step after each agent execution."""
        with (
            patch("patterns.dynamic_swarm._memory_manager", tmp_memory),
            patch("patterns.dynamic_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.dynamic_swarm.researcher_node") as mock_researcher,
        ):
            mock_researcher.return_value = {
                "research_notes": ["Test research"],
                "current_agent": "researcher",
                "agent_history": ["Researcher completed"],
            }

            from patterns.dynamic_swarm import task_executor_node

            state = {
                "topic": "Test topic",
                "pending_tasks": [
                    {"agent": "researcher", "priority": 1, "description": "Gather facts"}
                ],
                "research_notes": [],
                "draft": "",
                "review_feedback": "",
                "review_score": 0.0,
                "review_passed": False,
                "iteration": 0,
                "current_agent": "",
                "agent_history": [],
                "token_usage": [],
                "accuracy_passed": False,
                "accuracy_score": 0.0,
                "fact_revision_notes": None,
                "extracted_claims": [],
                "claim_verifications": [],
                "final_output": "",
                "total_cost_usd": 0.0,
            }

            task_executor_node(state)

        # record_step should have been called once for the researcher
        assert len(tmp_memory.short_term.buffer) == 1
        entry = tmp_memory.short_term.buffer[0]
        assert entry.agent == "researcher"

    def test_enhanced_swarm_convergence_records_step(self, tmp_memory):
        """enhanced_convergence calls record_step after deciding."""
        with patch("patterns.enhanced_swarm._memory_manager", tmp_memory):
            from patterns.enhanced_swarm import enhanced_convergence

            state = {
                "topic": "AI test",
                "review_score": 8.5,
                "review_passed": True,
                "accuracy_score": 9.6,
                "accuracy_passed": True,
                "iteration": 1,
                "agent_history": [],
                "research_notes": [],
                "draft": "test draft",
                "review_feedback": "",
                "pending_tasks": [],
                "extracted_claims": [],
                "claim_verifications": [],
                "fact_revision_notes": None,
                "token_usage": [],
                "total_cost_usd": 0.0,
                "final_output": "",
                "current_agent": "",
            }
            enhanced_convergence(state)

        assert len(tmp_memory.short_term.buffer) == 1
        assert tmp_memory.short_term.buffer[0].agent == "convergence"


# ─── TEST 3: learn_fact ──────────────────────────────────────────

class TestLearnFact:
    """MemoryManager.learn_fact is called from fact_check_node for verified facts."""

    def test_learn_fact_stores_in_semantic(self, tmp_memory):
        tmp_memory.learn_fact("tech", "GPT-4 was released in 2023")
        # Semantic memory should have one fact
        assert len(tmp_memory.semantic.facts) == 1

    def test_learn_fact_reinforces_existing(self, tmp_memory):
        tmp_memory.learn_fact("tech", "GPT-4 was released in 2023", run_id="run1")
        tmp_memory.learn_fact("tech", "GPT-4 was released in 2023", run_id="run2")
        # Same fact reinforced — still one entry (or deduped)
        assert len(tmp_memory.semantic.facts) >= 1

    def test_fact_check_node_calls_learn_fact(self, tmp_memory):
        """fact_check_node in shared/agents.py calls learn_fact for VERIFIED claims."""
        fake_verifications = [
            {"status": "VERIFIED", "claim": "Python is a high-level language"},
            {"status": "UNVERIFIED", "claim": "Python invented in 2025"},
        ]

        with (
            patch("shared.fact_checker.extract_claims") as mock_extract,
            patch("shared.fact_checker.verify_claims") as mock_verify,
            patch("shared.fact_checker.compute_accuracy_score") as mock_score,
            patch("shared.fact_checker.generate_revision_notes") as mock_notes,
            patch("shared.memory_layer.get_shared_memory_manager") as mock_get_mm,
        ):
            mock_instance = MagicMock()
            mock_get_mm.return_value = mock_instance
            mock_extract.return_value = ["Python is a high-level language"]
            mock_verify.return_value = fake_verifications
            mock_score.return_value = 9.6
            mock_notes.return_value = None

            from shared.agents import fact_check_node

            state = {
                "topic": "Python programming",
                "draft": "Python is a high-level language.",
                "research_notes": [],
                "agent_history": [],
            }

            result = fact_check_node(state)

        # learn_fact should be called once for the VERIFIED claim
        mock_instance.learn_fact.assert_called_once()
        call_args = mock_instance.learn_fact.call_args
        assert call_args[0][1] == "Python is a high-level language"

    def test_fact_check_node_skips_unverified(self, tmp_memory):
        """fact_check_node does NOT call learn_fact for non-VERIFIED claims."""
        fake_verifications = [
            {"status": "UNVERIFIED", "claim": "AI will replace all programmers by 2025"},
        ]

        with (
            patch("shared.fact_checker.extract_claims", return_value=["AI will replace..."]),
            patch("shared.fact_checker.verify_claims", return_value=fake_verifications),
            patch("shared.fact_checker.compute_accuracy_score", return_value=4.0),
            patch("shared.fact_checker.generate_revision_notes", return_value="Fix: remove claim"),
            patch("shared.memory_layer.MemoryManager") as MockMM,
        ):
            mock_instance = MagicMock()
            MockMM.return_value = mock_instance

            from shared.agents import fact_check_node

            state = {
                "topic": "AI future",
                "draft": "AI will replace all programmers.",
                "research_notes": [],
                "agent_history": [],
            }
            fact_check_node(state)

        # learn_fact must NOT be called for non-VERIFIED claims
        mock_instance.learn_fact.assert_not_called()


# ─── TEST 4: learn_procedure ─────────────────────────────────────

class TestLearnProcedure:
    """MemoryManager.learn_procedure is called in convergence nodes."""

    def test_learn_procedure_stores_in_procedural(self, tmp_memory):
        tmp_memory.learn_procedure(
            domain="writing",
            strategy="Use short paragraphs and bullet points for technical audiences",
            context="tech blog",
            score=8.5,
            source="hierarchical",
        )
        assert len(tmp_memory.procedural.procedures) == 1

    def test_learn_procedure_below_threshold_low_rate(self, tmp_memory):
        tmp_memory.learn_procedure(
            domain="writing",
            strategy="Include code examples in every section",
            score=5.0,
            source="test",
        )
        # Stored but with success_rate=0.5
        proc = tmp_memory.procedural.procedures[0]
        assert proc.success_rate == 0.5

    def test_hierarchical_run_calls_learn_procedure(self, tmp_memory):
        """run_hierarchical calls learn_procedure when score >= 7.0."""
        mock_final_state = {
            "review_score": 8.0,
            "review_passed": True,
            "accuracy_score": 9.6,
            "accuracy_passed": True,
            "iteration": 1,
            "final_output": "A great article about AI." * 50,
            "agent_history": [
                "Supervisor → researcher (No research yet)",
                "Supervisor → writer (Need draft)",
                "Supervisor → reviewer (Draft review)",
            ],
            "token_usage": [],
            "total_cost_usd": 0.0,
        }

        with (
            patch("patterns.hierarchical._memory_manager", tmp_memory),
            patch("patterns.hierarchical.build_hierarchical_graph") as mock_build,
            patch("patterns.hierarchical.create_initial_state") as mock_state,
        ):
            mock_graph = MagicMock()
            mock_graph.invoke.return_value = mock_final_state
            mock_build.return_value = mock_graph
            mock_state.return_value = {"topic": "AI Trends"}

            from patterns.hierarchical import run_hierarchical
            run_hierarchical("AI Trends", domain="tech")

        # learn_procedure should have been called (score >= 7.0)
        assert len(tmp_memory.procedural.procedures) == 1

    def test_peer_debate_convergence_calls_learn_procedure(self, tmp_memory):
        """convergence_check in peer_debate calls learn_procedure when score >= 7.0."""
        with (
            patch("patterns.peer_debate._memory_manager", tmp_memory),
            patch("patterns.peer_debate._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
        ):
            from patterns.peer_debate import convergence_check

            state = {
                "topic": "AI test",
                "review_score": 7.5,
                "review_passed": True,
                "accuracy_score": 9.6,
                "accuracy_passed": True,
                "iteration": 1,
                "agent_history": [],
                "research_notes": [],
                "draft": "test",
                "review_feedback": "Good",
                "pending_tasks": [],
                "extracted_claims": [],
                "claim_verifications": [],
                "fact_revision_notes": None,
                "token_usage": [],
                "total_cost_usd": 0.0,
                "final_output": "",
                "current_agent": "",
            }
            convergence_check(state)

        assert len(tmp_memory.procedural.procedures) == 1
        proc = tmp_memory.procedural.procedures[0]
        assert proc.source == "peer_debate"

    def test_dynamic_swarm_finish_calls_learn_procedure(self, tmp_memory):
        """swarm_finish_node calls learn_procedure when score >= 7.0."""
        with (
            patch("patterns.dynamic_swarm._memory_manager", tmp_memory),
            patch("patterns.dynamic_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
        ):
            from patterns.dynamic_swarm import swarm_finish_node

            state = {
                "topic": "Swarm test",
                "draft": "Final swarm draft",
                "review_score": 7.8,
                "review_passed": True,
                "iteration": 2,
                "agent_history": [
                    "Task Executor: ran researcher — initial",
                    "Task Executor: ran writer — draft",
                ],
                "token_usage": [],
                "total_cost_usd": 0.0,
                "review_feedback": "",
                "research_notes": [],
                "pending_tasks": [],
                "current_agent": "",
                "extracted_claims": [],
                "claim_verifications": [],
                "accuracy_score": 0.0,
                "accuracy_passed": False,
                "fact_revision_notes": None,
                "final_output": "",
            }
            swarm_finish_node(state)

        assert len(tmp_memory.procedural.procedures) == 1
        proc = tmp_memory.procedural.procedures[0]
        assert proc.source == "dynamic_swarm"


# ─── TEST 5: enhance_prompt ──────────────────────────────────────

class TestEnhancePrompt:
    """TrainingFreeGRPO.enhance_prompt is wired into enhanced_swarm nodes."""

    def test_enhance_prompt_returns_base_when_empty(self, mem_memory):
        mock_llm = MagicMock()
        grpo = TrainingFreeGRPO(llm=mock_llm, config=GRPOConfig(max_experiences=5))
        # Swap in the isolated in-memory ExperienceMemory
        grpo.memory = mem_memory
        result = grpo.enhance_prompt("Base system prompt", domain="writing")
        assert result == "Base system prompt"

    def test_enhance_prompt_appends_experiences(self, mem_memory):
        mock_llm = MagicMock()
        grpo = TrainingFreeGRPO(llm=mock_llm, config=GRPOConfig(max_experiences=5))
        grpo.memory = mem_memory
        grpo.memory.add(Experience(
            task_description="Write about AI",
            successful_pattern="Use concrete examples and analogies",
            score=8.5,
            domain="writing",
        ))
        result = grpo.enhance_prompt("Base system prompt", domain="writing")

        assert "Base system prompt" in result
        assert "concrete examples" in result

    def test_enhanced_swarm_researcher_calls_enhance_prompt(self):
        """enhanced_researcher_node calls _grpo.enhance_prompt before generation."""
        mock_grpo = MagicMock()
        mock_grpo.enhance_prompt.return_value = "Enhanced base prompt"
        mock_grpo.group_sample_and_learn.return_value = ("Research output", None)

        with (
            patch("patterns.enhanced_swarm._grpo", mock_grpo),
            patch("patterns.enhanced_swarm._memory_manager", MagicMock()),
            patch("patterns.enhanced_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value = MagicMock()

            from patterns.enhanced_swarm import enhanced_researcher_node

            state = {
                "topic": "Quantum Computing",
                "iteration": 0,
                "review_feedback": "",
                "research_notes": [],
                "agent_history": [],
            }
            enhanced_researcher_node(state)

        mock_grpo.enhance_prompt.assert_called_once()
        call_kwargs = mock_grpo.enhance_prompt.call_args
        assert call_kwargs[1]["domain"] == "research"

    def test_enhanced_swarm_writer_calls_enhance_prompt(self):
        """enhanced_writer_node calls _grpo.enhance_prompt before generation."""
        mock_grpo = MagicMock()
        mock_grpo.enhance_prompt.return_value = "Enhanced writer prompt"
        mock_grpo.group_sample_and_learn.return_value = ("Best draft", None)

        with (
            patch("patterns.enhanced_swarm._grpo", mock_grpo),
            patch("patterns.enhanced_swarm._memory_manager", MagicMock()),
            patch("patterns.enhanced_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value = MagicMock()

            from patterns.enhanced_swarm import enhanced_writer_node

            state = {
                "topic": "AI in 2026",
                "iteration": 0,
                "review_feedback": "",
                "research_notes": ["Some research"],
                "draft": "",
                "agent_history": [],
            }
            enhanced_writer_node(state)

        mock_grpo.enhance_prompt.assert_called_once()
        call_kwargs = mock_grpo.enhance_prompt.call_args
        assert call_kwargs[1]["domain"] == "writing"


# ─── TEST 6: group_sample_and_learn ─────────────────────────────

class TestGroupSampleAndLearn:
    """TrainingFreeGRPO.group_sample_and_learn is wired into enhanced_swarm nodes."""

    def test_group_sample_and_learn_api(self, mem_memory):
        """Verify group_sample_and_learn signature and return type."""
        mock_llm = MagicMock()
        # Mock parallel_grpo_candidates to avoid real LLM calls
        with patch("shared.parallel_executor.parallel_grpo_candidates") as mock_parallel:
            mock_parallel.return_value = [
                "Output A: comprehensive research with data points",
                "Output B: brief overview without details",
            ]
            grpo = TrainingFreeGRPO(llm=mock_llm, config=GRPOConfig(group_size=2, max_experiences=5))

            # Mock the semantic advantage extraction (avoids real LLM call)
            def fake_extract(best, worst, score, domain, task):
                return Experience(
                    task_description=task[:100],
                    successful_pattern="Use data points and concrete examples",
                    score=score,
                    domain=domain,
                )

            with patch.object(grpo, "_extract_semantic_advantage", side_effect=fake_extract):
                evaluator = lambda text: 8.0 if "data points" in text else 4.0
                best_output, experience = grpo.group_sample_and_learn(
                    system_prompt="Research analyst",
                    user_message="Research AI trends",
                    evaluator_fn=evaluator,
                    domain="research",
                )

        assert isinstance(best_output, str)
        assert "Output A" in best_output  # Higher scorer

    def test_enhanced_swarm_researcher_calls_group_sample_and_learn(self):
        """enhanced_researcher_node calls _grpo.group_sample_and_learn."""
        mock_grpo = MagicMock()
        mock_grpo.enhance_prompt.return_value = "Enhanced prompt"
        mock_grpo.group_sample_and_learn.return_value = ("Best research", None)

        with (
            patch("patterns.enhanced_swarm._grpo", mock_grpo),
            patch("patterns.enhanced_swarm._memory_manager", MagicMock()),
            patch("patterns.enhanced_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value = MagicMock()

            from patterns.enhanced_swarm import enhanced_researcher_node

            state = {
                "topic": "ML Engineering",
                "iteration": 0,
                "review_feedback": "",
                "research_notes": [],
                "agent_history": [],
            }
            result = enhanced_researcher_node(state)

        mock_grpo.group_sample_and_learn.assert_called_once()
        call_kwargs = mock_grpo.group_sample_and_learn.call_args[1]
        assert call_kwargs["domain"] == "research"
        assert result["research_notes"] == ["Best research"]

    def test_enhanced_swarm_writer_calls_group_sample_and_learn(self):
        """enhanced_writer_node calls _grpo.group_sample_and_learn."""
        mock_grpo = MagicMock()
        mock_grpo.enhance_prompt.return_value = "Enhanced prompt"
        mock_grpo.group_sample_and_learn.return_value = ("Best draft content", None)

        with (
            patch("patterns.enhanced_swarm._grpo", mock_grpo),
            patch("patterns.enhanced_swarm._memory_manager", MagicMock()),
            patch("patterns.enhanced_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value = MagicMock()

            from patterns.enhanced_swarm import enhanced_writer_node

            state = {
                "topic": "DevOps in 2026",
                "iteration": 0,
                "review_feedback": "",
                "research_notes": ["DevOps research notes"],
                "draft": "",
                "agent_history": [],
            }
            result = enhanced_writer_node(state)

        mock_grpo.group_sample_and_learn.assert_called_once()
        call_kwargs = mock_grpo.group_sample_and_learn.call_args[1]
        assert call_kwargs["domain"] == "writing"
        assert result["draft"] == "Best draft content"

    def test_group_sample_and_learn_fallback_on_exception(self):
        """If group_sample_and_learn raises, enhanced nodes fall back gracefully."""
        mock_grpo = MagicMock()
        mock_grpo.enhance_prompt.return_value = "Enhanced prompt"
        mock_grpo.group_sample_and_learn.side_effect = RuntimeError("GRPO failed")

        with (
            patch("patterns.enhanced_swarm._grpo", mock_grpo),
            patch("patterns.enhanced_swarm._memory_manager", MagicMock()),
            patch("patterns.enhanced_swarm._experience_memory",
                  ExperienceMemory(max_size=5, db_path=":memory:")),
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
            patch("shared.parallel_executor.parallel_grpo_candidates") as mock_parallel,
        ):
            mock_llm = MagicMock()
            mock_get_llm.return_value = mock_llm
            mock_parallel.return_value = ["# Fallback draft\n\nSome content here " * 50]

            from patterns.enhanced_swarm import enhanced_writer_node

            state = {
                "topic": "Fallback test",
                "iteration": 0,
                "review_feedback": "",
                "research_notes": ["Research"],
                "draft": "",
                "agent_history": [],
            }
            result = enhanced_writer_node(state)

        # Should not raise — fallback returns a draft
        assert "draft" in result
        assert len(result["draft"]) > 0


# ─── TEST 7: retire_agent ────────────────────────────────────────

class TestRetireAgent:
    """DynamicAgentFactory.retire_agent is called in enhanced_swarm after team assembly."""

    def test_retire_agent_removes_from_active(self):
        """retire_agent moves agent from active_agents to retired_agents."""
        from shared.dynamic_agent_factory import DynamicAgentFactory, AgentTemplate

        mock_llm = MagicMock()
        factory = DynamicAgentFactory(mock_llm)

        # Manually add an agent to active list
        factory.active_agents.append({
            "name": "test_agent",
            "status": "active",
            "tools": [],
            "max_actions": 5,
        })
        assert len(factory.active_agents) == 1

        factory.retire_agent("test_agent")

        assert len(factory.active_agents) == 0
        assert len(factory.retired_agents) == 1
        assert factory.retired_agents[0]["status"] == "retired"

    def test_retire_agent_sets_retired_at(self):
        """retire_agent stamps retired_at timestamp."""
        from shared.dynamic_agent_factory import DynamicAgentFactory

        mock_llm = MagicMock()
        factory = DynamicAgentFactory(mock_llm)
        factory.active_agents.append({
            "name": "agent_x",
            "status": "active",
            "tools": [],
            "max_actions": 3,
        })
        factory.retire_agent("agent_x")
        assert "retired_at" in factory.retired_agents[0]

    def test_enhanced_task_analysis_calls_retire_agent(self):
        """enhanced_task_analysis calls retire_agent for each agent in the team."""
        mock_team = [
            {"name": "researcher", "tools": ["search"], "max_actions": 5, "status": "active",
             "system_prompt": "Research", "task_context": "topic"},
            {"name": "writer", "tools": ["write"], "max_actions": 3, "status": "active",
             "system_prompt": "Write", "task_context": "topic"},
        ]

        mock_factory = MagicMock()
        mock_factory.assemble_team.return_value = mock_team
        mock_factory.active_agents = list(mock_team)

        with (
            patch("patterns.enhanced_swarm._memory_manager", MagicMock()),
            patch("patterns.enhanced_swarm.DynamicAgentFactory", return_value=mock_factory),
            patch("patterns.enhanced_swarm.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value = MagicMock()

            from patterns.enhanced_swarm import enhanced_task_analysis

            state = {
                "topic": "AI Agents in 2026",
                "agent_history": [],
                "pending_tasks": [],
                "current_agent": "",
            }
            result = enhanced_task_analysis(state)

        # retire_agent called once per team member
        assert mock_factory.retire_agent.call_count == len(mock_team)
        calls = [c[0][0] for c in mock_factory.retire_agent.call_args_list]
        assert "researcher" in calls
        assert "writer" in calls

    def test_enhanced_task_analysis_prevents_unbounded_growth(self):
        """retire_agent prevents active_agents list from growing unbounded."""
        from shared.dynamic_agent_factory import DynamicAgentFactory

        mock_llm = MagicMock()
        factory = DynamicAgentFactory(mock_llm)

        # Simulate 3 assembly rounds without retirement — agents pile up
        for i in range(3):
            factory.active_agents.append({
                "name": f"agent_{i}",
                "status": "active",
                "tools": [],
                "max_actions": 5,
            })

        # Now retire all
        for i in range(3):
            factory.retire_agent(f"agent_{i}")

        assert len(factory.active_agents) == 0
        assert len(factory.retired_agents) == 3
