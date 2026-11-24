"""HTTP capture server for receiving photos from ESP32.

ESP32's camera.Explain() POSTs multipart/form-data with:
- field 'question' (text)
- field 'file' (camera.jpg, JPEG image)

This server saves the JPEG and returns the file path so MCP client
can view the image via the Read tool.
"""

from __future__ import annotations

import hmac
import inspect
import ipaddress
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable

from aiohttp import web

from .debug_panel import PANEL_HTML
from .debug_status import DebugStatus, get_debug_status

logger = logging.getLogger(__name__)

CAPTURE_DIR = os.path.expanduser("~/.stackchan/captures")
CAPTURE_TOKEN_KEY = web.AppKey("capture_token", str)
InjectTextHandler = Callable[[str], bool | Awaitable[bool]]
INJECT_TEXT_HANDLER_KEY = web.AppKey(
    "inject_text_handler",
    object,
)
DEBUG_STATUS_KEY = web.AppKey("debug_status", DebugStatus)
TOKEN_OR_LOOPBACK_PATHS = frozenset({"/capture", "/debug/inject-text"})


def _extract_peer_ip(request: web.Request) -> str | None:
    """Return the transport peer IP address, ignoring forwarding headers."""
    transport = getattr(request, "transport", None)
    if transport is not None:
        peername = transport.get_extra_info("peername")
        if isinstance(peername, (tuple, list)) and peername:
            return str(peername[0])
        if isinstance(peername, str) and peername:
            return peername
    remote = getattr(request, "remote", None)
    return str(remote) if remote else None


def _is_loopback_address(host: str | None) -> bool:
    """Return True only for IPv4/IPv6 loopback peers, including IPv4-mapped IPv6."""
    if not host:
        return False
    raw = host.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if "%" in raw:
        raw = raw.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return mapped.is_loopback
    return addr.is_loopback


def _access_control_middleware():
    """Enforce loopback-only access on control/debug routes and token-or-loopback on capture/inject."""
    last_rejection_log: float | None = None

    @web.middleware
    async def access_control(request: web.Request, handler):
        nonlocal last_rejection_log
        if request.path in TOKEN_OR_LOOPBACK_PATHS:
            expected_token = request.app[CAPTURE_TOKEN_KEY]
            if expected_token:
                if not _is_authorized(
                    request.headers.get("Authorization", ""), expected_token
                ):
                    logger.warning("HTTP auth rejected: path=%r", request.path)
                    return web.json_response({"error": "Unauthorized"}, status=401)
                return await handler(request)

        peer_ip = _extract_peer_ip(request)
        if not _is_loopback_address(peer_ip):
            now = time.monotonic()
            if last_rejection_log is None or now - last_rejection_log >= 30:
                logger.warning(
                    "Rejected non-loopback HTTP request: peer=%r path=%r",
                    peer_ip,
                    request.path,
                )
                last_rejection_log = now
            return web.json_response({"error": "Loopback requests only"}, status=403)

        return await handler(request)

    return access_control


def _is_authorized(auth_header: str, expected_token: str) -> bool:
    """Return whether the bearer auth header matches the expected token."""
    return hmac.compare_digest(auth_header, f"Bearer {expected_token}")


async def handle_capture(request: web.Request) -> web.Response:
    """Handle photo upload from ESP32."""
    expected_token = request.app[CAPTURE_TOKEN_KEY]
    if expected_token and not _is_authorized(
        request.headers.get("Authorization", ""), expected_token
    ):
        logger.warning("Capture upload auth rejected")
        return web.Response(
            text='{"error": "Unauthorized"}',
            status=401,
            content_type="application/json",
        )

    os.makedirs(CAPTURE_DIR, exist_ok=True)

    reader = await request.multipart()
    question = ""
    image_path = ""

    async for part in reader:
        if part.name == "question":
            question = (await part.read()).decode("utf-8")
        elif part.name == "file":
            timestamp = int(time.time() * 1000)
            filename = f"capture_{timestamp}.jpg"
            image_path = os.path.join(CAPTURE_DIR, filename)
            with open(image_path, "wb") as f:
                while True:
                    chunk = await part.read_chunk(8192)
                    if not chunk:
                        break
                    f.write(chunk)

    if image_path and os.path.exists(image_path):
        file_size = os.path.getsize(image_path)
        logger.info(
            "Captured photo: %s (%d bytes), question: %s",
            image_path,
            file_size,
            question,
        )
        result = json.dumps({
            "image_path": image_path,
            "size_bytes": file_size,
            "question": question,
        })
        return web.Response(text=result, content_type="application/json")

    return web.Response(
        text='{"error": "No image received"}',
        status=400,
        content_type="application/json",
    )


async def handle_inject_text(request: web.Request) -> web.Response:
    """Inject a plain text user turn into the active Gemini Live session."""
    expected_token = request.app[CAPTURE_TOKEN_KEY]
    if expected_token and not _is_authorized(
        request.headers.get("Authorization", ""), expected_token
    ):
        logger.warning("Debug text injection auth rejected")
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    text = str(body.get("text", "")).strip() if isinstance(body, dict) else ""
    if not text:
        return web.json_response({"ok": False, "error": "text is required"}, status=400)

    handler = request.app[INJECT_TEXT_HANDLER_KEY]
    active_session = False
    if handler is not None:
        try:
            result = handler(text)
            if inspect.isawaitable(result):
                result = await result
            active_session = bool(result)
        except Exception as exc:
            logger.exception("Debug text injection failed")
            return web.json_response(
                {"ok": False, "active_session": False, "error": str(exc)}
            )

    return web.json_response({"ok": True, "active_session": active_session})


async def handle_debug_status(request: web.Request) -> web.Response:
    """Return the gateway status snapshot as JSON (read-only, loopback-only)."""
    return web.json_response(request.app[DEBUG_STATUS_KEY].snapshot())


async def handle_debug_panel(request: web.Request) -> web.Response:
    """Return the zero-dependency self-refreshing status panel."""
    return web.Response(text=PANEL_HTML, content_type="text/html")


async def handle_voice_health(request: web.Request) -> web.Response:
    """Report the supervised voice session, rather than HTTP-server liveness."""
    health = request.app[DEBUG_STATUS_KEY].snapshot().get("voice")
    health = health or {"state": "unavailable"}
    ready = health["state"] == "connected"
    return web.json_response({"ok": ready, **health}, status=200 if ready else 503)


def create_capture_app(
    capture_token: str = "",
    *,
    inject_text_handler: InjectTextHandler | None = None,
    debug_status: DebugStatus | None = None,
) -> web.Application:
    """Create the HTTP capture application."""
    app = web.Application(middlewares=[_access_control_middleware()])
    app[CAPTURE_TOKEN_KEY] = capture_token
    app[INJECT_TEXT_HANDLER_KEY] = inject_text_handler
    app[DEBUG_STATUS_KEY] = debug_status or get_debug_status()
    app.router.add_post("/capture", handle_capture)
    app.router.add_post("/debug/inject-text", handle_inject_text)
    app.router.add_get("/debug/status", handle_debug_status)
    app.router.add_get("/debug/voice-health", handle_voice_health)
    app.router.add_get("/debug/panel", handle_debug_panel)
    return app
