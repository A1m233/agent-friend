"""Content-free HTTP timing correlated with synchronous session-store work."""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from time import perf_counter
from uuid import uuid4

from agent.sessions.file_observation import session_request_id
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RequestObservationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        navigation_id = next(
            (
                value.decode("ascii")
                for key, value in scope.get("headers", [])
                if key == b"x-agent-friend-navigation-id"
                and re.fullmatch(rb"[a-zA-Z0-9_-]{1,64}", value)
            ),
            None,
        )
        token = session_request_id.set(request_id)
        started = perf_counter()
        response_started: float | None = None
        status = 0
        size = 0
        outcome = "success"

        async def observed_send(message: Message) -> None:
            nonlocal response_started, status, size
            if message["type"] == "http.response.start":
                response_started = perf_counter()
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-agent-friend-request-id", request_id.encode("ascii")),
                    ],
                }
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            finished = perf_counter()
            session_request_id.reset(token)
            with suppress(Exception):
                # Use the registered template, never the user-supplied URL or query.
                route = getattr(scope.get("route"), "path", "unmatched")
                logger.log(
                    logging.INFO
                    if navigation_id is not None
                    or finished - started >= 0.1
                    or outcome != "success"
                    else logging.DEBUG,
                    "http_request request_id=%s navigation_id=%s method=%s route=%s status=%s started=%.6f "
                    "response_started=%s finished=%.6f total_ms=%.3f bytes=%s outcome=%s",
                    request_id,
                    navigation_id,
                    scope.get("method"),
                    route,
                    status,
                    started,
                    response_started,
                    finished,
                    (finished - started) * 1000,
                    size,
                    outcome,
                )
