"""Google OAuth health monitor — detects scope mismatches and expiry."""
import json
from pathlib import Path
from datetime import datetime, timezone
from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR, GOOGLE_SCOPES
from jobpulse.telegram_bots import send_alert

logger = get_logger(__name__)

DEFAULT_TOKEN_PATH = DATA_DIR / "google_token.json"


def check_oauth_health(token_path: Path = None) -> dict:
    """Check Google OAuth token validity and scope coverage."""
    path = token_path or DEFAULT_TOKEN_PATH

    if not path.exists():
        return {"status": "missing", "missing_scopes": [], "message": "Token file not found"}

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "broken", "missing_scopes": [], "message": str(e)}

    token_scopes = set(data.get("scopes", []))
    required_scopes = set(GOOGLE_SCOPES)
    missing = sorted(required_scopes - token_scopes)

    if missing:
        return {
            "status": "scope_mismatch",
            "missing_scopes": missing,
            "message": f"Token missing {len(missing)} scope(s)",
        }

    # Check expiry
    expiry_str = data.get("expiry", "")
    hours_left = None
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            hours_left = (expiry - datetime.now(timezone.utc)).total_seconds() / 3600
        except ValueError:
            pass

    return {
        "status": "healthy",
        "missing_scopes": [],
        "hours_until_expiry": hours_left,
    }


def format_alert(health: dict) -> str | None:
    """Format a Telegram alert for unhealthy OAuth state. Returns None if healthy."""
    status = health["status"]

    if status == "healthy":
        return None

    if status == "missing":
        return (
            "\U0001f511 Google OAuth: token file missing.\n"
            "Run: python scripts/setup_integrations.py"
        )

    if status == "scope_mismatch":
        scopes = ", ".join(health["missing_scopes"])
        return (
            f"\U0001f511 Google OAuth: missing scopes: {scopes}\n"
            "Gmail/Calendar/Drive will fail on next token refresh.\n"
            "Run: python scripts/setup_integrations.py"
        )

    if status == "broken":
        return (
            f"\U0001f511 Google OAuth: token broken — {health.get('message', 'unknown')}\n"
            "Run: python scripts/setup_integrations.py"
        )

    return None


def run_health_check(token_path: Path = None, send_alerts: bool = True) -> dict:
    """Run health check and optionally send Telegram alert."""
    health = check_oauth_health(token_path)
    logger.info("OAuth health: %s", health["status"])

    if send_alerts:
        alert = format_alert(health)
        if alert:
            logger.warning("OAuth alert: %s", alert)
            send_alert(alert)

    return health
