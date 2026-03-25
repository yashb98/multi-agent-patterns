"""
DSPy + GEPA Prompt Optimisation Integration
=============================================

This module provides the bridge between our multi-agent system
and DSPy's prompt optimization capabilities.

DSPy replaces manual prompt engineering with PROGRAMMATIC optimization.
Instead of tweaking prompt strings by hand, you:
1. Define a SIGNATURE (input/output contract)
2. Define a METRIC (how to measure quality)
3. Run an OPTIMIZER (GEPA, MIPROv2, etc.)
4. Get back an OPTIMIZED prompt automatically

GEPA specifically:
- Uses TEXTUAL FEEDBACK (not just scalar scores)
- Reflects on trajectories to understand WHY prompts fail
- Evolves prompts through a tree of candidates
- Converges in very few rollouts (sample-efficient)

HOW THIS INTEGRATES WITH OUR SYSTEM:
Each agent (researcher, writer, reviewer) becomes a DSPy module.
The optimizer runs on a training set of (topic, expected_output) pairs.
The optimized prompts replace the hardcoded ones in shared/prompts.py.

NOTE: This module provides the ARCHITECTURE and integration pattern.
To actually run DSPy optimization, install dspy-ai and provide
training data. The module works both WITH and WITHOUT DSPy installed.
"""

import json
from typing import Optional
from dataclasses import dataclass, field
from shared.logging_config import get_logger

logger = get_logger(__name__)

from langchain_core.messages import SystemMessage, HumanMessage


@dataclass
class OptimizationResult:
    """Result of a prompt optimization run."""
    original_prompt: str
    optimized_prompt: str
    original_score: float
    optimized_score: float
    improvement_pct: float
    optimizer_used: str
    iterations: int
    training_examples: int


