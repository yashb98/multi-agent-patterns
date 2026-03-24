"""Configuration — loads all env vars with defaults."""

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(PROJECT_DIR / ".env")

# Google OAuth2
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", ""))
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET", ""))
GOOGLE_TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", str(PROJECT_DIR / "data" / "google_token.json"))

# Notion
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_TASKS_DB_ID = os.getenv("NOTION_TASKS_DB_ID", "")
NOTION_RESEARCH_DB_ID = os.getenv("NOTION_RESEARCH_DB_ID", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "yashb98")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Paths
DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR = PROJECT_DIR / "logs"
DB_PATH = DATA_DIR / "jobpulse.db"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
