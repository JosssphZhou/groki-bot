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
permits credential minting, voice session planning, and explicitly enabled native
Bot routes. Extra harness tools remain disabled by default. Audio-only probes
force an empty tool catalog and never register desktop presence.

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

Both voice providers use the same device transport, wake gate, end-conversation
callback, keepalive, and playback queue. Grok uses its own audio connection and
server VAD; it does not create a Gemini network session or require its API key.
Audio connection recovery and credential renewal are not automatic.

## Local tools and native desktop messages

The shared local executor supplies the same enabled tool declarations and device,
emotion, tracking, Mac and optional external-task actions to each provider.
Existing opt-in settings still apply. Grok normalizes dotted tool names on the
wire and dispatches them back to their original names. Provider-built-in Google
Search is not available through this adapter; queries may instead be delegated
to the configured desktop Bot, without claiming that a search already ran.

For native desktop handoff, configure **both** the gateway and helper with
`STACKCHAN_TOOL_BOT=YOUR_BOT_NAME` and `STACKCHAN_TOOL_BOT_ID=YOUR_AGENT_ID`, with
`STACKCHAN_GBOT_VOICE_BOT_ID` pointing to that same agent. This enables only the
native `send_task` route plus call-scoped presence and filtered event streams.
`ask_grokbot(task)` maps to this native route and does not use a CLI or the older
HTTP Bot bridge. `STACKCHAN_GBOT_VOICE_TOOLS=1` separately enables additional
harness tools such as conversation search; it is not needed for desktop handoff.

The helper filters current-call task outcomes and new owner-authored desktop
messages. The audio bridge forwards them with the client's native
`async_update.add` envelopes (`work_landed` or `user_message`). It does not add a
competing `response.create`. Tool results arriving during an active response
wait for that response to finish. Task result announcements do not grant
permission to dispatch another desktop task: a new real user input is required.
Duplicate model call IDs are ignored. Ordinary interruption stops speech while
accepted desktop work continues. Stopping the bridge closes local listeners and
drops late results; it cannot retract a task already delivered to the desktop.

Applications can inject `voice_tool_executor_factory` when constructing the
proxy. It receives device, wake, tracking, end-conversation and announcement
callbacks and returns a shared executor. Private extensions can therefore remain
outside this repository. No private phone implementation is included.

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
The subsequent shared-tool/native-channel integration was checked offline with
fake tools and a loopback SSE server (283 Python tests and seven Node helper
tests passed, with Ruff): local dispatch, normalized schemas,
call-scoped native envelopes, typed-message deduplication, interruption,
late-result rejection, and provider lifecycle hooks. The helper's native
connection and transcript subscription were separately checked at handshake
level. This does **not** establish real desktop task delivery, result narration,
or feature parity on a deployed robot; those require an explicitly authorized
end-to-end run of this newer integration. See
[latency methods and measurements](VOICE_LATENCY_BENCHMARK.md).

### Current capability evidence

| Capability | Implementation and evidence | Still requires deployment validation |
| --- | --- | --- |
| Login-state audio and interruption | Earlier real cloud audio; current queue and proxy regressions | Acoustic barge-in on the selected firmware |
| Wake, idle, end, injected speech | Shared proxy lifecycle and offline callback tests | Current robot wake and touch experience |
| Local tool catalog and dispatch | Shared executor, schema/alias checks, dispatch and concurrency tests | Each enabled physical or desktop action |
| Native desktop task handoff | `send_task` and `ask_grokbot` alias; offline dispatch/dedup tests | Actual task acceptance and completion |
| Desktop replies and typed messages | Filtered helper streams, native envelopes and loopback lifecycle tests; upstream handshake only | Actual account message delivery and narration |
| Private extensions | Injected executor factory; no private source in this repository | Host-specific extension tests and deployment |

No recordings, credentials, account identifiers or private device configuration
are included. Gateway licensing remains MIT; `gateway/LICENSE` is unchanged.
