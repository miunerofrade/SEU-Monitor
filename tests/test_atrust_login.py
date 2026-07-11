"""测试 aTrust 登录脚本的 URL 逻辑。"""

import os


class TestLoginUrlSettings:
    def test_loads_login_url_from_env(self, monkeypatch):
        """ATRUST_LOGIN_URL 应被正确读取。"""
        monkeypatch.setenv("ATRUST_LOGIN_URL", "https://vpn.seu.edu.cn")
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")
        monkeypatch.setenv("ATRUST_LOGIN_BACKEND", "container_cdp")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_url == "https://vpn.seu.edu.cn"
        assert s.atrust_login_backend == "container_cdp"

    def test_default_login_url(self, monkeypatch):
        """未设置时使用默认值。"""
        monkeypatch.delenv("ATRUST_LOGIN_URL", raising=False)
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_url == "https://vpn.seu.edu.cn"

    def test_vpn_check_url_is_different(self, monkeypatch):
        """VPN_CHECK_URL 不应等于 ATRUST_LOGIN_URL。"""
        monkeypatch.setenv("ATRUST_LOGIN_URL", "https://vpn.seu.edu.cn")
        monkeypatch.setenv("VPN_CHECK_URL", "https://cvs.seu.edu.cn")
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import _load_settings
        s = _load_settings()
        assert s.atrust_login_url != s.vpn_check_url


class TestLoginReturnCodes:
    def test_missing_credentials_returns_2(self, monkeypatch):
        """缺少 ATRUST_USERNAME/ATRUST_PASSWORD 应返回 2。"""
        monkeypatch.delenv("ATRUST_USERNAME", raising=False)
        monkeypatch.delenv("ATRUST_PASSWORD", raising=False)

        from scripts.atrust_login import do_login
        assert do_login() == 2

    def test_no_check_url_returns_2(self, monkeypatch):
        """VPN_CHECK_URL 未配置应返回 2。"""
        monkeypatch.delenv("VPN_CHECK_URL", raising=False)
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")

        from scripts.atrust_login import do_login
        assert do_login() == 2

    def test_healthy_vpn_no_login(self, monkeypatch):
        """healthcheck 成功时直接返回 0，不尝试登录。"""
        monkeypatch.setenv("ATRUST_USERNAME", "u")
        monkeypatch.setenv("ATRUST_PASSWORD", "p")
        monkeypatch.setenv("VPN_CHECK_URL", "https://cvs.seu.edu.cn")
        monkeypatch.setattr(
            "scripts.atrust_login.check_vpn_verbose",
            lambda **kw: (True, "OK"),
        )

        from scripts.atrust_login import do_login
        assert do_login() == 0
