"""voice_bridge 内部 HTTP 连接的网络策略。"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx


def is_loopback_url(url: str) -> bool:
    """判断 HTTP URL 是否指向本机回环地址。"""
    host = urlsplit(url).hostname
    if host is None:
        return False

    normalized_host = host.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        return True

    try:
        return ip_address(normalized_host).is_loopback
    except ValueError:
        return False


def create_http_client(url: str, **kwargs: Any) -> httpx.AsyncClient:
    """为目标 URL 创建客户端；回环流量不继承用户代理环境。"""
    return httpx.AsyncClient(trust_env=not is_loopback_url(url), **kwargs)
