import json
from uuid import uuid4
import numpy as np
from datetime import datetime
from fastapi import APIRouter, WebSocket, UploadFile, File, Response, Body, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect
from loguru import logger
from .bridge_auth import BRIDGE_TOKEN_HEADER, verify_bridge_token
from .furniture_state import (
    delete_task,
    diary_snapshot,
    scheduler_events_snapshot,
    timer_snapshot,
    todo_snapshot,
)
from .service_context import ServiceContext
from .websocket_handler import WebSocketHandler
from .proxy_handler import ProxyHandler
from .google_integration import GoogleIntegrationError, GoogleIntegrationService


def init_google_integration_routes(service: GoogleIntegrationService) -> APIRouter:
    """Expose the local, application-wide Google OAuth flow."""
    router = APIRouter(prefix="/api/integrations/google")

    @router.get("/status")
    async def google_status():
        return JSONResponse(await run_in_threadpool(service.status))

    @router.get("/gmail/unread-status")
    async def gmail_unread_status():
        try:
            return JSONResponse(await run_in_threadpool(service.unread_status))
        except GoogleIntegrationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except Exception as exc:  # Google client errors are never safe to expose.
            logger.warning("Gmail unread status failed: type={}", type(exc).__name__)
            return JSONResponse(
                {"error": "Gmail unread status is unavailable."}, status_code=502
            )

    @router.get("/gmail/unread")
    async def gmail_unread(page_size: int = 20, page_token: str = ""):
        if page_size < 1 or page_size > 50:
            return JSONResponse(
                {"error": "page_size must be between 1 and 50."}, status_code=400
            )
        if len(page_token) > 2048:
            return JSONResponse({"error": "page_token is invalid."}, status_code=400)
        try:
            payload = await run_in_threadpool(
                service.unread_messages,
                page_size=page_size,
                page_token=page_token or None,
            )
            return JSONResponse(payload)
        except GoogleIntegrationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except Exception as exc:
            logger.warning("Gmail unread listing failed: type={}", type(exc).__name__)
            return JSONResponse(
                {"error": "Unread Gmail messages are unavailable."}, status_code=502
            )

    @router.get("/authorize")
    async def google_authorize():
        try:
            url = await run_in_threadpool(service.authorization_url)
            return RedirectResponse(url, status_code=302)
        except GoogleIntegrationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.get("/oauth/callback")
    async def google_callback(state: str = "", code: str = "", error: str = ""):
        if error:
            message = "Google authorization was cancelled or denied."
            status_code = 400
        else:
            try:
                await run_in_threadpool(service.exchange_code, state, code)
                message = "Google is connected. You may close this window."
                status_code = 200
            except GoogleIntegrationError as exc:
                message = str(exc)
                status_code = 400
        # This intentionally contains no external resources, scripts, or callback data.
        return HTMLResponse(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Google authorization</title></head>"
            f"<body><main><h1>{message}</h1></main></body></html>",
            status_code=status_code,
            headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
        )

    return router


def init_furniture_routes() -> APIRouter:
    """Read-only views of the state behind furniture hover windows.

    The frontend polls these while a hover window is open. They expose only what
    the panel renders — no identifiers, no file paths, and no way to mutate the
    stores, which stay reachable to the agent only through proximity-gated MCP
    tools.
    """
    router = APIRouter(prefix="/api/furniture")

    @router.get("/todo")
    async def todo_window():
        return JSONResponse(await run_in_threadpool(todo_snapshot))

    @router.get("/timers")
    async def timer_window():
        return JSONResponse(await run_in_threadpool(timer_snapshot))
    
    @router.get("/diary")
    async def diary_window():
        return JSONResponse(await run_in_threadpool(diary_snapshot))

    return router


