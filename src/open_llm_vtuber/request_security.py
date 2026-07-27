"""Access controls for browser-facing HTTP and WebSocket endpoints.

The application is local-first, but browsers can still reach localhost from an
unrelated website. CORS alone is not an authorization boundary (and does not
apply to WebSockets), so sensitive routes validate the request target, browser
origin, and peer address before doing any work.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket, status

_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_WILDCARD_BIND_HOSTS = frozenset({"0.0.0.0", "::", ""})


def _normalize_hostname(hostname: str | None) -> str:
    if not hostname:
        return ""
    normalized = hostname.rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(normalized))
    except ValueError:
        try:
            return normalized.encode("idna").decode("ascii")
        except UnicodeError:
            return ""


def _hostname_from_host_header(host_header: str | None) -> str:
    host, _port = _host_and_port(host_header)
    return host


def _host_and_port(host_header: str | None) -> tuple[str, int | None]:
    if not host_header:
        return "", None
    if (
        host_header != host_header.strip()
        or any(character in host_header for character in "/\\@?#")
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in host_header
        )
        or any(character.isspace() for character in host_header)
    ):
        return "", None

    if host_header.startswith("["):
        closing_bracket = host_header.find("]")
        suffix = host_header[closing_bracket + 1 :] if closing_bracket >= 0 else ""
        if closing_bracket <= 1 or (
            suffix and (not suffix.startswith(":") or not suffix[1:].isdigit())
        ):
            return "", None
    else:
        if "[" in host_header or "]" in host_header or host_header.count(":") > 1:
            return "", None
        if ":" in host_header and not host_header.rsplit(":", 1)[1].isdigit():
            return "", None

    try:
        parsed = urlsplit(f"//{host_header}")
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return "", None
        return _normalize_hostname(parsed.hostname), parsed.port
    except ValueError:
        return "", None


def is_loopback_host(hostname: str | None) -> bool:
    normalized = _normalize_hostname(hostname)
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return address.ipv4_mapped.is_loopback
        return address.is_loopback
    except ValueError:
        return False


def build_trusted_hosts(
    bind_host: str | None,
    configured_hosts: Iterable[str] = (),
) -> frozenset[str]:
    """Build the exact Host-header allowlist for this server.

    Wildcard bind addresses describe sockets, not names clients should be
    allowed to send in Host headers, so they are deliberately not trusted.
    """

    if isinstance(configured_hosts, str):
        raise ValueError("trusted_hosts must be a list of exact hostnames or IPs")

    hosts = set(_LOOPBACK_NAMES)
    normalized_bind_host = _normalize_hostname(bind_host)
    if normalized_bind_host and normalized_bind_host not in _WILDCARD_BIND_HOSTS:
        hosts.add(_validate_explicit_host(bind_host or ""))

    for configured in configured_hosts:
        normalized = _validate_explicit_host(configured)
        if normalized in _WILDCARD_BIND_HOSTS:
            raise ValueError(f"Invalid trusted host: {configured!r}")
        hosts.add(normalized)
    return frozenset(hosts)


def _validate_explicit_host(hostname: str) -> str:
    if not isinstance(hostname, str) or hostname != hostname.strip():
        raise ValueError(f"Invalid trusted host: {hostname!r}")
    if (
        not hostname
        or "*" in hostname
        or any(character in hostname for character in "/\\@?#")
        or any(character.isspace() for character in hostname)
    ):
        raise ValueError(f"Invalid trusted host: {hostname!r}")

    candidate = (
        hostname[1:-1]
        if hostname.startswith("[") and hostname.endswith("]")
        else hostname
    )
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        if ":" in candidate:
            raise ValueError(f"Invalid trusted host: {hostname!r}") from None

    normalized = _normalize_hostname(candidate)
    labels = normalized.split(".")
    if (
        not normalized
        or len(normalized) > 253
        or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise ValueError(f"Invalid trusted host: {hostname!r}")
    return normalized


def cors_origin_regex(trusted_hosts: Iterable[str]) -> str:
    """Return a CORS regex limited to the configured HTTP(S) hostnames."""

    alternatives = []
    for host in sorted({_normalize_hostname(item) for item in trusted_hosts}):
        if not host:
            continue
        rendered = f"[{host}]" if ":" in host else host
        alternatives.append(re.escape(rendered))
    if not alternatives:
        return r"(?!)"
    return rf"^https?://(?:{'|'.join(alternatives)})(?::\d{{1,5}})?$"


def _trusted_hosts_from_app(app) -> frozenset[str]:
    configured = getattr(app.state, "trusted_hosts", None)
    if configured is None:
        # Router factories can be embedded without WebSocketServer. Preserve
        # local-only use in that case, but never interpret missing state as an
        # unrestricted Host allowlist.
        return _LOOPBACK_NAMES
    return frozenset(configured)


def _host_is_allowed(host_header: str | None, trusted_hosts: frozenset[str] | None):
    request_host = _hostname_from_host_header(host_header)
    if not request_host:
        return False
    allowed_hosts = _LOOPBACK_NAMES if trusted_hosts is None else trusted_hosts
    return request_host in allowed_hosts


def _origin_is_allowed(
    origin: str,
    host_header: str | None,
    trusted_hosts: frozenset[str] | None,
) -> bool:
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False

    origin_host = _normalize_hostname(parsed.hostname)
    request_host, request_port = _host_and_port(host_header)
    if not origin_host or not request_host:
        return False
    if trusted_hosts is not None and origin_host not in trusted_hosts:
        return False

    # Loopback aliases and ports are interchangeable for the local development
    # renderer. Network hosts must match both hostname and effective port.
    if is_loopback_host(origin_host) and is_loopback_host(request_host):
        return True
    if origin_host != request_host:
        return False
    default_port = 443 if parsed.scheme == "https" else 80
    return (origin_port or default_port) == (request_port or default_port)


def _is_electron_user_agent(user_agent: str | None) -> bool:
    return bool(user_agent and re.search(r"(?:^|\s)Electron/\d", user_agent))


def is_trusted_request(
    *,
    host_header: str | None,
    origin: str | None,
    client_host: str | None,
    user_agent: str | None,
    trusted_hosts: frozenset[str] | None,
    referer: str | None = None,
) -> bool:
    """Apply the common HTTP/WebSocket browser access policy."""

    # Treat an explicitly omitted allowlist as loopback-only as well. This keeps
    # the low-level helper fail-safe for callers outside the FastAPI dependency.
    trusted_hosts = _LOOPBACK_NAMES if trusted_hosts is None else trusted_hosts
    if not _host_is_allowed(host_header, trusted_hosts):
        return False
    request_host = _hostname_from_host_header(host_header)
    if is_loopback_host(request_host) and not is_loopback_host(client_host):
        # A remote native client can spoof Host and Origin headers. It must use
        # an explicitly trusted network hostname, never a loopback alias.
        return False

    if origin:
        if origin in {"null", "file://"}:
            # Packaged Electron renderers load from file:// and therefore send
            # a non-web origin. Restrict this exception to the loopback desktop
            # app; ordinary sandboxed web frames can also have null origins.
            return is_loopback_host(client_host) and _is_electron_user_agent(user_agent)
        return _origin_is_allowed(origin, host_header, trusted_hosts)

    if referer:
        try:
            parsed_referer = urlsplit(referer)
        except ValueError:
            return False
        if parsed_referer.scheme in {"http", "https"} and parsed_referer.netloc:
            referer_origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
            if _origin_is_allowed(referer_origin, host_header, trusted_hosts):
                return True

    # OAuth top-level redirects and native clients do not necessarily send an
    # Origin header. Never grant that exception to a remote network peer.
    return is_loopback_host(client_host)


def require_trusted_http_request(request: Request) -> None:
    """FastAPI dependency for private local application routes."""

    client_host = request.client.host if request.client else None
    if not is_trusted_request(
        host_header=request.headers.get("host"),
        origin=request.headers.get("origin"),
        client_host=client_host,
        user_agent=request.headers.get("user-agent"),
        trusted_hosts=_trusted_hosts_from_app(request.app),
        referer=request.headers.get("referer"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed.",
        )


def is_trusted_websocket(websocket: WebSocket) -> bool:
    client_host = websocket.client.host if websocket.client else None
    return is_trusted_request(
        host_header=websocket.headers.get("host"),
        origin=websocket.headers.get("origin"),
        client_host=client_host,
        user_agent=websocket.headers.get("user-agent"),
        trusted_hosts=_trusted_hosts_from_app(websocket.app),
    )
