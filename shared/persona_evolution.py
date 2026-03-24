"""
Adaptive Persona Evolution System
===================================

This module implements a persona evolution pipeline that:
1. SEARCHES for domain expertise (papers, methodologies, expert styles)
2. SYNTHESISES findings into a working persona
3. COMPRESSES to prevent context bloat
4. VALIDATES against evaluation criteria
5. REPEATS until convergence (score stops improving)

KEY INSIGHT: A persona can only improve if it receives NEW INFORMATION.
Pure iteration without new knowledge input leads to overfitting.
Each cycle must inject fresh external knowledge.

ARCHITECTURE:
    Base Persona
         ↓
    [Search for domain expertise]
         ↓
    [Synthesise into persona traits]
         ↓
    [Compress to core principles]
         ↓
    [Validate against eval set]
         ↓
    Score improved? → Yes → Loop back to Search
                    → No for N cycles → Stop (converged)

CONVERGENCE DETECTION:
We use a patience-based stopping criterion. If the persona score
hasn't improved for `patience` consecutive cycles, we stop.
This prevents both premature stopping (patience too low) and
wasteful iteration (patience too high).
"""

import json
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage


@dataclass
class PersonaEvolutionConfig:
    """Configuration for persona evolution."""
    max_cycles: int = 20              # Hard ceiling on evolution cycles
    patience: int = 4                 # Stop after N cycles without improvement
    improvement_threshold: float = 0.02  # Minimum relative improvement to count
    max_persona_tokens: int = 800     # Compress if persona exceeds this
    compress_target_tokens: int = 400 # Target length after compression
    search_queries_per_cycle: int = 3 # How many knowledge searches per cycle


@dataclass
class PersonaSnapshot:
    """A snapshot of the persona at a point in evolution."""
    cycle: int
    persona_text: str
    score: float
    feedback: str
    knowledge_sources: list = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%H:%M:%S")


