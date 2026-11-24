# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Regression tests for loopback and bearer-token access control on gateway HTTP routes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import aiohttp
from aiohttp import StreamReader
from aiohttp.base_protocol import BaseProtocol
from aiohttp.test_utils import make_mocked_request
import pytest

from stackchan_mcp.gateway import Gateway


def _make_payload(data: bytes, loop) -> StreamReader:
    protocol = BaseProtocol(loop)
    reader = StreamReader(protocol, limit=2**16, loop=loop)
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def _make_multipart_capture() -> tuple[bytes, str]:
    boundary = "----stackchan-test-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="question"\r\n\r\n'
        "what is this\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="camera.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8") + b"\xff\xd8\xff\xe0test-jpeg\xff\xd9" + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def _make_peer_request(
    app,
    method: str,
    path: str,
    *,
    peername,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    content_type: str | None = None,
):
    loop = asyncio.get_running_loop()
    req_headers = dict(headers or {})
    if content_type:
        req_headers["Content-Type"] = content_type
    payload = _make_payload(body, loop) if body else aiohttp.EMPTY_PAYLOAD

    transport = mock.Mock()
    transport.get_extra_info.side_effect = lambda name, default=None: (
        peername if name == "peername" else default
    )
    return make_mocked_request(
        method,
        path,
        headers=req_headers,
        app=app,
        transport=transport,
        payload=payload,
        loop=loop,
    )


async def _start_gateway(monkeypatch, tmp_path, *, env: dict[str, str] | None = None):
    for var in ("VISION_TOKEN", "STACKCHAN_TOKEN", "BEARER_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("WS_PORT", "0")
    monkeypatch.setenv("CAPTURE_PORT", "0")
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    monkeypatch.setenv("STACKCHAN_FACE_TRACKER_AUTOSTART", "0")
    monkeypatch.setattr("stackchan_mcp.capture_server.CAPTURE_DIR", str(tmp_path))
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)

    gw = Gateway()
    recorded: dict[str, list] = {
        "track": [],
        "intro": [],
        "arm": [],
        "inject": [],
    }

    async def record_track(detection):
        recorded["track"].append(detection)
        return True

    async def record_inject(text: str) -> bool:
        recorded["inject"].append(text)
        return True

    gw.tracking_bridge.handle_detection = record_track  # type: ignore[assignment]
    gw.demo_reactions.spawn_intro = lambda: recorded["intro"].append(True)  # type: ignore[method-assign]
    gw.demo_reactions.arm_face_entry = lambda: recorded["arm"].append(True)  # type: ignore[method-assign]
    gw.esp32 = SimpleNamespace(
        start=gw.esp32.start,
        stop=gw.esp32.stop,
        get_status=lambda: {"connected": False, "initialized": False},
        connection=SimpleNamespace(
            cloud_proxy=SimpleNamespace(
                _bridge=SimpleNamespace(inject_user_text=record_inject)
            )
        ),
    )

    await gw.start()
    port = gw._http_runner.addresses[0][1]
    return gw, gw._http_runner.app, f"http://127.0.0.1:{port}", recorded


LOOPBACK_ONLY_ENDPOINTS = [
    ("POST", "/track", b'{"x": 0.5, "y": 0.5, "confidence": 0.9}', "application/json"),
    ("GET", "/debug/status", b"", None),
    ("GET", "/debug/panel", b"", None),
    ("GET", "/debug/voice-health", b"", None),
    ("GET", "/demo/intro", b"", None),
    ("POST", "/demo/intro", b"", None),
    ("GET", "/demo/arm", b"", None),
    ("POST", "/demo/arm", b"", None),
]

