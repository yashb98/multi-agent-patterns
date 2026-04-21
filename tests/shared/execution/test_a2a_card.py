import json
import pytest


class TestAgentCard:
    def test_create_card(self):
        from shared.execution._a2a_card import AgentCard, AgentSkill
        card = AgentCard(
            name="scan-agent",
            description="Scans platforms",
            url="http://localhost:8090/a2a/scan-agent",
            skills=[AgentSkill(id="scan-platforms", name="Scan", description="Scan jobs")],
        )
        assert card.name == "scan-agent"
        assert len(card.skills) == 1

    def test_card_to_json(self):
        from shared.execution._a2a_card import AgentCard, AgentSkill
        card = AgentCard(
            name="test", description="t", url="http://localhost",
            skills=[AgentSkill(id="s1", name="S1", description="d")],
        )
        data = card.to_dict()
        assert data["name"] == "test"
        assert data["skills"][0]["id"] == "s1"
        assert "capabilities" in data


class TestFileRegistry:
    def test_register_and_get(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry, AgentCard, AgentSkill
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        card = AgentCard(name="scan-agent", description="s", url="http://localhost", skills=[])
        registry.register(card)
        found = registry.get("scan-agent")
        assert found is not None
        assert found.name == "scan-agent"

    def test_list_all(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry, AgentCard
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        registry.register(AgentCard(name="a1", description="", url="", skills=[]))
        registry.register(AgentCard(name="a2", description="", url="", skills=[]))
        agents = registry.list_all()
        assert len(agents) == 2

    def test_get_unknown_returns_none(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        assert registry.get("nope") is None

    def test_unregister(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry, AgentCard
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        registry.register(AgentCard(name="a1", description="", url="", skills=[]))
        registry.unregister("a1")
        assert registry.get("a1") is None
