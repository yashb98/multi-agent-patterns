"""Abstract base class for messaging platform adapters."""

from abc import ABC, abstractmethod
from shared.logging_config import get_logger

logger = get_logger(__name__)


class PlatformAdapter(ABC):
    """Base class for messaging platform adapters.

    Every adapter must implement send_message() and get_platform_name().
    Override format_for_platform() to adjust markdown/emoji for the target platform.
    """

    @abstractmethod
    def send_message(self, text: str, chat_id: str = None) -> bool:
        """Send a message. Returns True on success."""
        ...

    @abstractmethod
    def get_platform_name(self) -> str:
        """Return the platform name (e.g. 'Telegram', 'Slack', 'Discord')."""
        ...

    def format_for_platform(self, text: str) -> str:
        """Override to convert markdown or adjust formatting per platform."""
        return text
