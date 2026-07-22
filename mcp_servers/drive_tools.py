"""Google Drive tools unlocked near the archive shelf."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload
from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.google_integration import (
    GoogleAuthorizationRequired,
    GoogleIntegrationError,
    GoogleIntegrationService,
    calendar_subprocess_settings,
)

_MAX_RESULTS = 100
_MAX_TEXT_BYTES = 1_000_000
_EXPORT_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
mcp = FastMCP("drive")
_drive_service: GoogleIntegrationService | None = None


def _service() -> GoogleIntegrationService:
    global _drive_service
    if _drive_service is None:
        _drive_service = GoogleIntegrationService(calendar_subprocess_settings())
    return _drive_service


def _files():
    return _service().drive().files()


def _text(value: str, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} cannot be empty")
    if len(value) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return value


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": item.get("id", ""),
        "name": item.get("name", ""),
        "mime_type": item.get("mimeType", ""),
        "parents": item.get("parents", []),
        "size": item.get("size"),
        "modified_at": item.get("modifiedTime"),
        "web_view_link": item.get("webViewLink"),
    }


def _error(exc: Exception) -> str:
    if isinstance(exc, GoogleAuthorizationRequired):
        return f"Error: {exc}"
    if isinstance(exc, HttpError) and exc.resp.status == 404:
        return "Error: Drive file was not found"
    if isinstance(exc, (HttpError, GoogleIntegrationError, OSError)):
        return "Error: Google Drive request failed"
    return f"Error: {exc}"


@mcp.tool()
def list_drive_files(
    query: str = "",
    folder_id: str | None = None,
    page_token: str | None = None,
    limit: int = 50,
) -> str:
    """List or search non-trashed Google Drive files."""
    try:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        clauses = ["trashed = false"]
        query = _text(query, "query", 500, False)
        if query:
            clauses.append(f"({query})")
        if folder_id is not None:
            folder = _text(folder_id, "folder_id", 1024).replace("'", "\\'")
            clauses.append(f"'{folder}' in parents")
        kwargs: dict[str, Any] = {
            "q": " and ".join(clauses),
            "pageSize": max(1, min(limit, _MAX_RESULTS)),
            "fields": "nextPageToken,files(id,name,mimeType,parents,size,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc",
        }
        if page_token:
            kwargs["pageToken"] = _text(page_token, "page_token", 2048)
        response = _files().list(**kwargs).execute()
        return json.dumps(
            {
                "files": [
                    _metadata(item)
                    for item in response.get("files", [])
                    if isinstance(item, dict)
                ],
                "next_page_token": response.get("nextPageToken"),
            },
            ensure_ascii=False,
        )
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def get_drive_file(file_id: str) -> str:
    """Read Drive metadata and text content when the file type is supported."""
    try:
        file_id = _text(file_id, "file_id", 1024)
        api = _files()
        item = api.get(
            fileId=file_id,
            fields="id,name,mimeType,parents,size,modifiedTime,webViewLink",
        ).execute()
        result = _metadata(item)
        mime = str(item.get("mimeType") or "")
        size = int(item.get("size") or 0)
        if mime in _EXPORT_TYPES:
            content = api.export_media(
                fileId=file_id, mimeType=_EXPORT_TYPES[mime]
            ).execute()
        elif mime.startswith("text/") or mime in {
            "application/json",
            "application/xml",
        }:
            if size > _MAX_TEXT_BYTES:
                result["content_unavailable"] = "Text file exceeds the 1 MB limit."
                return json.dumps(result, ensure_ascii=False)
            content = api.get_media(fileId=file_id).execute()
        else:
            result["content_unavailable"] = "Binary content is not returned."
            return json.dumps(result, ensure_ascii=False)
        if not isinstance(content, bytes):
            raise ValueError("Drive returned invalid file content")
        if len(content) > _MAX_TEXT_BYTES:
            result["content_unavailable"] = "Exported content exceeds the 1 MB limit."
        else:
            result["content"] = content.decode("utf-8", errors="replace")
        return json.dumps(result, ensure_ascii=False)
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def create_drive_text_file(
    name: str, content: str, folder_id: str | None = None
) -> str:
    """Create a UTF-8 plain-text file in Google Drive."""
    try:
        name = _text(name, "name", 255)
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        data = content.encode("utf-8")
        if len(data) > _MAX_TEXT_BYTES:
            raise ValueError("content cannot exceed 1 MB")
        body: dict[str, Any] = {"name": name, "mimeType": "text/plain"}
        if folder_id is not None:
            body["parents"] = [_text(folder_id, "folder_id", 1024)]
        item = (
            _files()
            .create(
                body=body,
                media_body=MediaInMemoryUpload(data, mimetype="text/plain"),
                fields="id,name,mimeType,parents,size,modifiedTime,webViewLink",
            )
            .execute()
        )
        return json.dumps({"ok": True, "file": _metadata(item)}, ensure_ascii=False)
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def update_drive_text_file(
    file_id: str, name: str | None = None, content: str | None = None
) -> str:
    """Rename and/or replace the content of an existing Drive text file."""
    try:
        file_id = _text(file_id, "file_id", 1024)
        if name is None and content is None:
            raise ValueError("name or content must be provided")
        body = {"name": _text(name, "name", 255)} if name is not None else None
        media = None
        if content is not None:
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            data = content.encode("utf-8")
            if len(data) > _MAX_TEXT_BYTES:
                raise ValueError("content cannot exceed 1 MB")
            media = MediaInMemoryUpload(data, mimetype="text/plain")
        item = (
            _files()
            .update(
                fileId=file_id,
                body=body,
                media_body=media,
                fields="id,name,mimeType,parents,size,modifiedTime,webViewLink",
            )
            .execute()
        )
        return json.dumps({"ok": True, "file": _metadata(item)}, ensure_ascii=False)
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def move_drive_file(file_id: str, folder_id: str) -> str:
    """Move a Drive file into one destination folder."""
    try:
        file_id = _text(file_id, "file_id", 1024)
        folder_id = _text(folder_id, "folder_id", 1024)
        api = _files()
        current = api.get(fileId=file_id, fields="parents").execute()
        previous = ",".join(str(value) for value in current.get("parents", []))
        item = api.update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=previous or None,
            fields="id,name,mimeType,parents,size,modifiedTime,webViewLink",
        ).execute()
        return json.dumps({"ok": True, "file": _metadata(item)}, ensure_ascii=False)
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
