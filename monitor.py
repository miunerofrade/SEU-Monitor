#!/usr/bin/env python3
"""SEU-Monitor — 教务处公告监控系统。

用法:
    python monitor.py                           # 正常模式
    python monitor.py --config path/to/yaml
    python monitor.py --site jwc                # 单站点
    python monitor.py --dry-run                 # 试运行
    python monitor.py --check                   # 健康检查（含 VPN）
    python monitor.py --check-vpn              # 仅 VPN 健康检查

环境变量:
    FEISHU_WEBHOOK    飞书 Webhook 地址
    STORE_ROOT        已发送 ID 存储根目录
    SNAPSHOT_ROOT     快照存储根目录
    HTTP_PROXY        HTTP 代理地址
    HTTPS_PROXY       HTTPS 代理地址
    VPN_ENABLED       是否启用 VPN 检查（true/false）
    VPN_REQUIRED      是否强制 VPN（true/false，启用后全局检查）
    VPN_FAIL_FAST     VPN 不可用时是否直接退出（true/false）
    VPN_CHECK_URL     VPN 健康检查 URL
    REQUEST_TIMEOUT   请求超时秒数（默认 20）
"""

from __future__ import annotations

import argparse
import logging
import sys

from seu_monitor.core.runner import run_all, run_check, run_check_vpn


def parse_args(argv: list = None):
    parser = argparse.ArgumentParser(
        description="SEU-Monitor — 东南大学公告监控系统",
    )
    parser.add_argument(
        "--config",
        default="config/sites.yaml",
        help="配置文件路径（默认: config/sites.yaml）",
    )
    parser.add_argument(
        "--site",
        default="",
        help="只处理指定站点（按 site_id，如 jwc）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行：抓取但不推送、不 mark_seen、不下载附件",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="健康检查：检查配置和 VPN",
    )
    parser.add_argument(
        "--check-vpn",
        action="store_true",
        help="仅 VPN 健康检查：无论配置如何都强制检查",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from seu_monitor.core.settings import Settings

    settings = Settings.from_env_and_yaml()
    settings.config_path = args.config
    settings.dry_run = args.dry_run
    settings.site_filter = args.site

    if args.check_vpn:
        ok = run_check_vpn(settings)
        sys.exit(0 if ok else 1)

    if args.check:
        ok = run_check(settings)
        sys.exit(0 if ok else 1)

    run_all(settings)


if __name__ == "__main__":
    main()