def init_scheduler_routes() -> APIRouter:
    """Expose pending scheduler events to the local management UI."""
    router = APIRouter(prefix="/api/scheduler")

    @router.get("/events")
    async def scheduler_events():
        payload = await run_in_threadpool(scheduler_events_snapshot)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.delete("/events/{event_id}")
    async def delete_scheduler_event(event_id: str):
        if not event_id or len(event_id) > 128:
            return JSONResponse({"error": "invalid event id"}, status_code=400)
        try:
            deleted = await run_in_threadpool(delete_task, event_id)
        except OSError:
            logger.exception("Could not update the scheduler task store")
            return JSONResponse(
                {"error": "scheduled event could not be deleted"},
                status_code=503,
            )
        if not deleted:
            return JSONResponse({"error": "scheduled event not found"}, status_code=404)
        return JSONResponse({"deleted": True, "id": event_id})

    return router


def init_client_ws_route(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling the `/client-ws` WebSocket connections.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()
    ws_handler = WebSocketHandler(default_context_cache)

    @router.websocket("/client-ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for client connections"""
        await websocket.accept()
        client_uid = str(uuid4())

        try:
            await ws_handler.handle_new_connection(websocket, client_uid)
            await ws_handler.handle_websocket_communication(websocket, client_uid)
        except WebSocketDisconnect:
            await ws_handler.handle_disconnect(client_uid)
        except Exception as e:
            logger.error(f"Error in WebSocket connection: {e}")
            await ws_handler.handle_disconnect(client_uid)
            raise

    return router


def init_proxy_route(server_url: str) -> APIRouter:
    """
    Create and return API routes for handling proxy connections.

    Args:
        server_url: The WebSocket URL of the actual server

    Returns:
        APIRouter: Configured router with proxy WebSocket endpoint
    """
    router = APIRouter()
    proxy_handler = ProxyHandler(server_url)

    @router.websocket("/proxy-ws")
    async def proxy_endpoint(websocket: WebSocket):
        """WebSocket endpoint for proxy connections"""
        try:
            await proxy_handler.handle_client_connection(websocket)
        except Exception as e:
            logger.error(f"Error in proxy connection: {e}")
            raise

    return router


# Bounds for how long a bridge request may wait on the frontend. Prevents a
# caller from parking unbounded futures in the bridge.
_BRIDGE_TIMEOUT_MIN = 0.5
_BRIDGE_TIMEOUT_MAX = 60.0

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def init_frontend_action_routes() -> APIRouter:
    """Create routes that let local MCP subprocesses reach the active frontend.

    This endpoint is for the server's own MCP tool subprocesses only. It is
    guarded by a per-run shared secret (X-Bridge-Token) and restricted to
    loopback callers, because the app serves permissive CORS headers and an
    open endpoint here would let any website drive the frontend.
    """
    router = APIRouter()

    @router.post("/internal/frontend-action")
    async def frontend_action(request: Request, payload: dict = Body(...)):
        from .frontend_bridge import request_frontend_action

        client_host = request.client.host if request.client else None
        if client_host not in _LOOPBACK_HOSTS:
            return JSONResponse(
                {"ok": False, "result": {}, "error": "forbidden"},
                status_code=403,
            )
        if not verify_bridge_token(request.headers.get(BRIDGE_TOKEN_HEADER)):
            return JSONResponse(
                {"ok": False, "result": {}, "error": "invalid bridge token"},
                status_code=403,
            )

        action = payload.get("action")
        args = payload.get("args", {})
        client_uid = payload.get("client_uid")
        conf_uid = payload.get("conf_uid")
        try:
            timeout = float(payload.get("timeout", 15.0))
        except (TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "result": {}, "error": "timeout must be a number"},
                status_code=400,
            )
        timeout = max(_BRIDGE_TIMEOUT_MIN, min(_BRIDGE_TIMEOUT_MAX, timeout))

        if not isinstance(action, str) or not action:
            return JSONResponse(
                {"ok": False, "result": {}, "error": "missing action"},
                status_code=400,
            )
        if not isinstance(args, dict):
            return JSONResponse(
                {"ok": False, "result": {}, "error": "args must be an object"},
                status_code=400,
            )
        if client_uid is not None and (
            not isinstance(client_uid, str) or not client_uid
        ):
            return JSONResponse(
                {"ok": False, "result": {}, "error": "client_uid must be a string"},
                status_code=400,
            )
        if conf_uid is not None and (not isinstance(conf_uid, str) or not conf_uid):
            return JSONResponse(
                {"ok": False, "result": {}, "error": "conf_uid must be a string"},
                status_code=400,
            )

        ok, result, error = await request_frontend_action(
            action=action,
            args=args,
            timeout=timeout,
            client_uid=client_uid,
            conf_uid=conf_uid,
        )
        status_code = 200 if ok else 503
        return JSONResponse(
            {"ok": ok, "result": result, "error": error},
            status_code=status_code,
        )

    return router


def init_webtool_routes(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling web tool interactions.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()

    @router.get("/web-tool")
    async def web_tool_redirect():
        """Redirect /web-tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/web_tool")
    async def web_tool_redirect_alt():
        """Redirect /web_tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    # Reject absurdly large uploads before buffering them all in memory.
    max_asr_upload_bytes = 50 * 1024 * 1024  # ~25 min of 16-bit 24kHz mono

    @router.post("/asr")
    async def transcribe_audio(file: UploadFile = File(...)):
        """
        Endpoint for transcribing audio using the ASR engine
        """
        logger.info(f"Received audio file for transcription: {file.filename}")

        try:
            contents = await file.read(max_asr_upload_bytes + 1)
            if len(contents) > max_asr_upload_bytes:
                return Response(
                    content=json.dumps({"error": "Audio file too large"}),
                    status_code=413,
                    media_type="application/json",
                )

            # Validate minimum file size
            if len(contents) < 44:  # Minimum WAV header size
                raise ValueError("Invalid WAV file: File too small")

            # Decode the WAV header and get actual audio data
            wav_header_size = 44  # Standard WAV header size
            audio_data = contents[wav_header_size:]

            # Validate audio data size
            if len(audio_data) % 2 != 0:
                raise ValueError("Invalid audio data: Buffer size must be even")

            # Convert to 16-bit PCM samples to float32
            try:
                audio_array = (
                    np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
            except ValueError as e:
                raise ValueError(
                    f"Audio format error: {str(e)}. Please ensure the file is 16-bit PCM WAV format."
                )

            # Validate audio data
            if len(audio_array) == 0:
                raise ValueError("Empty audio data")

            text = await default_context_cache.asr_engine.async_transcribe_np(
                audio_array
            )
            logger.info(f"Transcription result: {text}")
            return {"text": text}

        except ValueError as e:
            logger.error(f"Audio format error: {e}")
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return Response(
                content=json.dumps(
                    {"error": "Internal server error during transcription"}
                ),
                status_code=500,
                media_type="application/json",
            )

    @router.websocket("/tts-ws")
    async def tts_endpoint(websocket: WebSocket):
        """WebSocket endpoint for TTS generation"""
        await websocket.accept()
        logger.info("TTS WebSocket connection established")

        try:
            while True:
                data = await websocket.receive_json()
                text = data.get("text")
                if not text:
                    continue

                logger.info(f"Received text for TTS: {text}")

                # Split text into sentences
                sentences = [s.strip() for s in text.split(".") if s.strip()]

                try:
                    # Generate and send audio for each sentence
                    for sentence in sentences:
                        sentence = sentence + "."  # Add back the period
                        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid4())[:8]}"
                        audio_path = (
                            await default_context_cache.tts_engine.async_generate_audio(
                                text=sentence, file_name_no_ext=file_name
                            )
                        )
                        logger.info(
                            f"Generated audio for sentence: {sentence} at: {audio_path}"
                        )

                        await websocket.send_json(
                            {
                                "status": "partial",
                                "audioPath": audio_path,
                                "text": sentence,
                            }
                        )

                    # Send completion signal
                    await websocket.send_json({"status": "complete"})

                except Exception as e:
                    logger.error(f"Error generating TTS: {e}")
                    await websocket.send_json({"status": "error", "message": str(e)})

        except WebSocketDisconnect:
            logger.info("TTS WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Error in TTS WebSocket connection: {e}")
            await websocket.close()

    return router
