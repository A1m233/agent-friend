"""Opt-in pytest guard: real HTTP transports may only reach loopback.

Used when testing provider routing changes so an obsolete Chat-only mock cannot
accidentally send a real Responses request. HTTPX MockTransport remains usable.
"""

from __future__ import annotations

from typing import Any

import httpcore
import pytest


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch: pytest.MonkeyPatch) -> None:
    # RESPX intercepts pool.handle_request, below HTTPX's transport. Guard
    # connection creation instead, so mocks resolve first. Check the requested
    # origin even when the configured HTTP proxy happens to be loopback.
    def guard(factory: Any) -> Any:
        def create_connection(pool: Any, origin: Any) -> Any:
            if origin.host not in {b"localhost", b"127.0.0.1", b"::1"}:
                pytest.fail(f"Unmocked external HTTP blocked: {origin.host!r}")
            return factory(pool, origin)

        return create_connection

    for pool in (
        httpcore.ConnectionPool,
        httpcore.AsyncConnectionPool,
        httpcore.HTTPProxy,
        httpcore.AsyncHTTPProxy,
    ):
        monkeypatch.setattr(pool, "create_connection", guard(pool.create_connection))
