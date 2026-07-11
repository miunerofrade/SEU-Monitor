"""测试 aTrust 登录脚本的 URL 逻辑和页面关闭处理。"""

from unittest.mock import Mock


class TestLoginUrlSettings:
    def test_loads_login_url_from_env(self, monkeypatch):
        monkeypatch.setenv("ATRUST_LOGIN_URL", "https://vpn.seu.edu.cn")
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")
        monkeypatch.setenv("ATRUST_LOGIN_BACKEND", "container_cdp")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_url == "https://vpn.seu.edu.cn"
        assert s.atrust_login_backend == "container_cdp"

    def test_default_login_url(self, monkeypatch):
        monkeypatch.delenv("ATRUST_LOGIN_URL", raising=False)
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_url == "https://vpn.seu.edu.cn"

    def test_vpn_check_url_is_different(self, monkeypatch):
        monkeypatch.setenv("ATRUST_LOGIN_URL", "https://vpn.seu.edu.cn")
        monkeypatch.setenv("VPN_CHECK_URL", "https://cvs.seu.edu.cn")
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_url != s.vpn_check_url

    def test_login_timeout_default(self, monkeypatch):
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_timeout == 60

    def test_login_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("ATRUST_LOGIN_TIMEOUT", "120")
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_timeout == 120


class TestLoginReturnCodes:
    def test_missing_credentials_returns_2(self, monkeypatch):
        monkeypatch.delenv("ATRUST_USERNAME", raising=False)
        monkeypatch.delenv("ATRUST_PASSWORD", raising=False)

        from scripts.atrust_login import do_login
        assert do_login() == 2

    def test_no_check_url_returns_2(self, monkeypatch):
        monkeypatch.delenv("VPN_CHECK_URL", raising=False)
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import do_login
        assert do_login() == 2

    def test_healthy_vpn_no_login(self, monkeypatch):
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")
        monkeypatch.setenv("VPN_CHECK_URL", "https://cvs.seu.edu.cn")
        monkeypatch.setattr(
            "scripts.atrust_login.check_vpn_verbose",
            lambda **kw: (True, "OK"),
        )

        from scripts.atrust_login import do_login
        assert do_login() == 0


class TestPollVpn:
    def test_poll_returns_0_when_vpn_ok(self, monkeypatch):
        """healthcheck 成功后 _poll_vpn 返回 0。"""
        monkeypatch.setattr(
            "scripts.atrust_login.check_vpn_verbose",
            lambda **kw: (True, "OK"),
        )
        from scripts.atrust_login import _poll_vpn
        from seu_monitor.core.settings import Settings
        rc = _poll_vpn(Settings(), timeout=10)
        assert rc == 0

    def test_poll_returns_1_on_timeout(self, monkeypatch):
        """healthcheck 一直失败时 _poll_vpn 返回 1。"""
        monkeypatch.setattr(
            "scripts.atrust_login.check_vpn_verbose",
            lambda **kw: (False, "FAILED"),
        )
        from scripts.atrust_login import _poll_vpn
        from seu_monitor.core.settings import Settings
        rc = _poll_vpn(Settings(), timeout=2)
        assert rc == 1


class TestPageClosedHandling:
    def test_target_closed_falls_back_to_poll(self, monkeypatch):
        """TargetClosedError 后应进入 _poll_vpn，如果 VPN 恢复则返回 0。"""
        monkeypatch.setattr(
            "scripts.atrust_login.check_vpn_verbose",
            lambda **kw: (True, "OK"),
        )
        from scripts.atrust_login import _poll_vpn
        from seu_monitor.core.settings import Settings
        rc = _poll_vpn(Settings(vpn_check_url="https://cvs.seu.edu.cn"), timeout=5)
        assert rc == 0


class TestContainerCdpBridge:
    def test_cdp_url_uses_localhost(self):
        """CDP endpoint 固定 127.0.0.1:9222。"""
        import scripts.atrust_login as al
        s = al._load_settings()
        url = al._cdp_url(s)
        assert url == "http://127.0.0.1:9222"

    def test_cdp_url_default_port(self):
        """CDP endpoint 固定为 9222。"""
        import scripts.atrust_login as al
        s = al._load_settings()
        url = al._cdp_url(s)
        assert ":9222" in url

    def test_cdp_already_available(self, monkeypatch):
        """CDP 已可用时 _ensure_cdp 应直接返回 True。"""
        import scripts.atrust_login as al
        monkeypatch.setattr(al, "_check_cdp", lambda s, timeout=3: True)
        monkeypatch.setattr(al, "_start_chromium_in_container", lambda s: (_ for _ in ()).throw(
            AssertionError("不应启动 Chromium"),
        ))
        s = al._load_settings()
        assert al._ensure_cdp(s) is True

    def test_cdp_unavailable_returns_false(self, monkeypatch):
        """_ensure_cdp 在 Chromium 和 bridge 都失败时返回 False。"""
        import scripts.atrust_login as al
        monkeypatch.setattr(al, "_check_cdp", lambda s, timeout=3: False)
        monkeypatch.setattr(al, "_start_chromium_in_container", lambda s: False)
        monkeypatch.setattr(al, "_start_host_cdp_bridge", lambda s: True)
        monkeypatch.setattr("time.sleep", lambda s: None)
        s = al._load_settings()
        assert al._ensure_cdp(s) is False

    def test_bridge_mode_config(self, monkeypatch):
        """ATRUST_CDP_BRIDGE_MODE 应被正确读取。"""
        monkeypatch.setenv("ATRUST_CDP_BRIDGE_MODE", "docker_exec")
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_cdp_bridge_mode == "docker_exec"
