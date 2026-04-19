"""Tests for per-archetype bullet variants and on-demand generation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.portfolio_variants import (
    MANUAL_VARIANTS,
    get_auto_entry,
    get_or_generate_variant_bullets,
    get_variant_bullets,
    load_auto_portfolio,
    save_auto_portfolio,
)


class TestManualVariants:
    def test_hero_project_has_all_archetypes(self):
        hero = MANUAL_VARIANTS.get("yashb98/multi-agent-patterns", {})
        expected = {"agentic", "data_scientist", "data_analyst", "ai_ml", "data_engineer", "data_platform"}
        assert set(hero.keys()) == expected

    def test_each_variant_has_bullets(self):
        for repo, archetypes in MANUAL_VARIANTS.items():
            for arch, bullets in archetypes.items():
                assert len(bullets) >= 2, f"{repo}/{arch} has too few bullets"
                for b in bullets:
                    assert "<b>" in b, f"{repo}/{arch} bullet missing <b> metric tag"

    def test_no_emdashes_in_variants(self):
        for repo, archetypes in MANUAL_VARIANTS.items():
            for arch, bullets in archetypes.items():
                for b in bullets:
                    assert "\u2014" not in b, f"{repo}/{arch} has em-dash"
                    assert "\u2013" not in b, f"{repo}/{arch} has en-dash"
                    assert "--" not in b, f"{repo}/{arch} has double dash"


class TestGetVariantBullets:
    def test_returns_manual_variant(self):
        result = get_variant_bullets("yashb98/multi-agent-patterns", "data_scientist")
        assert result is not None
        assert len(result) >= 3

    def test_returns_none_for_unknown_repo(self):
        assert get_variant_bullets("yashb98/nonexistent", "data_scientist") is None

    def test_returns_none_for_unknown_archetype(self):
        assert get_variant_bullets("yashb98/multi-agent-patterns", "nonexistent_arch") is None

    def test_different_archetypes_return_different_bullets(self):
        ds = get_variant_bullets("yashb98/multi-agent-patterns", "data_scientist")
        da = get_variant_bullets("yashb98/multi-agent-patterns", "data_analyst")
        assert ds is not None and da is not None
        assert ds[0] != da[0]


class TestOnDemandGeneration:
    def test_manual_variant_returned_instantly(self):
        result = get_or_generate_variant_bullets(
            "yashb98/multi-agent-patterns", "data_analyst",
            "Title", ["default bullet"], ["sql", "dashboards"],
        )
        assert "dashboard" in result[0].lower() or "analytics" in result[0].lower()

    def test_cached_variant_returned_without_llm(self, tmp_path):
        auto_path = tmp_path / "portfolio_auto.json"
        auto_data = {
            "entries": {},
            "variants": {
                "yashb98/cached-repo": {
                    "data_scientist": ["Cached bullet 1", "Cached bullet 2"],
                }
            },
            "last_synced": {},
        }
        auto_path.write_text(json.dumps(auto_data))

        with patch("jobpulse.portfolio_variants._AUTO_PATH", auto_path):
            result = get_or_generate_variant_bullets(
                "yashb98/cached-repo", "data_scientist",
                "Title", ["default"], ["python"],
            )
            assert result == ["Cached bullet 1", "Cached bullet 2"]

    def test_falls_back_to_defaults_on_generation_failure(self, tmp_path):
        auto_path = tmp_path / "portfolio_auto.json"
        auto_path.write_text(json.dumps({"entries": {}, "variants": {}, "last_synced": {}}))

        with patch("jobpulse.portfolio_variants._AUTO_PATH", auto_path), \
             patch("jobpulse.portfolio_variants._generate_jd_aware_bullets", return_value=None):
            result = get_or_generate_variant_bullets(
                "yashb98/unknown", "data_engineer",
                "Title", ["default bullet 1"], ["sql"],
            )
            assert result == ["default bullet 1"]

    def test_generated_variant_gets_cached(self, tmp_path):
        auto_path = tmp_path / "portfolio_auto.json"
        auto_path.write_text(json.dumps({"entries": {}, "variants": {}, "last_synced": {}}))

        fake_bullets = ["Generated bullet A", "Generated bullet B", "Generated bullet C"]

        with patch("jobpulse.portfolio_variants._AUTO_PATH", auto_path), \
             patch("jobpulse.portfolio_variants._generate_jd_aware_bullets", return_value=fake_bullets):
            result = get_or_generate_variant_bullets(
                "yashb98/new-repo", "agentic",
                "Title", ["default"], ["agents"],
            )
            assert result == fake_bullets

            cached = json.loads(auto_path.read_text())
            assert cached["variants"]["yashb98/new-repo"]["agentic"] == fake_bullets


class TestAutoPortfolio:
    def test_load_returns_empty_on_missing_file(self, tmp_path):
        with patch("jobpulse.portfolio_variants._AUTO_PATH", tmp_path / "missing.json"):
            result = load_auto_portfolio()
            assert result == {"entries": {}, "variants": {}, "last_synced": {}}

    def test_save_and_load_roundtrip(self, tmp_path):
        auto_path = tmp_path / "portfolio_auto.json"
        data = {
            "entries": {"yashb98/test": {"title": "Test", "url": "https://...", "bullets": ["b1"]}},
            "variants": {},
            "last_synced": {"yashb98/test": "2026-04-18"},
        }
        with patch("jobpulse.portfolio_variants._AUTO_PATH", auto_path):
            save_auto_portfolio(data)
            loaded = load_auto_portfolio()
            assert loaded["entries"]["yashb98/test"]["title"] == "Test"

    def test_get_auto_entry(self, tmp_path):
        auto_path = tmp_path / "portfolio_auto.json"
        entry = {"title": "Auto Project", "url": "https://github.com/x", "bullets": ["b1"]}
        auto_path.write_text(json.dumps({"entries": {"yashb98/auto": entry}, "variants": {}, "last_synced": {}}))

        with patch("jobpulse.portfolio_variants._AUTO_PATH", auto_path):
            result = get_auto_entry("yashb98/auto")
            assert result["title"] == "Auto Project"
            assert get_auto_entry("yashb98/missing") is None


class TestGetBestProjectsWithArchetype:
    @patch("jobpulse.skill_graph_store.SkillGraphStore")
    def test_archetype_swaps_bullets(self, mock_store_cls):
        from jobpulse.project_portfolio import get_best_projects_for_jd

        mock_match = MagicMock()
        mock_match.name = "yashb98/multi-agent-patterns"
        mock_match.skill_overlap = 5

        mock_store = MagicMock()
        mock_store.get_projects_for_skills.return_value = [mock_match]
        mock_store_cls.return_value = mock_store

        default = get_best_projects_for_jd(["python", "ml"], top_n=1)
        with_arch = get_best_projects_for_jd(["python", "ml"], archetype="data_analyst", top_n=1)

        assert len(default) == 1
        assert len(with_arch) == 1
        assert default[0]["bullets"][0] != with_arch[0]["bullets"][0]
        assert "dashboard" in with_arch[0]["bullets"][0].lower() or "analytics" in with_arch[0]["bullets"][0].lower()

    @patch("jobpulse.skill_graph_store.SkillGraphStore")
    def test_no_archetype_uses_default_bullets(self, mock_store_cls):
        from jobpulse.project_portfolio import PORTFOLIO, get_best_projects_for_jd

        mock_match = MagicMock()
        mock_match.name = "yashb98/multi-agent-patterns"
        mock_match.skill_overlap = 5

        mock_store = MagicMock()
        mock_store.get_projects_for_skills.return_value = [mock_match]
        mock_store_cls.return_value = mock_store

        result = get_best_projects_for_jd(["python"], top_n=1)
        assert result[0]["bullets"] == PORTFOLIO["yashb98/multi-agent-patterns"]["bullets"]