class PersonaEvolver:
    """
    Evolves an agent persona through search-synthesise-compress cycles.
    
    The evolver maintains a history of persona snapshots, enabling
    rollback to the best-performing version if evolution degrades quality.
    
    USAGE:
        evolver = PersonaEvolver(llm, config)
        best_persona = evolver.evolve(
            role="researcher",
            domain="quantum computing",
            base_persona="You are a research analyst.",
            evaluator_fn=my_eval_function
        )
    """
    
    def __init__(self, llm, config: Optional[PersonaEvolutionConfig] = None):
        self.llm = llm
        self.config = config or PersonaEvolutionConfig()
        self.history: list[PersonaSnapshot] = []
        self.best_snapshot: Optional[PersonaSnapshot] = None
    
    def evolve(
        self,
        role: str,
        domain: str,
        base_persona: str,
        evaluator_fn,
        search_fn=None,
    ) -> str:
        """
        Main evolution loop. Returns the best persona found.
        
        Parameters:
            role: Agent role (e.g., "researcher", "writer", "reviewer")
            domain: Knowledge domain (e.g., "quantum computing", "fintech")
            base_persona: Starting system prompt
            evaluator_fn: Function(persona_text) -> (score: float, feedback: str)
                         Evaluates how well the persona performs on test cases
            search_fn: Optional function(query) -> str for knowledge retrieval
                       If None, uses LLM's internal knowledge
        
        Returns:
            The best-performing persona text found during evolution.
        """
        print(f"\n{'='*60}")
        print(f"  PERSONA EVOLUTION - {role.upper()}")
        print(f"  Domain: {domain}")
        print(f"  Max cycles: {self.config.max_cycles}")
        print(f"  Patience: {self.config.patience}")
        print(f"{'='*60}")
        
        current_persona = base_persona
        cycles_without_improvement = 0
        
        # Initial evaluation
        score, feedback = evaluator_fn(current_persona)
        self._record_snapshot(0, current_persona, score, feedback)
        
        print(f"\n  Cycle 0 (baseline): Score = {score:.2f}")
        
        for cycle in range(1, self.config.max_cycles + 1):
            print(f"\n  --- Cycle {cycle}/{self.config.max_cycles} ---")
            
            # STEP 1: SEARCH — Gather new domain knowledge
            new_knowledge = self._search_for_expertise(
                role, domain, current_persona, feedback, search_fn
            )
            print(f"  [Search] Found {len(new_knowledge)} knowledge items")
            
            # STEP 2: SYNTHESISE — Integrate knowledge into persona
            evolved_persona = self._synthesise_persona(
                role, domain, current_persona, new_knowledge, feedback
            )
            print(f"  [Synthesise] New persona: {len(evolved_persona.split())} words")
            
            # STEP 3: COMPRESS — Prevent context bloat
            if len(evolved_persona.split()) > self.config.max_persona_tokens:
                evolved_persona = self._compress_persona(
                    evolved_persona, role, domain
                )
                print(f"  [Compress] Reduced to {len(evolved_persona.split())} words")
            
            # STEP 4: VALIDATE — Evaluate the evolved persona
            new_score, new_feedback = evaluator_fn(evolved_persona)
            self._record_snapshot(
                cycle, evolved_persona, new_score, new_feedback, new_knowledge
            )
            
            print(f"  [Validate] Score: {new_score:.2f} (prev: {score:.2f})")
            
            # STEP 5: CONVERGENCE CHECK
            relative_improvement = (new_score - score) / max(score, 0.01)
            
            if relative_improvement > self.config.improvement_threshold:
                # Improvement found — reset patience counter
                current_persona = evolved_persona
                score = new_score
                feedback = new_feedback
                cycles_without_improvement = 0
                print(f"  ✅ Improved by {relative_improvement*100:.1f}%")
            else:
                # No meaningful improvement
                cycles_without_improvement += 1
                feedback = new_feedback  # Still use latest feedback for search
                print(f"  ⚠️  No improvement ({cycles_without_improvement}/{self.config.patience})")
                
                if cycles_without_improvement >= self.config.patience:
                    print(f"\n  🛑 Converged after {cycle} cycles (patience exhausted)")
                    break
        
        # Return the best persona found across all cycles
        best = self.best_snapshot
        print(f"\n  {'='*60}")
        print(f"  EVOLUTION COMPLETE")
        print(f"  Best score: {best.score:.2f} (cycle {best.cycle})")
        print(f"  Total cycles: {len(self.history) - 1}")
        print(f"  {'='*60}")
        
        return best.persona_text
    
    def _search_for_expertise(
        self,
        role: str,
        domain: str,
        current_persona: str,
        feedback: str,
        search_fn=None,
    ) -> list[str]:
        """
        Search for domain knowledge to inform persona evolution.
        
        This is where NEW INFORMATION enters the evolution loop.
        Without this step, the persona just reshuffles existing knowledge.
        
        The search is TARGETED — it uses the latest feedback to identify
        specific knowledge gaps to fill.
        """
        # Generate targeted search queries based on feedback
        query_prompt = f"""Based on this feedback about a {role} agent's performance in the 
{domain} domain, generate {self.config.search_queries_per_cycle} specific search queries 
that would help find knowledge to improve the persona.

Current feedback: {feedback}

Return ONLY a JSON array of query strings. Example:
["query 1", "query 2", "query 3"]"""
        
        response = self.llm.invoke([
            SystemMessage(content="Generate targeted search queries."),
            HumanMessage(content=query_prompt)
        ])
        
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        
        try:
            queries = json.loads(raw)
        except json.JSONDecodeError:
            queries = [f"{domain} {role} best practices methodology"]
        
        # Execute searches
        knowledge_items = []
        for query in queries[:self.config.search_queries_per_cycle]:
            if search_fn:
                result = search_fn(query)
                knowledge_items.append(result)
            else:
                # Use LLM's internal knowledge as fallback
                result = self.llm.invoke([
                    SystemMessage(content=f"You are a domain expert in {domain}."),
                    HumanMessage(content=f"Provide concise expert knowledge about: {query}")
                ])
                knowledge_items.append(result.content[:500])
        
        return knowledge_items
    
    def _synthesise_persona(
        self,
        role: str,
        domain: str,
        current_persona: str,
        new_knowledge: list[str],
        feedback: str,
    ) -> str:
        """
        Synthesise new knowledge into an evolved persona.
        
        This is NOT just appending knowledge to the prompt.
        The LLM must INTEGRATE the knowledge into the persona's
        working style, reasoning approach, and evaluation criteria.
        """
        knowledge_text = "\n\n".join(new_knowledge)
        
        synthesis_prompt = f"""You are an expert prompt engineer evolving an AI agent's persona.

CURRENT PERSONA:
{current_persona}

NEW DOMAIN KNOWLEDGE:
{knowledge_text}

PERFORMANCE FEEDBACK (what needs improvement):
{feedback}

Your task: Create an IMPROVED persona for a {role} agent in the {domain} domain.

RULES:
1. INTEGRATE the new knowledge into the persona's working methodology
2. ADDRESS specific weaknesses identified in the feedback
3. PRESERVE what's already working well in the current persona
4. Be SPECIFIC — "use rigorous analysis" is useless; 
   "cross-reference claims against primary sources before including" is actionable
5. The persona should be a SYSTEM PROMPT, not a knowledge dump
6. Focus on HOW the agent should think and work, not WHAT it should know
7. Keep under {self.config.max_persona_tokens} words

Return ONLY the new persona text. No explanation."""
        
        response = self.llm.invoke([
            SystemMessage(content="You create expert agent personas."),
            HumanMessage(content=synthesis_prompt)
        ])
        
        return response.content.strip()
    
    def _compress_persona(
        self, persona: str, role: str, domain: str
    ) -> str:
        """
        Compress a persona that's grown too large.
        
        This is the FORGETTING mechanism that prevents overfitting.
        It distills the persona to its core principles, discarding
        memorised specifics while preserving generalised expertise.
        
        KEY INSIGHT: Compression forces prioritisation. The LLM must
        decide which instructions are truly essential vs. nice-to-have.
        This often produces a BETTER persona than the verbose original.
        """
        compress_prompt = f"""This {role} agent persona for the {domain} domain has grown 
too verbose ({len(persona.split())} words). Compress it to ~{self.config.compress_target_tokens} words.

RULES FOR COMPRESSION:
1. Keep the CORE METHODOLOGY and REASONING APPROACH
2. Keep SPECIFIC, ACTIONABLE instructions (not vague principles)
3. Remove redundancy and verbose explanations
4. Remove domain facts (the agent can look those up) — keep domain METHODOLOGY
5. The compressed version must be a complete, usable system prompt

CURRENT PERSONA:
{persona}

Return ONLY the compressed persona."""
        
        response = self.llm.invoke([
            SystemMessage(content="Compress to essentials."),
            HumanMessage(content=compress_prompt)
        ])
        
        return response.content.strip()
    
    def _record_snapshot(
        self,
        cycle: int,
        persona: str,
        score: float,
        feedback: str,
        knowledge: list = None,
    ):
        """Record a persona snapshot and update best."""
        snapshot = PersonaSnapshot(
            cycle=cycle,
            persona_text=persona,
            score=score,
            feedback=feedback,
            knowledge_sources=knowledge or [],
        )
        self.history.append(snapshot)
        
        if self.best_snapshot is None or score > self.best_snapshot.score:
            self.best_snapshot = snapshot
    
    def get_evolution_report(self) -> str:
        """Generate a human-readable evolution report."""
        lines = ["Persona Evolution Report", "=" * 40]
        for snap in self.history:
            marker = " ★" if snap == self.best_snapshot else ""
            lines.append(
                f"  Cycle {snap.cycle}: {snap.score:.2f}{marker}"
                f" ({len(snap.persona_text.split())} words)"
            )
        lines.append(f"\nBest: Cycle {self.best_snapshot.cycle} "
                     f"(score {self.best_snapshot.score:.2f})")
        return "\n".join(lines)
