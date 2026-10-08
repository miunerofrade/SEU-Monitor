"""Internal scan settings. The CLI exposes only credentials, webhook and interval.

Legacy environment variables remain accepted by edulog.py / existing schedulers.
"""

from __future__ import annotations
import os
from dataclasses import dataclass


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if value.lower() in ("true", "1", "yes", "on"):
        return True
    if value.lower() in ("false", "0", "no", "off"):
        return False
    raise ValueError("布尔值格式无效")


def _is_placeholder_path(value):
    return not value or not value.strip() or "/path" in value or "example" in value


@dataclass
class Settings:
    store_root: str = "store"
    snapshot_root: str = "snapshots"
    request_timeout: int = 20
    http_proxy: str | None = None
    https_proxy: str | None = None
    vpn_proxy: str | None = None
    vpn_check_url: str = ""
    vpn_enabled: bool = False
    vpn_required: bool = False
    vpn_fail_fast: bool = True
    feishu_webhook: str = ""
    dry_run: bool = False

    def resolve_proxies_dict(self):
        return {
            k: v
            for k, v in {"http": self.http_proxy, "https": self.https_proxy}.items()
            if v
        }

    @property
    def effective_vpn_proxy(self):
        return self.vpn_proxy or self.https_proxy or self.http_proxy

    def validate(self):
        if any(_is_placeholder_path(p) for p in (self.store_root, self.snapshot_root)):
            raise SystemExit(1)

    @classmethod
    def from_env_and_yaml(cls, yaml_config=None):
        # Keep old scheduler path / bool overrides without carrying a YAML loader.
        legacy = yaml_config or {}
        vpn = legacy.get("vpn", {})

        def boolean(key, name, default):
            return _parse_bool(os.environ.get(key, vpn.get(name, default)))

        return cls(
            store_root=os.environ.get("STORE_ROOT", legacy.get("store_root", "store")),
            snapshot_root=os.environ.get(
                "SNAPSHOT_ROOT", legacy.get("snapshot_root", "snapshots")
            ),
            request_timeout=int(os.environ.get("REQUEST_TIMEOUT", "20")),
            http_proxy=os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
            https_proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
            vpn_enabled=boolean("VPN_ENABLED", "enabled", False),
            vpn_required=boolean("VPN_REQUIRED", "required", False),
            vpn_fail_fast=boolean("VPN_FAIL_FAST", "fail_fast", True),
            vpn_proxy=vpn.get("proxy"),
            vpn_check_url=os.environ.get(
                "VPN_CHECK_URL", vpn.get("healthcheck_url", "")
            ),
            feishu_webhook=os.environ.get("FEISHU_WEBHOOK", ""),
        )
