# /add-intent — Scaffold a new intent across all systems

When adding a new command/intent, multiple files must be updated in sync. This command handles all the wiring.

## Steps

1. Ask: What is the intent name? (e.g., SHOW_WEATHER)
2. Ask: What are 3-5 example trigger phrases? (e.g., "weather", "what's the temperature")
3. Ask: Which bot handles it? (main/budget/research/jobs/alert)
4. Ask: What agent function will handle it? (existing file or needs new agent)

Then update ALL of these files:
- `jobpulse/dispatcher.py` — add to AGENT_MAP + correct *_INTENTS set
- `jobpulse/swarm_dispatcher.py` — add to AGENT_MAP + correct *_INTENTS set
- `shared/nlp_classifier.py` — add regex patterns + embedding examples for the new intent
- Create test in `tests/` for dispatch routing (both dispatchers) + NLP classification

Finally, run `/check-dispatch` to verify everything is in sync.
