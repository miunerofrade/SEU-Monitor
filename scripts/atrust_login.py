"""
aTrust 自动登录模块。

支持两种后端:
  - local:      本地 Playwright + chromium（默认，调试用）
  - container_cdp: 连接 aTrust 容器内 Chromium 的 CDP 端口

用法:
    python scripts/atrust_login.py              # 先 healthcheck，失败才登录
    python scripts/atrust_login.py --check-only # 只 healthcheck
    python scripts/atrust_login.py --login      # 强制登录

返回码:
    0 = VPN 已可用或自动登录成功
    1 = 自动登录失败
    2 = 自动登录未启用或缺少配置
    3 = 需要人工处理（验证码/二次认证/设备绑定）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))

from seu_monitor.core.healthcheck import check_vpn_verbose
from seu_monitor.core.settings import Settings

_MANUAL_KEYWORDS = [
    "验证码", "短信", "扫码", "二维码", "Token", "token",
    "手机号", "手机验证", "设备绑定", "二次认证", "双因素",
    "密保", "安全问题", "人脸", "指纹",
]


def _load_settings() -> Settings:
    settings = Settings.from_env_and_yaml()

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

    # ---- 登录入口（浏览器访问的 URL，如 vpn.seu.edu.cn） ----
    settings.atrust_login_url = os.environ.get(
        "ATRUST_LOGIN_URL",
        "https://vpn.seu.edu.cn",
    )
    settings.atrust_login_timeout = int(
        os.environ.get("ATRUST_LOGIN_TIMEOUT", "60")
    )

    # ---- container_cdp 专用配置 ----
    settings.atrust_login_backend = os.environ.get("ATRUST_LOGIN_BACKEND", "local")
    settings.atrust_container_name = os.environ.get("ATRUST_CONTAINER_NAME", "atrust")
    settings.atrust_cdp_internal_port = int(os.environ.get("ATRUST_CDP_INTERNAL_PORT", "9222"))
    settings.atrust_cdp_host_port = int(os.environ.get("ATRUST_CDP_HOST_PORT", "9223"))
    settings.atrust_container_chrome_user_data = os.environ.get(
        "ATRUST_CONTAINER_CHROME_USER_DATA_DIR", "/root/chrome-atrust-cdp"
    )
    settings.atrust_container_display = os.environ.get("ATRUST_CONTAINER_DISPLAY", ":1")

    return settings


def check_only() -> int:
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
    body_text = page.locator("body").text_content(timeout=5000)
    if body_text:
        for kw in _MANUAL_KEYWORDS:
            if kw in body_text:
                return True
    return False


def _take_screenshot(page, tag: str, settings: Settings):
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


# ---------------------------------------------------------------------------
# 表单填写（两个后端共用）
# ---------------------------------------------------------------------------

def _fill_login_form(page, username: str, password: str, settings: Settings) -> int:
    """在认证页上填写账号密码并提交。

    Returns:
        0 = 登录成功, 1 = 失败, 3 = 需要人工处理
    """
    if _detect_manual_intervention(page):
        _take_screenshot(page, "captcha", settings)
        print("检测到需要人工处理的页面（验证码/扫码等）")
        return 3

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
        try:
            if el.is_visible(timeout=2000):
                user_field = el
                break
        except Exception:
            continue
    if not user_field:
        print("未找到用户名输入框")
        _take_screenshot(page, "no_username_field", settings)
        return 1

    user_field.click()
    user_field.fill("")
    user_field.type(username, delay=50)

    pwd_selectors = [
        "input[type='password']",
        "input[placeholder*='密码']",
    ]
    pwd_field = None
    for sel in pwd_selectors:
        el = page.locator(sel).first
        try:
            if el.is_visible(timeout=2000):
                pwd_field = el
                break
        except Exception:
            continue
    if not pwd_field:
        print("未找到密码输入框")
        _take_screenshot(page, "no_pwd_field", settings)
        return 1

    pwd_field.click()
    pwd_field.type(password, delay=50)

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
        try:
            if el.is_visible(timeout=2000):
                login_btn = el
                break
        except Exception:
            continue
    if not login_btn:
        print("未找到登录按钮")
        _take_screenshot(page, "no_login_btn", settings)
        return 1

    print("提交登录...")
    login_btn.click()
    print("登录按钮已点击，等待 VPN 恢复...")

    # 点击后不依赖浏览器页面，进入 healthcheck 轮询
    timeout = settings.atrust_login_timeout
    return _poll_vpn(settings, timeout)


def _poll_vpn(settings: Settings, timeout: int = 60) -> int:
    """轮询 VPN healthcheck，不依赖浏览器状态。

    Returns:
        0 = VPN 可用, 1 = 超时失败
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        print(f"  VPN 检查 ({attempt})...")
        ok, msg = check_vpn_verbose(
            check_url=settings.vpn_check_url,
            proxy_url=settings.effective_vpn_proxy,
            timeout=settings.request_timeout,
        )
        if ok:
            print(f"VPN healthcheck OK，自动登录成功 ({msg})")
            return 0
        print(f"  VPN 尚未就绪: {msg}")
        time.sleep(2)

    print(f"VPN healthcheck 超时 ({timeout}s)，自动登录失败")
    return 1


