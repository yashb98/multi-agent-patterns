"""Google Calendar agent — fetch and create events via direct API."""

import os
import re
from datetime import datetime, timedelta
from jobpulse.config import GOOGLE_TOKEN_PATH, GOOGLE_SCOPES
from jobpulse import event_logger
from shared.google_retry import call_google_api_with_retry
from shared.logging_config import get_logger

logger = get_logger(__name__)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _get_calendar_service():
    """Build Calendar API service using stored OAuth2 token."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(GOOGLE_TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, GOOGLE_SCOPES)

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
        events_result = call_google_api_with_retry(
            lambda: service.events().list(
                calendarId="primary",
                timeMin=start.isoformat() + "Z",
                timeMax=end.isoformat() + "Z",
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            ).execute()
        )

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


def _parse_clock_value(raw: str, base: datetime) -> datetime:
    token = raw.strip().lower().replace(".", "")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", token)
    if not match:
        raise ValueError(f"Could not parse time: {raw}")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
    elif hour == 24 and minute == 0:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid time: {raw}")
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _extract_event_date(text: str, now: datetime) -> tuple[datetime, str | None]:
    lowered = text.lower()
    base = now
    if "tomorrow" in lowered:
        return base + timedelta(days=1), "tomorrow"
    if any(word in lowered for word in ("today", "tonight")):
        return base, "today"

    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        return base.replace(year=year, month=month, day=day), iso_match.group(0)

    uk_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if uk_match:
        day, month, year = map(int, uk_match.groups())
        return base.replace(year=year, month=month, day=day), uk_match.group(0)

    weekday_match = re.search(
        r"\b(?:(next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        lowered,
    )
    if weekday_match:
        want_next = bool(weekday_match.group(1))
        weekday = _WEEKDAYS[weekday_match.group(2)]
        delta = (weekday - base.weekday()) % 7
        if delta == 0 or want_next:
            delta += 7 if delta == 0 else 0
        target = base + timedelta(days=delta)
        return target, weekday_match.group(0)

    return base, None


def parse_event_request(text: str, now: datetime | None = None) -> dict | None:
    """Parse a simple natural-language event request into summary/start/end."""
    if not text or not text.strip():
        return None

    local_now = (now or datetime.now().astimezone()).replace(second=0, microsecond=0)
    event_day, date_fragment = _extract_event_date(text, local_now)

    time_range_match = re.search(
        r"\bfrom\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(?:to|\-)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if time_range_match:
        start = _parse_clock_value(time_range_match.group(1), event_day)
        end = _parse_clock_value(time_range_match.group(2), event_day)
        time_fragment = time_range_match.group(0)
    else:
        at_match = re.search(
            r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
            text,
            flags=re.IGNORECASE,
        )
        if not at_match:
            return None
        start = _parse_clock_value(at_match.group(1), event_day)
        time_fragment = at_match.group(0)

        duration_match = re.search(
            r"\bfor\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b",
            text,
            flags=re.IGNORECASE,
        )
        if duration_match:
            amount = int(duration_match.group(1))
            unit = duration_match.group(2).lower()
            minutes = amount * 60 if unit.startswith(("hour", "hr")) else amount
            end = start + timedelta(minutes=minutes)
            duration_fragment = duration_match.group(0)
        else:
            end = start + timedelta(minutes=30)
            duration_fragment = None

    if end <= start:
        end += timedelta(days=1)

    summary = text
    for fragment in [date_fragment, time_fragment, locals().get("duration_fragment")]:
        if fragment:
            summary = summary.replace(fragment, " ")
    summary = re.sub(
        r"\b(add|set|schedule|book|create)\b",
        " ",
        summary,
        flags=re.IGNORECASE,
    )
    summary = re.sub(r"\b(event|reminder|calendar)\b", " ", summary, flags=re.IGNORECASE)
    summary = re.sub(r"\s+", " ", summary).strip(" ,.-")
    if not summary:
        summary = "New event"

    return {
        "summary": summary,
        "start": start,
        "end": end,
    }


def create_event_from_text(text: str) -> dict:
    """Create a calendar event from a short natural-language request."""
    parsed = parse_event_request(text)
    if parsed is None:
        return {
            "ok": False,
            "error": (
                "I couldn't parse that event yet. Try something like "
                "'add event team sync tomorrow at 3pm for 45 minutes' or "
                "'schedule dentist on 2026-04-25 at 09:30'."
            ),
        }

    service = _get_calendar_service()
    if not service:
        return {
            "ok": False,
            "error": (
                "Google Calendar is not ready. Re-run integration setup so the token "
                "includes calendar write scope."
            ),
        }

    event = {
        "summary": parsed["summary"],
        "start": {"dateTime": parsed["start"].isoformat()},
        "end": {"dateTime": parsed["end"].isoformat()},
    }
    try:
        created = call_google_api_with_retry(
            lambda: service.events().insert(calendarId="primary", body=event).execute()
        )
        return {
            "ok": True,
            "summary": created.get("summary", parsed["summary"]),
            "html_link": created.get("htmlLink", ""),
            "start": parsed["start"],
            "end": parsed["end"],
        }
    except Exception as exc:
        msg = str(exc)
        if "insufficient authentication scopes" in msg.lower():
            msg = (
                "Calendar write scope is missing. Re-run integration setup so Google token "
                "includes calendar event permissions."
            )
        logger.error("Calendar create event failed: %s", exc)
        return {"ok": False, "error": msg}


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
