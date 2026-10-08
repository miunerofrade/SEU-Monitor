"""One private config file; paths and school endpoints have sensible defaults."""

from __future__ import annotations
import json
import os
from pathlib import Path

DEFAULTS = {
    "account": "",
    "password": "",
    "webhook": "",
    "interval": 3600,
    "port": 8888,
}


def data_directory(value=None):
    path = (
        Path(value or os.environ.get("MONITOR_DATA_DIR", "~/.local/share/seu-monitor"))
        .expanduser()
        .resolve()
    )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def load(directory):
    path = directory / "config.json"
    config = {**DEFAULTS, **(json.loads(path.read_text()) if path.exists() else {})}
    if not isinstance(config["interval"], int) or config["interval"] < 60:
        raise ValueError("扫描间隔至少 60 秒")
    if not isinstance(config["port"], int) or not 1024 <= config["port"] <= 65535:
        raise ValueError("代理端口必须在 1024–65535 之间")
    return config


def save(directory, config):
    if not isinstance(config["interval"], int) or config["interval"] < 60:
        raise ValueError("扫描间隔至少 60 秒")
    if not isinstance(config["port"], int) or not 1024 <= config["port"] <= 65535:
        raise ValueError("代理端口必须在 1024–65535 之间")
    temporary = directory / "config.json.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    temporary.chmod(0o600)
    temporary.replace(directory / "config.json")
