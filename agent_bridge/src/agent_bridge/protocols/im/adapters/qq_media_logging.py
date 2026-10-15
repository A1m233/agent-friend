"""Keep signed upload URLs and SDK response bodies out of application logs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_media_operation: ContextVar[bool] = ContextVar("qq_media_operation", default=False)


class _MediaTransportFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _media_operation.get()


# SDK/http transport diagnostics can include signed URLs on both success and failure.
# Suppress only this async operation; our caller records stable failure codes/receipts.
for _name in (
    "qqbot_agent_sdk.media_loader",
    "qqbot_agent_sdk.api_client",
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
):
    logging.getLogger(_name).addFilter(_MediaTransportFilter())


@contextmanager
def private_media_transport() -> Iterator[None]:
    token = _media_operation.set(True)
    try:
        yield
    finally:
        _media_operation.reset(token)
