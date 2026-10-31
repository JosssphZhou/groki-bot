#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Install the existing-login voice bridge as a macOS user LaunchAgent."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

LABEL = "com.stackchan.gbot-voice"


def render_agent(gateway, home, node, agent_id, desktop=False, tools=False, *, proxy_url=None):
    gateway, home, node = map(Path, (gateway, home, node))
    if not agent_id.strip() or len(agent_id) > 200 or any(ord(c) < 32 for c in agent_id):
        raise ValueError("A nonempty agent ID of at most 200 characters is required")
    # Explicit allowlist: never persist the parent's environment or account secrets.
    env = {
        "PATH": f"{node.parent}:/usr/bin:/bin",
        "STACKCHAN_GBOT_VOICE_BOT_ID": agent_id,
        "STACKCHAN_GBOT_VOICE_DESKTOP": "1" if desktop else "0",
        "STACKCHAN_GBOT_VOICE_TOOLS": "1" if tools else "0",
    }
    if proxy_url is not None:
        proxy = urlsplit(proxy_url)
        if (proxy.scheme not in ("http", "https") or not proxy.hostname
                or proxy.username is not None or proxy.password is not None
                or proxy.path not in ("", "/") or proxy.query or proxy.fragment
                or any(c.isspace() or ord(c) < 32 for c in proxy_url)):
            raise ValueError("Proxy URL must be an HTTP(S) origin without credentials")
        env.update({
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "NODE_USE_ENV_PROXY": "1",
            "NO_PROXY": "localhost,127.0.0.1,::1",
        })
    log = str(home / "Library/Logs/stackchan-gbot-voice.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [str(node), str(gateway / "scripts/gbot_voice_session_bridge.mjs")],
        "WorkingDirectory": str(gateway),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def install_agent(agent, home, *, replace=False):
    path = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    if path.exists() and not replace:
        raise FileExistsError(f"{path} already exists; use --replace to back it up and replace it")
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    loaded = subprocess.run(["launchctl", "print", target], capture_output=True).returncode == 0
    if loaded and not replace:
        raise FileExistsError("Service is already loaded; use --replace")
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S%f"))
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    if loaded:
        subprocess.run(["launchctl", "bootout", target], check=True, capture_output=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    (home / "Library/Logs").mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "wb", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            plistlib.dump(agent, stream)
        path.chmod(0o600)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True, capture_output=True)
    except Exception:
        # A failed update must not discard the previous service configuration.
        if backup:
            shutil.copy2(backup, path)
            if loaded:
                subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
        else:
            path.unlink(missing_ok=True)
        raise
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True, help="Same ID as STACKCHAN_GBOT_VOICE_BOT_ID")
    parser.add_argument("--enable-desktop", action="store_true", help="Enable native desktop communication")
    parser.add_argument("--enable-tools", action="store_true", help="Enable native tool calls")
    parser.add_argument("--proxy-url", help="HTTP(S) proxy without credentials; requires Node.js 24+")
    parser.add_argument("--replace", action="store_true", help="Back up and replace an existing service (interrupts its calls)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plist without writing files or starting services")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This installer requires macOS")
    node = shutil.which("node")
    if not node:
        parser.error("Node.js must be installed and available on PATH")
    gateway = Path(__file__).resolve().parents[1]
    try:
        agent = render_agent(gateway, Path.home(), node, args.agent_id, args.enable_desktop,
                             args.enable_tools, proxy_url=args.proxy_url)
        if args.dry_run:
            sys.stdout.buffer.write(plistlib.dumps(agent))
            return
        path = install_agent(agent, Path.home(), replace=args.replace)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")
    print(f"Installed {path}")
    print("Bridge starts at login and restarts after exit. Keep this checkout at its current path.")
    print("Check http://127.0.0.1:18771/health, then restart your gateway and verify a voice reply.")
    print("Bridge health alone does not verify upstream voice or desktop availability.")


if __name__ == "__main__":
    main()
