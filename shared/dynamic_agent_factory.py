"""
Dynamic Agent Factory
======================

This module implements runtime agent spawning based on task complexity.
Instead of pre-defining a fixed set of agents, the system:

1. ANALYSES the task to determine required capabilities
2. SELECTS from an agent registry of available specialisations
3. SPAWNS only the agents needed for this specific task
4. RETIRES agents once their sub-task is complete

KEY INSIGHT: The task determines the team, not the other way around.
A blog post about cooking needs different agents than one about 
distributed systems. Pre-defining both teams wastes resources.

COMPLEXITY BUDGET:
The system enforces a max active agent count (default 7).
This is based on coordination overhead research showing that
beyond 7 agents, the orchestrator spends more time managing
communication than agents spend doing useful work.

AGENT LIFECYCLE:
    Task Analysis → Spawn → Execute → Retire → Report
    
Each spawned agent has:
- A dynamically generated system prompt
- A defined set of actions (tools)
- A clear completion criteria
- A retirement trigger

AGENT REGISTRY:
The registry maps capability names to agent templates.
When the task analyzer identifies a needed capability,
the factory looks up the template and instantiates it.
"""

import json
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage


# ─── AGENT TEMPLATE REGISTRY ────────────────────────────────────
# Each template defines a type of agent that CAN be spawned.
# Not all agents are spawned for every task — only those
# whose capabilities match the task requirements.

@dataclass
class AgentTemplate:
    """Blueprint for a dynamically spawnable agent."""
    name: str                      # Unique identifier
    capability: str                # What this agent can do
    description: str               # Human-readable description
    base_prompt: str               # System prompt template
    max_actions: int               # Action complexity limit
    required_tools: list = field(default_factory=list)
    priority: int = 5              # Default priority (1=highest)
    
    def instantiate(self, domain: str, task_context: str) -> dict:
        """Create a live agent instance from this template."""
        # Customise the prompt for the specific domain/task
        prompt = self.base_prompt.format(
            domain=domain,
            task_context=task_context
        )
        return {
            "name": self.name,
            "prompt": prompt,
            "tools": self.required_tools,
            "max_actions": self.max_actions,
            "status": "active",
            "created_at": datetime.now().strftime("%H:%M:%S"),
        }


# ─── DEFAULT AGENT REGISTRY ─────────────────────────────────────

