[English](build.md) | [中文](build.zh-CN.md) | [日本語](build.ja.md)

[Path 1: firmware only](voice-backends.md#path-1-firmware-only)

# Build from source

You only need this if you want to change the firmware. To just use the robot, flash a release with the web flasher (path 1 above).

Docker is the recommended way to build; you do not need ESP-IDF on your machine. Start Docker Desktop (or the Docker daemon) first.

### Docker (recommended)

The image is `espressif/idf:v5.5.4`. The first pull is about 14 GB. The official image has no Node; the Makefile installs Node.js 18+ inside the container. Output goes to `build-cores3/`.

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # one-line patch for M5Unified
make build-docker BOARD=cores3
```

`BOARD=` can be `stopwatch`. In this release `cores3` and `stopwatch` are verified to build. `atoms3r` and `atoms3` have not been rebuilt yet: their earlier build failure (face animation assets overflowing the 1 MB storage partition) no longer applies because those assets were removed; see [known issues, item 4](known_issues.md). The Docker path must be given the same `BOARD` so the matching sdkconfig defaults are loaded; output goes to `build-<board>/`.

Flashing does not run inside Docker. To write the firmware you just built, run on your machine (replace the port with yours, see [Download mode](troubleshooting.md#download-mode)):

```sh
make flash BOARD=cores3 PORT=/dev/cu.usbmodem1101     # macOS example
make monitor BOARD=cores3 PORT=/dev/cu.usbmodem1101
```

`make flash` needs ESP-IDF installed on your machine (it loads the IDF environment and then calls `idf.py flash`). Without a local IDF, use the web flasher for a published release: <https://josssphzhou.github.io/groki-bot/>. That page reads GitHub Releases; it cannot pick files from `build-cores3/`.

### Local ESP-IDF

Environment: ESP-IDF 5.5 (verified on 5.5.4). Install it following the Espressif documentation, run `./install.sh esp32s3` in the IDF source directory, then `source export.sh`. The Makefile default is `IDF_PATH=$(HOME)/esp-idf/5.5.4`. Node.js 18+ must be on the build machine's `PATH` (CMake looks for `node` at configure time to compile `.avdsl` to bytecode; the compiler is in `tools/avatar_dsl/`). If the system `python3` is 3.14, IDF 5.5 looks for a matching virtual environment and `make` fails immediately when it is missing.

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # one-line patch for M5Unified
make set-target BOARD=cores3                 # first time only (each BOARD has its own build directory)
make build     BOARD=cores3
make flash     BOARD=cores3 PORT=/dev/cu.usbmodem1101    # Linux: /dev/ttyACM0
make monitor   BOARD=cores3 PORT=/dev/cu.usbmodem1101
```

Local `make build BOARD=<slug>` output goes to `build-<board>/`.

If CMake reports `Could not find NODE_EXECUTABLE`, the current environment (including the Docker image) has no `node` on `PATH`. No source file is missing.

`tools/apply-m5-patches.sh` only fixes the uninitialized `buf` in upstream M5Unified `RTC_PowerHub_Class::setAlarmIRQ`, which otherwise trips GCC 14 `-Werror=maybe-uninitialized`.

OpenAI and Gemini API keys are not compiled into the firmware; they are written to NVS at runtime through the BLE or Wi-Fi settings page. A compile-time default can be supplied in `sdkconfig.defaults.local`, which is gitignored.

## Web flasher and settings pages

Flash a published release from the browser (Chrome / Edge):

- **Flash**: <https://josssphzhou.github.io/groki-bot/>
- **BLE settings**: <https://josssphzhou.github.io/groki-bot/settings.html> (Web Bluetooth, desktop Chrome / Edge only)
- **Wi-Fi settings**: once the device is on Wi-Fi, open `http://stackchan-XXXXXX.local/` (mDNS)
- **iOS / SoftAP**: in AP mode, scan the Wi-Fi QR code on the LCD with the iPhone camera and the captive portal opens the settings page. On CoreS3 / StopWatch, tap the top-right corner of the screen to open the device UI and choose AP mode on the control page. On AtomS3R / AtomS3, a short press on BtnA opens the status overlay and a long press cycles `operation_mode`.

After a `vX.Y.Z` tag is pushed, CI (when GitHub Actions is enabled) builds the four boards and attaches them to a Release; the Pages site then updates. The web flasher source is `docs/index.html` and the BLE settings page source is `tools/settings.html`.
