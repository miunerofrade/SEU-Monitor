"""Native zju-connect sidecar; no Docker, CDP bridge or system VPN routes.

CAS ticket interception and release verification adapted from SEUdaily (MIT).
"""

from __future__ import annotations
import hashlib, io, json, os, platform, queue, re, signal, socket, subprocess, threading, time, zipfile
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

RELEASE = "v1.3.1"
SOURCE_COMMIT = "5d7f5b11fcf231f72a0ec0d888bf0f2eadcce1da"
SOURCE_URL = f"https://github.com/Mythologyli/zju-connect/tree/{SOURCE_COMMIT}"
SERVER = "https://vpn.seu.edu.cn"


class VPNError(RuntimeError):
    """Safe operational messages; never wrap raw gateway output."""


class ManualAuthenticationRequired(RuntimeError):
    pass


def state_directory() -> Path:
    path = Path(os.environ.get("VPN_STATE_DIR", ".vpn")).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _write_core_notices(directory: Path) -> None:
    """Keep upstream license and matching source references beside our download."""
    license_file = Path(__file__).with_name("licenses") / "AGPL-3.0.txt"
    (directory / "LICENSE").write_bytes(license_file.read_bytes())
    (directory / "SOURCE.txt").write_text(
        f"zju-connect {RELEASE}, unmodified upstream release\n"
        "License: GNU Affero General Public License version 3 (see LICENSE).\n"
        f"Upstream source: {SOURCE_URL}\n"
        f"Source archive: https://github.com/Mythologyli/zju-connect/archive/{SOURCE_COMMIT}.tar.gz\n"
        "Copyright notices, dependencies and build instructions are in the source tree.\n"
        "Provided WITHOUT ANY WARRANTY; see LICENSE for your rights and terms.\n",
        encoding="utf-8",
    )


