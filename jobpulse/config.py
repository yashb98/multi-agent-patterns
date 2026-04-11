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

# Canonical Google OAuth scopes — ALL agents must use this list when loading/refreshing
# the token, otherwise whichever agent refreshes first clobbers the scopes for the rest.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

# Notion
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_TASKS_DB_ID = os.getenv("NOTION_TASKS_DB_ID", "")
NOTION_RESEARCH_DB_ID = os.getenv("NOTION_RESEARCH_DB_ID", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "yashb98")

# Telegram — multi-bot setup
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")          # Main bot (all commands)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_BUDGET_BOT_TOKEN = os.getenv("TELEGRAM_BUDGET_BOT_TOKEN", "")    # Budget-only bot
TELEGRAM_RESEARCH_BOT_TOKEN = os.getenv("TELEGRAM_RESEARCH_BOT_TOKEN", "")  # Research/papers bot
TELEGRAM_ALERT_BOT_TOKEN = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "")      # Read-only alerts bot
TELEGRAM_JOBS_BOT_TOKEN = os.getenv("TELEGRAM_JOBS_BOT_TOKEN", "")        # Job applications bot
TELEGRAM_JOBS_CHAT_ID = os.getenv("TELEGRAM_JOBS_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))

# Slack
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

# Discord
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID", "")

# LLM
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CONVERSATION_MODEL = os.getenv("CONVERSATION_MODEL", "gpt-4.1-mini")

# Paths
DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR = PROJECT_DIR / "logs"
DB_PATH = DATA_DIR / "jobpulse.db"

# Remote shell
SHELL_TIMEOUT = int(os.getenv("SHELL_TIMEOUT", "30"))
SHELL_MAX_OUTPUT = int(os.getenv("SHELL_MAX_OUTPUT", "4000"))

# File operations
MAX_FILE_LINES = int(os.getenv("MAX_FILE_LINES", "100"))

# Notion Applications DB
NOTION_APPLICATIONS_DB_ID = os.getenv("NOTION_APPLICATIONS_DB_ID", "")
NOTION_BLOCKLIST_DB_ID = os.getenv("NOTION_BLOCKLIST_DB_ID", "")

# Google Drive folders for CV/Cover Letter uploads
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_DRIVE_RESUMES_FOLDER_ID = os.getenv("GOOGLE_DRIVE_RESUMES_FOLDER_ID", "")
GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID", "")

# Reed API
REED_API_KEY = os.getenv("REED_API_KEY", "")

# Job Autopilot
JOB_AUTOPILOT_ENABLED = os.getenv("JOB_AUTOPILOT_ENABLED", "true").lower() in ("true", "1", "yes")
JOB_AUTOPILOT_AUTO_SUBMIT = os.getenv("JOB_AUTOPILOT_AUTO_SUBMIT", "true").lower() in ("true", "1", "yes")
JOB_AUTOPILOT_MAX_DAILY = int(os.getenv("JOB_AUTOPILOT_MAX_DAILY", "60"))

# Perplexity
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

# Extension bridge
EXT_BRIDGE_HOST = os.getenv("EXT_BRIDGE_HOST", "localhost")
EXT_BRIDGE_PORT = int(os.getenv("EXT_BRIDGE_PORT", "8765"))

# Application engine mode
APPLICATION_ENGINE = os.getenv("APPLICATION_ENGINE", "extension")

# External application engine
ATS_ACCOUNT_PASSWORD = os.getenv("JOB_APPLY_PASSWORD", "")
GMAIL_VERIFY_TIMEOUT = int(os.getenv("GMAIL_VERIFY_TIMEOUT", "120"))
GMAIL_VERIFY_POLL_INTERVAL = int(os.getenv("GMAIL_VERIFY_POLL_INTERVAL", "5"))
PAGE_STABLE_TIMEOUT_MS = int(os.getenv("PAGE_STABLE_TIMEOUT_MS", "3000"))

# Salary / hours tracking
HOURLY_RATE = float(os.getenv("HOURLY_RATE", "13.99"))

# Dispatcher mode
JOBPULSE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in ("true", "1", "yes")

# Playwright CDP
PLAYWRIGHT_CDP_URL = os.environ.get("PLAYWRIGHT_CDP_URL", "http://localhost:9222")
PLAYWRIGHT_CDP_PORT = os.environ.get("PLAYWRIGHT_CDP_PORT", "9222")

# Local LLM provider (shared with shared/agents.py)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "gemma4:31b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# RLM (Recursive Language Model)
# When LLM_PROVIDER=local, auto-switch RLM to use Ollama backend + local model
_rlm_default_backend = "openai" if LLM_PROVIDER != "local" else "openai"
_rlm_default_model = "gpt-4.1-mini" if LLM_PROVIDER != "local" else LOCAL_LLM_MODEL
RLM_BACKEND = os.getenv("RLM_BACKEND", _rlm_default_backend)
RLM_ROOT_MODEL = os.getenv("RLM_ROOT_MODEL", _rlm_default_model)
RLM_MAX_ITERATIONS = int(os.getenv("RLM_MAX_ITERATIONS", "10"))
RLM_MAX_BUDGET = float(os.getenv("RLM_MAX_BUDGET", "0.10"))

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
