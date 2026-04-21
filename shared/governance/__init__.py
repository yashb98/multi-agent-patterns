"""Security & Governance — Pillar 5.

Score validation, output sanitization, API auth, and policy enforcement.
"""

from shared.governance._score_validator import (
    ReviewResult, clamp_score, validate_review,
    reset_anomaly_counter, get_anomaly_count,
)
from shared.governance._output_sanitizer import (
    sanitize_agent_output, strip_dangerous_tags,
    create_state_sanitizer, SANITIZE_FIELDS,
)
from shared.governance._api_auth import BearerAuthMiddleware, require_auth
from shared.governance._policy_engine import (
    PolicyResult, PolicyEnforcer, PolicyViolation,
    check_policy, POLICIES,
)
