"""
Open-LLM-VTuber Server
========================
This module contains the WebSocket server for Open-LLM-VTuber, which handles
the WebSocket connections, serves static files, and manages the web tool.
It uses FastAPI for the server and Starlette for static file serving.
"""

import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles as StarletteStaticFiles

from .routes import (
    init_client_ws_route,
    init_frontend_action_routes,
    init_furniture_routes,
    init_google_integration_routes,
    init_scheduler_routes,
    init_agenda_routes,
    init_operator_routes,
    init_webtool_routes,
    init_proxy_route,
)
from .service_context import ServiceContext
from .config_manager.utils import Config
from .google_integration import GoogleIntegrationService
from .request_security import (
    build_trusted_hosts,
    cors_origin_regex,
    require_trusted_http_request,
)
from .workspace import embeddings


# Static files remain under the app-level restricted CORS middleware.
class CORSStaticFiles(StarletteStaticFiles):
    """Static files handler with corrected JavaScript content types."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)

        if path.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"

        response.headers["Permissions-Policy"] = "xr-spatial-tracking=(self)"

        return response


class ProtectedStaticFiles(CORSStaticFiles):
    """Static files restricted by the private HTTP request policy."""

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive=receive)
        try:
            require_trusted_http_request(request)
        except HTTPException as exc:
            headers = dict(exc.headers or {})
            headers["Cache-Control"] = "no-store"
            response = JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        await super().__call__(scope, receive, send)


class AvatarStaticFiles(CORSStaticFiles):
    """
    Avatar files handler with security restrictions and CORS headers
    """

    async def get_response(self, path: str, scope):
        allowed_extensions = (".jpg", ".jpeg", ".png", ".gif", ".svg")
        if not any(path.lower().endswith(ext) for ext in allowed_extensions):
            return Response("Forbidden file type", status_code=403)
        response = await super().get_response(path, scope)
        return response


class WebSocketServer:
    """
    API server for Open-LLM-VTuber. This contains the websocket endpoint for the client, hosts the web tool, and serves static files.

    Creates and configures a FastAPI app, registers all routes
    (WebSocket, web tools, proxy) and mounts static assets with CORS.

    Args:
        config (Config): Application configuration containing system settings.
        default_context_cache (ServiceContext, optional):
            Pre‑initialized service context for sessions' service context to reference to.
            **If omitted, `initialize()` method needs to be called to load service context.**

    Notes:
        - If default_context_cache is omitted, call `await initialize()` to load service context cache.
        - Use `clean_cache()` to clear and recreate the local cache directory.
    """

    def __init__(self, config: Config, default_context_cache: ServiceContext = None):
        self.app = FastAPI(
            title="Open-LLM-VTuber Server",
            lifespan=self._lifespan,
        )
        self.config = config
        self._headless_autonomy_started = False
        self.default_context_cache = (
            default_context_cache or ServiceContext()
        )  # Use provided context or initialize a new empty one waiting to be loaded
        # It will be populated during the initialize method call
        system_config = config.system_config
        trusted_hosts = build_trusted_hosts(
            system_config.host,
            system_config.trusted_hosts,
        )
        self.app.state.trusted_hosts = trusted_hosts

        # Cross-origin access is limited to explicitly trusted hosts. Same-origin
        # production requests need no CORS header; loopback cross-port access is
        # retained for the development renderer. Packaged Electron sends a null
        # origin and is separately restricted to loopback + Electron user-agent
        # by request_security.
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["null", "file://"],
            allow_origin_regex=cors_origin_regex(trusted_hosts),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Include routes, passing the context instance
        # The context will be populated during the initialize step
        self.app.include_router(
            init_client_ws_route(default_context_cache=self.default_context_cache),
        )
        self.app.include_router(
            init_webtool_routes(default_context_cache=self.default_context_cache),
        )
        self.app.include_router(init_frontend_action_routes())
        self.app.include_router(init_furniture_routes())
        self.app.include_router(init_scheduler_routes())
        self.app.include_router(init_agenda_routes())
        self.app.include_router(init_operator_routes())
        self.google_integration = GoogleIntegrationService.from_config(
            config.system_config.google_integration
        )
        self.app.include_router(init_google_integration_routes(self.google_integration))

        # Initialize and include proxy routes if proxy is enabled
        if hasattr(system_config, "enable_proxy") and system_config.enable_proxy:
            # Construct the server URL for the proxy
            host = system_config.host
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            port = system_config.port
            server_url = f"ws://{host}:{port}/client-ws"
            self.app.include_router(
                init_proxy_route(server_url=server_url),
            )

        # Ensure runtime directories exist
        os.makedirs("cache", exist_ok=True)
        os.makedirs("avatars", exist_ok=True)
        self.app.mount(
            "/cache",
            ProtectedStaticFiles(directory="cache"),
            name="cache",
        )

        # Mount static files with CORS-enabled handlers
        self.app.mount(
            "/bg",
            CORSStaticFiles(directory="backgrounds"),
            name="backgrounds",
        )
        self.app.mount(
            "/avatars",
            AvatarStaticFiles(directory="avatars"),
            name="avatars",
        )

        # Mount web tool directory separately from frontend
        self.app.mount(
            "/web-tool",
            CORSStaticFiles(directory="web_tool", html=True),
            name="web_tool",
        )

        # Mount main frontend last (as catch-all)
        self.app.mount(
            "/",
            CORSStaticFiles(directory="frontend", html=True),
            name="frontend",
        )

    @asynccontextmanager
    async def _lifespan(self, _app: FastAPI):
        await self._start_headless_autonomy()
        try:
            yield
        finally:
            await self._stop_headless_autonomy()

    async def _start_headless_autonomy(self) -> None:
        """Start the safe server-lifetime owner after context initialization."""
        if self._headless_autonomy_started:
            return
        system_config = getattr(self.default_context_cache, "system_config", None)
        if system_config is None:
            return

        from .autonomy import (
            HEADLESS_CLIENT_UID,
            AutonomySettings,
            configure_headless_runtime,
        )

        settings = AutonomySettings.from_config(system_config)
        if not settings.enabled or not settings.headless_enabled:
            return

        async def discard_output(_payload: str) -> None:
            return None

        from .autonomy.presence import get_or_create_presence_ledger
        from .client_registry import register_client
        from .workspace import ensure_workspace

        workspace_root = ensure_workspace().root
        presence = get_or_create_presence_ledger(
            HEADLESS_CLIENT_UID,
            workspace_root,
            settings,
        )
        register_client(
            HEADLESS_CLIENT_UID,
            self.default_context_cache,
            discard_output,
        )
        await configure_headless_runtime(workspace_root, settings, presence)
        self._headless_autonomy_started = True

    async def _stop_headless_autonomy(self) -> None:
        if not self._headless_autonomy_started:
            return
        from .autonomy import HEADLESS_CLIENT_UID
        from .autonomy.presence import unregister_presence_ledger
        from .autonomy.runtime import reset_runtimes
        from .client_registry import unregister_client
        from .conversations.llm_request_queue import remove_queue

        await reset_runtimes()
        remove_queue(HEADLESS_CLIENT_UID)
        unregister_client(HEADLESS_CLIENT_UID)
        unregister_presence_ledger(HEADLESS_CLIENT_UID)
        self._headless_autonomy_started = False

    async def initialize(self):
        """Asynchronously load the service context from config.
        Calling this function is needed if default_context_cache was not provided to the constructor."""
        await self.default_context_cache.load_from_config(self.config)
        # Load the recall embedding model in the background: it takes tens of
        # seconds the first time, and no conversation turn should wait for it.
        # Until it is ready, workspace recall falls back to keyword matching.
        embeddings.warm_up()

    @staticmethod
    def clean_cache():
        """Clean the cache directory by removing and recreating it."""
        cache_dir = "cache"
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)