def install_core() -> Path:
    """Download a pinned official release, verifying its GitHub SHA256 digest."""
    configured = os.environ.get("VPN_BINARY")
    if configured:
        binary = Path(configured).expanduser().resolve()
        if not binary.is_file():
            raise VPNError("配置的 VPN 核心不存在")
        return binary
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(
        platform.system()
    )
    machine = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "AMD64": "amd64",
    }.get(platform.machine())
    if not system or not machine:
        raise VPNError("此平台请通过 VPN_BINARY 指定 zju-connect")
    directory = state_directory() / "bin"
    binary = directory / ("zju-connect.exe" if system == "windows" else "zju-connect")
    provenance = directory / "release.json"
    if binary.is_file() and provenance.is_file():
        saved = json.loads(provenance.read_text())
        if (
            saved.get("version") == RELEASE
            and saved.get("binarySha256")
            == hashlib.sha256(binary.read_bytes()).hexdigest()
        ):
            _write_core_notices(directory)
            return binary
    opener = build_opener(ProxyHandler({}))
    api = (
        f"https://api.github.com/repos/Mythologyli/zju-connect/releases/tags/{RELEASE}"
    )
    with opener.open(
        Request(api, headers={"User-Agent": "SEU-Monitor"}), timeout=30
    ) as response:
        release = json.load(response)
    name = f"zju-connect-{system}-{machine}.zip"
    asset = next((item for item in release["assets"] if item["name"] == name), None)
    if not asset or not str(asset.get("digest", "")).startswith("sha256:"):
        raise VPNError("官方发布缺少适用平台或校验摘要，请手动配置 VPN 核心")
    # The asset API avoids stale cached redirects on github.com release URLs.
    download_url = asset.get("url")
    download = (
        Request(
            download_url + "?download=1",
            headers={"User-Agent": "SEU-Monitor", "Accept": "application/octet-stream"},
        )
        if download_url
        else asset["browser_download_url"]
    )
    with opener.open(download, timeout=30) as response:
        archive = response.read(30 * 1024 * 1024 + 1)
    if (
        len(archive) > 30 * 1024 * 1024
        or hashlib.sha256(archive).hexdigest() != asset["digest"].split(":", 1)[1]
    ):
        raise VPNError("VPN 核心下载校验失败")
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        member = next(
            item for item in zipped.namelist() if Path(item).name == binary.name
        )
        binary.write_bytes(zipped.read(member))
    binary.chmod(0o700)
    provenance.write_text(
        json.dumps(
            {
                "version": RELEASE,
                "source": asset["browser_download_url"],
                "sourceCommit": SOURCE_COMMIT,
                "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            }
        )
    )
    _write_core_notices(directory)
    return binary


def validate_callback(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "vpn.seu.edu.cn"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.path != "/passport/v1/auth/cas"
    ):
        raise ValueError("VPN 回调地址不匹配")
    values = parse_qs(parsed.query)
    if values.get("sfDomain") != ["CAS-auth"] or not values.get("ticket"):
        raise ValueError("VPN 回调缺少正确认证域或票据")
    # v1.3.1 compares Host literally, omitting its default HTTPS port.
    return parsed._replace(netloc="vpn.seu.edu.cn", fragment="").geturl()


def core_command(binary: Path, port: int, directory: Path) -> list[str]:
    if not 1024 <= port <= 65535:
        raise ValueError("VPN_HTTP_PORT 必须在 1024–65535 之间")
    return [
        str(binary),
        "-protocol",
        "atrust",
        "-server",
        "vpn.seu.edu.cn",
        "-port",
        "443",
        "-auth-type",
        "auth/cas",
        "-login-domain",
        "CAS-auth",
        "-disable-zju-config",
        "-remote-dns-server",
        os.environ.get("VPN_DNS_SERVER", "202.119.24.12"),
        "-socks-bind",
        "",
        "-http-bind",
        f"127.0.0.1:{port}",
        "-client-data-file",
        str(directory / "client-data.json"),
    ]


def core_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(
            word in key.upper()
            for word in ("PASSWORD", "API_KEY", "TOKEN", "SECRET", "WEBHOOK")
        )
        and key.upper()
        not in (
            "VPN_USERNAME",
            "ATRUST_USERNAME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        )
        and not key.startswith("ZJU_CONNECT_")
    }
    debug = [
        value
        for value in environment.get("GODEBUG", "").split(",")
        if value and value.split("=", 1)[0] not in ("tlsmlkem", "tlssecpmlkem")
    ]
    environment["GODEBUG"] = ",".join([*debug, "tlsmlkem=0", "tlssecpmlkem=0"])
    return environment


def login_cas(login_url: str, stopped: threading.Event) -> str:
    from .cas import AuthenticationRequired, login

    try:
        return validate_callback(
            login(urljoin(SERVER, login_url), state_directory(), stopped)
        )
    except AuthenticationRequired as error:
        raise ManualAuthenticationRequired(str(error)) from error


def run_service(port: int = 8888) -> int:
    """Foreground process owned by systemd; failed tunnels exit for supervised restart."""
    from .healthcheck import check_vpn_verbose

    stopped = threading.Event()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def request_stop(*_):
        stopped.set()
        if os.environ.get("VPN_INTERACTIVE") == "true":
            raise KeyboardInterrupt

    for sig in previous:
        signal.signal(sig, request_stop)
    process = None
    try:
        directory = state_directory()
        binary = install_core()
        args = core_command(binary, port, directory)
        # Refuse a conflicting proxy before authenticating.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
        with build_opener(ProxyHandler({})).open(
            SERVER + "/public/manifest", timeout=15
        ) as response:
            if json.load(response).get("code") != 0:
                raise VPNError("VPN 服务暂不可用")
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=core_environment(),
        )
        lines = queue.Queue()

        def read_output():
            buffer = ""
            for char in iter(lambda: process.stdout.read(1), ""):
                buffer += char
                if char == "\n" or (buffer.endswith(": ") and "Please enter" in buffer):
                    lines.put(buffer.strip())
                    buffer = ""
            if buffer:
                lines.put(buffer.strip())
            lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = (
            time.monotonic() + int(os.environ.get("VPN_LOGIN_TIMEOUT", "300")) + 60
        )
        connected = False
        next_probe = 0
        proxy = f"http://127.0.0.1:{port}"
        check_url = os.environ.get("VPN_CHECK_URL") or "https://cvs.seu.edu.cn"
        while not stopped.is_set():
            if not connected and time.monotonic() > deadline:
                raise VPNError("VPN 连接超时")
            if connected and time.monotonic() >= next_probe:
                if not check_vpn_verbose(check_url, proxy, 10)[0]:
                    raise VPNError("VPN 数据通道不可用")
                next_probe = time.monotonic() + 60
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                raise VPNError("zju-connect 已退出")
            if "VPN client setup error" in line:
                raise VPNError("VPN 校园隧道建立失败，请检查网络后重新启动 VPN")
            if "Login error" in line:
                raise VPNError("VPN 登录失败，请检查账号、密码和学校认证要求")
            if match := re.search(r"Visit (\S+) to login", line):
                callback = login_cas(match[1], stopped)
                if stopped.is_set():
                    break
                process.stdin.write(callback + "\n")
                process.stdin.flush()
            elif "HTTP server listening" in line:
                ready = False
                for _ in range(3):
                    if check_vpn_verbose(check_url, proxy, 10)[0]:
                        ready = True
                        break
                    if stopped.wait(0.5):
                        break
                if not ready:
                    raise VPNError("VPN 认证完成但数据通道检查失败")
                connected = True
                next_probe = time.monotonic() + 60
                print(f"VPN 已连接，HTTP 代理：{proxy}", flush=True)
            elif "Please enter" in line and "callback" not in line.lower():
                raise ManualAuthenticationRequired("VPN 要求额外验证，请使用交互登录")
            # Never forward raw core output: it may contain CAS tickets or credentials.
        return 0
    except KeyboardInterrupt:
        return 0
    except ManualAuthenticationRequired as error:
        print(str(error), flush=True)
        return 3
    except VPNError as error:
        print(str(error), flush=True)
        return 1
    except Exception as error:
        print(
            f"VPN 启动或连接失败（{type(error).__name__}），请检查配置和网络",
            flush=True,
        )
        return 1
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
