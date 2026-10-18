[English](troubleshooting.md) | [中文](troubleshooting.zh-CN.md) | [日本語](troubleshooting.ja.md)

# Download mode

On CoreS3, hold the RST button on the bottom of the unit until the green LED lights up, then release it. The board then shows up as a serial port and the web flasher or `idf.py flash` can connect.

Serial port names:

| OS | Port |
|---|---|
| macOS | `/dev/cu.usbmodem*` (run `ls /dev/cu.usbmodem*`) |
| Linux | `/dev/ttyACM0` |
| Windows | `COMx` (check Device Manager) |

The Grok face is compiled into the firmware, so there is nothing else to flash.

[Known issues](known_issues.md)
