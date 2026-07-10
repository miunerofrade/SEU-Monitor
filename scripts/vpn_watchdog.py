"""
VPN Watchdog — 监控 VPN 状态，必要时自动登录，通知飞书。

流程:
  1. healthcheck
  2. 如果可用 → exit 0
  3. 如果不可用 → 调用 atrust_login.py --login
  4. 根据返回码发送飞书告警

用法:
    python scripts/vpn_watchdog.py
    python scripts/vpn_watchdog.py --dry-run   # 不发送飞书

返回码:
    0 = VPN 可用（或已自动恢复）
    1 = 自动登录失败，需手动 VNC
    2 = 自动登录未配置
    3 = 需要人工处理认证
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))

from seu_monitor.core.healthcheck import check_vpn_verbose
from seu_monitor.core.notify import FeishuNotifier
from seu_monitor.core.settings import Settings


def _call_atrust_login(force_login: bool = False) -> int:
    """调用 atrust_login.py 子进程。

    Returns:
        子进程返回码。
    """
    login_script = _SCRIPT_DIR / "scripts" / "atrust_login.py"
    cmd = [sys.executable, str(login_script)]
    if force_login:
        cmd.append("--login")

    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VPN Watchdog")
    parser.add_argument("--dry-run", action="store_true", help="不发送飞书告警")
    args = parser.parse_args()

    settings = Settings.from_env_and_yaml()
    notifier = FeishuNotifier(webhook_url=settings.feishu_webhook)
    dry_run = args.dry_run

    # Step 1: healthcheck
    ok, msg = check_vpn_verbose(
        check_url=settings.vpn_check_url,
        proxy_url=settings.effective_vpn_proxy,
        timeout=settings.request_timeout,
    )

    if ok:
        print("VPN 可用，无需操作")
        return 0

    # Step 2: 尝试自动登录
    print("VPN 不可用，尝试自动登录...")
    login_code = _call_atrust_login(force_login=True)

    if login_code == 0:
        print("VPN 已自动恢复")
        if not dry_run:
            notifier.send_alert(
                message="VPN 已通过自动登录恢复。",
                title="✅ SEU-Monitor: VPN 已自动恢复",
            )
        return 0

    elif login_code == 3:
        print("需要人工处理认证（验证码/扫码等）")
        if not dry_run:
            notifier.send_alert(
                message=(
                    "aTrust 自动登录遇到需要人工处理的页面（验证码/扫码/设备绑定等）。\n"
                    "请通过 VNC 登录 VPS 桌面，手动完成 aTrust 登录。\n"
                    "截图已保存到 logs/ 目录（如 ATRUST_SCREENSHOT_ON_FAIL=true）。"
                ),
                title="⚠️ SEU-Monitor: 需要手动认证",
            )
        return 3

    elif login_code == 2:
        print("自动登录未配置（缺少 ATRUST_USERNAME/ATRUST_PASSWORD）")
        if not dry_run:
            notifier.send_alert(
                message=(
                    "VPN 掉线且自动登录未配置。\n"
                    "请设置 ATRUST_USERNAME 和 ATRUST_PASSWORD，\n"
                    "或通过 VNC 手动登录 aTrust。"
                ),
                title="⚠️ SEU-Monitor: VPN 掉线",
            )
        return 2

    else:  # login_code == 1
        print("自动登录失败")
        if not dry_run:
            notifier.send_alert(
                message=(
                    "aTrust 自动登录执行失败，可能需要手动 VNC 登录。\n"
                    "登录脚本返回码: 1\n"
                    "请通过 VNC 登录 VPS 桌面，手动完成 aTrust 登录。"
                ),
                title="⚠️ SEU-Monitor: VPN 自动登录失败",
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
