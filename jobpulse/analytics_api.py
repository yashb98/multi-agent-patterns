"""Analytics API — GRPO success rates, persona drift, cost estimates, trends."""
from fastapi import APIRouter
from shared.logging_config import get_logger

logger = get_logger(__name__)
analytics_router = APIRouter(prefix="/api/analytics")


@analytics_router.get("/grpo")
def get_grpo_stats():
    """GRPO experience success rates grouped by intent."""
    try:
        from jobpulse.swarm_dispatcher import _get_exp_conn
        conn = _get_exp_conn()
        rows = conn.execute(
            "SELECT intent, COUNT(*) as count, AVG(score) as avg_score, "
            "MAX(score) as max_score, MIN(score) as min_score "
            "FROM experiences GROUP BY intent ORDER BY avg_score DESC"
        ).fetchall()
        conn.close()
        return {"intents": [dict(r) for r in rows]}
    except Exception as e:
        logger.error("Failed to fetch GRPO stats: %s", e)
        return {"intents": [], "error": str(e)}


@analytics_router.get("/personas")
def get_persona_stats():
    """Persona evolution status per agent."""
    try:
        from jobpulse.swarm_dispatcher import _get_exp_conn
        conn = _get_exp_conn()
        rows = conn.execute(
            "SELECT agent_name, generation, avg_score, "
            "LENGTH(evolved_prompt) as prompt_length, updated_at "
            "FROM persona_prompts ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return {"personas": [dict(r) for r in rows]}
    except Exception as e:
        logger.error("Failed to fetch persona stats: %s", e)
        return {"personas": [], "error": str(e)}


@analytics_router.get("/costs")
def get_cost_estimates():
    """Estimated cost per agent based on LLM call counts."""
    try:
        from jobpulse.process_logger import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT agent_name, "
            "COUNT(CASE WHEN step_type='llm_call' THEN 1 END) as llm_calls, "
            "COUNT(CASE WHEN step_type='api_call' THEN 1 END) as api_calls, "
            "COUNT(DISTINCT run_id) as total_runs "
            "FROM agent_process_trails "
            "GROUP BY agent_name ORDER BY llm_calls DESC"
        ).fetchall()
        conn.close()
        # Estimate: ~$0.001 per gpt-5o-mini call
        costs = []
        for r in rows:
            d = dict(r)
            d["estimated_cost_usd"] = round((d["llm_calls"] or 0) * 0.001, 4)
            costs.append(d)
        return {"agents": costs}
    except Exception as e:
        logger.error("Failed to fetch cost estimates: %s", e)
        return {"agents": [], "error": str(e)}


@analytics_router.get("/ab-tests")
def get_ab_tests():
    """Get all A/B tests with results."""
    from jobpulse.ab_testing import get_all_tests
    return {"tests": get_all_tests()}


@analytics_router.get("/nlp")
def get_nlp_stats():
    """Get NLP classifier statistics — model, examples, learned count."""
    try:
        from jobpulse.nlp_classifier import get_stats
        return get_stats()
    except ImportError:
        return {"error": "NLP classifier not available"}


@analytics_router.get("/trends")
def get_trends(days: int = 14):
    """Daily dispatch counts, errors, and LLM calls over time."""
    try:
        from jobpulse.process_logger import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT DATE(created_at) as day, "
            "COUNT(DISTINCT run_id) as dispatches, "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors, "
            "SUM(CASE WHEN step_type='llm_call' THEN 1 ELSE 0 END) as llm_calls "
            "FROM agent_process_trails "
            "WHERE created_at >= date('now', ? || ' days') "
            "GROUP BY DATE(created_at) ORDER BY day DESC",
            (f"-{days}",)
        ).fetchall()
        conn.close()
        return {"trends": [dict(r) for r in rows]}
    except Exception as e:
        logger.error("Failed to fetch trends: %s", e)
        return {"trends": [], "error": str(e)}
