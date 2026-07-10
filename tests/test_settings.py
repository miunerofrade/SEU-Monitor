"""测试运行时配置模块。"""

from seu_monitor.core.settings import Settings, _parse_bool


class TestParseBool:
    def test_true_variants(self):
        assert _parse_bool("true") is True
        assert _parse_bool("True") is True
        assert _parse_bool("TRUE") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True

    def test_false_variants(self):
        assert _parse_bool("false") is False
        assert _parse_bool("False") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False


class TestSettingsFromEnv:
    def test_defaults(self, monkeypatch):
        """无环境变量时使用默认值。"""
        monkeypatch.delenv("STORE_ROOT", raising=False)
        monkeypatch.delenv("SNAPSHOT_ROOT", raising=False)
        monkeypatch.delenv("MONITOR_CONFIG", raising=False)
        s = Settings.from_env_and_yaml()
        assert s.store_root == "store"
        assert s.snapshot_root == "snapshots"
        assert s.request_timeout == 20
        assert s.vpn_enabled is False
        assert s.vpn_required is False
        assert s.vpn_fail_fast is True

    def test_env_overrides(self, monkeypatch):
        """环境变量应覆盖默认值。"""
        monkeypatch.setenv("STORE_ROOT", "/data/store")
        monkeypatch.setenv("SNAPSHOT_ROOT", "/data/snapshots")
        monkeypatch.setenv("HTTP_PROXY", "http://proxy:8888")
        monkeypatch.setenv("REQUEST_TIMEOUT", "30")

        s = Settings.from_env_and_yaml()
        assert s.store_root == "/data/store"
        assert s.snapshot_root == "/data/snapshots"
        assert s.http_proxy == "http://proxy:8888"
        assert s.request_timeout == 30

    def test_vpn_env_overrides_yaml(self, monkeypatch):
        """VPN_REQUIRED=true 应覆盖 YAML 默认值。"""
        monkeypatch.setenv("VPN_REQUIRED", "true")
        monkeypatch.setenv("VPN_ENABLED", "true")

        yaml_cfg = {"vpn": {"enabled": False, "required": False}}
        s = Settings.from_env_and_yaml(yaml_cfg)
        assert s.vpn_enabled is True
        assert s.vpn_required is True

    def test_vpn_env_sets_fail_fast(self, monkeypatch):
        """VPN_FAIL_FAST 环境变量覆盖。"""
        monkeypatch.setenv("VPN_FAIL_FAST", "false")

        s = Settings.from_env_and_yaml()
        assert s.vpn_fail_fast is False


class TestResolveProxies:
    def test_no_proxy(self):
        s = Settings()
        assert s.resolve_proxies_dict() == {}

    def test_with_proxy(self):
        s = Settings(http_proxy="http://p:8888", https_proxy="http://p:8888")
        proxies = s.resolve_proxies_dict()
        assert proxies.get("http") == "http://p:8888"
        assert proxies.get("https") == "http://p:8888"

    def test_effective_vpn_proxy(self):
        s = Settings(vpn_proxy="http://vpn:8888")
        assert s.effective_vpn_proxy == "http://vpn:8888"

    def test_effective_vpn_proxy_fallback(self):
        s = Settings(https_proxy="http://proxy:8888")
        assert s.effective_vpn_proxy == "http://proxy:8888"
