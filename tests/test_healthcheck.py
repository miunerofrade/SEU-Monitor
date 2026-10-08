"""测试 VPN 数据通道健康检查。"""

import os
import tempfile
from unittest.mock import Mock

import pytest
import requests

from seu_monitor.core.healthcheck import check_vpn, check_vpn_verbose


# ---------------------------------------------------------------------------
# 单元测试：check_vpn
# ---------------------------------------------------------------------------


class TestCheckVpn:
    def test_success(self, monkeypatch):
        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=200)
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)
        assert check_vpn(check_url="https://cvs.seu.edu.cn") is True

    def test_status_500(self, monkeypatch):
        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=500)
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)
        assert check_vpn(check_url="https://cvs.seu.edu.cn") is False

    def test_proxy_error(self, monkeypatch):
        def mock_get(self, url, **kwargs):
            raise requests.exceptions.ProxyError("tinyproxy 500")

        monkeypatch.setattr("requests.Session.get", mock_get)
        assert check_vpn(check_url="https://cvs.seu.edu.cn") is False

    def test_tinyproxy_500(self, monkeypatch):
        """tinyproxy 500 应返回 False"""

        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=502)
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)
        assert check_vpn(check_url="https://cvs.seu.edu.cn") is False

    def test_empty_check_url(self):
        assert check_vpn(check_url="") is True


# ---------------------------------------------------------------------------
# 单元测试：check_vpn_verbose
# ---------------------------------------------------------------------------


class TestCheckVpnVerbose:
    def test_returns_ok_message(self, monkeypatch):
        def mock_get(self, url, **kwargs):
            resp = Mock(status_code=200)
            return resp

        monkeypatch.setattr("requests.Session.get", mock_get)
        ok, msg = check_vpn_verbose(check_url="https://cvs.seu.edu.cn")
        assert ok is True
        assert "OK" in msg

    def test_returns_fail_message(self, monkeypatch):
        def mock_get(self, url, **kwargs):
            raise requests.exceptions.ConnectTimeout("timeout")

        monkeypatch.setattr("requests.Session.get", mock_get)
        ok, msg = check_vpn_verbose(check_url="https://cvs.seu.edu.cn")
        assert ok is False
        assert "FAILED" in msg


# ---------------------------------------------------------------------------
# 集成测试：runner VPN auth 逻辑
# ---------------------------------------------------------------------------
