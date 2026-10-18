<!-- SPDX-FileCopyrightText: 2026 Joseph Zhou -->
<!-- SPDX-License-Identifier: MIT -->
# Experimental Grok Bot login voice backend

The opt-in `gbot` backend reuses a signed-in Grok Bot desktop account to obtain
an ephemeral voice credential. It does not require a separate xAI API key.
Gemini remains the default, and existing firmware does not need to be flashed.

This integration uses internal APIs observed in Grok Bot 0.71.2 on macOS. It is
version dependent; account access, voice availability and usage limits still apply.
A login session must be renewed in the desktop app if it expires.

## Local setup

Use your own authorized test agent ID. Start the loopback login helper from the
`gateway` directory in one terminal:

```sh
STACKCHAN_GBOT_VOICE_BOT_ID="YOUR_AGENT_ID" node scripts/gbot_voice_session_bridge.mjs
```

The helper reads the active account from local application storage and macOS
Keychain. It does not save credentials or change the desktop account state.
It listens on `127.0.0.1:18771`, rejects cross-origin/wrong-host requests, and
permits only credential minting, voice session planning and optional harness calls.
Harness tools are disabled by default. Do not enable them for audio-only tests.

Create two mono PCM16 WAV fixtures, at 16 or 24 kHz: one asking the assistant to
count slowly, and one saying “Stop counting. Say only: interruption successful.”
Then run a gateway-only probe in a separate terminal:

```sh
VOICE_EVIDENCE="$(mktemp -d)"
STACKCHAN_GBOT_VOICE_BOT_ID="YOUR_AGENT_ID" STACKCHAN_GBOT_VOICE_LANGUAGE=en \
  uv run --extra tts python scripts/probe_gbot_voice.py \
  --input-wav fixtures/count.wav --interrupt-wav fixtures/interrupt.wav \
  --output-dir "$VOICE_EVIDENCE/direct"
```

For the device protocol path, use `scripts/probe_gbot_voice_device.py` with the
same arguments. It sends real 16 kHz Opus through `GbotVoiceProxy`, decodes the
24 kHz Opus output in a fake device, injects an explicit device `abort`, and
checks queue clearing, `tts.stop`, late old-response rejection and a new reply.
No physical robot or full gateway process participates in these probes.

## Switching and scope

Set `STACKCHAN_VOICE_BACKEND=gbot` and `STACKCHAN_GBOT_VOICE_BOT_ID=YOUR_AGENT_ID`
for an explicitly started gateway session. Restore `STACKCHAN_VOICE_BACKEND=gemini`
to use the existing Gemini configuration. Failures do not automatically switch
providers. The normal gateway USB opt-in defaults remain unchanged.

Additional settings are `STACKCHAN_GBOT_VOICE_URL` (loopback HTTP only),
`STACKCHAN_GBOT_VOICE_LANGUAGE` (`zh` by default, `en` for the fixtures), and an
optional `STACKCHAN_GBOT_VOICE_NAME`. Temporary voice secrets are sent only to
`wss://api.x.ai`, with the minted model bound to the connection URL; redirects
are rejected. Account RPCs use a fixed allowlist and server error details are redacted.

This validation backend forwards microphone audio directly to server VAD; it
does not use the local wake gate. There is no new mapping of harness tools to
robot body controls. Reconnection and credential renewal are not automatic.

The receive loop stays independent of ordered audio playback. Interruption
cancels old output callbacks and proxy PCM/Opus queues before a new reply can play.
Firmware playback buffers may still drain after `tts.stop`, and firmware that
stops capturing its microphone while speaking cannot provide acoustic barge-in
through this gateway change alone.

## Validation boundary

An earlier implementation baseline passed real cloud voice exchanges using
synthetic English audio, plus fake-device Opus and cancellation checks. Those
results establish gateway behavior, not physical microphone/speaker performance.
The earlier local baseline also received user acceptance for physical robot
conversation and touch-triggered interruption, without acoustic timing; this
does not establish purely spoken acoustic barge-in.
The port onto this repository's current main was validated separately with
251 voice-related Python tests, three Node helper tests and Ruff; the ported
checkout was not deployed to or acoustically tested on a physical robot.
See [latency methods and measurements](VOICE_LATENCY_BENCHMARK.md).

No recordings, credentials, account identifiers or private device configuration
are included. Gateway licensing remains MIT; `gateway/LICENSE` is unchanged.
