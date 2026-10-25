#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Verify automatic recovery using real Grok audio and an isolated test connection.

Only the test connection is interrupted. No robot, running gateway, login data,
or network settings are changed. Speech is generated from a fixed test phrase.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import wave

import aiohttp
from aiohttp import web

from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge, validate_bridge_url
from stackchan_mcp.gbot_voice_proxy import GbotVoiceProxy
from stackchan_mcp.opus_codec import _patch_find_library_for_brew


async def wait_until(predicate, timeout=60):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.1)
    await asyncio.wait_for(wait(), timeout)


async def run(args):
    upstream = validate_bridge_url(args.bridge_url)
    available = False
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"scope": "isolated_connection_real_grok_audio_no_robot", "passed": False}
    pcm = bytearray()
    _patch_find_library_for_brew()
    from opuslib import Decoder
    decoder = Decoder(24000, 1)

    async def sink(message):
        if isinstance(message, bytes):
            pcm.extend(decoder.decode(message, 1440))

    async with aiohttp.ClientSession(trust_env=False) as http:
        async def relay(request):
            if request.headers.get("Origin"):
                return web.json_response({"error": "origin_forbidden"}, status=403)
            if not available:
                return web.json_response({"error": "test_helper_unavailable"}, status=503)
            body = await request.json() if request.method == "POST" else None
            async with http.request(
                request.method, upstream + request.path, json=body, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                # Ephemeral credentials are relayed in memory only.
                data = await response.json()
                return web.json_response(data, status=response.status)

        app = web.Application(client_max_size=65536)
        app.router.add_get("/credential", relay)
        app.router.add_post("/session", relay)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        def bridge_factory(**kwargs):
            return GbotVoiceBridge(
                **kwargs, bridge_url=f"http://127.0.0.1:{port}",
                agent_id=args.agent_id, local_tools=False,
            )

        proxy = GbotVoiceProxy(
            bridge_factory=bridge_factory, wake_gate_factory=lambda: None,
            health_check_interval_s=1, health_check_timeout_s=10,
            reconnect_initial_delay_s=1, reconnect_max_delay_s=5,
        )
        try:
            summary["initial_failure_observed"] = not await proxy.start(
                SimpleNamespace(device_id="recovery-probe"), {}, sink
            )
            available = True
            await wait_until(lambda: proxy.connected)
            await wait_until(lambda: proxy.health_snapshot()["successful_checks"] > 0)
            first = await proxy.speak_text(
                "自动恢复测试正常。", session_id="probe", prompt_audio_frames=[]
            )
            summary["after_initial_failure"] = {
                key: first.get(key) for key in ("ok", "frames_sent", "provider")
            }
            previous_attempts = proxy.health_snapshot()["reconnect_attempts"]
            await proxy._bridge._ws.close(code=1001)
            await wait_until(lambda: (
                proxy.connected
                and proxy.health_snapshot()["reconnect_attempts"] > previous_attempts
            ))
            second = await proxy.speak_text(
                "连接中断后已自动恢复。", session_id="probe", prompt_audio_frames=[]
            )
            summary["after_socket_close"] = {
                key: second.get(key) for key in ("ok", "frames_sent", "provider")
            }
            summary["health"] = proxy.health_snapshot()
            summary["passed"] = bool(
                summary["initial_failure_observed"] and first.get("ok") and second.get("ok")
            )
        except Exception as error:
            summary["error_type"] = type(error).__name__
        finally:
            await proxy.stop()
            await runner.cleanup()
    with wave.open(str(args.output_dir / "recovered-audio.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(pcm)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:18771")
    parser.add_argument("--agent-id", default=os.getenv("STACKCHAN_GBOT_VOICE_BOT_ID"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.agent_id:
        parser.error("Set --agent-id or STACKCHAN_GBOT_VOICE_BOT_ID")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
