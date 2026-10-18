# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Experimental realtime audio using a signed-in Grok Bot's ephemeral secret.

Long-lived login credentials remain inside the local login bridge. No secrets,
server error bodies, transcripts, or tool arguments are logged here.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from .voice_output_queue import VoiceOutputQueue

logger = logging.getLogger(__name__)


class Pcm16To24:
    """Streaming linear resampler, preserving interpolation across Opus packets."""

    def __init__(self):
        self.samples: list[int] = []
        self.phase = 0

    def convert(self, pcm: bytes) -> bytes:
        self.samples.extend(struct.unpack(f"<{len(pcm) // 2}h", pcm))
        output = []
        while self.phase // 3 + 1 < len(self.samples):
            index, fraction = divmod(self.phase, 3)
            output.append(round((self.samples[index] * (3 - fraction)
                                 + self.samples[index + 1] * fraction) / 3))
            self.phase += 2
        consumed = self.phase // 3
        self.samples = self.samples[consumed:]
        self.phase -= consumed * 3
        return struct.pack(f"<{len(output)}h", *output)


def validate_bridge_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Grok Bot login bridge must use loopback HTTP")
    return url.rstrip("/")


def validate_voice_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "wss" or parsed.hostname != "api.x.ai"
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or parsed.fragment):
        raise ValueError("Grok Bot voice requires wss://api.x.ai")
    return url


def voice_url_with_model(url, model):
    parsed = urlsplit(validate_voice_url(url))
    query = dict(parse_qsl(parsed.query))
    query["model"] = model
    return urlunsplit(parsed._replace(query=urlencode(query)))


