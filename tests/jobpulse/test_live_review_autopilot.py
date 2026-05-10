from __future__ import annotations

import asyncio
import json
import threading
from datetime import date

def test_approve_jobs_starts_live_review(monkeypatch):
    from jobpulse import job_autopilot

    monkeypatch.setattr(
        job_autopilot,
        "_load_actionable_pending",
        lambda: [
            {
                "job_id": "job-1",
                "title": "Backend Engineer",
                "company": "Acme",
                "platform": "greenhouse",
                "location": "Remote",
                "ats_score": 88.5,
                "url": "https://example.com/jobs/1",
            }
        ],
    )

    class FakeDB:
        def get_application(self, job_id):
            return {
                "cv_path": "/tmp/cv.pdf",
                "cover_letter_path": "/tmp/cl.pdf",
                "notion_page_id": "notion-1",
                "match_tier": "review",
                "matched_projects": "[]",
            }

        def get_application_by_notion_page_id(self, page_id):
            return None

        def get_listing(self, job_id):
            return {
                "url": "https://example.com/jobs/1",
                "ats_platform": "greenhouse",
                "location": "Remote",
            }

    monkeypatch.setattr(job_autopilot, "JobDB", lambda: FakeDB())
    monkeypatch.setattr(
        "jobpulse.application_materials.ensure_tailored_cv_for_job",
        lambda job_id, db=None: None,
    )

    captured: dict[str, object] = {}

    def fake_start_live_review(payload, **_kwargs):
        captured["payload"] = payload
        return {"started": True, "message": "ok"}

    monkeypatch.setattr(
        "jobpulse.live_review_applicator.start_live_review",
        fake_start_live_review,
    )

    result = job_autopilot.approve_jobs("1")

    assert "Starting live review" in result
    assert "`yes`" in result
    assert "`no`" in result
    assert "draft" not in result.lower()
    assert captured["payload"]["job_id"] == "job-1"
    assert captured["payload"]["url"] == "https://example.com/jobs/1"


def test_approve_jobs_with_pending_rows_skips_file_queue(monkeypatch):
    from jobpulse import job_autopilot

    monkeypatch.setattr(
        job_autopilot,
        "_load_actionable_pending",
        lambda: [],
    )

    class FakeDB:
        def get_application(self, job_id):
            return {
                "cv_path": "/tmp/cv.pdf",
                "notion_page_id": "n1",
                "match_tier": "review",
                "matched_projects": "[]",
            }

        def get_application_by_notion_page_id(self, page_id):
            return self.get_application("")

        def get_listing(self, job_id):
            return {"url": "https://example.com/x", "ats_platform": None, "location": "UK"}

    monkeypatch.setattr(job_autopilot, "JobDB", lambda: FakeDB())

    def fake_start_live_review(payload, **_kwargs):
        return {"started": True, "message": "ok"}

    monkeypatch.setattr(
        "jobpulse.live_review_applicator.start_live_review",
        fake_start_live_review,
    )

    override = [
        {
            "notion_page_id": "n1",
            "job_id": "jid99",
            "title": "Role",
            "company": "Co",
            "platform": "linkedin",
            "location": "UK",
            "url": "https://example.com/x",
            "ats_score": 90.0,
        },
    ]
    result = job_autopilot.approve_jobs("1", pending_rows=override)
    assert "Starting live review" in result


def test_parse_job_apply_next_cli():
    from jobpulse.job_autopilot import parse_job_apply_next_cli

    assert parse_job_apply_next_cli(["runner", "job-apply-next"]) == ("1", None)
    assert parse_job_apply_next_cli(["runner", "job-apply-next", "2"]) == ("2", None)
    assert parse_job_apply_next_cli(["runner", "job-apply-next", "2026-04-23"]) == (
        "1",
        date(2026, 4, 23),
    )
    assert parse_job_apply_next_cli(["runner", "job-apply-next", "3", "2026-04-23"]) == (
        "3",
        date(2026, 4, 23),
    )


def test_approve_jobs_rejects_multi_select(monkeypatch):
    from jobpulse import job_autopilot

    monkeypatch.setattr(
        job_autopilot,
        "_load_actionable_pending",
        lambda: [
            {"job_id": "job-1", "title": "A", "company": "A Co", "platform": "greenhouse"},
            {"job_id": "job-2", "title": "B", "company": "B Co", "platform": "lever"},
        ],
    )

    result = job_autopilot.approve_jobs("1,2")

    assert "One live application at a time" in result


