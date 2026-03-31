# Shared Modules

Cross-cutting utilities used by all systems. Dependency flows ONE WAY: systems import from shared/, never the reverse.

## Key Modules
- agents.py — get_llm(), orchestration agent nodes (researcher, writer, reviewer, fact_checker)
- fact_checker.py — Unified 3-level verification (research notes → web search → cache). Used by patterns AND jobpulse.
- state.py — AgentState definition for LangGraph
- nlp_classifier.py — 3-tier intent classification (regex → embeddings → LLM fallback)

## Rules
- NEVER import from patterns/, jobpulse/, or mindgraph_app/
- NEVER instantiate ChatOpenAI directly — always use get_llm() from agents.py
- All new shared utilities go here, not duplicated across systems
