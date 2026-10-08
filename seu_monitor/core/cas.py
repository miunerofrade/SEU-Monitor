"""SEU CAS HTTP password/SMS flow, adapted from SEUdaily's campus_auth.

No browser. The one-use VPN service ticket is returned without consuming it.
"""

from __future__ import annotations
import base64
import getpass
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.serialization import load_der_public_key
from .http import new_session

AUTH_ROOT = "https://auth.seu.edu.cn/auth/casback"


class AuthenticationRequired(RuntimeError):
    pass


def campus_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ("auth.seu.edu.cn", "vpn.seu.edu.cn")
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise AuthenticationRequired("认证跳转地址不属于学校认证系统")
    return url


def fingerprint(directory):
    path = directory / "device.json"
    if path.is_symlink():
        raise AuthenticationRequired("设备标识文件不能是符号链接")
    if path.exists():
        value = json.loads(path.read_text())["fingerprint"]
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise AuthenticationRequired("设备标识无效，请检查 VPN 数据目录")
        return value
    value = secrets.token_hex(16)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"fingerprint": value}, stream)
    return value


class CASLogin:
    def __init__(
        self, session, username, password, directory, stopped, interactive=False
    ):
        self.session, self.username, self.password = session, username, password
        self.directory, self.stopped, self.interactive = directory, stopped, interactive

    def follow(self, url):
        for _ in range(20):
            if self.stopped.is_set():
                raise AuthenticationRequired("登录已取消")
            campus_url(url)
            parsed = urlsplit(url)
            if (
                parsed.hostname == "vpn.seu.edu.cn"
                and parsed.path == "/passport/v1/auth/cas"
                and parse_qs(parsed.query).get("ticket")
            ):
                return url, None
            response = self.session.get(url, allow_redirects=False, timeout=15)
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers["location"])
                response.close()
                continue
            response.raise_for_status()
            return url, response
        raise AuthenticationRequired("校园认证跳转次数过多")

    def post(self, endpoint, payload):
        response = self.session.post(
            f"{AUTH_ROOT}/{endpoint}",
            json=payload,
            timeout=15,
            headers={
                "Origin": "https://auth.seu.edu.cn",
                "Referer": "https://auth.seu.edu.cn/dist/",
            },
        )
        try:
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise AuthenticationRequired("校园认证接口返回无效数据")
            return value
        finally:
            response.close()

    def encrypt(self, value):
        key = self.post("getChiperKey", {})["publicKey"]
        public = load_der_public_key(
            base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        )
        return base64.b64encode(public.encrypt(value.encode(), PKCS1v15())).decode()

    def authenticate(self, entry):
        submitted = False
        for _ in range(3):
            target, response = self.follow(entry)
            if response is None:
                return target
            response.close()
            parsed = urlsplit(target)
            if parsed.hostname != "auth.seu.edu.cn":
                raise AuthenticationRequired("学校没有返回 CAS 认证地址")
            query = parse_qs(parsed.query or parsed.fragment.partition("?")[2])
            service = query.get("service", [""])[0]
            campus_url(service)
            result = self.post(
                "verifyTgt", {"service": service, "loginType": "account"}
            )
            password_login = not result.get("success")
            if password_login:
                if submitted:
                    raise AuthenticationRequired("认证会话未保持，请重试登录")
                if not self.username or not self.password:
                    raise AuthenticationRequired(
                        "请先用 monitor vpn --account 配置校园账号"
                    )
                captcha = self.session.get(f"{AUTH_ROOT}/needCaptcha", timeout=15)
                try:
                    captcha.raise_for_status()
                    if captcha.json().get("code") == 4000:
                        raise AuthenticationRequired(
                            "学校要求图形验证码；当前 HTTP 登录暂不支持，请稍后重试"
                        )
                finally:
                    captcha.close()
                payload = {
                    "service": service,
                    "username": self.username,
                    "password": self.encrypt(self.password),
                    "captcha": "",
                    "rememberMe": False,
                    "loginType": "account",
                    "wxBinded": False,
                    "mobilePhoneNum": "",
                    "mobileVerifyCode": "",
                    "fingerPrint": fingerprint(self.directory),
                }
                result = self.post("casLogin", payload)
                if result.get("code") == 502:
                    if not self.interactive:
                        raise AuthenticationRequired(
                            "学校要求短信验证，请运行 monitor vpn --interactive"
                        )
                    if (
                        self.post("sendStage2Code", {"userId": self.username}).get(
                            "code"
                        )
                        != 200
                    ):
                        raise AuthenticationRequired("短信发送失败，请稍后重试")
                    print("短信验证码已发送到校园账号绑定手机", flush=True)
                    code = getpass.getpass("短信验证码：").strip()
                    if (
                        not code.isascii()
                        or not code.isdigit()
                        or not 4 <= len(code) <= 16
                    ):
                        raise AuthenticationRequired("请输入短信中的数字验证码")
                    # The school issues a fresh encryption key for stage-two login.
                    key = self.post("getChiperKey", {})["publicKey"]
                    public = load_der_public_key(
                        base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
                    )

                    def encrypt(value):
                        return base64.b64encode(
                            public.encrypt(value.encode(), PKCS1v15())
                        ).decode()

                    result = self.post(
                        "casLogin",
                        {
                            **payload,
                            "password": encrypt(self.password),
                            "mobileVerifyCode": encrypt(code),
                        },
                    )
                submitted = True
                if result.get("code") != 200:
                    raise AuthenticationRequired(
                        "校园认证未通过，请检查账号、密码或验证码"
                    )
            redirect = result.get("redirectUrl")
            if not redirect:
                raise AuthenticationRequired("校园认证未返回业务跳转地址")
            redirect = (
                f"{AUTH_ROOT}/loginRedirect?redirectUrl={redirect}"
                if password_login
                else redirect
            )
            callback, response = self.follow(redirect)
            if response is None:
                return callback
            response.close()
        raise AuthenticationRequired("校园 VPN 认证回调未建立")


def login(entry, directory, stopped):
    with new_session(proxy_override={}) as session:
        return CASLogin(
            session,
            os.environ.get("VPN_USERNAME") or os.environ.get("ATRUST_USERNAME", ""),
            os.environ.get("VPN_PASSWORD") or os.environ.get("ATRUST_PASSWORD", ""),
            directory,
            stopped,
            interactive=os.environ.get("VPN_INTERACTIVE") == "true",
        ).authenticate(entry)
