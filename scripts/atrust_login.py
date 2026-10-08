"""Compatibility launcher for the native VPN service (no Docker/CDP).

--login or no arguments keeps the proxy alive in the foreground. Use systemd
for unattended operation; --check-only is a short health check.
"""

from pathlib import Path
import argparse, os, sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seu_monitor.core.vpn import run_service
from seu_monitor.core.healthcheck import check_vpn_verbose
from seu_monitor.core.settings import Settings


def main():
    parser = argparse.ArgumentParser(description="zju-connect 校园 VPN 常驻服务")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--login", action="store_true", help="兼容旧参数，前台保持代理运行"
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("VPN_HTTP_PORT", "8888"))
    )
    args = parser.parse_args()
    if args.check_only:
        settings = Settings.from_env_and_yaml()
        url = settings.vpn_check_url or "https://cvs.seu.edu.cn"
        ok, _ = check_vpn_verbose(
            url,
            settings.effective_vpn_proxy or f"http://127.0.0.1:{args.port}",
            settings.request_timeout,
        )
        print("VPN 可用" if ok else "VPN 不可用")
        return 0 if ok else 1
    return run_service(args.port)


if __name__ == "__main__":
    sys.exit(main())