DEFAULT_REGISTRY = {
    "researcher": AgentTemplate(
        name="researcher",
        capability="information_gathering",
        description="Gathers facts, data, and expert opinions",
        base_prompt="""You are a Research Analyst specialising in {domain}.
Gather comprehensive, accurate information for the task: {task_context}
Focus on facts, data points, expert opinions, and primary sources.
Structure findings with clear categories.""",
        max_actions=4,
        required_tools=["search", "summarise"],
        priority=1,
    ),
    "writer": AgentTemplate(
        name="writer",
        capability="content_creation",
        description="Transforms research into polished written content",
        base_prompt="""You are a Technical Writer specialising in {domain}.
Transform research into engaging, accurate content for: {task_context}
Use only provided research. Write clearly for technical professionals.""",
        max_actions=2,
        required_tools=[],
        priority=3,
    ),
    "reviewer": AgentTemplate(
        name="reviewer",
        capability="quality_evaluation",
        description="Evaluates content quality with structured scoring",
        base_prompt="""You are a Technical Editor specialising in {domain}.
Evaluate content quality for: {task_context}
Score on accuracy, completeness, readability, practical value, structure.
Return structured JSON feedback.""",
        max_actions=2,
        required_tools=["fact_check"],
        priority=4,
    ),
    "code_expert": AgentTemplate(
        name="code_expert",
        capability="code_generation",
        description="Creates code examples and technical implementations",
        base_prompt="""You are a Senior Software Engineer specialising in {domain}.
Create clear, production-quality code examples for: {task_context}
Include error handling, comments, and best practices.
Use modern patterns and frameworks.""",
        max_actions=4,
        required_tools=["execute_code", "lint", "test"],
        priority=2,
    ),
    "fact_checker": AgentTemplate(
        name="fact_checker",
        capability="fact_verification",
        description="Verifies claims against authoritative sources",
        base_prompt="""You are a Fact-Checking Specialist for {domain}.
Verify all factual claims in the provided content: {task_context}
Flag unsupported claims, check statistics, verify attributions.
Rate each claim: verified, unverified, or false.""",
        max_actions=3,
        required_tools=["search", "verify"],
        priority=2,
    ),
    "seo_optimizer": AgentTemplate(
        name="seo_optimizer",
        capability="seo_optimization",
        description="Optimises content for search engine visibility",
        base_prompt="""You are an SEO Specialist for {domain} content.
Optimise the content for search visibility: {task_context}
Suggest title improvements, meta descriptions, keyword placement,
header structure, and internal linking opportunities.""",
        max_actions=2,
        required_tools=[],
        priority=5,
    ),
    "data_analyst": AgentTemplate(
        name="data_analyst",
        capability="data_analysis",
        description="Analyses data and creates visualisations",
        base_prompt="""You are a Data Analyst specialising in {domain}.
Analyse available data for: {task_context}
Create meaningful statistics, comparisons, and trends.
Present findings in clear, actionable formats.""",
        max_actions=4,
        required_tools=["compute", "visualise"],
        priority=2,
    ),
    "audience_adapter": AgentTemplate(
        name="audience_adapter",
        capability="audience_adaptation",
        description="Adjusts content tone and complexity for target audience",
        base_prompt="""You are a Communications Specialist for {domain}.
Adapt content for the target audience: {task_context}
Adjust technical depth, tone, examples, and vocabulary.
Ensure accessibility without sacrificing accuracy.""",
        max_actions=2,
        required_tools=[],
        priority=4,
    ),
}


@dataclass
class DynamicAgentFactoryConfig:
    """Configuration for the agent factory."""
    max_active_agents: int = 7         # Hard cap on concurrent agents
    min_agents: int = 2                # Always spawn at least this many
    complexity_threshold_low: float = 0.3   # Below this = simple task
    complexity_threshold_high: float = 0.7  # Above this = complex task


class TaskComplexityAnalyzer:
    """
    Analyses a task to determine its complexity and required capabilities.
    
    COMPLEXITY DIMENSIONS:
    1. Domain breadth — How many knowledge areas does this touch?
    2. Technical depth — How specialised is the required knowledge?
    3. Content type — Does it need code? Data? Visuals?
    4. Audience — Who is this for? (affects adaptation needs)
    5. Quality bar — How critical is accuracy?
    
    OUTPUT:
    A complexity score (0-1) and a list of required capabilities.
    The factory uses these to decide which agents to spawn.
    """
    
    def __init__(self, llm):
        self.llm = llm
    
    def analyze(self, topic: str, requirements: str = "") -> dict:
        """
        Analyse task complexity and return required capabilities.
        
        Returns:
            {
                "complexity_score": float (0-1),
                "required_capabilities": ["capability_1", ...],
                "reasoning": "why these capabilities are needed",
                "suggested_agent_count": int,
            }
        """
        print(f"\n  🔬 TASK COMPLEXITY ANALYSIS")
        print(f"  Topic: {topic}")
        
        analysis_prompt = f"""Analyse this content creation task and determine what 
specialist capabilities are needed.

TOPIC: {topic}
ADDITIONAL REQUIREMENTS: {requirements or 'None specified'}

AVAILABLE CAPABILITIES:
- information_gathering: General research and fact collection
- content_creation: Writing and prose generation
- quality_evaluation: Reviewing and scoring content
- code_generation: Creating code examples and implementations
- fact_verification: Verifying claims against sources
- seo_optimization: Search engine optimisation
- data_analysis: Analysing data and creating statistics
- audience_adaptation: Adjusting content for specific audiences

Return a JSON object with:
{{
    "complexity_score": <float 0.0 to 1.0>,
    "required_capabilities": ["capability_1", "capability_2", ...],
    "reasoning": "<brief explanation>",
    "suggested_agent_count": <int 2-7>
}}

SCORING GUIDE:
- 0.0-0.3: Simple topic, well-known, no code needed
- 0.3-0.7: Moderate complexity, some technical depth, maybe code
- 0.7-1.0: Complex, multi-domain, needs code + data + verification

ALWAYS include: information_gathering, content_creation, quality_evaluation
These are the core pipeline. Add others based on task needs.

Return ONLY the JSON."""
        
        response = self.llm.invoke([
            SystemMessage(content="You analyse task complexity for agent orchestration."),
            HumanMessage(content=analysis_prompt)
        ])
        
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        
        try:
            analysis = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: basic analysis
            analysis = {
                "complexity_score": 0.5,
                "required_capabilities": [
                    "information_gathering",
                    "content_creation",
                    "quality_evaluation"
                ],
                "reasoning": "Default analysis — LLM output parsing failed",
                "suggested_agent_count": 3,
            }
        
        # Enforce constraints
        core = {"information_gathering", "content_creation", "quality_evaluation"}
        caps = set(analysis.get("required_capabilities", []))
        caps.update(core)
        analysis["required_capabilities"] = list(caps)
        
        count = analysis.get("suggested_agent_count", 3)
        analysis["suggested_agent_count"] = max(2, min(7, count))
        
        print(f"  Complexity: {analysis['complexity_score']:.2f}")
        print(f"  Required capabilities: {analysis['required_capabilities']}")
        print(f"  Suggested agents: {analysis['suggested_agent_count']}")
        
        return analysis


