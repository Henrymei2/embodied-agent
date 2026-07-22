"""Application-wide Google OAuth and Calendar service.

Credentials are deliberately stored outside both the repository and the agent
workspace.  Callers receive sanitized status/errors and must never log token
payloads or authorization responses.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from loguru import logger
from oauthlib.oauth2 import OAuth2Error
from platformdirs import user_data_path
from requests import RequestException

GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
)
_STATE_TTL_SECONDS = 10 * 60
_MAX_PENDING_STATES = 64
_UNRESOLVED_ENV_PREFIX = "${"


class GoogleIntegrationError(RuntimeError):
    """A user-safe Google integration error."""


class GoogleAuthorizationRequired(GoogleIntegrationError):
    """Google credentials are missing, expired, or revoked."""


@dataclass(frozen=True)
class GoogleIntegrationSettings:
    enabled: bool
    client_secrets_file: str
    redirect_uri: str
    token_file: Path


def default_token_file() -> Path:
    return user_data_path("Open-LLM-VTuber", appauthor=False) / "google_token.json"


def calendar_subprocess_settings() -> GoogleIntegrationSettings:
    """Build token-only settings for the Calendar MCP subprocess."""
    return GoogleIntegrationSettings(
        enabled=True,
        client_secrets_file="",
        redirect_uri="",
        token_file=Path(
            os.environ.get(
                "EMBODIED_AGENT_GOOGLE_TOKEN_FILE", str(default_token_file())
            )
        ).expanduser(),
    )


class GoogleIntegrationService:
    def __init__(self, settings: GoogleIntegrationSettings):
        self.settings = settings
        # Keep the PKCE verifier with its single-use CSRF state. Recent
        # google-auth-oauthlib releases enable PKCE by default, so recreating a
        # Flow for the callback without this verifier makes Google reject the
        # otherwise valid authorization code as invalid_grant.
        self._states: dict[str, tuple[float, str | None]] = {}
        self._state_lock = threading.Lock()

    @classmethod
    def from_config(cls, config: Any) -> "GoogleIntegrationService":
        return cls(
            GoogleIntegrationSettings(
                enabled=bool(config.enabled),
                client_secrets_file=str(config.client_secrets_file),
                redirect_uri=str(config.redirect_uri),
                token_file=default_token_file(),
            )
        )

    @property
    def configured(self) -> bool:
        if not self.settings.enabled:
            return False
        filename = self.settings.client_secrets_file.strip()
        if not filename or filename.startswith(_UNRESOLVED_ENV_PREFIX):
            return False
        path = Path(filename).expanduser()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        web = payload.get("web") if isinstance(payload, dict) else None
        redirect_uris = web.get("redirect_uris", []) if isinstance(web, dict) else []
        return bool(
            isinstance(web, dict)
            and web.get("client_id")
            and web.get("client_secret")
            and self.settings.redirect_uri
            and self.settings.redirect_uri in redirect_uris
        )

    def _credentials_path(self) -> Path:
        return self.settings.token_file.expanduser()

    def _load_credentials(self) -> Credentials:
        path = self._credentials_path()
        if not path.exists():
            raise GoogleAuthorizationRequired("Google Calendar is not authorized.")
        try:
            credentials = Credentials.from_authorized_user_file(
                str(path), scopes=list(GOOGLE_SCOPES)
            )
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(GoogleAuthRequest())
                self._save_credentials(credentials)
            if not credentials.valid:
                raise GoogleAuthorizationRequired(
                    "Google authorization has expired. Please authorize again."
                )
            return credentials
        except GoogleAuthorizationRequired:
            raise
        except (OSError, ValueError, RefreshError) as exc:
            raise GoogleAuthorizationRequired(
                "Google authorization is invalid or revoked. Please authorize again."
            ) from exc

    def _save_credentials(self, credentials: Credentials) -> None:
        path = self._credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(credentials.to_json(), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _flow(
        self, *, state: str | None = None, code_verifier: str | None = None
    ) -> Flow:
        if not self.configured:
            raise GoogleIntegrationError(
                "Google OAuth is not configured. Set a valid client secrets file."
            )
        flow = Flow.from_client_secrets_file(
            str(Path(self.settings.client_secrets_file).expanduser()),
            scopes=list(GOOGLE_SCOPES),
            state=state,
            code_verifier=code_verifier,
        )
        flow.redirect_uri = self.settings.redirect_uri
        return flow

    def authorization_url(self) -> str:
        if not self.configured:
            raise GoogleIntegrationError(
                "Google OAuth is not configured. Set a valid client secrets file."
            )
        state = secrets.token_urlsafe(32)
        flow = self._flow(state=state)
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent select_account",
        )
        now = time.monotonic()
        with self._state_lock:
            self._states = {
                key: pending
                for key, pending in self._states.items()
                if pending[0] > now
            }
            if len(self._states) >= _MAX_PENDING_STATES:
                oldest = min(self._states, key=lambda key: self._states[key][0])
                self._states.pop(oldest, None)
            self._states[state] = (
                now + _STATE_TTL_SECONDS,
                flow.code_verifier,
            )
        return url

    def exchange_code(self, state: str, code: str) -> None:
        if not state or not code:
            raise GoogleIntegrationError("The OAuth callback is missing required data.")
        now = time.monotonic()
        with self._state_lock:
            pending = self._states.pop(state, None)
        if pending is None or pending[0] <= now:
            raise GoogleIntegrationError("The OAuth request is invalid or expired.")
        _, code_verifier = pending
        try:
            flow = self._flow(state=state, code_verifier=code_verifier)
            flow.fetch_token(code=code)
            self._save_credentials(flow.credentials)
        except GoogleIntegrationError:
            raise
        except OAuth2Error as exc:
            # OAuthlib exceptions may contain the complete token response in their
            # string representation. Log only the exception class and the stable
            # OAuth error identifier, never the message or response payload.
            error = exc.error if isinstance(exc.error, str) else "unknown"
            safe_error = error if error.replace("_", "").isalnum() else "unknown"
            logger.warning(
                "Google OAuth token exchange rejected: type={}, error={}",
                type(exc).__name__,
                safe_error,
            )
            if safe_error == "invalid_client":
                message = (
                    "Google rejected the OAuth client credentials. Download a new "
                    "Web application client JSON and restart the server."
                )
            elif safe_error == "invalid_grant":
                message = (
                    "Google rejected the authorization code. Start authorization "
                    "again and complete it within ten minutes."
                )
            else:
                message = f"Google authorization failed ({safe_error})."
            raise GoogleIntegrationError(message) from exc
        except RequestException as exc:
            logger.warning(
                "Google OAuth token exchange network failure: type={}",
                type(exc).__name__,
            )
            raise GoogleIntegrationError(
                "Google's token service could not be reached. Check the network "
                "connection and try authorization again."
            ) from exc
        except Exception as exc:
            # Do not stringify the exception: third-party exceptions can embed the
            # authorization code, client secret, or full token response.
            logger.warning(
                "Google OAuth token exchange failed: type={}", type(exc).__name__
            )
            raise GoogleIntegrationError(
                "Google authorization could not be completed."
            ) from exc

    def calendar(self):
        credentials = self._load_credentials()
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def gmail(self):
        credentials = self._load_credentials()
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def drive(self):
        credentials = self._load_credentials()
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _gmail_header(message: dict[str, Any], name: str) -> str:
        payload = message.get("payload")
        headers = payload.get("headers", []) if isinstance(payload, dict) else []
        for header in headers:
            if (
                isinstance(header, dict)
                and str(header.get("name", "")).lower() == name.lower()
            ):
                return str(header.get("value") or "")
        return ""

    def unread_status(self) -> dict[str, bool]:
        response = (
            self.gmail()
            .users()
            .messages()
            .list(
                userId="me",
                labelIds=["UNREAD"],
                includeSpamTrash=False,
                maxResults=1,
            )
            .execute()
        )
        return {"has_unread": bool(response.get("messages"))}

    def unread_messages(
        self, *, page_size: int = 20, page_token: str | None = None
    ) -> dict[str, Any]:
        messages_api = self.gmail().users().messages()
        kwargs: dict[str, Any] = {
            "userId": "me",
            "labelIds": ["UNREAD"],
            "includeSpamTrash": False,
            "maxResults": page_size,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        response = messages_api.list(**kwargs).execute()
        items = []
        for reference in response.get("messages", []):
            if not isinstance(reference, dict) or not reference.get("id"):
                continue
            message = messages_api.get(
                userId="me",
                id=reference["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            items.append(
                {
                    "message_id": str(message.get("id") or ""),
                    "thread_id": str(message.get("threadId") or ""),
                    "sender": self._gmail_header(message, "From"),
                    "subject": self._gmail_header(message, "Subject") or "(no subject)",
                    "received_at": self._gmail_header(message, "Date"),
                    "snippet": str(message.get("snippet") or ""),
                }
            )
        return {
            "messages": items,
            "next_page_token": response.get("nextPageToken"),
            "has_unread": bool(items),
        }

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "message": "Google OAuth is not configured on this server.",
            }
        try:
            self.calendar().calendarList().get(calendarId="primary").execute()
            return {
                "configured": True,
                "connected": True,
                "status": "connected",
                "message": "Google Calendar is connected.",
            }
        except GoogleAuthorizationRequired:
            return {
                "configured": True,
                "connected": False,
                "status": "authorization_required",
                "message": "Authorize Google Calendar to continue.",
            }
        except HttpError as exc:
            if exc.resp.status == 401:
                return {
                    "configured": True,
                    "connected": False,
                    "status": "authorization_required",
                    "message": "Google authorization is invalid. Please authorize again.",
                }
            return {
                "configured": True,
                "connected": False,
                "status": "error",
                "message": "Google Calendar could not be reached.",
            }
        except (OSError, ValueError):
            return {
                "configured": True,
                "connected": False,
                "status": "error",
                "message": "Google Calendar could not be reached.",
            }
