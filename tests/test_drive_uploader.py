"""Tests for Google Drive uploader — CV and cover letter auto-upload."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open


@pytest.fixture
def mock_drive_service():
    """Create a mock Google Drive service."""
    service = MagicMock()
    # files().list().execute() — for dedup check
    service.files().list().execute.return_value = {"files": []}
    # files().create().execute() — for upload
    service.files().create().execute.return_value = {"id": "file123abc"}
    # permissions().create().execute() — for sharing
    service.permissions().create().execute.return_value = {}
    return service


class TestUploadToDrive:
    """Test the core upload_to_drive function."""

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_returns_shareable_link(self, mock_get_svc, mock_drive_service, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        link = upload_to_drive(pdf, folder_id="folder123")
        assert link is not None
        assert "file123abc" in link
        assert "drive.google.com" in link

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_sets_anyone_reader_permission(self, mock_get_svc, mock_drive_service, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        upload_to_drive(pdf, folder_id="folder123")

        # Verify permission was set — find the call with fileId kwarg
        perm_calls = mock_drive_service.permissions().create.call_args_list
        real_calls = [c for c in perm_calls if c[1].get("fileId")]
        assert len(real_calls) == 1
        assert real_calls[0][1]["fileId"] == "file123abc"
        assert real_calls[0][1]["body"]["role"] == "reader"
        assert real_calls[0][1]["body"]["type"] == "anyone"

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_to_correct_folder(self, mock_get_svc, mock_drive_service, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        upload_to_drive(pdf, folder_id="my_folder_id")

        create_call = mock_drive_service.files().create.call_args
        metadata = create_call[1]["body"]
        assert "my_folder_id" in metadata["parents"]

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_custom_filename(self, mock_get_svc, mock_drive_service, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        upload_to_drive(pdf, folder_id="folder123", filename="Custom_Name.pdf")

        create_call = mock_drive_service.files().create.call_args
        metadata = create_call[1]["body"]
        assert metadata["name"] == "Custom_Name.pdf"

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_returns_none_on_no_service(self, mock_get_svc, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = None
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        link = upload_to_drive(pdf, folder_id="folder123")
        assert link is None

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_returns_none_on_missing_file(self, mock_get_svc, mock_drive_service):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        link = upload_to_drive(Path("/nonexistent/file.pdf"), folder_id="folder123")
        assert link is None

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_returns_none_on_empty_folder_id(self, mock_get_svc, mock_drive_service, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        link = upload_to_drive(pdf, folder_id="")
        assert link is None


class TestDedupLogic:
    """Test that existing files are updated, not duplicated."""

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_existing_file_gets_updated(self, mock_get_svc, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        service = MagicMock()
        # Simulate existing file found
        service.files().list().execute.return_value = {
            "files": [{"id": "existing_file_id", "name": "test.pdf"}]
        }
        service.files().update().execute.return_value = {"id": "existing_file_id"}
        service.permissions().create().execute.return_value = {}
        mock_get_svc.return_value = service

        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 updated content")

        link = upload_to_drive(pdf, folder_id="folder123")

        assert link is not None
        assert "existing_file_id" in link
        # Should call update, not create
        service.files().update.assert_called()

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_new_file_gets_created(self, mock_get_svc, mock_drive_service, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        mock_get_svc.return_value = mock_drive_service
        pdf = tmp_path / "new_file.pdf"
        pdf.write_bytes(b"%PDF-1.4 new content")

        link = upload_to_drive(pdf, folder_id="folder123")

        assert link is not None
        mock_drive_service.files().create.assert_called()


class TestUploadCvAndCoverLetter:
    """Test convenience functions for CV and cover letter uploads."""

    @patch("jobpulse.drive_uploader.upload_to_drive")
    @patch("jobpulse.drive_uploader.GOOGLE_DRIVE_RESUMES_FOLDER_ID", "resume_folder_id")
    def test_upload_cv(self, mock_upload, tmp_path):
        from jobpulse.drive_uploader import upload_cv

        mock_upload.return_value = "https://drive.google.com/file/d/abc/view"
        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        link = upload_cv(pdf, "Google")

        mock_upload.assert_called_once()
        call_args = mock_upload.call_args
        assert call_args[1]["folder_id"] == "resume_folder_id"
        assert "Google" in call_args[1]["filename"]
        assert link == "https://drive.google.com/file/d/abc/view"

    @patch("jobpulse.drive_uploader.upload_to_drive")
    @patch("jobpulse.drive_uploader.GOOGLE_DRIVE_COVERLETTERS_FOLDER_ID", "cl_folder_id")
    def test_upload_cover_letter(self, mock_upload, tmp_path):
        from jobpulse.drive_uploader import upload_cover_letter

        mock_upload.return_value = "https://drive.google.com/file/d/xyz/view"
        pdf = tmp_path / "cl.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        link = upload_cover_letter(pdf, "Meta")

        mock_upload.assert_called_once()
        call_args = mock_upload.call_args
        assert call_args[1]["folder_id"] == "cl_folder_id"
        assert "Meta" in call_args[1]["filename"]
        assert call_args[1]["filename"] == "Cover_Letter_Meta.pdf"

    @patch("jobpulse.drive_uploader.upload_to_drive")
    @patch("jobpulse.drive_uploader.GOOGLE_DRIVE_RESUMES_FOLDER_ID", "")
    def test_upload_cv_no_folder_id_returns_none(self, mock_upload, tmp_path):
        from jobpulse.drive_uploader import upload_cv

        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        link = upload_cv(pdf, "Google")
        assert link is None
        mock_upload.assert_not_called()

    @patch("jobpulse.drive_uploader._get_drive_service")
    def test_upload_handles_api_error_gracefully(self, mock_get_svc, tmp_path):
        from jobpulse.drive_uploader import upload_to_drive

        service = MagicMock()
        service.files().list().execute.return_value = {"files": []}
        service.files().create().execute.side_effect = Exception("API quota exceeded")
        mock_get_svc.return_value = service

        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        link = upload_to_drive(pdf, folder_id="folder123")
        assert link is None  # Graceful failure, not crash
