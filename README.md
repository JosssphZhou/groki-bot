English | [中文](README.zh-CN.md) | [日本語](README.ja.md)

![Groki Bot pixel-art banner: a desktop robot shaped like a retro CRT computer, with a round white face on the screen](docs/images/banner.png)
<!-- demo-video
     Demo video to be added. After publishing, place the embed code (iframe or video) below this comment block.
-->

# Groki Bot

Groki Bot is an open-source desktop robot (M5Stack CoreS3 with a Stack-chan base) that you can talk to through the Grok Bot app, with firmware, a computer-side gateway and a Grok Bot link in one repository.

## Features

- Native voice (macOS): talk directly with an agent in the Grok Bot app, using the app's existing login on your Mac, with no separate xAI voice API key.
- Hands-free wake word: wake up your robot at any time by saying "Hi Grok", powered by lightweight local voice detection in the desktop gateway.
- Dynamic face tracking: keep the robot facing you as you move, using your Mac camera for face detection and real-time dual-axis servo steering.
- Expressive eye animations: an on-device expression engine shows 15 expressions with blinking.
- One-click web flashing and OTA: deploy without setting up a local toolchain, flashing firmware directly from Chrome or Edge and updating over Wi-Fi via on-device OTA.

## Quick Start: Talk via Grok Bot Native Voice

1. Flash the firmware and connect to Wi-Fi: open the web flasher in desktop Chrome or Edge to write the firmware, then configure Wi-Fi on the setup page.
2. Install the gateway on your Mac: clone this repository and sync the dependencies in the `gateway/` directory.
   ```sh
   cd gateway && uv sync --all-extras
   ```
3. Switch to Grok voice: Gemini is the default backend, so set `STACKCHAN_VOICE_BACKEND=gbot` and your agent name and ID in `gateway/.env`, keep the Grok Bot desktop app signed in, run the login helper in a second terminal, start the gateway, and point the robot at the gateway in the BLE settings page.
4. Start talking by saying "Hi Grok": for detailed environment variables, login bridge commands, and troubleshooting, see [Configure Grok Bot native voice](docs/quick-start.md#configure-grok-bot-native-voice).

For an agent named `assistant`, set `STACKCHAN_TOOL_BOT=assistant` in the gateway's `.env` and follow the gateway setup for the remaining settings.

## Documentation

- [Quick Start and Configuration](docs/quick-start.md)
- [Other Voice Backends](docs/voice-backends.md)
- [Features](docs/features.md)
- [Hardware](docs/hardware.md)
- [Build from Source](docs/build.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md)
- [Repository Structure](docs/repository.md)
- [Gateway](gateway/README.md)

Eye-ring outline data is for non-commercial use only (from aora-bot Emotion Ball under the Community License), while all other source code is licensed under the Boost Software License 1.0: see the [Licenses Page](docs/licenses.html) for details.

## License

[BSL-1.0](LICENSE) | [Gateway (MIT)](gateway/LICENSE) | [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | [docs/licenses.html](docs/licenses.html) | [repository](docs/repository.md)

## Acknowledgements

Groki Bot started from Kenta IDA's [stackchan-idf](https://github.com/ciniml/stackchan-idf) (BSL-1.0), and the gateway from kisaragi-mochi's [stackchan-mcp](https://github.com/kisaragi-mochi/stackchan-mcp) (MIT). Thanks to them and to the Stack-chan community.
