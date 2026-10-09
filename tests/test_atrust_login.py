"""Native VPN regression tests; Docker/CDP behavior has been retired."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from seu_monitor.core import vpn

CALLBACK = (
    "https://vpn.seu.edu.cn/passport/v1/auth/cas?sfDomain=CAS-auth&ticket=one-use"
)


def test_callback_validates_and_removes_default_port():
    assert vpn.validate_callback(CALLBACK.replace(".cn/", ".cn:443/")) == CALLBACK


@pytest.mark.parametrize(
    "url",
    [
        CALLBACK.replace("https:", "http:"),
        CALLBACK.replace("vpn.seu", "evil.seu"),
        CALLBACK.replace("CAS-auth", "other"),
        CALLBACK.replace("ticket=one-use", "ticket="),
        CALLBACK.replace(".cn/", ".cn:444/"),
    ],
)
def test_callback_rejects_wrong_origin_or_ticket(url):
    with pytest.raises(ValueError):
        vpn.validate_callback(url)


def test_environment_hides_secrets_and_avoids_proxy_recursion(monkeypatch):
    for key in (
        "VPN_PASSWORD",
        "ATRUST_PASSWORD",
        "FEISHU_WEBHOOK",
        "HTTP_PROXY",
        "ZJU_CONNECT_PASSWORD",
    ):
        monkeypatch.setenv(key, "secret")
    env = vpn.core_environment()
    assert "VPN_PASSWORD" not in env and "HTTP_PROXY" not in env
    assert "tlsmlkem=0" in env["GODEBUG"]
    assert "secret" not in " ".join(vpn.core_command(Path("core"), 8888, Path("state")))


def test_installer_verified_download_then_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("VPN_BINARY", raising=False)
    monkeypatch.setattr(vpn.platform, "system", lambda: "Linux")
    monkeypatch.setattr(vpn.platform, "machine", lambda: "x86_64")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("zju-connect", b"binary")
    archive = buffer.getvalue()
    manifest = {
        "assets": [
            {
                "name": "zju-connect-linux-amd64.zip",
                "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
                "browser_download_url": "https://example.com/core.zip",
                "url": "https://api.github.com/repos/Mythologyli/zju-connect/releases/assets/1",
            }
        ]
    }
    opener = Mock()
    opener.open.side_effect = [
        io.BytesIO(json.dumps(manifest).encode()),
        io.BytesIO(archive),
    ]
    monkeypatch.setattr(vpn, "build_opener", lambda *args: opener)
    binary = vpn.install_core()
    assert binary.read_bytes() == b"binary"
    assert (binary.parent / "LICENSE").is_file()
    assert vpn.install_core() == binary
    assert opener.open.call_count == 2
    request = opener.open.call_args_list[1].args[0]
    assert request.get_header("Accept") == "application/octet-stream"
    assert request.full_url.endswith("?download=1")


def test_installer_rejects_bad_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("VPN_BINARY", raising=False)
    monkeypatch.setattr(vpn.platform, "system", lambda: "Linux")
    monkeypatch.setattr(vpn.platform, "machine", lambda: "x86_64")
    opener = Mock()
    manifest = {
        "assets": [
            {
                "name": "zju-connect-linux-amd64.zip",
                "digest": "sha256:wrong",
                "browser_download_url": "https://example.com/core.zip",
            }
        ]
    }
    opener.open.side_effect = [
        io.BytesIO(json.dumps(manifest).encode()),
        io.BytesIO(b"bad"),
    ]
    monkeypatch.setattr(vpn, "build_opener", lambda *args: opener)
    with pytest.raises(RuntimeError, match="校验失败"):
        vpn.install_core()
    assert not (tmp_path / "bin/zju-connect").exists()


def test_service_consumes_callback_and_cleans_up_without_logging_ticket(
    tmp_path, monkeypatch, capsys
):
    import threading
    from unittest.mock import MagicMock

    monkeypatch.setenv("VPN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(vpn, "install_core", lambda: tmp_path / "core")
    monkeypatch.setattr(vpn.socket, "socket", MagicMock())
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"code":0}')
    monkeypatch.setattr(vpn, "build_opener", lambda *a: opener)
    process = Mock()
    process.stdout = io.StringIO(
        "Visit https://vpn.seu.edu.cn/auth to login\nHTTP server listening\n"
    )
    process.poll.return_value = None
    monkeypatch.setattr(vpn.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(vpn, "login_cas", lambda *a: CALLBACK)
    monkeypatch.setattr(
        "seu_monitor.core.healthcheck.check_vpn_verbose", lambda *a: (True, "OK")
    )
    assert vpn.run_service() == 1  # EOF is a failed core, supervised by systemd.
    process.stdin.write.assert_called_once_with(CALLBACK + "\n")
    process.terminate.assert_called_once()
    output = capsys.readouterr().out
    assert "one-use" not in output
    assert "VPN 已连接" in output


@pytest.mark.parametrize('checks', [
    [True, False, False, False],
    [True, False, False, True, False, False, False],
])
def test_service_requires_three_consecutive_probe_failures(tmp_path, monkeypatch, capsys, checks):
    from unittest.mock import MagicMock
    monkeypatch.setenv('VPN_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(vpn, 'install_core', lambda: tmp_path/'core')
    monkeypatch.setattr(vpn.socket, 'socket', MagicMock())
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"code":0}')
    monkeypatch.setattr(vpn, 'build_opener', lambda *a: opener)
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(vpn.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(vpn.threading, 'Thread', MagicMock())
    clock = [0]
    monkeypatch.setattr(vpn.time, 'monotonic', lambda: clock[0])
    def output(timeout):
        clock[0] += 900
        if clock[0] == 900: return 'HTTP server listening'
        raise vpn.queue.Empty()
    queued = Mock()
    queued.get.side_effect = output
    monkeypatch.setattr(vpn.queue, 'Queue', lambda: queued)
    probe = Mock(side_effect=[(ok, 'probe') for ok in checks])
    monkeypatch.setattr('seu_monitor.core.healthcheck.check_vpn_verbose', probe)
    assert vpn.run_service() == 1
    assert probe.call_count == len(checks)
    process.terminate.assert_called_once()
    log = capsys.readouterr().out
    assert '连续 3 次检查失败' in log
    if len(checks) > 4: assert '检查恢复正常' in log
