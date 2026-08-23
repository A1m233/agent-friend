"""Network safety checks for local webpage and PDF readers."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

from ....tool_runtime.evidence import normalize_url


class UnsafeUrlError(ValueError):
    pass


class UntrustedDnsResolutionError(UnsafeUrlError):
    """The local resolver cannot prove that an authorized hostname is public."""


def validate_public_url(url: str) -> str:
    try:
        normalized = normalize_url(url)
    except ValueError as exc:
        raise UnsafeUrlError(str(exc)) from exc
    host = urlsplit(normalized).hostname
    assert host is not None
    folded_host = host.lower()
    if folded_host == "localhost" or folded_host.endswith(".localhost"):
        raise UnsafeUrlError("localhost is not allowed")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not literal_address.is_global:
            raise UnsafeUrlError("URL contains a non-public IP address")
        return normalized
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise UntrustedDnsResolutionError("host resolution failed") from exc
    if not addresses:
        raise UntrustedDnsResolutionError("host did not resolve")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UntrustedDnsResolutionError("resolved address is invalid") from exc
        if not parsed.is_global:
            raise UntrustedDnsResolutionError("URL resolution includes a non-public address")
    return normalized


def resolve_redirect(current_url: str, location: str) -> str:
    if not location:
        raise UnsafeUrlError("redirect is missing Location")
    try:
        return validate_public_url(urljoin(current_url, location))
    except UntrustedDnsResolutionError as exc:
        raise UnsafeUrlError("redirect target could not be verified as public") from exc