class PromptOptimizer:
    """
    Unified prompt optimization interface.
    
    Supports three backends:
    1. DSPy + GEPA (best, requires dspy-ai package)
    2. DSPy + MIPROv2 (good, requires dspy-ai + more data)
    3. LLM-based meta-optimization (fallback, no extra deps)
    
    The fallback uses our own optimization loop that mimics
    GEPA's core idea: reflect on failures and evolve the prompt.
    
    USAGE:
        optimizer = PromptOptimizer(llm)
        result = optimizer.optimize(
            agent_role="researcher",
            current_prompt="You are a researcher...",
            training_data=[
                {"input": "AI agents", "expected_quality": 8.0},
                {"input": "quantum computing", "expected_quality": 7.5},
            ],
            evaluator_fn=score_output,
        )
        new_prompt = result.optimized_prompt
    """
    
    def __init__(self, llm):
        self.llm = llm
        self._check_dspy_available()
    
    def _check_dspy_available(self):
        """Check if DSPy is installed."""
        try:
            import dspy
            self.dspy_available = True
        except ImportError:
            self.dspy_available = False
    
    def optimize(
        self,
        agent_role: str,
        current_prompt: str,
        training_data: list[dict],
        evaluator_fn,
        method: str = "auto",
    ) -> OptimizationResult:
        """
        Optimize an agent's prompt using the best available method.
        
        Parameters:
            agent_role: "researcher", "writer", or "reviewer"
            current_prompt: Current system prompt to optimize
            training_data: List of {"input": str, "expected_quality": float}
            evaluator_fn: Function(prompt, input) -> (output, score)
            method: "gepa", "mipro", "meta", or "auto"
        
        Returns:
            OptimizationResult with original and optimized prompts
        """
        if method == "auto":
            if self.dspy_available and len(training_data) >= 10:
                method = "gepa"
            else:
                method = "meta"
        
        logger.info("Prompt optimization — %s | method=%s | examples=%d",
                    agent_role, method, len(training_data))
        
        if method == "gepa" and self.dspy_available:
            return self._optimize_with_gepa(
                agent_role, current_prompt, training_data, evaluator_fn
            )
        elif method == "mipro" and self.dspy_available:
            return self._optimize_with_mipro(
                agent_role, current_prompt, training_data, evaluator_fn
            )
        else:
            return self._optimize_with_meta(
                agent_role, current_prompt, training_data, evaluator_fn
            )
    
    def _optimize_with_gepa(
        self, role, prompt, data, evaluator
    ) -> OptimizationResult:
        """
        Optimize using DSPy GEPA.
        
        GEPA uses reflective evolution:
        1. Run the program with current prompt
        2. Collect trajectories and scores
        3. LLM reflects on what went wrong
        4. Propose improved prompt candidates
        5. Evaluate candidates, keep the best
        6. Build a tree of evolved prompts
        """
        import dspy
        
        # Configure DSPy
        lm = dspy.LM("openai/gpt-4o-mini")
        dspy.configure(lm=lm)
        
        # Define signature for the agent
        class AgentSignature(dspy.Signature):
            topic: str = dspy.InputField(desc="The topic to process")
            output: str = dspy.OutputField(desc=f"The {role}'s output")
        
        # Create the module
        agent_module = dspy.ChainOfThought(AgentSignature)
        
        # Prepare training set
        trainset = [
            dspy.Example(
                topic=d["input"],
                output=""  # GEPA will generate and evaluate
            ).with_inputs("topic")
            for d in data
        ]
        
        # Define metric with textual feedback
        def metric(gold, pred, trace=None):
            output = pred.output if hasattr(pred, 'output') else str(pred)
            _, score = evaluator(prompt, gold.topic)
            feedback = f"Score: {score}/10"
            return dspy.Prediction(score=score / 10.0, feedback=feedback)
        
        # Run GEPA optimizer
        optimizer = dspy.GEPA(metric=metric)
        optimized = optimizer.compile(
            agent_module,
            trainset=trainset,
        )
        
        # Extract the optimized prompt
        optimized_prompt = str(optimized)
        
        # Score comparison
        orig_scores = [evaluator(prompt, d["input"])[1] for d in data[:5]]
        new_scores = [evaluator(optimized_prompt, d["input"])[1] for d in data[:5]]
        
        orig_avg = sum(orig_scores) / len(orig_scores)
        new_avg = sum(new_scores) / len(new_scores)
        
        return OptimizationResult(
            original_prompt=prompt,
            optimized_prompt=optimized_prompt,
            original_score=orig_avg,
            optimized_score=new_avg,
            improvement_pct=((new_avg - orig_avg) / max(orig_avg, 0.01)) * 100,
            optimizer_used="GEPA",
            iterations=len(trainset),
            training_examples=len(data),
        )
    
    def _optimize_with_mipro(
        self, role, prompt, data, evaluator
    ) -> OptimizationResult:
        """Optimize using DSPy MIPROv2."""
        # MIPROv2 follows similar pattern to GEPA
        # but uses Bayesian optimization over instructions + demos
        # Implementation mirrors GEPA but with different optimizer
        return self._optimize_with_meta(role, prompt, data, evaluator)
    
    def _optimize_with_meta(
        self, role, prompt, data, evaluator
    ) -> OptimizationResult:
        """
        Fallback: LLM-based meta-optimization.
        
        This mimics GEPA's core loop without the DSPy dependency:
        1. Run the agent on training examples
        2. Collect scores and identify failures
        3. Ask the LLM to reflect on WHY failures happened
        4. Ask the LLM to rewrite the prompt to fix failures
        5. Repeat for N iterations
        
        This is less sophisticated than GEPA (no tree search,
        no population-based evolution) but captures the core idea
        of reflective prompt improvement.
        """
        logger.info("Using meta-optimization (LLM-based reflection)")
        
        current_prompt = prompt
        best_prompt = prompt
        best_score = 0.0
        
        # Initial evaluation
        scores_and_feedback = []
        for d in data[:5]:  # Evaluate on subset
            output, score = evaluator(current_prompt, d["input"])
            scores_and_feedback.append({
                "input": d["input"],
                "score": score,
                "output_preview": output[:200] if output else "",
            })
        
        initial_avg = sum(s["score"] for s in scores_and_feedback) / len(scores_and_feedback)
        best_score = initial_avg
        logger.info("Initial avg score: %.2f", initial_avg)
        
        # Meta-optimization loop
        max_meta_iterations = 5
        for iteration in range(1, max_meta_iterations + 1):
            logger.debug("Meta-iteration %d/%d", iteration, max_meta_iterations)
            
            # Identify failures (below-average scores)
            avg = sum(s["score"] for s in scores_and_feedback) / len(scores_and_feedback)
            failures = [s for s in scores_and_feedback if s["score"] < avg]
            successes = [s for s in scores_and_feedback if s["score"] >= avg]
            
            if not failures:
                logger.debug("No failures to learn from — stopping")
                break
            
            # REFLECT: Ask LLM why failures happened
            reflection_prompt = f"""You are optimizing a system prompt for a {role} agent.

CURRENT PROMPT:
{current_prompt}

SUCCESSFUL EXAMPLES (score >= {avg:.1f}):
{json.dumps(successes[:2], indent=2)}

FAILED EXAMPLES (score < {avg:.1f}):
{json.dumps(failures[:2], indent=2)}

REFLECT:
1. What patterns do the successful examples share?
2. What went wrong in the failed examples?
3. What specific changes to the prompt would fix the failures
   while preserving what works?

Then REWRITE the entire system prompt incorporating these improvements.
Return ONLY the new prompt text. No explanation."""
            
            response = self.llm.invoke([
                SystemMessage(content="You optimize AI agent system prompts through reflection."),
                HumanMessage(content=reflection_prompt)
            ])
            
            candidate_prompt = response.content.strip()
            
            # Evaluate the candidate
            new_scores = []
            for d in data[:5]:
                output, score = evaluator(candidate_prompt, d["input"])
                new_scores.append({
                    "input": d["input"],
                    "score": score,
                    "output_preview": output[:200] if output else "",
                })
            
            new_avg = sum(s["score"] for s in new_scores) / len(new_scores)
            logger.debug("Candidate score: %.2f (prev best: %.2f)", new_avg, best_score)
            
            if new_avg > best_score:
                best_prompt = candidate_prompt
                best_score = new_avg
                current_prompt = candidate_prompt
                scores_and_feedback = new_scores
                logger.info("New best prompt found (%.2f)", new_avg)
            else:
                logger.debug("No improvement, keeping previous best")
        
        improvement = ((best_score - initial_avg) / max(initial_avg, 0.01)) * 100
        
        logger.info("Final: %.2f → %.2f (%+.1f%%)", initial_avg, best_score, improvement)
        
        return OptimizationResult(
            original_prompt=prompt,
            optimized_prompt=best_prompt,
            original_score=initial_avg,
            optimized_score=best_score,
            improvement_pct=improvement,
            optimizer_used="meta-reflection",
            iterations=max_meta_iterations,
            training_examples=len(data),
        )
