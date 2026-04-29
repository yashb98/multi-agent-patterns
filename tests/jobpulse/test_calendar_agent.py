from datetime import datetime, timezone

from jobpulse.calendar_agent import parse_event_request
from jobpulse.command_router import Intent, classify


def test_parse_event_request_supports_tomorrow_and_duration():
    now = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)

    parsed = parse_event_request(
        "add event team sync tomorrow at 3pm for 45 minutes",
        now=now,
    )

    assert parsed is not None
    assert parsed["summary"] == "team sync"
    assert parsed["start"].day == 23
    assert parsed["start"].hour == 15
    assert int((parsed["end"] - parsed["start"]).total_seconds() / 60) == 45


def test_parse_event_request_supports_explicit_time_range():
    now = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)

    parsed = parse_event_request(
        "schedule interview next monday from 2pm to 3:30pm",
        now=now,
    )

    assert parsed is not None
    assert parsed["summary"] == "interview"
    assert parsed["start"].weekday() == 0
    assert parsed["end"].hour == 15
    assert parsed["end"].minute == 30


def test_calendar_create_event_commands_route_to_create_event():
    parsed = classify("add event team sync tomorrow at 3pm for 45 minutes")
    assert parsed.intent == Intent.CREATE_EVENT