SPOOFED_LOCAL_HEADERS = {
    "X-Forwarded-For": "127.0.0.1",
    "Forwarded": "for=127.0.0.1",
    "X-Real-IP": "127.0.0.1",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body,content_type", LOOPBACK_ONLY_ENDPOINTS)
@pytest.mark.parametrize(
    "peername",
    [
        ("192.168.1.50", 54321),
        ("::ffff:192.168.1.50", 54321, 0, 0),
        ("2001:db8::1", 54321, 0, 0),
        None,
    ],
)
async def test_loopback_only_endpoints_reject_non_loopback_peers(
    monkeypatch, tmp_path, method, path, body, content_type, peername
):
    gw, app, _base_url, recorded = await _start_gateway(
        monkeypatch, tmp_path, env={"STACKCHAN_TOKEN": "shared-secret"}
    )
    try:
        headers = {
            **SPOOFED_LOCAL_HEADERS,
            "Authorization": "Bearer shared-secret",
        }
        req = _make_peer_request(
            app,
            method,
            path,
            peername=peername,
            headers=headers,
            body=body,
            content_type=content_type,
        )
        resp = await app._handle(req)
        assert resp.status == 403
        assert recorded["track"] == []
        assert recorded["intro"] == []
        assert recorded["arm"] == []
    finally:
        await gw.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peername",
    [
        ("192.168.1.50", 54321),
        ("::ffff:192.168.1.50", 54321, 0, 0),
        None,
    ],
)
async def test_token_endpoints_reject_non_loopback_when_no_token_configured(
    monkeypatch, tmp_path, peername
):
    gw, app, _base_url, recorded = await _start_gateway(monkeypatch, tmp_path)
    try:
        capture_body, capture_ct = _make_multipart_capture()
        capture_req = _make_peer_request(
            app,
            "POST",
            "/capture",
            peername=peername,
            headers={**SPOOFED_LOCAL_HEADERS, "Authorization": "Bearer anything"},
            body=capture_body,
            content_type=capture_ct,
        )
        capture_resp = await app._handle(capture_req)
        assert capture_resp.status == 403
        assert list(tmp_path.iterdir()) == []

        inject_req = _make_peer_request(
            app,
            "POST",
            "/debug/inject-text",
            peername=peername,
            headers={**SPOOFED_LOCAL_HEADERS, "Authorization": "Bearer anything"},
            body=b'{"text": "hello"}',
            content_type="application/json",
        )
        inject_resp = await app._handle(inject_req)
        assert inject_resp.status == 403
        assert recorded["inject"] == []
    finally:
        await gw.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peername",
    [
        ("127.0.0.1", 54321),
        ("::1", 54321, 0, 0),
        ("::ffff:127.0.0.1", 54321, 0, 0),
    ],
)
async def test_loopback_variants_allow_local_only_endpoints_even_with_token_set(
    monkeypatch, tmp_path, peername
):
    gw, app, _base_url, recorded = await _start_gateway(
        monkeypatch, tmp_path, env={"STACKCHAN_TOKEN": "shared-secret"}
    )
    try:
        for method, path, body, content_type in LOOPBACK_ONLY_ENDPOINTS:
            req = _make_peer_request(
                app,
                method,
                path,
                peername=peername,
                body=body,
                content_type=content_type,
            )
            resp = await app._handle(req)
            expected = 503 if path == "/debug/voice-health" else 200
            assert resp.status == expected, f"{method} {path} with {peername} returned {resp.status}"
        assert len(recorded["track"]) == 1
        assert len(recorded["intro"]) == 2
        assert len(recorded["arm"]) == 2
    finally:
        await gw.stop()


@pytest.mark.asyncio
async def test_real_loopback_http_requests_succeed_without_token(monkeypatch, tmp_path):
    gw, _app, base_url, recorded = await _start_gateway(monkeypatch, tmp_path)
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{base_url}/track",
                json={"x": 0.4, "y": 0.5, "confidence": 0.9},
            ) as resp:
                assert resp.status == 200
                assert (await resp.json())["ok"] is True

            async with client.get(f"{base_url}/debug/status") as resp:
                assert resp.status == 200
                assert "device" in await resp.json()

            async with client.get(f"{base_url}/debug/panel") as resp:
                assert resp.status == 200
                assert "/debug/status" in await resp.text()

            async with client.get(f"{base_url}/debug/voice-health") as resp:
                assert resp.status == 503
                assert (await resp.json())["ok"] is False

            for method, path in (
                ("GET", "/demo/intro"),
                ("POST", "/demo/intro"),
                ("GET", "/demo/arm"),
                ("POST", "/demo/arm"),
            ):
                async with client.request(method, f"{base_url}{path}") as resp:
                    assert resp.status == 200
                    assert (await resp.json())["ok"] is True

            async with client.post(
                f"{base_url}/debug/inject-text",
                json={"text": "ping"},
            ) as resp:
                assert resp.status == 200
                assert (await resp.json()) == {"ok": True, "active_session": True}

            form = aiohttp.FormData()
            form.add_field("question", "describe")
            form.add_field(
                "file",
                b"\xff\xd8\xff\xe0jpeg\xff\xd9",
                filename="camera.jpg",
                content_type="image/jpeg",
            )
            async with client.post(f"{base_url}/capture", data=form) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["question"] == "describe"
                assert data["size_bytes"] > 0

        assert len(recorded["track"]) == 1
        assert len(recorded["intro"]) == 2
        assert len(recorded["arm"]) == 2
        assert recorded["inject"] == ["ping"]
    finally:
        await gw.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("token_var", ["VISION_TOKEN", "STACKCHAN_TOKEN", "BEARER_TOKEN"])
