[English](troubleshooting.md) | [中文](troubleshooting.zh-CN.md) | [日本語](troubleshooting.ja.md)

# 下载模式

CoreS3：按住机身底部的 RST 键，直到绿灯亮起再松开。板子随后作为串口出现，刷机页或 `idf.py flash` 就能连上。

串口名称：

| 系统 | 串口 |
|---|---|
| macOS | `/dev/cu.usbmodem*`（运行 `ls /dev/cu.usbmodem*` 查看） |
| Linux | `/dev/ttyACM0` |
| Windows | `COMx`（在设备管理器里查看） |

Grok 脸编在固件里，刷完固件就能显示，不需要另外写入资源。

[Known issues](known_issues.md)
