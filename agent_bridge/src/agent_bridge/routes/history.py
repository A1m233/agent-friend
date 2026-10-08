"""Bounded browsing only; disconnects never cancel an Agent run."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from threading import Event
from time import perf_counter
from typing import Any, cast

from agent.sessions.file_observation import session_request_id
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request

from agent import SessionNotFoundError

from ..assembly import BridgeRuntime

logger = logging.getLogger(__name__)


class HistoryReadCancelled(Exception):
    pass


async def _read(request: Request, operation: Callable[[Callable[[], None]], Any]) -> Any:
    released = Event()

    def check() -> None:
        if released.is_set():
            raise HistoryReadCancelled()

    work = asyncio.create_task(asyncio.to_thread(operation, check))
    try:
        while not work.done():
            if await request.is_disconnected():
                released.set()
                raise HTTPException(status_code=499, detail={"error": "cancelled"})
            await asyncio.wait({work}, timeout=0.1)
        return work.result()
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "history_changed",
                "message": "历史已更新，请重新打开消息。",
            },
        ) from exc
    finally:
        released.set()
        # The worker cooperatively stops at the index boundary. Retrieve its
        # eventual exception without waiting on unrelated requests or tasks.
        if not work.done():
            work.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/sessions", tags=["history"])

    def method(name: str) -> Callable[..., Any]:
        target = getattr(runtime.persistent_store, name, None)
        if not callable(target):
            raise HTTPException(status_code=503, detail="Bounded history unavailable")
        return cast(Callable[..., Any], target)

    @router.get("/{session_id}/history")
    async def history(
        session_id: str,
        request: Request,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
        epoch: str | None = None,
        message_id: str | None = None,
        item_limit: int = Query(80, ge=1, le=80),
        byte_limit: int = Query(262144, ge=8192, le=262144),
    ) -> dict[str, Any]:
        if sum(value is not None for value in (before, after, around)) > 1:
            raise HTTPException(status_code=400, detail="Choose one history direction")
        started = perf_counter()
        load = method("history_page")
        page = await _read(
            request,
            lambda check: load(
                session_id,
                before=before,
                after=after,
                around=around,
                epoch=epoch,
                message_id=message_id,
                item_limit=item_limit,
                byte_limit=byte_limit,
                cancel_check=check,
            ),
        )
        page = dict(page)
        page["history_items"] = page.pop("events", [])
        page["history_cursor"] = page.get("before_cursor") if page.get("has_before") else None
        page["revision"] = runtime.persistent_session_manager.current_revision(session_id)
        if runtime.approval_broker is not None:
            page.update(runtime.approval_broker.mode_state(session_id).to_dict())
        logger.info(
            "history_page request_id=%s direction=%s items=%d total_ms=%.3f",
            session_request_id.get(),
            "around" if around else "before" if before else "after" if after else "latest",
            len(page["history_items"]),
            (perf_counter() - started) * 1000,
        )
        return page

    @router.get("/{session_id}/history/content")
    async def content(
        session_id: str,
        request: Request,
        ref: str,
        version: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(65536, ge=256, le=65536),
    ) -> dict[str, Any]:
        load = method("history_content")
        result = await _read(
            request,
            lambda check: load(
                session_id,
                ref,
                version=version,
                offset=offset,
                limit=limit,
                cancel_check=check,
            ),
        )
        return dict(result)

    app.include_router(router)
