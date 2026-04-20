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
CONVERSATION_MODEL = os.getenv("CONVERSATION_MODEL", "gpt-5-mini")

# Multi-provider fallback
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
LLM_PROVIDER_FALLBACK = os.getenv("LLM_PROVIDER_FALLBACK", "openai,anthropic,gemini")

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
JOB_AUTOPILOT_AUTO_SUBMIT = os.getenv("JOB_AUTOPILOT_AUTO_SUBMIT", "false").lower() in ("true", "1", "yes")
JOB_AUTOPILOT_MAX_DAILY = int(os.getenv("JOB_AUTOPILOT_MAX_DAILY", "10"))

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

# Applicant profile — loaded from env vars, never hardcoded in source
APPLICANT_FIRST_NAME = os.getenv("APPLICANT_FIRST_NAME", "")
APPLICANT_LAST_NAME = os.getenv("APPLICANT_LAST_NAME", "")
APPLICANT_EMAIL = os.getenv("APPLICANT_EMAIL", "")
APPLICANT_PHONE = os.getenv("APPLICANT_PHONE", "")
APPLICANT_LINKEDIN = os.getenv("APPLICANT_LINKEDIN", "")
APPLICANT_GITHUB = os.getenv("APPLICANT_GITHUB", "")
APPLICANT_PORTFOLIO = os.getenv("APPLICANT_PORTFOLIO", "")
APPLICANT_LOCATION = os.getenv("APPLICANT_LOCATION", "")
APPLICANT_EDUCATION = os.getenv("APPLICANT_EDUCATION", "")

APPLICANT_PROFILE: dict[str, str] = {
    "first_name": APPLICANT_FIRST_NAME,
    "last_name": APPLICANT_LAST_NAME,
    "email": APPLICANT_EMAIL,
    "phone": APPLICANT_PHONE,
    "linkedin": APPLICANT_LINKEDIN,
    "github": APPLICANT_GITHUB,
    "portfolio": APPLICANT_PORTFOLIO,
    "education": APPLICANT_EDUCATION,
    "location": APPLICANT_LOCATION,
}

WORK_AUTH: dict[str, object] = {
    "requires_sponsorship": os.getenv("WORK_AUTH_REQUIRES_SPONSORSHIP", "false").lower() in ("true", "1"),
    "visa_status": os.getenv("WORK_AUTH_VISA_STATUS", ""),
    "right_to_work_uk": os.getenv("WORK_AUTH_RIGHT_TO_WORK", "true").lower() in ("true", "1"),
    "notice_period": os.getenv("WORK_AUTH_NOTICE_PERIOD", ""),
    "salary_expectation": os.getenv("WORK_AUTH_SALARY_EXPECTATION", ""),
}

# Salary / hours tracking
HOURLY_RATE = float(os.getenv("HOURLY_RATE", "13.99"))

# Dispatcher mode
JOBPULSE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in ("true", "1", "yes")

# Playwright CDP
PLAYWRIGHT_CDP_URL = os.environ.get("PLAYWRIGHT_CDP_URL", "http://localhost:9222")
PLAYWRIGHT_CDP_PORT = os.environ.get("PLAYWRIGHT_CDP_PORT", "9222")

# Local LLM provider (shared with shared/agents.py)
# "auto" (default) probes Ollama and uses local if reachable, else cloud
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "gemma4:31b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Import resolved provider from shared/agents.py for downstream decisions
try:
    from shared.agents import is_local_llm as _is_local_check
    _resolved_local = _is_local_check()
except ImportError:
    _resolved_local = LLM_PROVIDER == "local"

# RLM (Recursive Language Model)
# When local LLM is active, use Ollama backend + local model
# When auto-falling back to cloud, use older/cheaper model (gpt-4o-mini)
_rlm_default_backend = "openai"
_rlm_default_model = LOCAL_LLM_MODEL if _resolved_local else "gpt-4o-mini"
RLM_BACKEND = os.getenv("RLM_BACKEND", _rlm_default_backend)
RLM_ROOT_MODEL = os.getenv("RLM_ROOT_MODEL", _rlm_default_model)
RLM_MAX_ITERATIONS = int(os.getenv("RLM_MAX_ITERATIONS", "10"))
RLM_MAX_BUDGET = float(os.getenv("RLM_MAX_BUDGET", "0.10"))

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
