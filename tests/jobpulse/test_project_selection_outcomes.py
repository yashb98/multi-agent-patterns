"""Tests for project selection outcome tracking."""

from __future__ import annotations

import pytest

from jobpulse.project_selection_outcomes import ProjectOutcomeTracker


class TestProjectOutcomeTracker:
    def test_record_and_retrieve(self, tmp_path):
        tracker = ProjectOutcomeTracker(db_path=str(tmp_path / "outcomes.db"))
        tracker.record_selection(
            project_id="agent-system",
            project_name="Multi-Agent System",
            archetype="ml_engineer",
            ats_score=85,
        )
        top = tracker.top_projects_for("ml_engineer", top_n=5, min_selections=1)
        assert len(top) == 1
        assert top[0].project_id == "agent-system"
        assert top[0].times_selected == 1

    def test_update_outcome(self, tmp_path):
        tracker = ProjectOutcomeTracker(db_path=str(tmp_path / "outcomes.db"))
        tracker.record_selection(
            project_id="agent-system",
            project_name="Multi-Agent System",
            archetype="ml_engineer",
            ats_score=85,
        )
        updated = tracker.update_outcome(
            "agent-system", "ml_engineer", got_interview=True
        )
        assert updated is True

        stats = tracker.get_stats("ml_engineer")
        assert stats["total_interviews"] == 1
        assert stats["interview_rate"] == 1.0

    def test_ranking_by_interview_rate(self, tmp_path):
        tracker = ProjectOutcomeTracker(db_path=str(tmp_path / "outcomes.db"))
        # Project A: 2 selections, 2 interviews = 100%
        tracker.record_selection("proj-a", "Project A", "backend", ats_score=80)
        tracker.record_selection("proj-a", "Project A", "backend", ats_score=82)
        tracker.update_outcome("proj-a", "backend", got_interview=True)
        tracker.update_outcome("proj-a", "backend", got_interview=True)

        # Project B: 3 selections, 1 interview = 33%
        tracker.record_selection("proj-b", "Project B", "backend", ats_score=75)
        tracker.record_selection("proj-b", "Project B", "backend", ats_score=78)
        tracker.record_selection("proj-b", "Project B", "backend", ats_score=77)
        tracker.update_outcome("proj-b", "backend", got_interview=True)

        top = tracker.top_projects_for("backend", top_n=2, min_selections=2)
        assert len(top) == 2
        assert top[0].project_id == "proj-a"  # 100% interview rate
        assert top[1].project_id == "proj-b"  # 33% interview rate

    def test_min_selections_filter(self, tmp_path):
        tracker = ProjectOutcomeTracker(db_path=str(tmp_path / "outcomes.db"))
        tracker.record_selection("proj-a", "Project A", "frontend", ats_score=90)
        tracker.record_selection("proj-a", "Project A", "frontend", ats_score=92)
        tracker.record_selection("proj-b", "Project B", "frontend", ats_score=85)

        top = tracker.top_projects_for("frontend", top_n=5, min_selections=2)
        assert len(top) == 1
        assert top[0].project_id == "proj-a"

    def test_stats_aggregate(self, tmp_path):
        tracker = ProjectOutcomeTracker(db_path=str(tmp_path / "outcomes.db"))
        tracker.record_selection("p1", "P1", "devops", ats_score=80)
        tracker.record_selection("p2", "P2", "devops", ats_score=75)
        tracker.update_outcome("p1", "devops", got_interview=True)

        stats = tracker.get_stats()
        assert stats["distinct_projects"] == 2
        assert stats["total_selections"] == 2
        assert stats["total_interviews"] == 1
        assert stats["interview_rate"] == 0.5

    def test_no_records_returns_empty(self, tmp_path):
        tracker = ProjectOutcomeTracker(db_path=str(tmp_path / "outcomes.db"))
        top = tracker.top_projects_for("unknown", top_n=5)
        assert top == []
        stats = tracker.get_stats("unknown")
        assert stats["distinct_projects"] == 0
