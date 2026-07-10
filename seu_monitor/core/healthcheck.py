"""VPN/代理健康检查模块。

在访问需要 VPN 的资源前检查代理是否可用。
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import requests

from seu_monitor.core.http import new_session

logger = logging.getLogger(__name__)


def check_vpn(
    check_url: str,
    proxy_url: Optional[str] = None,
    timeout: int = 15,
) -> bool:
    """检查 VPN/代理是否可用（返回简单布尔值）。

    Args:
        check_url: 健康检查 URL（如 https://cvs.seu.edu.cn）。
        proxy_url: 代理地址（如 http://127.0.0.1:8888）。
        timeout: 请求超时秒数。

    Returns:
        True 表示 VPN 可用，False 表示不可用。
    """
    ok, _ = check_vpn_verbose(check_url=check_url, proxy_url=proxy_url, timeout=timeout)
    return ok


def check_vpn_verbose(
    check_url: str,
    proxy_url: Optional[str] = None,
    timeout: int = 15,
) -> Tuple[bool, str]:
    """检查 VPN/代理是否可用（返回详细状态字符串）。

    Returns:
        (True/False, 状态描述字符串)
    """
    if not check_url:
        return True, "VPN_CHECK_URL 未配置，跳过"

    proxies = {}
    if proxy_url:
        proxies["http"] = proxy_url
        proxies["https"] = proxy_url

    session = new_session(timeout=timeout)
    if proxies:
        session.proxies.update(proxies)

    try:
        resp = session.get(check_url, timeout=timeout, verify=False)
        ok = resp.status_code < 500
        if ok:
            msg = f"OK - status={resp.status_code}"
        else:
            msg = f"FAILED - status={resp.status_code}"
        logger.info("VPN 健康检查: %s (%s)", msg, check_url)
        return ok, msg
    except requests.exceptions.ConnectTimeout:
        msg = "FAILED - ConnectTimeout (连接超时)"
        logger.warning("VPN 健康检查失败: %s (%s)", msg, check_url)
        return False, msg
    except requests.exceptions.ProxyError as e:
        msg = f"FAILED - ProxyError ({e})"
        logger.warning("VPN 健康检查失败: %s (%s)", msg, check_url)
        return False, msg
    except requests.exceptions.ConnectionError as e:
        msg = f"FAILED - ConnectionError ({e})"
        logger.warning("VPN 健康检查失败: %s (%s)", msg, check_url)
        return False, msg
    except Exception as e:
        msg = f"FAILED - {type(e).__name__} ({e})"
        logger.warning("VPN 健康检查异常: %s (%s)", msg, check_url)
        return False, msg
