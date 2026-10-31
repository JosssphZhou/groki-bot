# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Service installation checks; never start a real bridge or read login data."""
import importlib.util
from pathlib import Path
import plistlib
import subprocess

import pytest

spec = importlib.util.spec_from_file_location(
    "gbot_launchd", Path(__file__).parents[1] / "scripts/install_gbot_voice_launchd.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_service_restarts_and_does_not_copy_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "must-not-be-persisted")
    gateway = tmp_path / "checkout with spaces/gateway"
    node = tmp_path / "node bin/node"
    agent = installer.render_agent(gateway, tmp_path, node, "agent-1")
    encoded = plistlib.dumps(agent)
    result = plistlib.loads(encoded)
    assert b"must-not-be-persisted" not in encoded
    assert result["ProgramArguments"] == [str(node), str(gateway / "scripts/gbot_voice_session_bridge.mjs")]
    assert result["RunAtLoad"] and result["KeepAlive"]
    assert result["EnvironmentVariables"]["STACKCHAN_GBOT_VOICE_DESKTOP"] == "0"
    assert result["EnvironmentVariables"]["STACKCHAN_GBOT_VOICE_TOOLS"] == "0"


def test_cli_proxy_configuration_survives_service_install(monkeypatch, capsysbinary):
    monkeypatch.setenv("ACCESS_TOKEN", "must-not-be-persisted")
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/usr/local/bin/node")
    monkeypatch.setattr(installer.sys, "argv", [
        "install_gbot_voice_launchd.py", "--agent-id", "agent-1", "--dry-run",
        "--proxy-url", "http://127.0.0.1:3067",
    ])
    installer.main()
    encoded = capsysbinary.readouterr().out
    env = plistlib.loads(encoded)["EnvironmentVariables"]
    assert env["HTTP_PROXY"] == "http://127.0.0.1:3067"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["NO_PROXY"] == "localhost,127.0.0.1,::1"
    assert b"must-not-be-persisted" not in encoded


@pytest.mark.parametrize("agent_id", ["", "  ", "x\n", "x" * 201])
def test_invalid_agent_id_rejected(tmp_path, agent_id):
    with pytest.raises(ValueError):
        installer.render_agent(tmp_path, tmp_path, "/usr/local/bin/node", agent_id)


def test_install_registers_service_without_shell(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if args[1] == "print" else 0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    agent = installer.render_agent(tmp_path, tmp_path, "/usr/local/bin/node", "id", True, True)
    path = installer.install_agent(agent, tmp_path)
    assert plistlib.loads(path.read_bytes()) == agent
    assert path.stat().st_mode & 0o777 == 0o600
    assert calls[-1] == ["launchctl", "bootstrap", f"gui/{installer.os.getuid()}", str(path)]
    assert agent["EnvironmentVariables"]["STACKCHAN_GBOT_VOICE_DESKTOP"] == "1"
    with pytest.raises(FileExistsError):
        installer.install_agent(agent, tmp_path)


def test_failed_update_restores_existing_service(tmp_path, monkeypatch):
    path = tmp_path / "Library/LaunchAgents" / f"{installer.LABEL}.plist"
    path.parent.mkdir(parents=True)
    original = plistlib.dumps({"Label": installer.LABEL, "old": True})
    path.write_bytes(original)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "bootstrap" and sum(c[1] == "bootstrap" for c in calls) == 1:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    agent = installer.render_agent(tmp_path, tmp_path, "/usr/local/bin/node", "id")
    with pytest.raises(subprocess.CalledProcessError):
        installer.install_agent(agent, tmp_path, replace=True)
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.bak-*"))[0].read_bytes() == original
    assert [c[1] for c in calls] == ["print", "bootout", "bootstrap", "bootstrap"]
