import pytest
from shared.execution._event_store import Event


def _make_event(event_type: str, payload: dict, stream_id: str = "test:1") -> Event:
    return Event(
        event_id="fake", stream_id=stream_id, event_type=event_type,
        payload=payload, metadata={}, schema_v=1, created_at="2026-04-21T09:00:00",
    )


class TestScanProjector:
    def test_initial_state(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        assert state["platforms_done"] == []
        assert state["platforms_in_progress"] is None
        assert state["jobs_found"] == 0

    def test_platform_started(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("scan.platform_started", {"platform": "linkedin"}))
        assert state["platforms_in_progress"] == "linkedin"

    def test_platform_done(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("scan.platform_started", {"platform": "linkedin"}))
        state = p.apply(state, _make_event("scan.platform_done", {"platform": "linkedin", "count": 12}))
        assert "linkedin" in state["platforms_done"]
        assert state["platforms_in_progress"] is None
        assert state["jobs_found"] == 12

    def test_idempotent_replay(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        event = _make_event("scan.job_screened", {"job_id": "x", "job_index": 3})
        state = p.initial_state()
        state = p.apply(state, event)
        assert state["job_cursor"] == 3
        assert state["jobs_screened"] == 1

    def test_full_scan_lifecycle(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        events = [
            _make_event("scan.window_started", {"platforms": ["linkedin", "indeed"]}),
            _make_event("scan.platform_started", {"platform": "linkedin"}),
            _make_event("scan.jobs_found", {"platform": "linkedin", "count": 5}),
            _make_event("scan.job_screened", {"job_id": "a", "job_index": 0}),
            _make_event("scan.job_screened", {"job_id": "b", "job_index": 1}),
            _make_event("scan.platform_done", {"platform": "linkedin", "count": 5}),
            _make_event("scan.platform_started", {"platform": "indeed"}),
        ]
        for e in events:
            state = p.apply(state, e)
        assert state["platforms_done"] == ["linkedin"]
        assert state["platforms_in_progress"] == "indeed"
        assert state["jobs_screened"] == 2
        assert state["job_cursor"] == 1


class TestFormProjector:
    def test_initial_state(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        assert state["current_page"] == 0
        assert state["pages_filled"] == []
        assert state["auth_status"] == "pending"

    def test_auth_complete(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("form.auth_complete", {"method": "sso_google"}))
        assert state["auth_status"] == "complete"
        assert state["auth_method"] == "sso_google"

    def test_page_fill_lifecycle(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("form.page_detected", {"page": 1, "total_est": 3}))
        state = p.apply(state, _make_event("form.fields_filled", {
            "page": 1, "results": [{"label": "Name", "value": "Yash", "ok": True}],
        }))
        state = p.apply(state, _make_event("form.page_verified", {"page": 1, "confidence": 0.95}))
        state = p.apply(state, _make_event("form.page_advanced", {"from": 1, "to": 2}))
        assert 1 in state["pages_filled"]
        assert state["current_page"] == 2

    def test_submitted(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("form.submitted", {"dry_run": False}))
        assert state["submitted"] is True
        assert state["dry_run"] is False


class TestPatternProjector:
    def test_initial_state(self):
        from shared.execution._projectors import PatternProjector
        p = PatternProjector()
        state = p.initial_state()
        assert state["iteration"] == 0
        assert state["status"] == "pending"

    def test_iteration_lifecycle(self):
        from shared.execution._projectors import PatternProjector
        p = PatternProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("pattern.iteration_started", {"iteration": 1, "agent": "researcher"}))
        assert state["iteration"] == 1
        assert state["status"] == "running"
        state = p.apply(state, _make_event("pattern.review_scored", {"iteration": 1, "quality": 7.2, "accuracy": 9.1}))
        assert state["last_quality"] == 7.2
        assert state["last_accuracy"] == 9.1

    def test_converged(self):
        from shared.execution._projectors import PatternProjector
        p = PatternProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("pattern.converged", {"iteration": 3, "final_score": 8.4, "reason": "dual_gate"}))
        assert state["status"] == "converged"
        assert state["final_score"] == 8.4


class TestProjectStream:
    def test_project_stream_from_events(self, event_store):
        from shared.execution._projectors import ScanProjector, project_stream
        event_store.emit("scan:t1", "scan.window_started", {"platforms": ["linkedin"]})
        event_store.emit("scan:t1", "scan.platform_started", {"platform": "linkedin"})
        event_store.emit("scan:t1", "scan.platform_done", {"platform": "linkedin", "count": 7})
        state = project_stream(event_store, "scan:t1", ScanProjector())
        assert state["platforms_done"] == ["linkedin"]
        assert state["jobs_found"] == 7