def test_show_pending_jobs_includes_active_review(monkeypatch):
    from jobpulse import job_autopilot

    monkeypatch.setattr(
        job_autopilot,
        "_rebuild_pending_from_notion",
        lambda found_on=None: [
            {
                "notion_page_id": "n1",
                "title": "Backend Engineer",
                "company": "Acme",
                "platform": "greenhouse",
                "location": "Remote",
                "url": "https://example.com/jobs/1",
                "ats_score": 88.5,
            }
        ],
    )

    monkeypatch.setattr(
        "jobpulse.live_review_applicator.get_active_review",
        lambda: {
            "title": "ML Engineer",
            "company": "Beta",
            "platform": "linkedin",
        },
    )

    result = job_autopilot.show_pending_jobs()

    assert "Currently reviewing" in result
    assert "ML Engineer — Beta" in result
    assert "1. Backend Engineer — Acme" in result
    assert '"apply 1"' in result


def test_load_actionable_pending_uses_cache_then_notion(monkeypatch):
    from jobpulse import job_autopilot

    cached = [
        {
            "notion_page_id": "n1",
            "title": "Backend Engineer",
            "company": "Acme",
            "platform": "greenhouse",
            "url": "https://example.com/jobs/1",
            "ats_score": 88.5,
        }
    ]
    monkeypatch.setattr(job_autopilot, "_load_pending", lambda: cached)

    jobs = job_autopilot._load_actionable_pending()

    assert len(jobs) == 1
    assert jobs[0]["notion_page_id"] == "n1"


def test_load_actionable_pending_rebuilds_from_notion_when_cache_empty(monkeypatch):
    from jobpulse import job_autopilot

    monkeypatch.setattr(job_autopilot, "_load_pending", lambda: [])
    saved: dict[str, object] = {}
    monkeypatch.setattr(job_autopilot, "_save_pending", lambda jobs: saved.setdefault("jobs", jobs))

    notion_rows = [
        {
            "notion_page_id": "n1",
            "title": "Backend Engineer",
            "company": "Acme",
            "platform": "greenhouse",
            "location": "Remote",
            "url": "https://example.com/jobs/1",
            "ats_score": 88.5,
            "ats_platform": "greenhouse",
            "found_date": "2026-04-22",
            "salary": "",
            "matched_projects": [],
        }
    ]
    monkeypatch.setattr(
        job_autopilot,
        "fetch_found_jobs_from_notion",
        lambda found_on=None: notion_rows,
    )

    jobs = job_autopilot._load_actionable_pending()

    assert len(jobs) == 1
    assert jobs[0]["notion_page_id"] == "n1"
    assert jobs[0]["title"] == "Backend Engineer"
    assert "jobs" in saved


