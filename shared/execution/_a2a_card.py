"""A2A Agent Cards and Registry.

Agent Cards describe agent capabilities in Google A2A-compatible format.
FileAgentRegistry persists cards to JSON (local deployment).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AgentSkill:
    id: str
    name: str
    description: str
    input_modes: list[str] = field(default_factory=lambda: ["application/json"])
    output_modes: list[str] = field(default_factory=lambda: ["application/json"])


@dataclass
class AgentCard:
    name: str
    description: str
    url: str
    skills: list[AgentSkill]
    version: str = "1.0.0"
    streaming: bool = True
    push_notifications: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": {
                "streaming": self.streaming,
                "pushNotifications": self.push_notifications,
                "stateTransitionHistory": True,
            },
            "skills": [
                {
                    "id": s.id, "name": s.name, "description": s.description,
                    "inputModes": s.input_modes, "outputModes": s.output_modes,
                }
                for s in self.skills
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentCard:
        skills = [
            AgentSkill(
                id=s["id"], name=s["name"], description=s["description"],
                input_modes=s.get("inputModes", ["application/json"]),
                output_modes=s.get("outputModes", ["application/json"]),
            )
            for s in data.get("skills", [])
        ]
        caps = data.get("capabilities", {})
        return cls(
            name=data["name"], description=data["description"],
            url=data["url"], skills=skills, version=data.get("version", "1.0.0"),
            streaming=caps.get("streaming", True),
            push_notifications=caps.get("pushNotifications", True),
        )


class AgentRegistry(Protocol):
    def register(self, card: AgentCard) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> AgentCard | None: ...
    def list_all(self) -> list[AgentCard]: ...


class FileAgentRegistry:
    """File-backed agent registry for local deployment."""

    def __init__(self, path: str = "data/agent_registry.json"):
        self._path = Path(path)
        self._cards: dict[str, AgentCard] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            for item in data:
                self._cards[item["name"]] = AgentCard.from_dict(item)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            [c.to_dict() for c in self._cards.values()], indent=2
        ))

    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card
        self._save()
        logger.info("Registered agent: %s", card.name)

    def unregister(self, name: str) -> None:
        self._cards.pop(name, None)
        self._save()

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list_all(self) -> list[AgentCard]:
        return list(self._cards.values())
