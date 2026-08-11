"""Gmail tools unlocked near the mailbox."""

from __future__ import annotations

import base64
import json
import sys
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.google_integration import (
    GoogleAuthorizationRequired,
    GoogleIntegrationError,
    GoogleIntegrationService,
    calendar_subprocess_settings,
)
from src.open_llm_vtuber.demo_scenarios import (
    record_simulated_write,
    simulated_fixture,
)
from mcp_servers.bridge_client import get_scene_mode, post_frontend_action_result

_MAX_RESULTS = 100
_MAX_BODY = 100_000
mcp = FastMCP("gmail")
_gmail_service: GoogleIntegrationService | None = None


def _service() -> GoogleIntegrationService:
    global _gmail_service
    if _gmail_service is None:
        _gmail_service = GoogleIntegrationService(calendar_subprocess_settings())
    return _gmail_service


def _messages():
    return _service().gmail().users().messages()


def _text(value: str, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} cannot be empty")
    if len(value) > maximum or "\r" in value or "\n" in value:
        raise ValueError(f"{field} is invalid")
    return value


def _recipients(value: str, field: str) -> str:
    value = _text(value, field, 2000)
    addresses = [address for _, address in getaddresses([value])]
    if not addresses or any("@" not in address for address in addresses):
        raise ValueError(f"{field} must contain valid email addresses")
    return value


def _headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload")
    values: dict[str, str] = {}
    for header in payload.get("headers", []) if isinstance(payload, dict) else []:
        if isinstance(header, dict):
            values[str(header.get("name", "")).lower()] = str(header.get("value") or "")
    return values


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
        "utf-8", errors="replace"
    )


def _body(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    mime = payload.get("mimeType")
    data = payload.get("body", {}).get("data")
    if mime == "text/plain" and isinstance(data, str):
        return _decode(data)[:_MAX_BODY]
    plain = ""
    fallback = ""
    for part in payload.get("parts", []):
        value = _body(part)
        if not value:
            continue
        if isinstance(part, dict) and part.get("mimeType") == "text/plain":
            plain += value
        elif not fallback:
            fallback = value
    if plain:
        return plain[:_MAX_BODY]
    if mime == "text/html" and isinstance(data, str):
        return _decode(data)[:_MAX_BODY]
    return fallback[:_MAX_BODY]


def _serialize(message: dict[str, Any], include_body: bool = False) -> dict[str, Any]:
    headers = _headers(message)
    result = {
        "message_id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject") or "(no subject)",
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", ""),
        "labels": message.get("labelIds", []),
    }
    if include_body:
        result["body"] = _body(message.get("payload"))
    return result


def _raw(to: str, subject: str, body: str, cc: str, bcc: str) -> str:
    if not isinstance(body, str) or len(body) > _MAX_BODY:
        raise ValueError("body must be a string no longer than 100000 characters")
    email = EmailMessage()
    email["To"] = _recipients(to, "to")
    email["Subject"] = _text(subject, "subject", 998, False)
    if cc:
        email["Cc"] = _recipients(cc, "cc")
    if bcc:
        email["Bcc"] = _recipients(bcc, "bcc")
    email.set_content(body)
    return base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")


def _error(exc: Exception) -> str:
    if isinstance(exc, GoogleAuthorizationRequired):
        return f"Error: {exc}"
    if isinstance(exc, HttpError) and exc.resp.status == 404:
        return "Error: Gmail message was not found"
    if isinstance(exc, (HttpError, GoogleIntegrationError, OSError)):
        return "Error: Gmail request failed"
    return f"Error: {exc}"


