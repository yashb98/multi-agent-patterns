"""Google Drive uploader for CV and cover letter PDFs.

Uploads generated PDFs to dedicated Google Drive folders and returns
shareable links for Notion Job Tracker integration.

Uses the same OAuth2 credentials as gmail_agent.py and calendar_agent.py.
Non-blocking: returns None on any failure (PDF still exists locally).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import (
    GOOGLE_DRIVE_RESUMES_FOLDER_ID,
    GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID,
    GOOGLE_TOKEN_PATH,
)

logger = get_logger(__name__)

_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def _get_drive_service() -> Any | None:
    """Build Google Drive API v3 service using stored OAuth2 token.

    Returns None if credentials are missing or invalid.
    Same pattern as gmail_agent._get_gmail_service().
    """
    try:
        import os

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(GOOGLE_TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(
                GOOGLE_TOKEN_PATH, [_DRIVE_SCOPE]
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(GOOGLE_TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
            else:
                logger.warning(
                    "No valid Drive credentials. Run: python scripts/setup_integrations.py"
                )
                return None

        return build("drive", "v3", credentials=creds)
    except ImportError:
        logger.warning(
            "Install: pip install google-auth-oauthlib google-api-python-client"
        )
        return None
    except Exception as exc:
        logger.warning("Failed to build Drive service: %s", exc)
        return None


def upload_to_drive(
    local_path: Path,
    *,
    folder_id: str,
    filename: str | None = None,
) -> str | None:
    """Upload a file to a Google Drive folder.

    If a file with the same name already exists in the folder, it is updated
    (no duplicates). Sets 'anyone with link can view' permission.

    Args:
        local_path: Path to the local file.
        folder_id: Google Drive folder ID to upload into.
        filename: Override filename (default: use local filename).

    Returns:
        Shareable Google Drive link, or None on failure.
    """
    if not folder_id:
        logger.warning("drive_uploader: no folder_id provided — skipping upload")
        return None

    if not local_path.exists():
        logger.warning("drive_uploader: file not found: %s", local_path)
        return None

    service = _get_drive_service()
    if service is None:
        return None

    upload_name = filename or local_path.name

    try:
        from googleapiclient.http import MediaFileUpload

        # Check for existing file (dedup)
        existing = service.files().list(
            q=f"name = '{upload_name}' and '{folder_id}' in parents and trashed = false",
            pageSize=1,
            fields="files(id, name)",
        ).execute()

        existing_files = existing.get("files", [])
        media = MediaFileUpload(str(local_path), mimetype="application/pdf")

        if existing_files:
            # Update existing file
            file_id = existing_files[0]["id"]
            service.files().update(
                fileId=file_id,
                media_body=media,
            ).execute()
            logger.info(
                "drive_uploader: updated existing file '%s' (id=%s)",
                upload_name, file_id,
            )
        else:
            # Create new file
            file_metadata = {
                "name": upload_name,
                "parents": [folder_id],
            }
            result = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id",
            ).execute()
            file_id = result["id"]
            logger.info(
                "drive_uploader: uploaded new file '%s' (id=%s)",
                upload_name, file_id,
            )

        # Set shareable permission (anyone with link can view)
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()

        link = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        logger.info("drive_uploader: shareable link: %s", link)
        return link

    except Exception as exc:
        logger.warning("drive_uploader: upload failed for %s: %s", local_path.name, exc)
        return None


def upload_cv(cv_path: Path, company: str) -> str | None:
    """Upload CV PDF to the Resumes folder on Google Drive.

    Args:
        cv_path: Path to the CV PDF.
        company: Company name (used in filename).

    Returns:
        Shareable link or None.
    """
    if not GOOGLE_DRIVE_RESUMES_FOLDER_ID:
        logger.warning("drive_uploader: GOOGLE_DRIVE_RESUMES_FOLDER_ID not set — skipping CV upload")
        return None

    safe_company = company.replace("/", "_").replace(" ", "_")
    filename = f"Yash_Bishnoi_{safe_company}.pdf"

    return upload_to_drive(
        cv_path,
        folder_id=GOOGLE_DRIVE_RESUMES_FOLDER_ID,
        filename=filename,
    )


def upload_cover_letter(cl_path: Path, company: str) -> str | None:
    """Upload cover letter PDF to the Cover Letters folder on Google Drive.

    Args:
        cl_path: Path to the cover letter PDF.
        company: Company name (used in filename).

    Returns:
        Shareable link or None.
    """
    if not GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID:
        logger.warning(
            "drive_uploader: GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID not set — skipping CL upload"
        )
        return None

    safe_company = company.replace("/", "_").replace(" ", "_")
    filename = f"Yash_Bishnoi_{safe_company}_CoverLetter.pdf"

    return upload_to_drive(
        cl_path,
        folder_id=GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID,
        filename=filename,
    )
