"""User systemd units own the monitor and its independent VPN process."""

from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

UNITS = {"monitor": "seu-monitor.service", "vpn": "seu-vpn.service"}


def command(*arguments, check=True):
    if not shutil.which("systemctl"):
        raise RuntimeError("启动/停止需要 Linux systemd；当前系统可使用 doctor 检查")
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            check=check,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            'systemd 用户服务操作失败；请检查用户会话，服务器首次运行 sudo loginctl enable-linger "$USER"，再查看 journalctl --user'
        ) from error


def quote(value):
    return (
        '"'
        + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        + '"'
    )


def install(directory):
    command("show-environment")  # Check the user manager before writing units.
    target = Path.home() / ".config/systemd/user"
    target.mkdir(parents=True, exist_ok=True)
    for name, unit in UNITS.items():
        arguments = [
            sys.executable,
            "-m",
            "seu_monitor.cli",
            "--data-dir",
            str(directory),
            "--worker",
            name,
        ]
        target.joinpath(unit).write_text(
            f"[Unit]\nDescription=SEU-Monitor {name}\nStartLimitIntervalSec=600\nStartLimitBurst=5\n\n"
            f"[Service]\nType=simple\nExecStart={' '.join(map(quote, arguments))}\n"
            "Restart=on-failure\nRestartSec=15\nRestartPreventExitStatus=3\n"
            "TimeoutStopSec=45\nUMask=0077\nNoNewPrivileges=true\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
    command("daemon-reload")


def start(directory, name, restart=False):
    install(directory)
    command("enable", UNITS[name])
    command("reset-failed", UNITS[name], check=False)
    command("restart" if restart else "start", UNITS[name])


def stop():
    for unit in UNITS.values():
        command("disable", "--now", unit)


def status():
    for name, unit in UNITS.items():
        result = command("is-active", unit, check=False)
        print(f"{name}: {result.stdout.strip() or '未安装'}")
