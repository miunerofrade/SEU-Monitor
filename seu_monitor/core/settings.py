"""运行时配置 — 统一从环境变量 / YAML / 默认值读取。

优先级：环境变量 > YAML 配置值 > 代码默认值
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 占位符路径模式 — 匹配后拒绝启动
_PLACEHOLDER_PATTERNS = [
    "/path",
    "/path/",
    "/path/to/",
    "example",
]


def _is_placeholder_path(path: str) -> bool:
    """检查路径是否是未修改的占位符。"""
    if not path or not path.strip():
        return True
    cleaned = path.strip().rstrip("/")
    for pat in _PLACEHOLDER_PATTERNS:
        if pat in cleaned:
            return True
    return False


def _validate_paths(store_root: str, snapshot_root: str):
    """校验 STORE_ROOT 和 SNAPSHOT_ROOT，占位符时报错退出。"""
    if _is_placeholder_path(store_root):
        print("❌ STORE_ROOT 是无效占位符路径，请设置为实际路径。")
        print("   例如: STORE_ROOT=/home/ubuntu/SEU-Monitor/store")
        print("   当前值:", repr(store_root))
        sys.exit(1)
    if _is_placeholder_path(snapshot_root):
        print("❌ SNAPSHOT_ROOT 是无效占位符路径，请设置为实际路径。")
        print("   例如: SNAPSHOT_ROOT=/home/ubuntu/seu-snapshots")
        print("   当前值:", repr(snapshot_root))
        sys.exit(1)


def _parse_bool(val: str) -> bool:
    """将字符串解析为布尔值。"""
    if isinstance(val, bool):
        return val
    v = val.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"无法解析为布尔值: {val}")


@dataclass
class Settings:
    """集中管理所有运行时配置。"""

    # ---- 路径 ----
    store_root: str = "store"
    snapshot_root: str = "snapshots"
    config_path: str = "config/sites.yaml"

    # ---- 网络 ----
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    request_timeout: int = 20

    # ---- VPN ----
    vpn_enabled: bool = False
    vpn_required: bool = False
    vpn_fail_fast: bool = True
    vpn_proxy: Optional[str] = None
    vpn_check_url: str = ""

    # ---- 通知 ----
    feishu_webhook: str = ""

    # ---- 运行时 ----
    dry_run: bool = False
    site_filter: str = ""
    run_check: bool = False
    check_vpn_only: bool = False  # --check-vpn 模式

    def resolve_proxies_dict(self) -> Dict[str, str]:
        """返回 requests 可用的 proxies 字典。"""
        proxies = {}
        if self.http_proxy:
            proxies["http"] = self.http_proxy
        if self.https_proxy:
            proxies["https"] = self.https_proxy
        return proxies

    @property
    def effective_vpn_proxy(self) -> Optional[str]:
        """返回最终使用的 VPN 代理地址。"""
        return self.vpn_proxy or self.https_proxy or self.http_proxy

    def validate(self):
        """校验所有关键配置项，失败时退出。"""
        _validate_paths(self.store_root, self.snapshot_root)

    @classmethod
    def from_env_and_yaml(cls, yaml_config: Optional[dict] = None) -> Settings:
        """从环境变量和可选的 YAML 配置构造 Settings。"""

        env = os.environ

        def _env(key: str, fallback: str = "") -> str:
            return env.get(key, fallback)

        def _bool_env(key: str, default: bool) -> bool:
            val = env.get(key)
            if val is not None and val.strip():
                return _parse_bool(val)
            return default

        def _str_env(key: str, default: str = "") -> str:
            return env.get(key, default)

        # VPN 配置：环境变量优先，否则从 YAML vpn section 读
        vpn_enabled = _bool_env("VPN_ENABLED", False)
        vpn_required = _bool_env("VPN_REQUIRED", False)
        vpn_fail_fast = _bool_env("VPN_FAIL_FAST", True)
        vpn_proxy = _str_env("HTTP_PROXY") or _str_env("https_proxy") or ""
        vpn_check_url = _str_env("VPN_CHECK_URL", "")

        # 如果有 YAML vpn 配置且环境变量没覆盖，用 YAML 值
        if yaml_config:
            yaml_vpn = yaml_config.get("vpn", {})
            if not env.get("VPN_ENABLED"):
                vpn_enabled = yaml_vpn.get("enabled", vpn_enabled)
            if not env.get("VPN_REQUIRED"):
                vpn_required = yaml_vpn.get("required", vpn_required)
            if not env.get("VPN_FAIL_FAST"):
                vpn_fail_fast = yaml_vpn.get("fail_fast", vpn_fail_fast)
            if not env.get("HTTPS_PROXY") and not env.get("HTTP_PROXY"):
                vpn_proxy = yaml_vpn.get("proxy", vpn_proxy)
            if not env.get("VPN_CHECK_URL"):
                vpn_check_url = yaml_vpn.get("healthcheck_url", vpn_check_url)

        # 从 YAML 顶层读取常规配置
        def _yaml_str(key: str, default: str = "") -> str:
            if yaml_config and key in yaml_config:
                v = yaml_config[key]
                if isinstance(v, str):
                    return v
            return default

        return cls(
            store_root=env.get("STORE_ROOT") or _yaml_str("store_root", "store"),
            snapshot_root=env.get("SNAPSHOT_ROOT") or _yaml_str("snapshot_root", "snapshots"),
            config_path=env.get("MONITOR_CONFIG", "config/sites.yaml"),
            http_proxy=env.get("HTTP_PROXY") or env.get("http_proxy"),
            https_proxy=env.get("HTTPS_PROXY") or env.get("https_proxy"),
            request_timeout=int(env.get("REQUEST_TIMEOUT", "20")),
            vpn_enabled=vpn_enabled,
            vpn_required=vpn_required,
            vpn_fail_fast=vpn_fail_fast,
            vpn_proxy=vpn_proxy,
            vpn_check_url=vpn_check_url,
            feishu_webhook=env.get("FEISHU_WEBHOOK", ""),
        )
