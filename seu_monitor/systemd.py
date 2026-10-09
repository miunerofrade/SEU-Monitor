"""User systemd units own the monitor and its independent VPN process."""

from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path
from . import config

UNITS = {"monitor": "seu-monitor.service", "vpn": "seu-vpn.service"}
TIMER = "seu-monitor.timer"


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
        if name == "monitor":
            text = (
                "[Unit]\nDescription=SEU-Monitor single scan\n\n"
                f"[Service]\nType=oneshot\nExecStart={' '.join(map(quote, arguments))}\n"
                "Restart=no\nTimeoutStartSec=30min\nTimeoutStopSec=45\n"
                "UMask=0077\nNoNewPrivileges=true\n"
            )
        else:
            text = (
                f"[Unit]\nDescription=SEU-Monitor {name}\nStartLimitIntervalSec=600\nStartLimitBurst=5\n\n"
                f"[Service]\nType=simple\nExecStart={' '.join(map(quote, arguments))}\n"
                "Restart=on-failure\nRestartSec=15\nRestartPreventExitStatus=3\n"
                "TimeoutStopSec=45\nUMask=0077\nNoNewPrivileges=true\n\n"
                "[Install]\nWantedBy=default.target\n"
            )
        target.joinpath(unit).write_text(text, encoding="utf-8")
    interval = config.load(directory)["interval"]
    target.joinpath(TIMER).write_text(
        "[Unit]\nDescription=SEU-Monitor scheduled scans\n\n"
        f"[Timer]\nOnActiveSec=1s\nOnUnitInactiveSec={interval}s\n"
        "AccuracySec=1s\nUnit=seu-monitor.service\n\n"
        "[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    command("daemon-reload")


def start(directory, name, restart=False):
    install(directory)
    if name == "monitor":
        # Remove the former persistent worker and its boot symlink on upgrade.
        command("disable", "--now", UNITS[name])
        command("reset-failed", UNITS[name], check=False)
        command("enable", TIMER)
        command("restart" if restart else "start", TIMER)
        return
    command("enable", UNITS[name])
    command("reset-failed", UNITS[name], check=False)
    command("restart" if restart else "start", UNITS[name])


def stop():
    command("disable", "--now", TIMER)
    for unit in UNITS.values():
        command("disable", "--now", unit)


def status():
    for name, unit in {"monitor timer": TIMER, "vpn": UNITS["vpn"]}.items():
        result = command("is-active", unit, check=False)
        print(f"{name}: {result.stdout.strip() or '未安装'}")
    result = command("list-timers", TIMER, "--no-pager", check=False)
    print(result.stdout.strip())


def logs(target=None, lines=50, follow=False):
    if lines < 1:
        raise ValueError("日志条数必须大于 0")
    if not shutil.which("journalctl"):
        raise RuntimeError("查看日志需要 Linux journalctl")
    arguments = ["journalctl", "--user", "--no-pager", "-n", str(lines)]
    for unit in ([UNITS[target]] if target else UNITS.values()):
        arguments.extend(["-u", unit])
    if follow:
        arguments.append("-f")
    try:
        # Inherit terminal streams so follow mode displays output immediately.
        return subprocess.run(arguments).returncode
    except KeyboardInterrupt:
        return 0
