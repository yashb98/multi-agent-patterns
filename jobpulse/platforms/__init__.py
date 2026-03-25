"""Platform adapters — abstract messaging layer for Telegram, Slack, Discord."""

from jobpulse.platforms.base import PlatformAdapter
from jobpulse.platforms.telegram_adapter import TelegramAdapter
from jobpulse.platforms.slack_adapter import SlackAdapter
from jobpulse.platforms.discord_adapter import DiscordAdapter

__all__ = ["PlatformAdapter", "TelegramAdapter", "SlackAdapter", "DiscordAdapter"]
