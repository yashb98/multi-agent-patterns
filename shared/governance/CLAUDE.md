# Security & Governance (shared/governance/)

Score validation, output sanitization, API auth, and policy enforcement — Pillar 5 of 6.

## Core Components
- **ScoreValidator** (`_score_validator.py`): Clamp scores to [0, 10], detect NaN/Inf/out-of-bounds. Emit governance.score_anomaly after 3+ anomalies.
- **OutputSanitizer** (`_output_sanitizer.py`): Strip dangerous XML/HTML tags from agent output. XML boundary wrapping for defense-in-depth.
- **ApiAuth** (`_api_auth.py`): Bearer token middleware for FastAPI. Kill switch: `API_AUTH_REQUIRED=false`.
- **PolicyEngine** (`_policy_engine.py`): Declarative policies — score bounds, cost cap ($2/run), call limits. Raises PolicyViolation.

## Usage
```python
from shared.governance import validate_review, clamp_score, require_auth

# Validate LLM review output
result = validate_review({"overall_score": 999})
assert result.overall_score == 10.0  # clamped

# Add auth to FastAPI app
require_auth(app)  # no-op if API_AUTH_REQUIRED=false
```

## Rules
- All score validation goes through clamp_score() or validate_review()
- Patterns derive their own review_passed from clamped scores — validator does NOT decide pass/fail
- API auth attaches at the FastAPI app level, not on individual routers
- PolicyViolation is caught by pattern orchestrators, not swallowed silently
- Tests MUST use monkeypatch for env vars — never set real API_AUTH_TOKEN
