"""Network safety checks for the local PDF fallback."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin

from ....tool_runtime.context import normalize_url


class UnsafeUrlError(ValueError):
    pass


def validate_public_url(url: str) -> str:
    try:
        normalized = normalize_url(url)
    except ValueError as exc:
        raise UnsafeUrlError(str(exc)) from exc
    from urllib.parse import urlsplit

    host = urlsplit(normalized).hostname
    assert host is not None
    if host.lower() == "localhost":
        raise UnsafeUrlError("localhost is not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise UnsafeUrlError("host resolution failed") from exc
    if not addresses:
        raise UnsafeUrlError("host did not resolve")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeUrlError("resolved address is invalid") from exc
        if not parsed.is_global:
            raise UnsafeUrlError("URL resolves to a non-public address")
    return normalized


def resolve_redirect(current_url: str, location: str) -> str:
    if not location:
        raise UnsafeUrlError("redirect is missing Location")
    return validate_public_url(urljoin(current_url, location))
