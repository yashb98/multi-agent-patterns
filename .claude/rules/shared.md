# Rules: Shared Modules (shared/**/*)

## Dependency Direction
shared/ modules MUST NOT import from patterns/, jobpulse/, or mindgraph_app/.
Dependency flows one way: systems → shared. Never the reverse.

## get_llm()
All LLM instantiation goes through get_llm() in shared/agents.py.
Never call ChatOpenAI() or similar constructors directly anywhere in the codebase.

## NLP Classifier (shared/nlp_classifier.py)
3-tier pipeline: regex (instant) → semantic embeddings (5ms) → LLM fallback ($0.001).
- When adding intents: add regex patterns first, then embedding examples, then LLM gets it for free.
- 250+ examples across 41 intents.
- Strip trailing punctuation before classification (Whisper adds ".", "!", "?").

## Fact Checker (shared/fact_checker.py)
Unified module used by both patterns/ and jobpulse/.
- 3-level verification: research notes → external (Semantic Scholar, web search) → cache
- Honest scoring: abstract-only verification = 0.5 (5.0/10), not 1.0
- Human-readable explanations required for every verification result
- Cache in data/verified_facts.db — tests must use tmp_path
