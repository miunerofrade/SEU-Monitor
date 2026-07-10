"""
aTrust 自动登录模块。

基于 Playwright 的统一认证自动登录，在 VPN healthcheck 失败后调用。

用法:
    python scripts/atrust_login.py              # 先 healthcheck，失败才登录
    python scripts/atrust_login.py --check-only # 只 healthcheck
    python scripts/atrust_login.py --login      # 强制登录

返回码:
    0 = VPN 已可用或自动登录成功
    1 = 自动登录失败
    2 = 自动登录未启用或缺少配置
    3 = 需要人工处理（验证码/二次认证/设备绑定）

依赖:
    playwright install chromium
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 path，使 import seu_monitor 可用
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))

from seu_monitor.core.healthcheck import check_vpn_verbose
from seu_monitor.core.settings import Settings


# 关键词检测 —— 出现任一即认为需要人工处理
_MANUAL_KEYWORDS = [
    "验证码", "短信", "扫码", "二维码", "Token", "token",
    "手机号", "手机验证", "设备绑定", "二次认证", "双因素",
    "密保", "安全问题", "人脸", "指纹",
]


def _load_settings() -> Settings:
    """加载配置，并注入 .env 中的 VPN / Playwright 相关变量。"""
    settings = Settings.from_env_and_yaml()

    # Playwright 相关环境变量（不从 YAML 读）
    settings.atrust_username = os.environ.get("ATRUST_USERNAME", "")
    settings.atrust_password = os.environ.get("ATRUST_PASSWORD", "")
    settings.atrust_user_data_dir = os.environ.get(
        "ATRUST_USER_DATA_DIR",
        "/home/ubuntu/.cache/seu-monitor/atrust-playwright",
    )
    settings.atrust_screenshot_on_fail = (
        os.environ.get("ATRUST_SCREENSHOT_ON_FAIL", "true").lower()
        in ("true", "1", "yes")
    )
    return settings


def check_only() -> int:
    """仅执行 VPN healthcheck。"""
    settings = _load_settings()
    ok, msg = check_vpn_verbose(
        check_url=settings.vpn_check_url,
        proxy_url=settings.effective_vpn_proxy,
        timeout=settings.request_timeout,
    )
    if ok:
        print(f"VPN 可用: {msg}")
        return 0
    else:
        print(f"VPN 不可用: {msg}")
        return 1


def _detect_manual_intervention(page) -> bool:
    """检测页面是否出现需要人工处理的关键词。"""
    body_text = page.locator("body").text_content(timeout=5000)
    if body_text:
        for kw in _MANUAL_KEYWORDS:
            if kw in body_text:
                return True
    return False


def _take_screenshot(page, tag: str, settings: Settings):
    """按配置保存失败截图。"""
    if not settings.atrust_screenshot_on_fail:
        return
    logs_dir = _SCRIPT_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir / f"atrust_fail_{tag}_{ts}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"[截图] 已保存: {path}")
    except Exception as e:
        print(f"[截图] 保存失败: {e}")


def do_login() -> int:
    """执行 aTrust 统一认证登录。

    流程:
        1. 加载配置，检查必要配置。
        2. healthcheck — 如果已经可用则直接返回 0。
        3. 启动 Playwright persistent context。
        4. 访问 VPN_CHECK_URL。
        5. 检测是否需要人工处理 → 返回 3。
        6. 填写账号密码，点击登录。
        7. 等待页面跳转。
        8. 再次 healthcheck 验证。
        9. 返回结果码。
    """
    settings = _load_settings()

    # 检查必要配置
    if not settings.atrust_username or not settings.atrust_password:
        print("ATRUST_USERNAME 或 ATRUST_PASSWORD 未配置")
        return 2

    target_url = settings.vpn_check_url
    if not target_url:
        print("VPN_CHECK_URL 未配置，无法执行登录")
        return 2

    proxy_url = settings.effective_vpn_proxy

    # ---- 先 healthcheck ----
    ok, _ = check_vpn_verbose(
        check_url=target_url,
        proxy_url=proxy_url,
        timeout=settings.request_timeout,
    )
    if ok:
        print("VPN 已可用，无需登录")
        return 0

    # ---- 启动浏览器 ----
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright，请执行: pip install playwright && playwright install chromium")
        return 1

    user_data_dir = settings.atrust_user_data_dir
    print(f"启动浏览器 (user_data_dir={user_data_dir})")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
                ignore_https_errors=True,
            )
            page = browser.new_page()

            try:
                print(f"正在访问: {target_url}")
                page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)  # 等待页面渲染

                current_url = page.url.lower()
                # 判断是否在认证页
                on_login = any(kw in current_url for kw in ["auth", "login", "cas", "sso", "oauth"])

                if not on_login:
                    print("未检测到认证页，可能已登录")
                else:
                    # 检测人工处理
                    if _detect_manual_intervention(page):
                        _take_screenshot(page, "captcha", settings)
                        print("检测到需要人工处理的页面（验证码/扫码等）")
                        browser.close()
                        return 3

                    username = settings.atrust_username
                    password = settings.atrust_password

                    # 填写账号
                    user_selectors = [
                        "input[placeholder*='一卡通']",
                        "input[placeholder*='ID']",
                        ".input-username-pc",
                        "input[name*=user]",
                        "input[id*=user]",
                        "input[placeholder*=账号]",
                        "input[placeholder*=用户名]",
                        "input[type=text]",
                    ]
                    user_field = None
                    for sel in user_selectors:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            user_field = el
                            break
                    if not user_field:
                        print("未找到用户名输入框")
                        _take_screenshot(page, "no_username_field", settings)
                        browser.close()
                        return 1

                    user_field.click()
                    user_field.fill("")
                    user_field.type(username, delay=50)

                    # 填写密码
                    pwd_selectors = [
                        "input[type='password']",
                        "input[placeholder*='密码']",
                    ]
                    pwd_field = None
                    for sel in pwd_selectors:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            pwd_field = el
                            break
                    if not pwd_field:
                        print("未找到密码输入框")
                        _take_screenshot(page, "no_pwd_field", settings)
                        browser.close()
                        return 1

                    pwd_field.click()
                    pwd_field.type(password, delay=50)

                    # 点击登录按钮
                    btn_selectors = [
                        "button:has-text('登 录')",
                        "button:has-text('登录')",
                        ".login-button-pc",
                        ".ant-btn-primary",
                        "button[type=submit]",
                    ]
                    login_btn = None
                    for sel in btn_selectors:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            login_btn = el
                            break
                    if not login_btn:
                        print("未找到登录按钮")
                        _take_screenshot(page, "no_login_btn", settings)
                        browser.close()
                        return 1

                    print("提交登录...")
                    login_btn.click()

                    # 等待跳转 —— 不再停留在 auth/login 页面
                    try:
                        page.wait_for_url(
                            lambda url: not any(
                                kw in url.lower()
                                for kw in ["authserver", "login", "cas", "sso"]
                            ),
                            timeout=30000,
                        )
                        print("页面已跳转，认证完成")
                    except Exception:
                        # 超时后检测是否有人工处理页
                        if _detect_manual_intervention(page):
                            _take_screenshot(page, "post_login_captcha", settings)
                            print("登录后出现人工处理页面")
                            browser.close()
                            return 3
                        _take_screenshot(page, "login_timeout", settings)
                        print("登录超时")
                        browser.close()
                        return 1

                # ---- 验证: healthcheck ----
                print("验证 VPN 连通性...")
                time.sleep(2)  # 等待代理生效
                ok, msg = check_vpn_verbose(
                    check_url=target_url,
                    proxy_url=proxy_url,
                    timeout=settings.request_timeout,
                )
                if ok:
                    print(f"VPN 已恢复: {msg}")
                    browser.close()
                    return 0
                else:
                    print(f"VPN 仍未恢复: {msg}")
                    browser.close()
                    return 1

            except Exception as e:
                print(f"登录过程异常: {e}")
                _take_screenshot(page, "exception", settings)
                browser.close()
                return 1

    except Exception as e:
        print(f"浏览器启动失败: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="aTrust 自动登录")
    parser.add_argument("--check-only", action="store_true", help="仅运行 VPN healthcheck")
    parser.add_argument("--login", action="store_true", help="强制尝试登录")
    args = parser.parse_args()

    if args.check_only:
        sys.exit(check_only())

    if args.login:
        sys.exit(do_login())

    # 默认行为：先 healthcheck，失败才登录
    if check_only() == 0:
        sys.exit(0)
    sys.exit(do_login())


if __name__ == "__main__":
    main()
