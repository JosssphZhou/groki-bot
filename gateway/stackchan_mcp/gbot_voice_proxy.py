# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Opt-in Grok Bot voice, retaining the established device audio transport."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .gbot_voice_bridge import GbotVoiceBridge
from .gemini_voice_proxy import GeminiVoiceProxy
from .opus_codec import StackchanOpusCodec

logger = logging.getLogger(__name__)


class GbotVoiceProxy(GeminiVoiceProxy):
    async def start(self, info, device_hello, send_to_device):
        self.info = info
        self._device_hello = device_hello
        self._send_to_device = send_to_device
        self._stopped = False
        self._reported_dead = False
        self._ready_event.clear()
        try:
            self._codec = (self.codec_factory or StackchanOpusCodec)()
            factory = self.bridge_factory or GbotVoiceBridge
            self._bridge = factory(
                on_audio=self._handle_gemini_audio,
                on_turn_complete=self.end_tts,
                on_interrupt=self._abort_in_flight_tts,
                on_session_dead=self._handle_session_dead,
            )
            await self._bridge.start()
            if not await self._bridge.wait_connected(timeout=15):
                raise RuntimeError("Grok Bot session update timeout")
        except Exception:
            logger.warning("Grok Bot voice unavailable (details redacted)")
            if self._bridge:
                await self._bridge.stop()
            self._bridge = None
            self._ready_event.set()
            return False
        self.server_hello = {"provider": "gbot_voice", "experimental": True}
        self._ready_event.set()
        logger.info("Grok Bot voice ready; microphone passthrough verification mode")
        return True

    async def _forward_gated_device_pcm(self, pcm):
        # Validation mode uses server VAD, avoiding a second local wake/VAD gate.
        if self._bridge:
            await self._bridge.send_audio(pcm)

    async def send_device_json(self, message):
        if not self.connected:
            return False
        if message.get("type") == "abort":
            await self._bridge.interrupt()
        return True

    async def speak_text(self, text: str, *, session_id: str,
                         prompt_audio_frames: list[bytes],
                         emotion: str | None = None) -> dict[str, Any]:
        result = {"ok": False, "provider": "gbot_voice", "frames_sent": 0,
                  "text": text, "emotion": emotion}
        if not self.connected:
            return {**result, "error": "Grok Bot voice is disconnected"}
        self._speak_text_active = True
        self._speak_text_frames = 0
        try:
            await self._bridge.send_text("请只读出这句话：" + text)
            deadline = asyncio.get_running_loop().time() + 30
            while asyncio.get_running_loop().time() < deadline:
                if self._speak_text_frames > 0 and not self._tts_active:
                    break
                await asyncio.sleep(0.05)
            return {**result, "ok": self._speak_text_frames > 0,
                    "frames_sent": self._speak_text_frames}
        except Exception:
            return {**result, "error": "Grok Bot voice request failed"}
        finally:
            self._speak_text_active = False
