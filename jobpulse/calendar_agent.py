"""Google Calendar agent — fetches today + tomorrow events via direct API."""

import os
from datetime import datetime, timedelta
from jobpulse.config import GOOGLE_TOKEN_PATH
from jobpulse import event_logger
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _get_calendar_service():
    """Build Calendar API service using stored OAuth2 token."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(GOOGLE_TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH,
                ["https://www.googleapis.com/auth/calendar.readonly"])

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(GOOGLE_TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
            else:
                logger.warning("No valid credentials. Run: python scripts/setup_integrations.py")
                return None

        return build("calendar", "v3", credentials=creds)
    except ImportError:
        logger.warning("Install: pip install google-auth-oauthlib google-api-python-client")
        return None
    except Exception as e:
        logger.error("Auth error: %s", e)
        return None


def _fetch_events(service, start: datetime, end: datetime) -> list[dict]:
    """Fetch events between start and end."""
    try:
        events_result = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = []
        for event in events_result.get("items", []):
            start_raw = event.get("start", {})
            start_dt = start_raw.get("dateTime", start_raw.get("date", ""))
            # Parse to readable time
            try:
                dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                time_str = dt.strftime("%I:%M %p")
            except (ValueError, TypeError) as e:
                logger.debug("Could not parse start time %s: %s", start_dt, e)
                time_str = start_dt

            end_raw = event.get("end", {})
            end_dt = end_raw.get("dateTime", end_raw.get("date", ""))
            try:
                edt = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                end_str = edt.strftime("%I:%M %p")
            except (ValueError, TypeError) as e:
                logger.debug("Could not parse end time %s: %s", end_dt, e)
                end_str = end_dt

            events.append({
                "title": event.get("summary", "(no title)"),
                "start": time_str,
                "end": end_str,
                "location": event.get("location", ""),
                "start_iso": start_dt,
            })
        return events
    except Exception as e:
        logger.error("Error fetching events: %s", e)
        return []


def get_today_and_tomorrow(trigger: str = "scheduled_check") -> dict:
    """Fetch today's and tomorrow's events. Returns dict with today_events and tomorrow_events."""
    from jobpulse.process_logger import ProcessTrail
    trail = ProcessTrail("calendar_agent", trigger)

    with trail.step("api_call", "Connect to Calendar API") as s:
        service = _get_calendar_service()
        if not service:
            s["output"] = "No valid credentials"
            trail.finalize("Failed: no Calendar credentials")
            return {"today_events": [], "tomorrow_events": []}
        s["output"] = "Connected successfully"

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    tomorrow_end = today_start + timedelta(days=2)

    with trail.step("api_call", "Fetch today's events") as s:
        today_events = _fetch_events(service, today_start, today_end)
        s["output"] = f"Found {len(today_events)} events today"
        s["metadata"] = {"count": len(today_events)}

    with trail.step("api_call", "Fetch tomorrow's events") as s:
        tomorrow_events = _fetch_events(service, today_end, tomorrow_end)
        s["output"] = f"Found {len(tomorrow_events)} events tomorrow"
        s["metadata"] = {"count": len(tomorrow_events)}

    # Log each event to simulation
    for ev in today_events:
        event_logger.log_event(
            event_type="calendar_event",
            agent_name="calendar_agent",
            action="fetched_event",
            content=f"{ev['start']} — {ev['title']}",
            metadata={"title": ev["title"], "start": ev["start"], "location": ev.get("location", "")},
        )

    trail.finalize(f"Today: {len(today_events)} events. Tomorrow: {len(tomorrow_events)} events")
    return {
        "today_events": today_events,
        "tomorrow_events": tomorrow_events,
    }


def get_upcoming_reminders(within_minutes: int = 120) -> list[dict]:
    """Get events starting within N minutes (for reminder alerts)."""
    service = _get_calendar_service()
    if not service:
        return []

    now = datetime.utcnow()
    window_end = now + timedelta(minutes=within_minutes)
    events = _fetch_events(service, now, window_end)

    reminders = []
    for event in events:
        try:
            event_time = datetime.fromisoformat(event["start_iso"].replace("Z", "+00:00"))
            diff = (event_time.replace(tzinfo=None) - now).total_seconds() / 60
            if diff > 0:
                hours = int(diff // 60)
                mins = int(diff % 60)
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} min"
                reminders.append({
                    "title": event["title"],
                    "start": event["start"],
                    "location": event.get("location", ""),
                    "in": time_str,
                })
        except (ValueError, TypeError) as e:
            logger.debug("Could not parse reminder time: %s", e)
    return reminders


def format_events(events: list[dict]) -> str:
    """Format events as readable text."""
    if not events:
        return "  No events"
    lines = []
    for e in events:
        loc = f" ({e['location']})" if e.get("location") else ""
        lines.append(f"  • {e['start']} — {e['title']}{loc}")
    return "\n".join(lines)
