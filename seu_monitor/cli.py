"""monitor / stop / ps / doctor / vpn."""

from __future__ import annotations
import argparse
import getpass
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from . import config, systemd
from .core.settings import Settings


def parser():
    result = argparse.ArgumentParser(
        prog="monitor", description="教务处通知监控（systemd 管理）"
    )
    result.add_argument("--data-dir", help="数据目录，默认 ~/.local/share/seu-monitor")
    result.add_argument("--webhook", help="保存飞书 webhook；不设置也可只归档")
    result.add_argument("--interval", type=int, help="扫描间隔秒数，默认 3600")
    result.add_argument("--worker", choices=["monitor", "vpn"], help=argparse.SUPPRESS)
    commands = result.add_subparsers(dest="command")
    commands.add_parser("stop", help="停止监控和 VPN，并取消开机启动")
    commands.add_parser("ps", help="显示 systemd 服务状态")
    commands.add_parser("doctor", help="检查配置、教务处和 VPN")
    vpn = commands.add_parser("vpn", help="配置或启动 zju-connect VPN")
    vpn.add_argument("--account", help="校园账号")
    vpn.add_argument("--password", dest="password", help="密码（省略时交互输入）")
    vpn.add_argument("--port", type=int, help="HTTP 代理端口，默认 8888")
    vpn.add_argument(
        "--interactive",
        action="store_true",
        help="前台 HTTP 登录并输入短信验证码，不启动后台 VPN",
    )
    return result


def settings_for(directory, values):
    proxy = f"http://127.0.0.1:{values['port']}" if values["account"] else None
    return Settings(
        store_root=str(directory / "state"),
        snapshot_root=str(directory / "web"),
        feishu_webhook=values["webhook"],
        http_proxy=proxy,
        https_proxy=proxy,
        vpn_enabled=bool(proxy),
        vpn_proxy=proxy,
        vpn_check_url="https://cvs.seu.edu.cn",
    )


def worker(name, directory, values):
    if name == "vpn":
        from .core.vpn import run_service

        os.environ.update(
            VPN_USERNAME=values["account"],
            VPN_PASSWORD=values["password"],
            VPN_STATE_DIR=str(directory / "vpn"),
        )
        result = run_service(values["port"])
        if result:
            from .core.notify import FeishuNotifier

            try:
                webhook = config.load(directory)["webhook"]
                if webhook:
                    FeishuNotifier(webhook).send_alert(
                        "VPN 连接失败或需要人工验证，已暂停 VPN 自动重试。"
                        "通知监控继续通过直连抓取。"
                        "查看 VPN 日志后执行 monitor vpn 恢复；"
                        "需要短信验证时执行 monitor vpn --interactive。",
                        title="SEU-Monitor：VPN 已暂停",
                    )
            except Exception:
                print("VPN 告警发送失败，仍暂停自动重试", flush=True)
            # systemd 的 RestartPreventExitStatus=3 阻止重复启动和告警。
            return 3
        return 0
    from .core.runner import run_all

    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    while not stopped.is_set():
        try:
            run_all(settings_for(directory, config.load(directory)))
        except Exception:
            logging.exception("扫描失败，下轮重试")
        stopped.wait(config.load(directory)["interval"])
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        directory = config.data_directory(args.data_dir)
        values = config.load(directory)
        if args.worker:
            return worker(args.worker, directory, values)
        if args.webhook is not None:
            values["webhook"] = args.webhook
        if args.interval is not None:
            values["interval"] = args.interval
        if args.command == "vpn":
            if args.account is not None:
                values["account"] = args.account
                if args.password is None and sys.stdin.isatty():
                    values["password"] = getpass.getpass("校园密码：")
            if args.password is not None:
                values["password"] = args.password
            if args.port is not None:
                values["port"] = args.port
            config.save(directory, values)
            config.load(directory)  # Validate before touching services.
            if not values["account"] or not values["password"]:
                raise ValueError("请先提供 --account；密码可交互输入或使用 --password")
            if args.interactive:
                systemd.command("stop", systemd.UNITS["vpn"], check=False)
                os.environ["VPN_INTERACTIVE"] = "true"
                return worker("vpn", directory, values)
            systemd.start(directory, "vpn", restart=True)
            print("VPN 服务已启动；monitor ps 查看状态")
        elif args.command == "stop":
            systemd.stop()
            print("监控和 VPN 已停止")
        elif args.command == "ps":
            systemd.status()
            print(f"文件：{directory / 'web'}")
        elif args.command == "doctor":
            from .core.runner import run_check

            print(f"配置：{directory / 'config.json'}")
            print(f"推送：{'已配置' if values['webhook'] else '未配置，仅归档'}")
            return 0 if run_check(settings_for(directory, values)) else 1
        else:
            from .migration import migrate

            migrate(directory, Path.cwd())
            config.save(directory, values)
            config.load(directory)
            if values["account"]:
                systemd.start(directory, "vpn")
            systemd.start(
                directory,
                "monitor",
                restart=args.interval is not None or args.webhook is not None,
            )
            print(
                f"监控已启动，每 {values['interval']} 秒扫描；文件：{directory / 'web'}"
            )
        return 0
    except Exception as error:
        # Never include credentials, callback tickets or webhook URLs in CLI output.
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error), file=sys.stderr)
        else:
            print(
                f"操作失败（{type(error).__name__}）；请用 monitor doctor / journalctl 检查",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
