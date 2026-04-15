"""Tests for /cancel command."""

import pytest


def test_cancel_intent_in_both_dispatchers():
    """cancel intent should be recognized by both dispatchers."""
    from jobpulse.handler_registry import get_handler_map
    from jobpulse.command_router import Intent

    handler_map = get_handler_map()
    assert Intent.CANCEL in handler_map, "Intent.CANCEL missing from handler_registry"

    # swarm_dispatcher uses handler_registry too, but also has SIMPLE_INTENTS.
    # Verify CANCEL is in SIMPLE_INTENTS so swarm routes it directly.
    from jobpulse.swarm_dispatcher import analyze_task
    from jobpulse.command_router import ParsedCommand
    cmd = ParsedCommand(intent=Intent.CANCEL, args="", raw="/cancel")
    tasks = analyze_task(cmd, None)
    assert len(tasks) == 1
    assert tasks[0]["agent"] == Intent.CANCEL.value


def test_cancel_handler_sets_event():
    """_handle_cancel should set the module-level _cancel_event."""
    from jobpulse.dispatcher import _handle_cancel, _cancel_event
    from jobpulse.command_router import ParsedCommand, Intent

    _cancel_event.clear()
    cmd = ParsedCommand(intent=Intent.CANCEL, args="", raw="/cancel")
    reply = _handle_cancel(cmd)

    assert _cancel_event.is_set(), "_cancel_event should be set after _handle_cancel"
    assert "cancel" in reply.lower() or "stop" in reply.lower()


def test_cancel_handler_returns_confirmation():
    """_handle_cancel should return a user-friendly confirmation string."""
    from jobpulse.dispatcher import _handle_cancel
    from jobpulse.command_router import ParsedCommand, Intent

    cmd = ParsedCommand(intent=Intent.CANCEL, args="", raw="/cancel")
    reply = _handle_cancel(cmd)

    assert isinstance(reply, str)
    assert len(reply) > 0


def test_cancel_pattern_matches_slash_cancel():
    """'/cancel' should classify as CANCEL intent."""
    from jobpulse.command_router import classify_rule_based, Intent

    result = classify_rule_based("/cancel")
    assert result is not None
    assert result.intent == Intent.CANCEL


def test_cancel_pattern_matches_cancel_scan():
    """'cancel scan' should classify as CANCEL intent."""
    from jobpulse.command_router import classify_rule_based, Intent

    result = classify_rule_based("cancel scan")
    assert result is not None
    assert result.intent == Intent.CANCEL
