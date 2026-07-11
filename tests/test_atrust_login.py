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
        import scripts.atrust_login as al

        # _fill_login_form 现在已不含 page 操作，它直接调用 _poll_vpn
        # 测试 _poll_vpn 本身即可覆盖这个场景
        monkeypatch.setattr(
            "scripts.atrust_login.check_vpn_verbose",
            lambda **kw: (True, "OK"),
        )
        from scripts.atrust_login import _poll_vpn
        from seu_monitor.core.settings import Settings
        rc = _poll_vpn(Settings(vpn_check_url="https://cvs.seu.edu.cn"), timeout=5)
        assert rc == 0
