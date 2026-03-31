# Google Drive Auto-Upload — Design Spec

**Goal:** After generating CV/Cover Letter PDFs, automatically upload to Google Drive (separate Resume and Cover Letter folders) and sync the shareable links to Notion Job Tracker.

**Architecture:** New `drive_uploader.py` module using Google Drive API v3. Hooks into `job_autopilot.py` after PDF generation. Updates Notion `CV Version` and `Cover Letter` file properties with external URLs.

**Tech Stack:** `google-api-python-client`, `google-auth` (already installed), Google Drive API v3

---

## 1. Drive Uploader (`jobpulse/drive_uploader.py`)

### Interface

```python
def upload_to_drive(local_path: Path, folder_id: str, filename: str | None = None) -> str | None:
    """Upload a file to Google Drive folder. Returns shareable link or None on failure."""

def upload_cv(cv_path: Path, company: str) -> str | None:
    """Upload CV PDF to Resume folder. Returns shareable link."""

def upload_cover_letter(cl_path: Path, company: str) -> str | None:
    """Upload Cover Letter PDF to Cover Letter folder. Returns shareable link."""
```

### Behavior

1. Load credentials from `data/google_token.json` (same pattern as `gmail_agent.py`)
2. Build Drive service: `build("drive", "v3", credentials=creds)`
3. Upload file with `MediaFileUpload` to specified folder
4. Set permission: `anyone` with `reader` role (shareable link)
5. Return `https://drive.google.com/file/d/{file_id}/view?usp=sharing`
6. On any failure: log warning, return None (non-blocking — PDF still exists locally)

### Dedup

Before uploading, check if a file with the same name already exists in the folder. If yes, update it instead of creating a duplicate.

---

## 2. Config (`jobpulse/config.py`)

Add three env vars:

```python
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_DRIVE_RESUMES_FOLDER_ID = os.getenv("GOOGLE_DRIVE_RESUMES_FOLDER_ID", "")
GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID", "")
```

---

## 3. Notion Sync (`jobpulse/job_notion_sync.py`)

Add `cv_drive_link` and `cl_drive_link` parameters to `build_update_payload()`. Notion `CV Version` and `Cover Letter` are `file` type properties — set as external URLs:

```python
"CV Version": {"files": [{"type": "external", "name": "CV.pdf", "external": {"url": cv_drive_link}}]}
"Cover Letter": {"files": [{"type": "external", "name": "CoverLetter.pdf", "external": {"url": cl_drive_link}}]}
```

---

## 4. Autopilot Integration (`jobpulse/job_autopilot.py`)

After `generate_cv_pdf()` and `generate_cover_letter_pdf()`, call:

```python
cv_drive_link = upload_cv(cv_path, listing.company)
cl_drive_link = upload_cover_letter(cover_letter_path, listing.company)
```

Pass links to Notion sync. Store links in `ApplicationRecord` (add `cv_drive_link` and `cl_drive_link` fields to `application_models.py`).

---

## 5. Testing

- Unit tests for `drive_uploader.py` with mocked Google API
- Test dedup logic (file exists → update, not create)
- Test Notion payload with file properties
- All tests use mocks — never call real Drive API
