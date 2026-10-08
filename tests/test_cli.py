import json
import subprocess
from pathlib import Path
from unittest.mock import Mock
import pytest
from seu_monitor import cli, config, systemd


def test_default_start_and_persistent_interval(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        systemd, "start", lambda directory, name, **kw: calls.append(name)
    )
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--data-dir", str(tmp_path / "data"), "--interval", "1800"]) == 0
    assert calls == ["monitor"]
    assert config.load(tmp_path / "data")["interval"] == 1800
    assert (tmp_path / "data/config.json").stat().st_mode & 0o777 == 0o600


def test_vpn_credentials_are_saved_not_in_systemd_command(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    calls = []
    monkeypatch.setattr(systemd, "command", lambda *args, **kw: calls.append(args))
    directory = tmp_path / "data"
    assert (
        cli.main(
            [
                "--data-dir",
                str(directory),
                "vpn",
                "--account",
                "123",
                "--password",
                "secret-never-in-unit",
            ]
        )
        == 0
    )
    text = (home / ".config/systemd/user/seu-vpn.service").read_text()
    assert "secret-never-in-unit" not in text and "123" not in text
    assert "--worker" in text and "RestartPreventExitStatus=3" in text
    assert ("restart", "seu-vpn.service") in calls
    assert config.load(directory)["password"] == "secret-never-in-unit"
    calls.clear()
    assert cli.main(["--data-dir", str(directory)]) == 0
    assert ("start", "seu-vpn.service") in calls
    assert ("start", "seu-monitor.service") in calls


def test_typo_rejected():
    with pytest.raises(SystemExit) as error:
        cli.parser().parse_args(["vpn", "--passowrd", "x"])
    assert error.value.code == 2


def test_invalid_interval_preserves_valid_config(tmp_path, monkeypatch):
    config.save(tmp_path, dict(config.DEFAULTS))
    start = Mock()
    monkeypatch.setattr(systemd, "start", start)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--data-dir", str(tmp_path), "--interval", "1"]) == 1
    assert config.load(tmp_path)["interval"] == 3600
    start.assert_not_called()


def test_stop_disables_both_units(monkeypatch):
    calls = []
    monkeypatch.setattr(systemd, "command", lambda *args, **kw: calls.append(args))
    systemd.stop()
    assert calls == [
        ("disable", "--now", "seu-monitor.service"),
        ("disable", "--now", "seu-vpn.service"),
    ]


def test_systemd_failure_does_not_claim_started(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["systemctl"])

    monkeypatch.setattr(systemd, "start", fail)
    assert cli.main(["--data-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "监控已启动" not in output.out


def test_unit_quotes_spaces_and_percent():
    assert systemd.quote("/a b/%dir") == '"/a b/%%dir"'
