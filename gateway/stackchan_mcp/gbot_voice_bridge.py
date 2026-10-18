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
import re
import struct
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from .voice_output_queue import VoiceOutputQueue
from .gbot_native_channel import NativeVoiceChannel

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
            output.append(
                round(
                    (
                        self.samples[index] * (3 - fraction)
                        + self.samples[index + 1] * fraction
                    )
                    / 3
                )
            )
            self.phase += 2
        consumed = self.phase // 3
        self.samples = self.samples[consumed:]
        self.phase -= consumed * 3
        return struct.pack(f"<{len(output)}h", *output)


def validate_bridge_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Grok Bot login bridge must use loopback HTTP")
    return url.rstrip("/")


def validate_voice_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != "api.x.ai"
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
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
    def __init__(
        self,
        *,
        on_audio,
        on_turn_complete,
        on_interrupt,
        on_session_dead,
        bridge_url=None,
        agent_id=None,
        on_transcript=None,
        esp32=None,
        api_key=None,
        response_modality=None,
        on_text=None,
        on_end_conversation=None,
        usb_transport=None,
        on_head_command=None,
        wake_gate_state_getter=None,
        set_face_tracking=None,
        debug_status=None,
        tool_executor=None,
        tool_executor_factory=None,
        tool_extensions=(),
        local_tools=True,
        conversation_idle_timeout_s=8.0,
    ):
        self.url = validate_bridge_url(
            bridge_url
            or os.getenv("STACKCHAN_GBOT_VOICE_URL", "http://127.0.0.1:18771")
        )
        self.agent_id = (
            agent_id
            or os.getenv("STACKCHAN_GBOT_VOICE_BOT_ID")
            or os.getenv("STACKCHAN_TOOL_BOT_ID", "")
        )
        self.language = os.getenv("STACKCHAN_GBOT_VOICE_LANGUAGE", "zh")
        self.call_id = str(uuid.uuid4())
        self._on_transcript = on_transcript
        self._on_text = on_text
        self._on_end_conversation = on_end_conversation
        self._executor = tool_executor
        self._executor_factory = tool_executor_factory
        self._executor_options = dict(
            esp32=esp32,
            usb_transport=usb_transport,
            on_head_command=on_head_command,
            wake_gate_state_getter=wake_gate_state_getter,
            set_face_tracking=set_face_tracking,
            on_end_conversation=on_end_conversation,
            debug_status=debug_status,
            extensions=tool_extensions,
            announce_result=self._announce_result,
            cancel_idle=self._cancel_idle,
        )
        self._local_tools_enabled = local_tools
        self._local_tools = {}
        self._remote_tools = {}
        self._tool_calls_seen = set()
        self._native = None
        self._desktop_enabled = False
        self._user_epoch = 0
        self._desktop_epoch = 0
        self._user_speaking = False
        self._pending_response = False
        self._response_requested = False
        self._pending_updates = []
        self._idle_task = None
        self._conversation_idle_timeout_s = conversation_idle_timeout_s
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
        self.metrics = {
            "input_pcm_bytes": 0,
            "output_pcm_bytes": 0,
            "interruptions": 0,
            "discarded_audio_deltas": 0,
            "speech_starts": 0,
            "server_errors": {},
        }

    def _configure_tools(self, session):
        # Catalog comes from the shared executor; provider-specific wire aliases
        # preserve dotted names without broadening the enabled tool set.
        if self._local_tools_enabled and self._executor is None:
            from .voice_tools import VoiceToolExecutor

            self._executor = (self._executor_factory or VoiceToolExecutor)(
                **self._executor_options
            )
        target = os.getenv("STACKCHAN_TOOL_BOT_ID", "")
        self._desktop_enabled = bool(
            self._local_tools_enabled
            and os.getenv("STACKCHAN_TOOL_BOT", "").strip()
            and target
            and target == self.agent_id
        )
        declarations = []
        self._local_tools.clear()
        self._remote_tools.clear()
        used = set()
        if not self._local_tools_enabled:
            return declarations
        if self._executor and self._local_tools_enabled:
            for declaration in self._executor.declarations():
                original = declaration["name"]
                if original == "ask_grokbot" and not self._desktop_enabled:
                    continue
                alias = re.sub(r"[^a-zA-Z0-9_-]", "_", original)
                if alias in used:
                    raise ValueError("voice tool alias collision")
                used.add(alias)
                self._local_tools[alias] = original
                declarations.append({**declaration, "name": alias, "type": "function"})
        extra = os.getenv("STACKCHAN_GBOT_VOICE_TOOLS") == "1"
        for declaration in session.get("tools", []):
            name = declaration.get("name")
            if name == "send_task":
                enabled = self._desktop_enabled
            elif name == "end_the_call":
                enabled = self._on_end_conversation is not None
            else:
                enabled = extra and self._local_tools_enabled
            if not enabled:
                continue
            if not isinstance(name, str) or name in used:
                raise ValueError("voice tool name collision")
            used.add(name)
            self._remote_tools[name] = declaration
            declarations.append(declaration)
        return declarations

    def _cancel_idle(self):
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None

    async def _finish_turn(self):
        await self._on_turn_complete()
        self._cancel_idle()
        if (
            self._on_end_conversation
            and not self._stopped
            and self._conversation_idle_timeout_s > 0
        ):

            async def idle():
                await asyncio.sleep(self._conversation_idle_timeout_s)
                if (
                    not self._stopped
                    and not self._response_in_progress
                    and not self._user_speaking
                ):
                    await self._on_end_conversation()

            self._idle_task = asyncio.create_task(idle())

    async def _flush_pending(self):
        if (
            self._stopped
            or self._response_in_progress
            or self._response_requested
            or self._user_speaking
        ):
            return
        if self._pending_updates:
            event = self._pending_updates.pop(0)
            self._response_requested = True
            await self._send(event)  # Native async_update starts its own response.
        elif self._pending_response:
            self._pending_response = False
            self._response_requested = True
            await self._send({"type": "response.create"})

    async def _request_response(self):
        self._pending_response = True
        await self._flush_pending()

    async def _native_update(self, event, is_user):
        if self._stopped or not self.running:
            return
        if is_user:
            self._user_epoch += 1
            self._cancel_idle()
            if self._executor:
                text = event["async_update"]["payload"][0]["message"]["content"]
                await self._executor.observe_user_input(text, finished=True)
        self._pending_updates.append(event)
        await self._flush_pending()

    async def _announce_result(self, text):
        # Shared executor already labels this as untrusted result data.
        if self._stopped:
            return False
        await self._native_update(
            {
                "type": "async_update.add",
                "event_id": "local-result-" + str(uuid.uuid4()),
                "async_update": {
                    "topic": "work_landed",
                    "payload": [
                        {
                            "message": {"type": "text", "content": text},
                            "timestampMs": int(time.time() * 1000),
                        }
                    ],
                },
            },
            False,
        )
        return True

    @property
    def current_turn_emotion_face(self):
        return getattr(self._executor, "current_turn_emotion_face", None)

    def clear_turn_emotion_face(self):
        if self._executor:
            self._executor.clear_turn_emotion_face()

    async def send_keepalive_audio(self, pcm):
        await self._send_pcm(pcm)

    async def send_activity_start(self):
        self._cancel_idle()

    async def send_activity_end(self):
        await self.send_audio_stream_end()

    async def clear_conversation_context(self):
        # A new call owns fresh context; the realtime API has no clear-all verb.
        self._pending_response = False
        self._cancel_idle()

    async def inject_user_text(self, text):
        # Programmatic speech/notices do not grant desktop dispatch authority.
        if self._stopped:
            return False
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._request_response()
        return True

    async def _local(self, path, payload=None):
        assert self._http is not None
        method = "GET" if payload is None else "POST"
        async with self._http.request(
            method, self.url + path, json=payload, allow_redirects=False
        ) as response:
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
        self._http = aiohttp.ClientSession(
            trust_env=False,
            trace_configs=[trace],
            timeout=aiohttp.ClientTimeout(total=30),
        )
        try:
            # Independent requests; keep failure cleanup compatible with Python 3.10.
            minted, session = await gather_with_cancel(
                self._local("/credential"),
                self._local(
                    "/session",
                    {
                        "agentId": self.agent_id,
                        "agentName": "Groki",
                        "spokenLanguage": self.language,
                    },
                ),
            )
            credential = minted["credential"]
            url = voice_url_with_model(credential["websocketUrl"], credential["model"])
            if float(credential["expiresAtUnixSeconds"]) <= time.time() + 5:
                raise RuntimeError("Grok Bot temporary credential expired")
            self._ws = await self._http.ws_connect(
                url,
                protocols=["xai-client-secret." + credential["clientSecret"]],
                heartbeat=20,
                max_msg_size=8 * 1024 * 1024,
            )
            del credential
            declarations = self._configure_tools(session)
            config = {
                "instructions": session.get("instructions", "请用中文简短自然地对话。")
                + (
                    "\n\n" + self._executor.instructions(model_search=False)
                    if self._executor and self._local_tools_enabled
                    else ""
                ),
                "resumption": {"enabled": True},
                "turn_detection": {"type": "server_vad"},
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {
                            "model": "grok-transcribe",
                            "language_hint": self.language,
                        },
                    },
                    "output": {"format": {"type": "audio/pcm", "rate": 24000}},
                },
                "tools": declarations,
            }
            if self.language == "auto":
                config["audio"]["input"]["transcription"].pop("language_hint")
            if os.getenv("STACKCHAN_GBOT_VOICE_NAME"):
                config["voice"] = os.environ["STACKCHAN_GBOT_VOICE_NAME"]
            await self._send({"type": "session.update", "session": config})
            self.running = True
            self._task = asyncio.create_task(self._receive())
            if self._desktop_enabled:
                self._native = NativeVoiceChannel(
                    self._http,
                    self.url,
                    self.agent_id,
                    self.call_id,
                    self._native_update,
                )
                await self._native.start()
        except asyncio.CancelledError:
            await self.stop()
            raise
        except Exception:
            await self.stop()
            raise RuntimeError(
                "Grok Bot voice startup failed (details redacted)"
            ) from None

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
        self._cancel_idle()
        await self._send_pcm(pcm)

    async def _send_pcm(self, pcm):
        audio = self._resampler.convert(pcm)
        if audio:
            await self._send(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("ascii"),
                }
            )
            self.metrics["input_pcm_bytes"] += len(audio)

    async def send_audio_stream_end(self):
        # Server VAD commits completed speech. Explicit commits of empty buffers fail.
        return None

    async def send_text(self, text):
        self._cancel_idle()
        self._user_epoch += 1
        if self._executor:
            await self._executor.observe_user_input(text, finished=True)
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._request_response()

    async def interrupt(self):
        self._cancel_idle()
        self._pending_response = False
        active = self._response_in_progress or bool(
            self._turn_task and not self._turn_task.done()
        )
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
            self._response_requested = False
            await self._send({"type": "response.cancel"})

    async def _handle_event(self, event):
        kind = event.get("type")
        if kind == "session.updated":
            self._connected_event.set()
        elif kind == "response.created":
            self._active_response = event.get("response", {}).get("id")
            self._response_in_progress = True
            self._response_requested = False
            self.response_started_at[self._active_response] = time.monotonic()
            self._drop_until_response = False
        elif kind == "input_audio_buffer.speech_started":
            self.metrics["speech_starts"] += 1
            self._user_epoch += 1
            self._user_speaking = True
            self._cancel_idle()
            if self._response_in_progress or (
                self._turn_task and not self._turn_task.done()
            ):
                await self.interrupt()
        elif kind == "input_audio_buffer.speech_stopped":
            self._user_speaking = False
            # Server VAD owns the response to real microphone speech.
        elif kind in {"response.audio.delta", "response.output_audio.delta"}:
            response_id = event.get("response_id") or self._active_response
            if self._drop_until_response or response_id in self._suppressed:
                self.metrics["discarded_audio_deltas"] += 1
                return
            audio = base64.b64decode(event.get("delta", ""), validate=True)
            self.metrics["output_pcm_bytes"] += len(audio)
            self.audio_bytes_by_response[response_id] = (
                self.audio_bytes_by_response.get(response_id, 0) + len(audio)
            )
            self._output.submit(lambda: self._on_audio(audio))
            await asyncio.sleep(0)
        elif kind in {
            "response.audio_transcript.done",
            "response.output_audio_transcript.done",
            "conversation.item.input_audio_transcription.completed",
        }:
            if self._on_transcript:
                await self._on_transcript(event)
            text = event.get("transcript", "")
            if kind == "conversation.item.input_audio_transcription.completed":
                if self._executor:
                    await self._executor.observe_user_input(text, finished=True)
            elif self._on_text and text:
                await self._on_text(text)
        elif kind == "response.done":
            response_id = event.get("response", {}).get("id") or self._active_response
            if response_id != self._active_response:
                return
            self._response_in_progress = False
            if response_id not in self._suppressed:
                # Order normal completion after its audio without blocking VAD.
                self._turn_task = self._output.submit(self._finish_turn)
                await asyncio.sleep(0)
            await self._flush_pending()
        elif kind == "response.function_call_arguments.done" and (
            event.get("name") in self._local_tools
            or event.get("name") in self._remote_tools
        ):
            task = asyncio.create_task(self._tool(event))
            self._tools.add(task)
            task.add_done_callback(self._tools.discard)
        elif kind == "error":
            error = event.get("error", {})
            code = error.get("code")
            param = error.get("param")
            allowed_codes = {
                "response_cancel_not_active",
                "invalid_request_error",
                "invalid_value",
                "rate_limit_exceeded",
                "server_error",
                "authentication_error",
                "session_expired",
            }
            allowed_params = {
                "response",
                "session",
                "audio",
                "session.voice",
                "session.audio",
                "model",
                "tools",
            }
            code = code if code in allowed_codes else "other"
            param = param if param in allowed_params else "other"
            key = code + ":" + param
            errors = self.metrics["server_errors"]
            errors[key] = errors.get(key, 0) + 1
            logger.warning("Grok Bot voice server error code=%s param=%s", code, param)

    async def _tool(self, event):
        call_id = event.get("call_id")
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id in self._tool_calls_seen
        ):
            return
        if len(self._tool_calls_seen) >= 4096:
            return
        self._tool_calls_seen.add(call_id)
        name = event.get("name")
        original = self._local_tools.get(name)
        try:
            arguments = json.loads(event.get("arguments", "{}"))
            if not isinstance(arguments, dict):
                raise ValueError("invalid tool arguments")
            if name in self._remote_tools:
                from .voice_tools import _valid_arguments

                if not _valid_arguments(
                    arguments, self._remote_tools[name].get("parameters", {})
                ):
                    raise ValueError("invalid remote tool arguments")
            if original == "ask_grokbot" or name == "send_task":
                field = "task" if original == "ask_grokbot" else "request"
                request = arguments.get(field)
                if (
                    not self._desktop_enabled
                    or self._user_epoch <= self._desktop_epoch
                    or not isinstance(request, str)
                    or not request.strip()
                    or len(request) > 20000
                    or set(arguments) != {field}
                ):
                    result = {
                        "ok": False,
                        "error": "desktop task requires a new explicit user request",
                    }
                else:
                    self._desktop_epoch = self._user_epoch
                    result = await self._local(
                        "/tool",
                        {
                            "agentId": self.agent_id,
                            "callId": self.call_id,
                            "name": "send_task",
                            "input": {"request": request},
                            "line": {
                                "theyJustTalked": True,
                                "newsOwed": False,
                                "receiptOwed": False,
                                "alreadySpokeThisTurn": False,
                            },
                        },
                    )
            elif name == "end_the_call":
                result = {"ok": True}
                if self._on_end_conversation:
                    await self._on_end_conversation()
            elif original and self._executor:
                result = await self._executor.execute(original, arguments)
            elif name in self._remote_tools:
                result = await self._local(
                    "/tool",
                    {
                        "agentId": self.agent_id,
                        "callId": self.call_id,
                        "name": name,
                        "input": arguments,
                        "line": {
                            "theyJustTalked": True,
                            "newsOwed": False,
                            "receiptOwed": False,
                            "alreadySpokeThisTurn": False,
                        },
                    },
                )
            else:
                result = {"ok": False, "error": "voice tool not enabled"}
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {"ok": False, "error": "voice tool failed"}
        if self._stopped or not self.running:
            return
        try:
            await self._send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    },
                }
            )
            await self._request_response()
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
                if self._native:
                    await self._native.close()
                if self._executor:
                    await self._executor.close()
                self._pending_updates.clear()
                for task in self._tools:
                    task.cancel()
                await self._on_session_dead()

    async def stop(self):
        self._stopped = True
        self._cancel_idle()
        self._pending_updates.clear()
        self._pending_response = False
        self.running = False
        self._connected_event.clear()
        await self._output.cancel()
        if self._native:
            await self._native.close()
        if self._executor:
            await self._executor.close()
        current = asyncio.current_task()
        tasks = [t for t in [self._task, *self._tools] if t and t is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._ws:
            await self._ws.close()
        if self._http:
            await self._http.close()