async def gather_with_cancel(*awaitables):
    """Run independent requests together and fully cancel siblings on failure."""
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class GbotVoiceBridge:
    def __init__(self, *, on_audio, on_turn_complete, on_interrupt,
                 on_session_dead, bridge_url=None, agent_id=None, on_transcript=None):
        self.url = validate_bridge_url(bridge_url or os.getenv(
            "STACKCHAN_GBOT_VOICE_URL", "http://127.0.0.1:18771"))
        self.agent_id = agent_id or os.getenv("STACKCHAN_GBOT_VOICE_BOT_ID") or os.getenv(
            "STACKCHAN_TOOL_BOT_ID", "")
        self.language = os.getenv("STACKCHAN_GBOT_VOICE_LANGUAGE", "zh")
        self.call_id = str(uuid.uuid4())
        self._on_transcript = on_transcript
        self.audio_bytes_by_response = {}
        self.response_started_at = {}
        self._on_audio = on_audio
        self._on_turn_complete = on_turn_complete
        self._on_interrupt = on_interrupt
        self._on_session_dead = on_session_dead
        self._connected_event = asyncio.Event()
        self._http = None
        self._ws = None
        self._task = None
        self._turn_task = None
        self._output = VoiceOutputQueue()
        self._tools: set[asyncio.Task] = set()
        self._suppressed: set[str] = set()
        self._active_response = None
        self._response_in_progress = False
        self._drop_until_response = False
        self._resampler = Pcm16To24()
        self._stopped = False
        self.running = False
        self.metrics = {"input_pcm_bytes": 0, "output_pcm_bytes": 0,
                        "interruptions": 0, "discarded_audio_deltas": 0,
                        "speech_starts": 0, "server_errors": {}}

    async def _local(self, path, payload=None):
        assert self._http is not None
        method = "GET" if payload is None else "POST"
        async with self._http.request(method, self.url + path, json=payload,
                                      allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError("Grok Bot local bridge request failed")
            result = await response.json()
        if result.get("ok") is False:
            raise RuntimeError("Grok Bot local bridge rejected request")
        return result

    async def start(self):
        trace = aiohttp.TraceConfig()

        async def reject_redirect(*_args):
            raise RuntimeError("Voice redirects are disabled")

        trace.on_request_redirect.append(reject_redirect)
        self._http = aiohttp.ClientSession(trust_env=False, trace_configs=[trace],
                                         timeout=aiohttp.ClientTimeout(total=30))
        try:
            # Independent requests; keep failure cleanup compatible with Python 3.10.
            minted, session = await gather_with_cancel(
                self._local("/credential"),
                self._local("/session", {
                    "agentId": self.agent_id, "agentName": "Groki",
                    "spokenLanguage": self.language,
                }),
            )
            credential = minted["credential"]
            url = voice_url_with_model(credential["websocketUrl"], credential["model"])
            if float(credential["expiresAtUnixSeconds"]) <= time.time() + 5:
                raise RuntimeError("Grok Bot temporary credential expired")
            self._ws = await self._http.ws_connect(
                url, protocols=["xai-client-secret." + credential["clientSecret"]],
                heartbeat=20, max_msg_size=8 * 1024 * 1024)
            del credential
            config = {
                "instructions": session.get("instructions", "请用中文简短自然地对话。"),
                "resumption": {"enabled": True},
                "turn_detection": {"type": "server_vad"},
                "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                                     "transcription": {"model": "grok-transcribe",
                                                       "language_hint": self.language}},
                          "output": {"format": {"type": "audio/pcm", "rate": 24000}}},
                "tools": session.get("tools", [])
                if os.getenv("STACKCHAN_GBOT_VOICE_TOOLS") == "1" else [],
            }
            if self.language == "auto":
                config["audio"]["input"]["transcription"].pop("language_hint")
            if os.getenv("STACKCHAN_GBOT_VOICE_NAME"):
                config["voice"] = os.environ["STACKCHAN_GBOT_VOICE_NAME"]
            await self._send({"type": "session.update", "session": config})
            self.running = True
            self._task = asyncio.create_task(self._receive())
        except asyncio.CancelledError:
            await self.stop()
            raise
        except Exception:
            await self.stop()
            raise RuntimeError("Grok Bot voice startup failed (details redacted)") from None

    async def wait_connected(self, timeout=10.0):
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return self.running

    async def _send(self, event):
        if self._ws is None or self._ws.closed:
            raise RuntimeError("Grok Bot voice is disconnected")
        await self._ws.send_json(event)

    async def send_audio(self, pcm):
        audio = self._resampler.convert(pcm)
        if audio:
            await self._send({"type": "input_audio_buffer.append",
                              "audio": base64.b64encode(audio).decode("ascii")})
            self.metrics["input_pcm_bytes"] += len(audio)

    async def send_audio_stream_end(self):
        # Server VAD commits completed speech. Explicit commits of empty buffers fail.
        return None

    async def send_text(self, text):
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}})
        await self._send({"type": "response.create"})

    async def interrupt(self):
        active = self._response_in_progress or bool(self._turn_task and not self._turn_task.done())
        self._drop_until_response = True
        if self._active_response:
            self._suppressed.add(self._active_response)
        await self._output.cancel()
        self._turn_task = None
        await self._on_interrupt()
        if active:
            self.metrics["interruptions"] += 1
        if self._response_in_progress:
            self._response_in_progress = False
            await self._send({"type": "response.cancel"})

    async def _handle_event(self, event):
        kind = event.get("type")
        if kind == "session.updated":
            self._connected_event.set()
        elif kind == "response.created":
            self._active_response = event.get("response", {}).get("id")
            self._response_in_progress = True
            self.response_started_at[self._active_response] = time.monotonic()
            self._drop_until_response = False
        elif kind == "input_audio_buffer.speech_started":
            self.metrics["speech_starts"] += 1
            if self._response_in_progress or (self._turn_task and not self._turn_task.done()):
                await self.interrupt()
        elif kind in {"response.audio.delta", "response.output_audio.delta"}:
            response_id = event.get("response_id") or self._active_response
            if self._drop_until_response or response_id in self._suppressed:
                self.metrics["discarded_audio_deltas"] += 1
                return
            audio = base64.b64decode(event.get("delta", ""), validate=True)
            self.metrics["output_pcm_bytes"] += len(audio)
            self.audio_bytes_by_response[response_id] = (
                self.audio_bytes_by_response.get(response_id, 0) + len(audio))
            self._output.submit(lambda: self._on_audio(audio))
            await asyncio.sleep(0)
        elif kind in {"response.audio_transcript.done", "response.output_audio_transcript.done",
                      "conversation.item.input_audio_transcription.completed"}:
            if self._on_transcript:
                await self._on_transcript(event)
        elif kind == "response.done":
            response_id = event.get("response", {}).get("id") or self._active_response
            if response_id != self._active_response:
                return
            self._response_in_progress = False
            if response_id not in self._suppressed:
                # Order normal completion after its audio without blocking VAD.
                self._turn_task = self._output.submit(self._on_turn_complete)
                await asyncio.sleep(0)
        elif (kind == "response.function_call_arguments.done"
              and os.getenv("STACKCHAN_GBOT_VOICE_TOOLS") == "1"):
            task = asyncio.create_task(self._tool(event))
            self._tools.add(task)
            task.add_done_callback(self._tools.discard)
        elif kind == "error":
            error = event.get("error", {})
            code = error.get("code")
            param = error.get("param")
            allowed_codes = {"response_cancel_not_active", "invalid_request_error",
                             "invalid_value", "rate_limit_exceeded", "server_error",
                             "authentication_error", "session_expired"}
            allowed_params = {"response", "session", "audio", "session.voice",
                              "session.audio", "model", "tools"}
            code = code if code in allowed_codes else "other"
            param = param if param in allowed_params else "other"
            key = code + ":" + param
            errors = self.metrics["server_errors"]
            errors[key] = errors.get(key, 0) + 1
            logger.warning("Grok Bot voice server error code=%s param=%s", code, param)

    async def _tool(self, event):
        try:
            result = await self._local("/tool", {
                "agentId": self.agent_id, "callId": self.call_id,
                "name": event["name"], "input": json.loads(event.get("arguments", "{}")),
                "line": {"theyJustTalked": True, "newsOwed": False,
                         "receiptOwed": False, "alreadySpokeThisTurn": False},
            })
        except Exception:
            result = {"ok": False, "error": "Bot tool bridge failed"}
        try:
            await self._send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": event["call_id"],
                "output": json.dumps(result, ensure_ascii=False)}})
            await self._send({"type": "response.create"})
        except Exception:
            logger.warning("Grok Bot voice tool response failed (details redacted)")

    async def _receive(self):
        try:
            async for message in self._ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_event(json.loads(message.data))
                elif message.type == aiohttp.WSMsgType.ERROR:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Grok Bot voice receive failed (details redacted)")
        finally:
            # A sink callback can initiate stop() and await this receiver. Its
            # owning stop() already cancelled output, so do not await it back.
            if not self._stopped:
                await self._output.cancel()
            self.running = False
            self._connected_event.clear()
            if not self._stopped:
                await self._on_session_dead()

    async def stop(self):
        self._stopped = True
        self.running = False
        self._connected_event.clear()
        await self._output.cancel()
        tasks = [t for t in [self._task, *self._tools] if t]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._ws:
            await self._ws.close()
        if self._http:
            await self._http.close()
