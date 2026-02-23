"""
Google Drive helpers for compliance audit report exports.
"""

from __future__ import annotations

from datetime import datetime
import random
import time
from typing import Any

from google.oauth2 import service_account

from services.auth_manager import resolve_service_account_key_path

_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def _is_retryable_drive_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "resp", None)
        status = getattr(resp, "status", None)

    if status in (429, 500, 502, 503, 504):
        return True

    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "internalerror",
            "internal error",
            "backend error",
            "rate limit",
            "quota exceeded",
        )
    )


def _execute_with_retry(request_obj: Any, max_attempts: int = 5) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return request_obj.execute()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable_drive_error(exc):
                break
            delay = (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Drive API request failed without an exception.")


def _build_drive_service() -> Any:
    try:
        from googleapiclient.discovery import build  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-api-python-client is not installed. "
            "Run: pip install 'google-api-python-client>=2.120,<3'"
        ) from exc

    key_path = resolve_service_account_key_path()
    if not key_path.exists():
        raise FileNotFoundError(
            f"Google service account key not found at '{key_path}'. "
            "Set GOOGLE_SERVICE_ACCOUNT_JSON to the correct path."
        )

    creds = service_account.Credentials.from_service_account_file(
        str(key_path),
        scopes=[_DRIVE_FILE_SCOPE],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_or_create_subfolder(service: Any, parent_id: str, folder_name: str) -> str:
    escaped = folder_name.replace("'", "\\'")
    query = (
        f"name = '{escaped}' and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "trashed = false and "
        f"'{parent_id}' in parents"
    )

    result = _execute_with_retry(
        service.files().list(
            q=query,
            fields="files(id,name)",
            pageSize=1,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
    )
    files = result.get("files", [])
    if files:
        return str(files[0]["id"])

    created = _execute_with_retry(
        service.files().create(
            body={
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id,name",
            supportsAllDrives=True,
        )
    )
    return str(created["id"])


def upload_markdown_report(
    report_text: str,
    file_name: str,
    folder_id: str,
) -> dict[str, Any]:
    """Upload a markdown report file to Google Drive.

    Args:
        report_text: Markdown content to upload.
        file_name: Target file name.
        folder_id: Destination Google Drive folder ID.

    Returns:
        Drive API response payload for the created file.
    """
    try:
        from googleapiclient.http import MediaInMemoryUpload  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-api-python-client is not installed. "
            "Run: pip install 'google-api-python-client>=2.120,<3'"
        ) from exc

    service = _build_drive_service()

    metadata: dict[str, Any] = {"name": file_name}
    folder = folder_id.strip()
    if folder:
        metadata["parents"] = [folder]

    media = MediaInMemoryUpload(
        report_text.encode("utf-8"),
        mimetype="text/markdown",
        resumable=True,
    )

    return _execute_with_retry(
        service.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink,createdTime,parents",
            supportsAllDrives=True,
        )
    )


def upload_markdown_as_google_doc(
    report_text: str,
    file_name: str,
    folder_id: str,
) -> dict[str, Any]:
    """Upload markdown content converted into a Google Doc.

    Drive import conversion is used by creating a Google Doc MIME target while
    uploading markdown content.
    """
    try:
        from googleapiclient.http import MediaInMemoryUpload  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-api-python-client is not installed. "
            "Run: pip install 'google-api-python-client>=2.120,<3'"
        ) from exc

    service = _build_drive_service()

    metadata: dict[str, Any] = {
        "name": file_name,
        "mimeType": "application/vnd.google-apps.document",
    }
    folder = folder_id.strip()
    if folder:
        metadata["parents"] = [folder]

    media = MediaInMemoryUpload(
        report_text.encode("utf-8"),
        mimetype="text/markdown",
        resumable=True,
    )

    return _execute_with_retry(
        service.files().create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink,createdTime,parents,mimeType",
            supportsAllDrives=True,
        )
    )