def _verify_vpn(settings: Settings) -> int:
    """healthcheck 验证。

    Returns:
        0 = VPN 可用, 1 = 仍未恢复
    """
    print("验证 VPN 连通性...")
    time.sleep(2)
    ok, msg = check_vpn_verbose(
        check_url=settings.vpn_check_url,
        proxy_url=settings.effective_vpn_proxy,
        timeout=settings.request_timeout,
    )
    if ok:
        print(f"VPN 已恢复: {msg}")
        return 0
    else:
        print(f"VPN 仍未恢复: {msg}")
        return 1


# ---------------------------------------------------------------------------
# 后端：local
# ---------------------------------------------------------------------------

def _login_local(settings: Settings) -> int:
    """本地 Playwright 登录（直接 headless chromium）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright，请执行: pip install playwright && playwright install chromium")
        return 1

    print(f"启动浏览器 (local, user_data_dir={settings.atrust_user_data_dir})")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=settings.atrust_user_data_dir,
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
                ignore_https_errors=True,
            )
            page = browser.new_page()

            try:
                print(f"登录入口: {settings.atrust_login_url}")
                print(f"检查 URL:  {settings.vpn_check_url}")
                page.goto(settings.atrust_login_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)

                on_login = any(
                    kw in page.url.lower()
                    for kw in ["auth", "login", "cas", "sso", "oauth"]
                )
                if not on_login:
                    print("未检测到认证页，可能已登录")
                else:
                    rc = _fill_login_form(page, settings.atrust_username, settings.atrust_password, settings)
                    if rc != 0:
                        browser.close()
                        return rc

                rc = _verify_vpn(settings)
                browser.close()
                return rc

            except Exception as e:
                print(f"登录过程异常: {e}")
                _take_screenshot(page, "exception", settings)
                browser.close()
                return 1

    except Exception as e:
        print(f"浏览器启动失败: {e}")
        return 1


# ---------------------------------------------------------------------------
# 后端：container_cdp
# ---------------------------------------------------------------------------

def _run_docker(*args: str) -> subprocess.CompletedProcess:
    """在 aTrust 容器内执行命令。"""
    cmd = ["docker", "exec", args[0]] if args[0] == "atrust" else ["docker", "exec"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _get_container_ip(container_name: str) -> str:
    """获取 Docker 容器的 IP 地址。"""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container_name],
        capture_output=True, text=True, timeout=15,
    )
    ip = result.stdout.strip()
    if not ip:
        raise RuntimeError(f"无法获取容器 {container_name} 的 IP 地址")
    return ip


def _start_chromium_in_container(settings: Settings) -> bool:
    """在容器内启动 Chromium 并打开 CDP 端口。

    Returns:
        True 表示启动成功或已在运行。
    """
    container = settings.atrust_container_name
    chrome_data = settings.atrust_container_chrome_user_data
    display = settings.atrust_container_display
    cdp_port = settings.atrust_cdp_internal_port

    # 先检查是否已有 Chromium 进程
    check = subprocess.run(
        ["docker", "exec", container, "pgrep", "-f", "chrome.*atrust-cdp"],
        capture_output=True, text=True, timeout=15,
    )
    if check.returncode == 0:
        print("容器内 Chromium 已在运行")
        return True

    print("启动容器内 Chromium...")
    cmd = [
        "docker", "exec", "-d", container,
        "chromium",
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={chrome_data}",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--ozone-platform=x11",
        f"--display={display}",
        "about:blank",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"Chromium 启动失败: {result.stderr.strip()}")
            return False
        time.sleep(2)
        print("Chromium 已启动")
        return True
    except subprocess.TimeoutExpired:
        print("Chromium 启动超时")
        return False


def _start_socat_in_container(settings: Settings) -> bool:
    """在容器内启动 socat，将内部 CDP 端口转发到 host 端口。

    socat TCP-LISTEN:<host_port>,bind=0.0.0.0,fork,reuseaddr \\
          TCP:127.0.0.1:<internal_port>
    """
    container = settings.atrust_container_name
    internal = settings.atrust_cdp_internal_port
    host_port = settings.atrust_cdp_host_port

    check = subprocess.run(
        ["docker", "exec", container, "pgrep", "-f", f"socat.*{host_port}"],
        capture_output=True, text=True, timeout=15,
    )
    if check.returncode == 0:
        print("socat 已在运行")
        return True

    print(f"启动 socat (0.0.0.0:{host_port} → 127.0.0.1:{internal})...")
    cmd = [
        "docker", "exec", "-d", container,
        "socat",
        f"TCP-LISTEN:{host_port},bind=0.0.0.0,fork,reuseaddr",
        f"TCP:127.0.0.1:{internal}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            print(f"socat 启动失败: {result.stderr.strip()}")
            return False
        time.sleep(1)
        print("socat 已启动")
        return True
    except subprocess.TimeoutExpired:
        print("socat 启动超时")
        return False


def _connect_cdp_and_login(settings: Settings) -> int:
    """通过 CDP 连接到容器内 Chromium 并执行登录。

    Returns:
        0=成功, 1=失败, 3=需要人工处理
    """
    container_ip = _get_container_ip(settings.atrust_container_name)
    cdp_url = f"http://{container_ip}:{settings.atrust_cdp_host_port}"

    # 验证 CDP 可达
    import urllib.request
    import json
    try:
        resp = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=10)
        data = json.loads(resp.read().decode())
        print(f"CDP 连接成功: {data.get('Browser', '?')}")
    except Exception as e:
        print(f"CDP 连接失败 ({cdp_url}/json/version): {e}")
        print("  可能原因: Chromium 未启动 / socat 未启动 / 端口不可达")
        print("  手动排查:")
        print(f"    docker exec {settings.atrust_container_name} pgrep -af chrome")
        print(f"    docker exec {settings.atrust_container_name} pgrep -af socat")
        print(f"    curl {cdp_url}/json/version")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright")
        return 1

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts
            pages = contexts[0].pages if contexts else []
            page = pages[0] if pages else browser.new_page()

            print(f"登录入口: {settings.atrust_login_url}")
            print(f"检查 URL:  {settings.vpn_check_url}")

            # 判断当前页面是否已是认证页
            current_url = page.url.lower()
            already_on_login = any(
                kw in current_url
                for kw in ["auth", "login", "cas", "sso", "oauth"]
            )

            if already_on_login:
                print("当前页面已是认证页，直接填表（不跳转）")
            else:
                print(f"跳转到登录入口: {settings.atrust_login_url}")
                page.goto(settings.atrust_login_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)

            on_login = any(
                kw in page.url.lower()
                for kw in ["auth", "login", "cas", "sso", "oauth"]
            )
            if not on_login:
                print("未检测到认证页，可能已登录")
                return _verify_vpn(settings)

            # 检测人工处理（点击前做，点击后页面可能关闭）
            if _detect_manual_intervention(page):
                _take_screenshot(page, "captcha", settings)
                print("检测到需要人工处理的页面（验证码/扫码等）")
                return 3

            # 填写并点击登录
            rc = _fill_login_form(
                page, settings.atrust_username,
                settings.atrust_password, settings,
            )
            # _fill_login_form 内部已调用 _poll_vpn，rc 就是最终结果
            return rc

    except Exception as e:
        e_str = str(e).lower()
        if any(kw in e_str for kw in ["closed", "detached", "target"]):
            print(f"登录后页面关闭 ({type(e).__name__})，通过 VPN healthcheck 判断...")
            return _poll_vpn(settings, settings.atrust_login_timeout)
        print(f"CDP 登录异常: {e}")
        return 1


def _login_container_cdp(settings: Settings) -> int:
    """容器 CDP 登录后端。"""
    if not _start_chromium_in_container(settings):
        return 1
    if not _start_socat_in_container(settings):
        return 1
    return _connect_cdp_and_login(settings)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def do_login() -> int:
    settings = _load_settings()

    if not settings.atrust_username or not settings.atrust_password:
        print("ATRUST_USERNAME 或 ATRUST_PASSWORD 未配置")
        return 2

    if not settings.vpn_check_url:
        print("VPN_CHECK_URL 未配置，无法执行登录")
        return 2

    # 先 healthcheck
    ok, _ = check_vpn_verbose(
        check_url=settings.vpn_check_url,
        proxy_url=settings.effective_vpn_proxy,
        timeout=settings.request_timeout,
    )
    if ok:
        print("VPN 已可用，无需登录")
        return 0

    # 选择后端
    backend = settings.atrust_login_backend
    print(f"登录后端: {backend}")
    print(f"登录入口: {settings.atrust_login_url}")
    print(f"检查 URL:  {settings.vpn_check_url}")

    if backend == "container_cdp":
        return _login_container_cdp(settings)
    else:
        return _login_local(settings)


def main():
    parser = argparse.ArgumentParser(description="aTrust 自动登录")
    parser.add_argument("--check-only", action="store_true", help="仅运行 VPN healthcheck")
    parser.add_argument("--login", action="store_true", help="强制尝试登录")
    args = parser.parse_args()

    if args.check_only:
        sys.exit(check_only())
    if args.login:
        sys.exit(do_login())

    if check_only() == 0:
        sys.exit(0)
    sys.exit(do_login())


if __name__ == "__main__":
    main()
