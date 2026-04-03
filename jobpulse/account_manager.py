"""ATS platform credential manager.

Stores one account per domain. Uses a single password from ATS_ACCOUNT_PASSWORD
env var and the user's profile email. Credentials stored in SQLite.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import ATS_ACCOUNT_PASSWORD
from jobpulse.ext_models import AccountInfo

logger = get_logger(__name__)

_DEFAULT_DB = "data/ats_accounts.db"


class AccountManager:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    domain TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    verified INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_login TEXT DEFAULT ''
                )
            """)

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url or domain_or_url.startswith("www."):
            parsed = urlparse(
                domain_or_url if "://" in domain_or_url else f"https://{domain_or_url}"
            )
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def has_account(self, domain_or_url: str) -> bool:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT 1 FROM accounts WHERE domain = ?", (domain,)).fetchone()
        return row is not None

    def create_account(self, domain_or_url: str) -> tuple[str, str]:
        from jobpulse.applicator import PROFILE
        import jobpulse.account_manager as _self_mod

        domain = self._normalize_domain(domain_or_url)
        email = PROFILE["email"]
        password = _self_mod.ATS_ACCOUNT_PASSWORD

        if not password:
            raise ValueError("ATS_ACCOUNT_PASSWORD env var not set")

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT email, password FROM accounts WHERE domain = ?", (domain,)
            ).fetchone()
            if existing:
                return existing[0], existing[1]
            conn.execute(
                "INSERT INTO accounts (domain, email, password, created_at) VALUES (?, ?, ?, ?)",
                (domain, email, password, datetime.now(UTC).isoformat()),
            )
        logger.info("Created account for %s with email %s", domain, email)
        return email, password

    def get_credentials(self, domain_or_url: str) -> tuple[str, str]:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT email, password FROM accounts WHERE domain = ?", (domain,)
            ).fetchone()
        if not row:
            raise KeyError(f"No account for {domain}")
        return row[0], row[1]

    def get_account_info(self, domain_or_url: str) -> AccountInfo:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT domain, email, verified, created_at, last_login FROM accounts WHERE domain = ?",
                (domain,),
            ).fetchone()
        if not row:
            raise KeyError(f"No account for {domain}")
        return AccountInfo(
            domain=row[0], email=row[1], verified=bool(row[2]),
            created_at=row[3], last_login=row[4] or "",
        )

    def mark_verified(self, domain_or_url: str):
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("UPDATE accounts SET verified = 1 WHERE domain = ?", (domain,))

    def mark_login_success(self, domain_or_url: str):
        domain = self._normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("UPDATE accounts SET last_login = ? WHERE domain = ?", (now, domain))
