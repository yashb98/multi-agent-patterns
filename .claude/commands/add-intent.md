# /add-intent — Scaffold a new intent across all systems

When adding a new command/intent, multiple files must be updated in sync. This command handles all the wiring.

## Steps

1. Ask: What is the intent name? (e.g., SHOW_WEATHER)
2. Ask: What are 3-5 example trigger phrases? (e.g., "weather", "what's the temperature")
3. Ask: Which bot handles it? (main/budget/research/jobs/alert)
4. Ask: What agent function will handle it? (existing file or needs new agent)

Then update ALL of these files:
- `jobpulse/handler_registry.py` — add handler to the shared handler map
- `jobpulse/intent_registry.py` — add intent to the correct intent group
- `jobpulse/command_router.py` — add to Intent enum + classification logic
- Verify both `jobpulse/dispatcher.py` AND `jobpulse/swarm_dispatcher.py` pick it up via `get_handler_map()`
- `jobpulse/nlp_classifier.py` — add embedding examples for the new intent (do NOT add regex — regex tier is legacy)
- Create test in `tests/` for dispatch routing (both dispatchers) + NLP classification

Finally, run `/check-dispatch` to verify everything is in sync.
