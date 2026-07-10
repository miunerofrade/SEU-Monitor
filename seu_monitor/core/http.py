"""HTTP 请求会话封装。

支持：
- 统一 User-Agent / 超时
- 环境变量代理（HTTP_PROXY / HTTPS_PROXY）
- 调用方传参覆盖代理
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from seu_monitor.core.settings import Settings


def _resolve_proxy() -> Optional[Dict[str, str]]:
    """读取 HTTP_PROXY / HTTPS_PROXY 环境变量（可选）。"""
    http = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    proxies: Dict[str, str] = {}
    if http:
        proxies["http"] = http
    if https:
        proxies["https"] = https
    return proxies if proxies else None


def _default_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }


def new_session(
    timeout: int = 20,
    proxy_override: Optional[Dict[str, str]] = None,
    max_retries: int = 1,
) -> requests.Session:
    """创建一个预配置的 requests.Session。

    Args:
        timeout: 请求超时秒数。
        proxy_override: 调用方指定的代理，会覆盖环境变量代理。
        max_retries: 最大重试次数（连接超时或 502/503 时重试）。
    """
    session = requests.Session()
    session.headers.update(_default_headers())
    session.timeout = timeout  # 作为属性暂存，调用时用 session.timeout

    # 代理：优先用调用方传入的代理，否则用环境变量
    proxies = proxy_override or _resolve_proxy()
    if proxies:
        session.proxies.update(proxies)

    # 重试策略（最多重试 max_retries 次，不激进）
    retry = Retry(
        total=max_retries,
        connect=max_retries,
        status=max_retries,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def do_get(
    session: requests.Session,
    url: str,
    timeout: Optional[int] = None,
) -> requests.Response:
    """封装 GET 请求，自动设置编码。"""
    t = timeout if timeout is not None else getattr(session, "timeout", 20)
    resp = session.get(url, timeout=t)
    resp.encoding = resp.apparent_encoding or "utf-8"
    resp.raise_for_status()
    return resp