class DynamicAgentFactory:
    """
    Spawns and manages agents dynamically based on task analysis.
    
    LIFECYCLE:
    1. Task arrives → TaskComplexityAnalyzer determines needs
    2. Factory matches capabilities to agent templates
    3. Agents are instantiated with domain-specific prompts
    4. Agents execute their sub-tasks
    5. Completed agents are retired (freed from active pool)
    6. Factory reports which agents contributed what
    
    USAGE:
        factory = DynamicAgentFactory(llm)
        team = factory.assemble_team(
            topic="Quantum ML for Drug Discovery",
            requirements="Needs code examples, academic tone"
        )
        # team is a list of instantiated agent configs
    """
    
    def __init__(
        self,
        llm,
        registry: dict = None,
        config: Optional[DynamicAgentFactoryConfig] = None,
    ):
        self.llm = llm
        self.registry = registry or DEFAULT_REGISTRY
        self.config = config or DynamicAgentFactoryConfig()
        self.analyzer = TaskComplexityAnalyzer(llm)
        self.active_agents: list[dict] = []
        self.retired_agents: list[dict] = []
    
    def assemble_team(
        self, topic: str, requirements: str = ""
    ) -> list[dict]:
        """
        Analyse the task and spawn the right team of agents.
        
        Returns a list of instantiated agent configs, each with:
        - name, prompt, tools, max_actions, status, created_at
        """
        print(f"\n{'='*60}")
        print(f"  DYNAMIC AGENT FACTORY")
        print(f"{'='*60}")
        
        # Step 1: Analyse task complexity
        analysis = self.analyzer.analyze(topic, requirements)
        
        # Step 2: Match capabilities to templates
        required_caps = analysis["required_capabilities"]
        target_count = analysis["suggested_agent_count"]
        
        # Find matching templates
        matched = []
        for cap in required_caps:
            for name, template in self.registry.items():
                if template.capability == cap:
                    matched.append(template)
                    break
        
        # Sort by priority (lower = more important)
        matched.sort(key=lambda t: t.priority)
        
        # Enforce agent count limits
        max_agents = min(
            self.config.max_active_agents,
            target_count,
            len(matched)
        )
        selected = matched[:max_agents]
        
        # Step 3: Instantiate agents
        team = []
        for template in selected:
            agent = template.instantiate(
                domain=self._extract_domain(topic),
                task_context=topic
            )
            team.append(agent)
            self.active_agents.append(agent)
        
        print(f"\n  Team assembled ({len(team)} agents):")
        for agent in team:
            print(f"    • {agent['name']} — {len(agent['tools'])} tools, "
                  f"max {agent['max_actions']} actions")
        
        return team
    
    def retire_agent(self, agent_name: str):
        """Retire an agent after its sub-task is complete."""
        for agent in self.active_agents:
            if agent["name"] == agent_name:
                agent["status"] = "retired"
                agent["retired_at"] = datetime.now().strftime("%H:%M:%S")
                self.retired_agents.append(agent)
                self.active_agents.remove(agent)
                print(f"  Agent '{agent_name}' retired")
                return
    
    def register_agent_template(self, template: AgentTemplate):
        """Add a new agent template to the registry at runtime."""
        self.registry[template.name] = template
        print(f"  Registered new template: '{template.name}'")
    
    def create_custom_agent(
        self, name: str, capability_description: str, domain: str
    ) -> AgentTemplate:
        """
        Use LLM to CREATE a new agent template from a description.
        
        This is the most powerful feature: the system can invent
        entirely new agent types that weren't in the registry.
        
        Example:
            factory.create_custom_agent(
                "regulatory_expert",
                "Checks content against GDPR and data privacy regulations",
                "data privacy"
            )
        """
        print(f"\n  🏗️  Creating custom agent: {name}")
        
        creation_prompt = f"""Create a system prompt for an AI agent with this specification:

NAME: {name}
CAPABILITY: {capability_description}
DOMAIN: {domain}

The system prompt should:
1. Define the agent's role clearly
2. Specify its methodology and approach
3. Define output format expectations
4. Include domain-specific constraints
5. Be under 300 words

Also determine:
- What tools this agent would need (from: search, summarise, execute_code, 
  lint, test, verify, compute, visualise, fact_check)
- Maximum actions per task (2-5)
- Priority level (1=critical, 5=nice-to-have)

Return JSON:
{{
    "system_prompt": "<the full system prompt>",
    "tools": ["tool1", "tool2"],
    "max_actions": <int>,
    "priority": <int>
}}"""
        
        response = self.llm.invoke([
            SystemMessage(content="You design AI agent specifications."),
            HumanMessage(content=creation_prompt)
        ])
        
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            spec = {
                "system_prompt": f"You are a {name} specialising in {domain}. {capability_description}",
                "tools": ["search"],
                "max_actions": 3,
                "priority": 3,
            }
        
        template = AgentTemplate(
            name=name,
            capability=capability_description,
            description=capability_description,
            base_prompt=spec["system_prompt"] + "\n\nDomain context: {domain}\nTask: {task_context}",
            max_actions=spec.get("max_actions", 3),
            required_tools=spec.get("tools", []),
            priority=spec.get("priority", 3),
        )
        
        self.register_agent_template(template)
        return template
    
    def _extract_domain(self, topic: str) -> str:
        """Extract a domain identifier from the topic."""
        # Simple extraction — in production, use the LLM
        words = topic.lower().split()
        domain_keywords = {
            "ai", "ml", "machine", "learning", "data", "cloud",
            "web", "mobile", "security", "blockchain", "quantum",
            "finance", "health", "science", "engineering",
        }
        domain = [w for w in words if w in domain_keywords]
        return " ".join(domain[:3]) if domain else "technology"
    
    def get_team_report(self) -> str:
        """Generate a report of the current and retired team."""
        lines = [
            "Dynamic Agent Factory Report",
            "=" * 40,
            f"\nActive agents: {len(self.active_agents)}",
        ]
        for a in self.active_agents:
            lines.append(f"  • {a['name']} (since {a['created_at']})")
        
        lines.append(f"\nRetired agents: {len(self.retired_agents)}")
        for a in self.retired_agents:
            lines.append(f"  • {a['name']} ({a['created_at']} → {a.get('retired_at', '?')})")
        
        total_tools = sum(len(a["tools"]) for a in self.active_agents)
        lines.append(f"\nTotal active tools: {total_tools}")
        lines.append(f"Registered templates: {len(self.registry)}")
        
        return "\n".join(lines)
