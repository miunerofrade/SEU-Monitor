"""Check the native VPN and optionally alert; systemd owns tunnel recovery."""

import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seu_monitor.core.healthcheck import check_vpn_verbose
from seu_monitor.core.settings import Settings
from seu_monitor.core.notify import FeishuNotifier


def main():
    parser = argparse.ArgumentParser(
        description="VPN 健康告警（重连由 seu-vpn.service 负责）"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env_and_yaml()
    ok, _ = check_vpn_verbose(
        settings.vpn_check_url or "https://cvs.seu.edu.cn",
        settings.effective_vpn_proxy or "http://127.0.0.1:8888",
        settings.request_timeout,
    )
    if ok:
        print("VPN 可用")
        return 0
    print("VPN 不可用，请检查 seu-vpn.service；重连由常驻服务负责")
    if not args.dry_run:
        FeishuNotifier(webhook_url=settings.feishu_webhook).send_alert(
            message="VPN 数据通道不可用。请检查 seu-vpn.service 日志；短信验证请运行 monitor vpn --interactive，在终端输入验证码。",
            title="SEU-Monitor: VPN 不可用",
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