def upload_markdown_report_to_launch_audit_folder(
    report_text: str,
    file_name: str,
    root_folder_id: str,
    launch_id: int,
    source_asin: str | None = None,
) -> dict[str, Any]:
    """Upload report into Drive subfolders: Launch_<id>_<asin>/Compliance/<YYYY-MM-DD>."""
    asin = (source_asin or "").strip().upper()
    launch_folder_name = f"Launch_{launch_id}" + (f"_{asin}" if asin else "")
    date_folder_name = datetime.utcnow().strftime("%Y-%m-%d")

    service = _build_drive_service()
    launch_folder_id = _get_or_create_subfolder(
        service, root_folder_id.strip(), launch_folder_name
    )
    compliance_folder_id = _get_or_create_subfolder(
        service, launch_folder_id, "Compliance"
    )
    date_folder_id = _get_or_create_subfolder(
        service, compliance_folder_id, date_folder_name
    )

    uploaded = upload_markdown_report(
        report_text=report_text,
        file_name=file_name,
        folder_id=date_folder_id,
    )
    uploaded["audit_folder_path"] = (
        f"{launch_folder_name}/Compliance/{date_folder_name}"
    )
    uploaded["audit_folder_id"] = date_folder_id
    return uploaded


def upload_markdown_gdoc_to_launch_audit_folder(
    report_text: str,
    file_name: str,
    root_folder_id: str,
    launch_id: int,
    source_asin: str | None = None,
) -> dict[str, Any]:
    """Upload report as a Google Doc into Launch_<id>_<asin>/Compliance/<YYYY-MM-DD>."""
    asin = (source_asin or "").strip().upper()
    launch_folder_name = f"Launch_{launch_id}" + (f"_{asin}" if asin else "")
    date_folder_name = datetime.utcnow().strftime("%Y-%m-%d")

    service = _build_drive_service()
    launch_folder_id = _get_or_create_subfolder(
        service, root_folder_id.strip(), launch_folder_name
    )
    compliance_folder_id = _get_or_create_subfolder(
        service, launch_folder_id, "Compliance"
    )
    date_folder_id = _get_or_create_subfolder(
        service, compliance_folder_id, date_folder_name
    )

    gdoc_name = file_name[:-3] if file_name.lower().endswith(".md") else file_name
    uploaded = upload_markdown_as_google_doc(
        report_text=report_text,
        file_name=gdoc_name,
        folder_id=date_folder_id,
    )
    uploaded["audit_folder_path"] = (
        f"{launch_folder_name}/Compliance/{date_folder_name}"
    )
    uploaded["audit_folder_id"] = date_folder_id
    return uploaded


def upload_markdown_gdoc_to_launch_stage_folder(
    report_text: str,
    file_name: str,
    root_folder_id: str,
    launch_id: int,
    stage_folder: str,
    source_asin: str | None = None,
) -> dict[str, Any]:
    """Upload report as Google Doc to Launch_<id>_<asin>/<Stage>/<YYYY-MM-DD>."""
    asin = (source_asin or "").strip().upper()
    launch_folder_name = f"Launch_{launch_id}" + (f"_{asin}" if asin else "")
    date_folder_name = datetime.utcnow().strftime("%Y-%m-%d")
    stage_folder_clean = (stage_folder or "Reports").strip() or "Reports"

    service = _build_drive_service()
    launch_folder_id = _get_or_create_subfolder(
        service, root_folder_id.strip(), launch_folder_name
    )
    stage_folder_id = _get_or_create_subfolder(
        service, launch_folder_id, stage_folder_clean
    )
    date_folder_id = _get_or_create_subfolder(
        service, stage_folder_id, date_folder_name
    )

    gdoc_name = file_name[:-3] if file_name.lower().endswith(".md") else file_name
    uploaded = upload_markdown_as_google_doc(
        report_text=report_text,
        file_name=gdoc_name,
        folder_id=date_folder_id,
    )
    uploaded["audit_folder_path"] = (
        f"{launch_folder_name}/{stage_folder_clean}/{date_folder_name}"
    )
    uploaded["audit_folder_id"] = date_folder_id
    return uploaded
