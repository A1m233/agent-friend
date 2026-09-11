"""Validate the pinned exec-server network callback without doing I/O on its reader."""

from __future__ import annotations

import ipaddress
import re
from typing import Any


def valid_network_request(request: object) -> bool:
    if not isinstance(request, dict) or set(request) != {"host", "port", "protocol"}:
        return False
    host, port = request["host"], request["port"]
    if (
        not isinstance(host, str)
        or not 0 < len(host) <= 253
        or type(port) is not int
        or not 0 < port < 65536
        or request["protocol"] not in ("http", "https_connect", "socks5_tcp")
    ):
        return False
    try:
        # Never turn a callback into an exception to private-address policy.
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?", host)) and (
            host.rstrip(".").lower() != "localhost"
        )


def network_decision(allowed: bool) -> dict[str, Any]:
    return {
        "decision": {"type": "allow"}
        if allowed
        else {
            "type": "deny",
            "reason": "command_network_not_authorized",
        }
    }
