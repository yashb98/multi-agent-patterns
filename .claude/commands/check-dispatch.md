# /check-dispatch — Verify intent→agent mapping across both dispatchers

This prevents the most common production bug: adding an intent to one dispatcher but not the other.

## Steps

1. Read `jobpulse/dispatcher.py` — extract all keys from AGENT_MAP dict
2. Read `jobpulse/swarm_dispatcher.py` — extract all keys from AGENT_MAP dict
3. Compare the two sets. Report:
   - ✅ Intents present in BOTH dispatchers
   - ❌ Intents in dispatcher.py but MISSING from swarm_dispatcher.py
   - ❌ Intents in swarm_dispatcher.py but MISSING from dispatcher.py
4. Read `shared/nlp_classifier.py` — extract all intent names from examples/patterns
5. Report intents in dispatchers but missing from NLP classifier
6. Print summary: total intents, mismatches, recommendation

If ANY mismatch is found, print the exact code needed to fix it.