def test_get_active_review_falls_back_to_persisted_file(monkeypatch, tmp_path):
    from jobpulse import live_review_applicator
    import os, time

    review_file = tmp_path / "live_review_active.json"
    review_file.write_text(
        json.dumps(
            {
                "session_id": "sess-1",
                "url": "https://example.com/jobs/1",
                # pid + started_at required by _load_persisted_review's
                # freshness/owner check (added in production after the
                # original test was written).
                "pid": os.getpid(),
                "started_at": time.time(),
                "job": {
                    "job_id": "job-1",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "platform": "greenhouse",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(live_review_applicator, "_ACTIVE_REVIEW_FILE", review_file)
    monkeypatch.setattr(live_review_applicator, "_active_session", None)

    result = live_review_applicator.get_active_review()

    assert result == {
        "session_id": "sess-1",
        "job_id": "job-1",
        "title": "Backend Engineer",
        "company": "Acme",
        "platform": "greenhouse",
        "url": "https://example.com/jobs/1",
    }


def test_resume_persisted_review_action_rejects_and_restores_pending(monkeypatch, tmp_path):
    from jobpulse import live_review_applicator

    import os, time
    review_file = tmp_path / "live_review_active.json"
    review_file.write_text(
        json.dumps(
            {
                "session_id": "sess-1",
                "url": "https://example.com/jobs/1",
                "approval_page_url": "https://example.com/jobs/1/apply",
                "pid": os.getpid(),
                "started_at": time.time(),
                "job": {
                    "job_id": "job-1",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "platform": "greenhouse",
                    "url": "https://example.com/jobs/1",
                    "cv_path": "/tmp/cv.pdf",
                },
                "fill_result": {"success": True},
                "agent_mapping": {"Email": "a@b.com"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(live_review_applicator, "_ACTIVE_REVIEW_FILE", review_file)

    updates: list[tuple[str, str]] = []

    class FakeDB:
        def update_status(self, job_id, new_status):
            updates.append((job_id, new_status))

        def get_application(self, job_id):
            return {"status": "Pending Approval"}

        def get_listing(self, job_id):
            return None

        def save_application(self, **kwargs):
            pass

    class ImmediateThread:
        def __init__(self, target, args=(), **kwargs):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr("jobpulse.job_db.JobDB", lambda: FakeDB())
    monkeypatch.setattr(live_review_applicator.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(live_review_applicator, "send_telegram", lambda *args, **kwargs: None)

    result = live_review_applicator.resume_persisted_review_action(
        {"kind": "live_review", "session_id": "sess-1"},
        approved=False,
    )

    assert "Cancelled submission" in result
    assert updates == [("job-1", "Pending Approval")]
    assert not review_file.exists()


def test_fill_and_request_approval_mentions_unresolved_fields(monkeypatch):
    from jobpulse import live_review_applicator

    session = live_review_applicator.LiveReviewSession.__new__(
        live_review_applicator.LiveReviewSession
    )
    session.job = {"title": "Backend Engineer", "company": "Acme"}
    session.session_id = "sess-1"
    session.url = "https://example.com/jobs/1"
    session._fill_result = {}
    session._agent_mapping = {}
    session._final_mapping = {}
    session._action = None
    session._action_event = threading.Event()

    def fake_run_async(coro, timeout=None):
        coro.close()
        return {
            "success": True,
            "agent_mapping": {"Email": "a@b.com"},
            "agent_fill_stats": {"failed_labels": ["Country", "Ethnicity"]},
        }

    review_calls: list[dict] = []
    approval_calls: list[str] = []

    monkeypatch.setattr(live_review_applicator, "_run_async", fake_run_async)
    monkeypatch.setattr(session, "_capture_screenshot", lambda: None)
    monkeypatch.setattr(session, "_persist_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_review_applicator, "_bring_chrome_to_front", lambda **kwargs: None)
    monkeypatch.setattr(
        session,
        "_send_review_notification",
        lambda screenshot_path, **kwargs: review_calls.append(kwargs),
    )
    monkeypatch.setattr(
        live_review_applicator,
        "request_approval",
        lambda question, **kwargs: approval_calls.append(question),
    )

    session.fill_and_request_approval()

    assert session._agent_mapping == {"Email": "a@b.com"}
    assert review_calls[0]["unresolved_labels"] == ["Country", "Ethnicity"]
    assert "Country, Ethnicity" in approval_calls[0]


def test_run_submit_and_confirm_re_requests_human_help(monkeypatch):
    from jobpulse import live_review_applicator

    session = live_review_applicator.LiveReviewSession.__new__(
        live_review_applicator.LiveReviewSession
    )
    session.job = {"title": "Backend Engineer", "company": "Acme"}
    session.session_id = "sess-1"
    session.url = "https://example.com/jobs/1"
    session._fill_result = {"agent_fill_stats": {"failed_labels": ["Country"]}}
    session._agent_mapping = {"Country": "UK"}
    session._final_mapping = {"Country": ""}
    session._action = "submit"
    session._action_event = threading.Event()

    requested: list[tuple[list[str], str]] = []

    def fake_run_async(coro, timeout=None):
        coro.close()
        return {"clicked": "submitted", "saw_error": False, "final_url": "https://example.com/review"}

    monkeypatch.setattr(live_review_applicator, "_run_async", fake_run_async)
    monkeypatch.setattr(
        session,
        "_request_manual_help",
        lambda labels, *, reason: requested.append((labels, reason)),
    )

    result = session.run_submit_and_confirm()

    assert result == "awaiting_human"
    assert requested == [(["Country"], "Some fields still need human fixes before submit.")]
    assert session._action is None
    assert session._action_event.is_set() is False


def test_release_detaches_without_closing_tab(monkeypatch):
    from jobpulse import live_review_applicator

    class FakePW:
        def __init__(self):
            self.stopped = False

        async def stop(self):
            self.stopped = True

    class FakeDriver:
        def __init__(self):
            self._pw = FakePW()

    session = live_review_applicator.LiveReviewSession.__new__(
        live_review_applicator.LiveReviewSession
    )
    driver = FakeDriver()
    session._driver = driver
    session._page = object()

    monkeypatch.setattr(
        live_review_applicator,
        "_run_async",
        lambda coro, timeout=None: asyncio.run(coro),
    )

    session.release()

    assert driver._pw.stopped is True
    assert session._driver is None
    assert session._page is None


def test_send_review_notification_sends_review_documents(monkeypatch, tmp_path):
    from jobpulse import live_review_applicator

    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"cv")
    cl_path = tmp_path / "cl.pdf"
    cl_path.write_bytes(b"cl")

    session = live_review_applicator.LiveReviewSession.__new__(
        live_review_applicator.LiveReviewSession
    )
    session.job = {"title": "Backend Engineer", "company": "Acme", "platform": "greenhouse"}
    session.cv_path = cv_path
    session.cover_letter_path = cl_path

    docs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "jobpulse.telegram_bots.send_jobs_document",
        lambda path, caption="": docs.append((path, caption)) or True,
    )
    monkeypatch.setattr(
        "jobpulse.telegram_bots.send_jobs_photo",
        lambda path, caption="": True,
    )

    session._send_review_notification("/tmp/review.png", unresolved_labels=["Country Options"])

    assert docs == [
        (str(cv_path), "CV for review: Backend Engineer @ Acme"),
        (str(cl_path), "Cover letter for review: Backend Engineer @ Acme"),
    ]
