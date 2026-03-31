# /test-agent — Run tests for a specific agent

## Usage
Provide the agent name (e.g., "budget", "gmail", "arxiv") and this runs all relevant tests.

## Steps

1. Identify the agent file (e.g., "budget" → `jobpulse/budget_agent.py`)
2. Find all test files that reference this agent: `grep -rl "<agent_name>" tests/`
3. Run only those tests: `python -m pytest <found_files> -v`
4. Check if the agent's intents exist in both dispatchers (run the check-dispatch logic for just this agent's intents)
5. Print: tests passed/failed, dispatch status, related NLP intents
