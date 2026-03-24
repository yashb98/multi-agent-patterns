---
name: compare-patterns
description: Run and compare all orchestration patterns on a given topic
disable-model-invocation: true
---

Compare orchestration patterns on: $ARGUMENTS

1. Ensure `OPENAI_API_KEY` is set in the environment
2. Run `python run_all.py "$ARGUMENTS"`
3. Read the output files from `outputs/`:
   - `outputs/hierarchical_output.md`
   - `outputs/debate_output.md`
   - `outputs/swarm_output.md`
4. Summarize the comparison:
   - Which pattern scored highest?
   - Which was fastest?
   - Which produced the most detailed output?
   - What are the quality differences?
5. Recommend which pattern to use for this specific topic and why
