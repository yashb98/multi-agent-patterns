"""Health API — daemon status, agent success rates, error logs, rate limits."""
from fastapi import APIRouter
from shared.logging_config import get_logger

logger = get_logger(__name__)
health_router = APIRouter(prefix="/api/health")


@health_router.get("/status")
def get_status():
    """Overall system health: daemon heartbeat, uptime, connected platforms."""
    from jobpulse.healthcheck import check_daemon_health
    from jobpulse.config import TELEGRAM_BOT_TOKEN, SLACK_BOT_TOKEN, DISCORD_BOT_TOKEN
    health = check_daemon_health()
    platforms = []
    if TELEGRAM_BOT_TOKEN: platforms.append("telegram")
    if SLACK_BOT_TOKEN: platforms.append("slack")
    if DISCORD_BOT_TOKEN: platforms.append("discord")
    return {
        "daemon": health,
        "platforms": platforms,
    }


@health_router.get("/errors")
def get_errors(limit: int = 50):
    """Recent errors from agent process trails."""
    try:
        from jobpulse.process_logger import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT run_id, agent_name, step_name, step_output, created_at "
            "FROM agent_process_trails WHERE status='error' "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return {"errors": [dict(r) for r in rows]}
    except Exception as e:
        logger.error("Failed to fetch errors: %s", e)
        return {"errors": [], "error": str(e)}


@health_router.get("/agents")
def get_agent_health():
    """Per-agent success rates and stats."""
    try:
        from jobpulse.process_logger import get_agent_stats
        return {"agents": get_agent_stats()}
    except Exception as e:
        logger.error("Failed to fetch agent stats: %s", e)
        return {"agents": [], "error": str(e)}


@health_router.get("/rate-limits")
def get_rate_limit_status():
    """Current API rate limit status."""
    try:
        from shared.rate_monitor import get_current_limits
        return {"limits": get_current_limits()}
    except Exception as e:
        logger.error("Failed to fetch rate limits: %s", e)
        return {"limits": [], "error": str(e)}


@health_router.post("/export")
def trigger_export():
    """Trigger a full data export."""
    from jobpulse.export import export_all
    path = export_all()
    return {"status": "ok", "path": path}