async def test_token_endpoints_require_bearer_and_allow_remote_with_valid_token(
    monkeypatch, tmp_path, token_var
):
    secret = f"secret-from-{token_var.lower()}"
    gw, app, _base_url, recorded = await _start_gateway(
        monkeypatch, tmp_path, env={token_var: secret}
    )
    try:
        for peername in (("192.168.1.50", 54321), ("127.0.0.1", 54321)):
            for bad_headers in ({}, {"Authorization": "Bearer wrong-token"}):
                cap_body, cap_ct = _make_multipart_capture()
                cap_resp = await app._handle(
                    _make_peer_request(
                        app,
                        "POST",
                        "/capture",
                        peername=peername,
                        headers=bad_headers,
                        body=cap_body,
                        content_type=cap_ct,
                    )
                )
                assert cap_resp.status == 401

                inj_resp = await app._handle(
                    _make_peer_request(
                        app,
                        "POST",
                        "/debug/inject-text",
                        peername=peername,
                        headers=bad_headers,
                        body=b'{"text": "nope"}',
                        content_type="application/json",
                    )
                )
                assert inj_resp.status == 401

            good_headers = {"Authorization": f"Bearer {secret}"}
            cap_body, cap_ct = _make_multipart_capture()
            cap_ok = await app._handle(
                _make_peer_request(
                    app,
                    "POST",
                    "/capture",
                    peername=peername,
                    headers=good_headers,
                    body=cap_body,
                    content_type=cap_ct,
                )
            )
            assert cap_ok.status == 200
            assert json.loads(cap_ok.text)["question"] == "what is this"

            inj_ok = await app._handle(
                _make_peer_request(
                    app,
                    "POST",
                    "/debug/inject-text",
                    peername=peername,
                    headers=good_headers,
                    body=b'{"text": "allowed"}',
                    content_type="application/json",
                )
            )
            assert inj_ok.status == 200
            assert json.loads(inj_ok.text) == {"ok": True, "active_session": True}

        assert recorded["inject"] == ["allowed", "allowed"]
    finally:
        await gw.stop()


@pytest.mark.asyncio
async def test_token_endpoints_reject_non_ascii_and_malformed_tokens_without_500(
    monkeypatch, tmp_path
):
    gw, app, _base_url, recorded = await _start_gateway(
        monkeypatch, tmp_path, env={"STACKCHAN_TOKEN": "shared-secret"}
    )
    try:
        invalid_headers = [
            {},
            {"Authorization": ""},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer "},
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "shared-secret"},
            {"Authorization": "Bearer 令牌"},
            {"Authorization": "Bearer caf\u00e9"},
            {"Authorization": "Bearer " + ("a" * 4096)},
        ]
        for peername in (("192.168.1.50", 54321), ("127.0.0.1", 54321)):
            for bad_headers in invalid_headers:
                cap_body, cap_ct = _make_multipart_capture()
                cap_resp = await app._handle(
                    _make_peer_request(
                        app,
                        "POST",
                        "/capture",
                        peername=peername,
                        headers=bad_headers,
                        body=cap_body,
                        content_type=cap_ct,
                    )
                )
                assert cap_resp.status == 401

                inj_resp = await app._handle(
                    _make_peer_request(
                        app,
                        "POST",
                        "/debug/inject-text",
                        peername=peername,
                        headers=bad_headers,
                        body=b'{"text": "nope"}',
                        content_type="application/json",
                    )
                )
                assert inj_resp.status == 401

        # Exercise aiohttp's real HTTP header parser over TCP with raw non-ASCII bytes.
        port = gw._http_runner.addresses[0][1]
        for raw_auth in (
            "Bearer 令牌".encode("utf-8"),
            b"Bearer caf\xe9",
            b"Bearer " + (b"x" * 4096),
        ):
            for path, content_type, body in (
                ("/debug/inject-text", b"application/json", b'{"text":"ping"}'),
                (
                    "/capture",
                    b"multipart/form-data; boundary=----b",
                    b"------b--\r\n",
                ),
            ):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    raw_request = (
                        f"POST {path} HTTP/1.1\r\n".encode("ascii")
                        + b"Host: 127.0.0.1\r\n"
                        + b"Authorization: "
                        + raw_auth
                        + b"\r\n"
                        + b"Content-Type: "
                        + content_type
                        + b"\r\n"
                        + f"Content-Length: {len(body)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + body
                    )
                    writer.write(raw_request)
                    await writer.drain()
                    raw_response = await reader.read()
                finally:
                    writer.close()
                    await writer.wait_closed()
                status_line = raw_response.split(b"\r\n", 1)[0].decode("latin-1")
                assert status_line.startswith("HTTP/1.1 401 "), (
                    f"{path} with {raw_auth!r} returned {status_line!r}"
                )

        assert list(tmp_path.iterdir()) == []
        assert recorded["inject"] == []
    finally:
        await gw.stop()

    # Without any configured token, non-loopback requests with non-ASCII headers still return 403.
    gw_no_token, app_no_token, _url, rec_no_token = await _start_gateway(monkeypatch, tmp_path)
    try:
        cap_body, cap_ct = _make_multipart_capture()
        cap_resp = await app_no_token._handle(
            _make_peer_request(
                app_no_token,
                "POST",
                "/capture",
                peername=("192.168.1.50", 54321),
                headers={"Authorization": "Bearer 令牌"},
                body=cap_body,
                content_type=cap_ct,
            )
        )
        assert cap_resp.status == 403

        inj_resp = await app_no_token._handle(
            _make_peer_request(
                app_no_token,
                "POST",
                "/debug/inject-text",
                peername=("192.168.1.50", 54321),
                headers={"Authorization": "Bearer 令牌"},
                body=b'{"text": "nope"}',
                content_type="application/json",
            )
        )
        assert inj_resp.status == 403
        assert list(tmp_path.iterdir()) == []
        assert rec_no_token["inject"] == []
    finally:
        await gw_no_token.stop()