@mcp.tool()
def search_emails(
    query: str = "", page_token: str | None = None, limit: int = 50
) -> str:
    """Search Gmail messages using Gmail query syntax."""
    try:
        fixture = simulated_fixture()
        if fixture is not None:
            email = fixture.get("email")
            return json.dumps(
                {
                    "messages": [email] if isinstance(email, dict) else [],
                    "next_page_token": None,
                    "simulated": True,
                },
                ensure_ascii=False,
            )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        kwargs: dict[str, Any] = {
            "userId": "me",
            "q": _text(query, "query", 1000, False),
            "maxResults": max(1, min(limit, _MAX_RESULTS)),
            "includeSpamTrash": False,
        }
        if page_token:
            kwargs["pageToken"] = _text(page_token, "page_token", 2048)
        api = _messages()
        response = api.list(**kwargs).execute()
        found = []
        for item in response.get("messages", []):
            message = api.get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            found.append(_serialize(message))
        return json.dumps(
            {"messages": found, "next_page_token": response.get("nextPageToken")},
            ensure_ascii=False,
        )
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def get_email(message_id: str) -> str:
    """Read one Gmail message, including its bounded text body."""
    try:
        fixture = simulated_fixture()
        if fixture is not None:
            email = fixture.get("email")
            if not isinstance(email, dict):
                return "Error: the simulated mailbox contains no messages"
            # The simulated mailbox holds exactly one message, so any requested
            # id resolves to it.  This keeps identifier hallucination inside the
            # demo instead of escaping into a real Gmail request.
            return json.dumps(email, ensure_ascii=False)
        message = (
            _messages()
            .get(userId="me", id=_text(message_id, "message_id", 1024), format="full")
            .execute()
        )
        return json.dumps(_serialize(message, True), ensure_ascii=False)
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
async def materialize_email(message_id: str) -> str:
    """Materialize one Gmail message as a temporary, grabbable VR card.

    This only creates the card at the mailbox. It does not pick it up, move the
    agent, offer it, or assume that physical presentation is the right channel.
    """
    if get_scene_mode() != "vr":
        return "Error: email cards are only available in VR mode"
    try:
        fixture = simulated_fixture()
        if fixture is not None:
            rendered = fixture.get("email")
            if not isinstance(rendered, dict):
                return "Error: the simulated mailbox contains no messages"
        else:
            message = (
                _messages()
                .get(
                    userId="me", id=_text(message_id, "message_id", 1024), format="full"
                )
                .execute()
            )
            rendered = _serialize(message, True)
        ok, error, result = await post_frontend_action_result(
            "materialize_email",
            {
                "message_id": rendered["message_id"],
                "sender": str(rendered.get("from", ""))[:320],
                "subject": str(rendered.get("subject", ""))[:200],
                "summary": str(rendered.get("snippet", ""))[:280],
                "body": str(rendered.get("body", ""))[:8000],
            },
        )
        if not ok:
            return f"Error: could not materialize email: {error}"
        return str(
            result.get("summary")
            or "OK: created a temporary email card at the mailbox."
        )[:2000]
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def create_email_draft(
    to: str, subject: str, body: str, cc: str = "", bcc: str = ""
) -> str:
    """Create an unsent Gmail draft."""
    try:
        if simulated_fixture() is not None:
            record = record_simulated_write(
                "gmail",
                {
                    "action": "create_draft",
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "cc": cc,
                    "bcc": bcc,
                },
            )
            return json.dumps(
                {
                    "ok": True,
                    "draft_id": record["id"],
                    "message_id": "",
                    "simulated": True,
                }
            )
        draft = (
            _service()
            .gmail()
            .users()
            .drafts()
            .create(
                userId="me", body={"message": {"raw": _raw(to, subject, body, cc, bcc)}}
            )
            .execute()
        )
        return json.dumps(
            {
                "ok": True,
                "draft_id": draft.get("id", ""),
                "message_id": draft.get("message", {}).get("id", ""),
            }
        )
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def send_email(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Send a new plain-text email through Gmail."""
    try:
        if simulated_fixture() is not None:
            record = record_simulated_write(
                "gmail",
                {
                    "action": "send",
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "cc": cc,
                    "bcc": bcc,
                },
            )
            return json.dumps(
                {
                    "ok": True,
                    "message_id": record["id"],
                    "thread_id": "",
                    "simulated": True,
                }
            )
        sent = (
            _messages()
            .send(userId="me", body={"raw": _raw(to, subject, body, cc, bcc)})
            .execute()
        )
        return json.dumps(
            {
                "ok": True,
                "message_id": sent.get("id", ""),
                "thread_id": sent.get("threadId", ""),
            }
        )
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


def _modify(message_id: str, add: list[str], remove: list[str]) -> str:
    if simulated_fixture() is not None:
        record = record_simulated_write(
            "gmail",
            {
                "action": "modify",
                "message_id": message_id,
                "add": add,
                "remove": remove,
            },
        )
        return json.dumps(
            {
                "ok": True,
                "message_id": message_id,
                "record_id": record["id"],
                "simulated": True,
            }
        )
    message_id = _text(message_id, "message_id", 1024)
    message = (
        _messages()
        .modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": add, "removeLabelIds": remove},
        )
        .execute()
    )
    return json.dumps({"ok": True, "message_id": message.get("id", message_id)})


@mcp.tool()
def mark_email_read(message_id: str) -> str:
    """Remove the UNREAD label from a Gmail message."""
    try:
        return _modify(message_id, [], ["UNREAD"])
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def mark_email_unread(message_id: str) -> str:
    """Add the UNREAD label to a Gmail message."""
    try:
        return _modify(message_id, ["UNREAD"], [])
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def archive_email(message_id: str) -> str:
    """Archive a Gmail message by removing its INBOX label."""
    try:
        return _modify(message_id, [], ["INBOX"])
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
