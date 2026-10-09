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
    assert ("disable", "--now", "seu-monitor.service") in calls
    assert ("enable", "seu-monitor.timer") in calls
    assert ("start", "seu-monitor.timer") in calls


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
        ("disable", "--now", "seu-monitor.timer"),
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


@pytest.mark.parametrize('exit_code', [1, 3])
@pytest.mark.parametrize('alert_result', [True, False, RuntimeError('offline')])
def test_vpn_failure_alerts_once_and_prevents_restart(tmp_path, monkeypatch, exit_code, alert_result):
    from seu_monitor.core import vpn, notify
    values = {**config.DEFAULTS, 'account':'123', 'password':'private', 'webhook':'configured'}
    config.save(tmp_path, values)
    monkeypatch.setattr(vpn, 'run_service', lambda port: exit_code)
    send = Mock(side_effect=alert_result) if isinstance(alert_result, Exception) else Mock(return_value=alert_result)
    monkeypatch.setattr(notify.FeishuNotifier, 'send_alert', send)
    assert cli.worker('vpn', tmp_path, values) == 3
    send.assert_called_once()


@pytest.mark.parametrize('exit_code', [0, 1])
def test_vpn_no_webhook_does_not_send(tmp_path, monkeypatch, exit_code):
    from seu_monitor.core import vpn, notify
    config.save(tmp_path, dict(config.DEFAULTS))
    monkeypatch.setattr(vpn, 'run_service', lambda port: exit_code)
    send = Mock()
    monkeypatch.setattr(notify.FeishuNotifier, 'send_alert', send)
    assert cli.worker('vpn', tmp_path, config.load(tmp_path)) == (3 if exit_code else 0)
    send.assert_not_called()


@pytest.mark.parametrize('fails', [False, True])
def test_scan_worker_runs_once_then_exits(tmp_path, monkeypatch, fails):
    from seu_monitor.core import runner
    config.save(tmp_path, dict(config.DEFAULTS))
    scan = Mock(side_effect=RuntimeError('offline')) if fails else Mock(return_value=0)
    monkeypatch.setattr(runner, 'run_all', scan)
    assert cli.worker('monitor', tmp_path, config.load(tmp_path)) == (1 if fails else 0)
    scan.assert_called_once()


def test_timer_tracks_config_and_scan_service_is_not_persistent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(systemd, 'command', Mock())
    data = tmp_path/'data'
    data.mkdir()
    config.save(data, {**config.DEFAULTS, 'interval':7200})
    systemd.install(data)
    units = tmp_path/'.config/systemd/user'
    service = (units/'seu-monitor.service').read_text()
    timer = (units/'seu-monitor.timer').read_text()
    assert 'Type=oneshot' in service and 'Restart=no' in service
    assert 'WantedBy=default.target' not in service
    assert 'OnActiveSec=1s' in timer and 'OnUnitInactiveSec=7200s' in timer
    assert 'WantedBy=timers.target' in timer
    config.save(data, {**config.DEFAULTS, 'interval':1800})
    systemd.install(data)
    assert 'OnUnitInactiveSec=1800s' in (units/'seu-monitor.timer').read_text()


@pytest.mark.parametrize('arguments,target,lines,follow', [
    (['log'],None,50,False),
    (['log','vpn','-f','-n','100'],'vpn',100,True),
    (['log','monitor'],'monitor',50,False),
])
def test_log_dispatch_does_not_require_config(monkeypatch, arguments, target, lines, follow):
    logs = Mock(return_value=0)
    monkeypatch.setattr(systemd, 'logs', logs)
    monkeypatch.setattr(config, 'data_directory', Mock(side_effect=AssertionError('must not load config')))
    assert cli.main(arguments) == 0
    logs.assert_called_once_with(target,lines,follow)


def test_logs_follow_streams_selected_service_and_handles_interrupt(monkeypatch):
    monkeypatch.setattr(systemd.shutil, 'which', lambda command:'/usr/bin/'+command)
    run = Mock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(systemd.subprocess, 'run', run)
    assert systemd.logs('vpn',100,True) == 0
    run.assert_called_once_with(['journalctl','--user','--no-pager','-n','100','-u','seu-vpn.service','-f'])


def test_logs_default_includes_both_services_and_preserves_exit_code(monkeypatch):
    monkeypatch.setattr(systemd.shutil,'which',lambda command:'/usr/bin/'+command)
    run = Mock(return_value=subprocess.CompletedProcess([],1))
    monkeypatch.setattr(systemd.subprocess,'run',run)
    assert systemd.logs() == 1
    run.assert_called_once_with(['journalctl','--user','--no-pager','-n','50','-u','seu-monitor.service','-u','seu-vpn.service'])


def test_logs_rejects_invalid_count():
    with pytest.raises(ValueError, match='大于 0'):
        systemd.logs(lines=0)
