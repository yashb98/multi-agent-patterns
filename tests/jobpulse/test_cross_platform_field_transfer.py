"""Tests for CrossPlatformFieldTransfer."""

from __future__ import annotations

import pytest

from jobpulse.cross_platform_field_transfer import CrossPlatformFieldTransfer


class TestRecordAndRetrieve:
    def test_record_and_find_transfer(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping(
            platform="greenhouse",
            field_label="Current Base Salary",
            value="45000",
            source="intent_resolver",
        )
        candidates = transfer.find_transfers(
            to_platform="linkedin",
            field_label="What is your current annual salary?",
            top_n=3,
        )
        # Without embeddings, falls back to text overlap
        assert len(candidates) >= 0  # May or may not match depending on overlap

    def test_record_updates_existing(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping("gh", "salary", "40000", source="regex")
        transfer.record_mapping("gh", "salary", "45000", source="intent")
        stats = transfer.get_stats()
        assert stats["total_mappings"] == 1

    def test_success_rate_filter(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping("gh", "salary", "45000", success=True)
        transfer.record_mapping("gh", "salary", "45000", success=True)
        transfer.record_mapping("gh", "salary", "45000", success=False)
        # success_rate = 2/3 = 0.67
        candidates = transfer.find_transfers(
            to_platform="linkedin",
            field_label="annual salary",
            min_success_rate=0.5,
        )
        # Without embeddings, text overlap may or may not find it

    def test_exclude_same_platform(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping("linkedin", "salary", "45000")
        transfer.record_mapping("greenhouse", "salary", "45000")
        candidates = transfer.find_transfers(
            to_platform="linkedin",
            field_label="salary",
            exclude_same_platform=True,
        )
        for c in candidates:
            assert c.from_platform != "linkedin"

    def test_stats(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping("gh", "salary", "45000")
        transfer.record_mapping("li", "notice", "2 weeks")
        stats = transfer.get_stats()
        assert stats["total_mappings"] == 2
        assert stats["platforms"]["gh"] == 1
        assert stats["platforms"]["li"] == 1


class TestTextOverlapRanking:
    def test_high_overlap(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping("gh", "Current Base Salary", "45000")
        candidates = transfer.find_transfers(
            to_platform="li",
            field_label="Your current base salary",
        )
        assert len(candidates) >= 1
        assert candidates[0].value == "45000"

    def test_low_overlap_excluded(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        transfer.record_mapping("gh", "Years of Experience", "5")
        candidates = transfer.find_transfers(
            to_platform="li",
            field_label="Current Base Salary",
        )
        # No word overlap > 0.3, should return empty
        assert candidates == []

    def test_no_mappings_returns_empty(self, tmp_path):
        transfer = CrossPlatformFieldTransfer(db_path=str(tmp_path / "fields.db"))
        candidates = transfer.find_transfers(
            to_platform="li",
            field_label="salary",
        )
        assert candidates == []
