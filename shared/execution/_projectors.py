"""Projectors fold event streams into current state.

Each projector is a pure function: deterministic and idempotent.
Replaying an event twice produces the same state.
"""

from __future__ import annotations

import copy
from typing import Protocol

from shared.execution._event_store import Event, EventStore


class Projector(Protocol):
    def initial_state(self) -> dict: ...
    def apply(self, state: dict, event: Event) -> dict: ...


class ScanProjector:
    def initial_state(self) -> dict:
        return {
            "platforms_done": [],
            "platforms_in_progress": None,
            "jobs_found": 0,
            "jobs_screened": 0,
            "job_cursor": 0,
        }

    def apply(self, state: dict, event: Event) -> dict:
        t = event["event_type"]
        p = event["payload"]
        if t == "scan.platform_started":
            state["platforms_in_progress"] = p["platform"]
        elif t == "scan.platform_done":
            if p["platform"] not in state["platforms_done"]:
                state["platforms_done"].append(p["platform"])
            state["platforms_in_progress"] = None
            state["jobs_found"] += p.get("count", 0)
        elif t == "scan.job_screened":
            state["jobs_screened"] += 1
            state["job_cursor"] = p.get("job_index", state["job_cursor"])
        return state


class FormProjector:
    def initial_state(self) -> dict:
        return {
            "current_page": 0,
            "total_pages_est": 0,
            "pages_filled": [],
            "auth_status": "pending",
            "auth_method": "",
            "submitted": False,
            "dry_run": None,
            "field_results": {},
        }

    def apply(self, state: dict, event: Event) -> dict:
        t = event["event_type"]
        p = event["payload"]
        if t == "form.auth_complete":
            state["auth_status"] = "complete"
            state["auth_method"] = p.get("method", "")
        elif t == "form.page_detected":
            state["current_page"] = p.get("page", state["current_page"])
            state["total_pages_est"] = p.get("total_est", state["total_pages_est"])
        elif t == "form.fields_filled":
            page = p.get("page", state["current_page"])
            state["field_results"][page] = p.get("results", [])
        elif t == "form.page_verified":
            pass
        elif t == "form.page_advanced":
            from_page = p.get("from", state["current_page"])
            if from_page not in state["pages_filled"]:
                state["pages_filled"].append(from_page)
            state["current_page"] = p.get("to", state["current_page"] + 1)
        elif t == "form.submitted":
            state["submitted"] = True
            state["dry_run"] = p.get("dry_run")
        return state


class PatternProjector:
    def initial_state(self) -> dict:
        return {
            "iteration": 0,
            "status": "pending",
            "last_quality": 0.0,
            "last_accuracy": 0.0,
            "final_score": 0.0,
        }

    def apply(self, state: dict, event: Event) -> dict:
        t = event["event_type"]
        p = event["payload"]
        if t == "pattern.iteration_started":
            state["iteration"] = p.get("iteration", state["iteration"] + 1)
            state["status"] = "running"
        elif t == "pattern.review_scored":
            state["last_quality"] = p.get("quality", 0.0)
            state["last_accuracy"] = p.get("accuracy", 0.0)
        elif t == "pattern.converged":
            state["status"] = "converged"
            state["final_score"] = p.get("final_score", 0.0)
        elif t == "pattern.finished":
            state["status"] = "finished"
        return state


def project_stream(store: EventStore, stream_id: str, projector: Projector) -> dict:
    """Replay all events in a stream through a projector to get current state."""
    snap = store.load_snapshot(stream_id)
    if snap:
        state = copy.deepcopy(snap["snapshot_state"])
        events = store.get_stream(stream_id, after_event_id=snap["last_event_id"])
    else:
        state = projector.initial_state()
        events = store.get_stream(stream_id)
    for event in events:
        state = projector.apply(state, event)
    return state
