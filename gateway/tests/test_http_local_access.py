# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: MIT
"""HTTP regressions for the gateway's local tracking and debug endpoints."""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from stackchan_mcp.capture_server import create_capture_app
from stackchan_mcp.gateway import Gateway, TrackQuietAccessLogger


@asynccontextmanager
async def gateway_client(monkeypatch, remote, **app_options):
    monkeypatch.setenv("STACKCHAN_USB_DISABLE", "1")
    monkeypatch.delenv("STACKCHAN_USB_TRANSPORT", raising=False)
    gateway = Gateway()
    bridge = SimpleNamespace(handle_detection=AsyncMock(return_value=True))
    gateway.tracking_bridge = bridge
    app = create_capture_app(**app_options)
    app.router.add_post("/track", gateway._handle_track)

    @web.middleware
    async def simulate_peer(request, handler):
        # Change the peer address at the HTTP boundary, never via proxy headers.
        return await handler(request.clone(remote=remote))

    app.middlewares.insert(0, simulate_peer)
    server = TestServer(app)
    await server.start_server(access_log_class=TrackQuietAccessLogger)
    async with TestClient(server) as client:
        yield client, bridge


@pytest.mark.asyncio
async def test_lan_tracking_is_rejected_before_reaching_bridge(monkeypatch):
    monkeypatch.delenv("STACKCHAN_ALLOW_REMOTE_DEBUG", raising=False)
    async with gateway_client(monkeypatch, "192.0.2.10") as (client, bridge):
        response = await client.post(
            "/track", json={"x": 0.4, "y": 0.55, "confidence": 0.88}
        )
        assert response.status == 403
        bridge.handle_detection.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
async def test_explicit_remote_access_opt_in_warns_and_allows_requests(
    monkeypatch, caplog, value
):
    monkeypatch.setenv("STACKCHAN_ALLOW_REMOTE_DEBUG", value)
    async with gateway_client(monkeypatch, "192.0.2.10") as (client, bridge):
        assert (await client.get("/debug/status")).status == 200
        assert (await client.get("/debug/panel")).status == 200
        assert (await client.post("/track", json={"x": 0.4})).status == 200
        bridge.handle_detection.assert_awaited_once_with({"x": 0.4})
    assert "STACKCHAN_ALLOW_REMOTE_DEBUG" in caplog.text
    assert "WARNING" in caplog.text


@pytest.mark.asyncio
async def test_rejections_log_peer_and_path_without_flooding_access_logs(
    monkeypatch, caplog
):
    import stackchan_mcp.capture_server as capture_server

    monkeypatch.delenv("STACKCHAN_ALLOW_REMOTE_DEBUG", raising=False)
    now = [100.0]
    monkeypatch.setattr(
        capture_server, "time", SimpleNamespace(monotonic=lambda: now[0])
    )
    caplog.set_level(logging.INFO)
    async with gateway_client(monkeypatch, "192.0.2.10") as (client, _):
        caplog.clear()
        for path in ["/debug/status", "/debug/panel", "/debug/status"]:
            assert (await client.get(path)).status == 403
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING
        assert "192.0.2.10" in caplog.text
        assert "/debug/status" in caplog.text
        now[0] += 30
        assert (await client.post("/track", json={})).status == 403
        assert len(caplog.records) == 2
        assert "/track" in caplog.records[-1].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", ["127.0.0.1", "127.0.0.2", "::1"])
async def test_loopback_tracking_and_debug_work_even_with_remote_forwarding_headers(
    monkeypatch, remote
):
    monkeypatch.delenv("STACKCHAN_ALLOW_REMOTE_DEBUG", raising=False)
    headers = {"X-Forwarded-For": "192.0.2.10", "Forwarded": "for=192.0.2.10"}
    payload = {"x": 0.4, "y": 0.55, "confidence": 0.88}
    async with gateway_client(monkeypatch, remote) as (client, bridge):
        response = await client.post("/track", json=payload, headers=headers)
        assert response.status == 200
        assert await response.json() == {"ok": True, "moved": True}
        bridge.handle_detection.assert_awaited_once_with(payload)
        assert (await client.get("/debug/status", headers=headers)).status == 200
        assert (await client.get("/debug/panel", headers=headers)).status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote", ["192.0.2.10", "2001:db8::10", "::ffff:192.0.2.10", "", "localhost"]
)
async def test_non_loopback_or_unknown_peers_cannot_spoof_proxy_headers(
    monkeypatch, remote
):
    monkeypatch.delenv("STACKCHAN_ALLOW_REMOTE_DEBUG", raising=False)
    headers = {"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"}
    async with gateway_client(monkeypatch, remote) as (client, bridge):
        assert (await client.post("/track", json={}, headers=headers)).status == 403
        assert (await client.get("/debug/status", headers=headers)).status == 403
        assert (await client.get("/debug/panel", headers=headers)).status == 403
        bridge.handle_detection.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "typo"])
async def test_remote_access_requires_explicit_opt_in(monkeypatch, value):
    monkeypatch.setenv("STACKCHAN_ALLOW_REMOTE_DEBUG", value)
    async with gateway_client(monkeypatch, "192.0.2.10") as (client, _):
        assert (await client.get("/debug/status")).status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote,allow_remote,token,expected",
    [
        ("192.0.2.10", "0", "", 403),
        ("192.0.2.10", "0", "test-token", 403),
        ("127.0.0.1", "0", "", 401),
        ("127.0.0.1", "0", "wrong", 401),
        ("127.0.0.1", "0", "test-token", 200),
        ("192.0.2.10", "1", "", 401),
        ("192.0.2.10", "1", "test-token", 200),
    ],
)
async def test_text_injection_checks_peer_before_token(
    monkeypatch, remote, allow_remote, token, expected
):
    monkeypatch.setenv("STACKCHAN_ALLOW_REMOTE_DEBUG", allow_remote)
    inject = AsyncMock(return_value=True)
    async with gateway_client(
        monkeypatch,
        remote,
        capture_token="test-token",
        inject_text_handler=inject,
    ) as (client, _):
        response = await client.post(
            "/debug/inject-text",
            json={"text": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status == expected
        if expected == 200:
            inject.assert_awaited_once_with("hello")
        else:
            inject.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured,provided,expected",
    [("", "", 200), ("test-token", "", 401), ("test-token", "test-token", 200)],
)
async def test_lan_capture_keeps_existing_token_rules(
    monkeypatch, tmp_path, configured, provided, expected
):
    monkeypatch.delenv("STACKCHAN_ALLOW_REMOTE_DEBUG", raising=False)
    monkeypatch.setattr("stackchan_mcp.capture_server.CAPTURE_DIR", str(tmp_path))
    image = b"\xff\xd8\xff\xd9"  # Synthetic JPEG payload, never a camera photo.
    data = FormData()
    data.add_field("question", "test capture")
    data.add_field("file", image, filename="test.jpg", content_type="image/jpeg")
    async with gateway_client(monkeypatch, "192.0.2.10", capture_token=configured) as (
        client,
        _,
    ):
        response = await client.post(
            "/capture", data=data, headers={"Authorization": f"Bearer {provided}"}
        )
        assert response.status == expected
        if expected == 200:
            assert (await response.json())["size_bytes"] == len(image)
            assert next(tmp_path.iterdir()).read_bytes() == image
        else:
            assert list(tmp_path.iterdir()) == []
