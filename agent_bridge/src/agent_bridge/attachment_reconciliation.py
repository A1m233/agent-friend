"""Rebuild canonical attachment reachability from append-only session events."""

from __future__ import annotations

import logging
from threading import Event

from agent.sessions.query import load_business_session

from .assembly import BridgeRuntime

logger = logging.getLogger(__name__)


def reconcile_attachments(
    runtime: BridgeRuntime, stopping: Event | None = None
) -> dict[str, int] | None:
    store = runtime.attachment_store
    if store is None:
        return None
    try:
        with store.reference_guard:
            if stopping is not None:
                quick = store.reconcile_if_no_gc_candidates()
                if quick is not None:
                    return quick
            return _reconcile_locked(runtime, stopping)
    except Exception:
        logger.exception("attachment reconciliation failed")
        return None


def _reconcile_locked(runtime: BridgeRuntime, stopping: Event | None) -> dict[str, int] | None:
    store = runtime.attachment_store
    assert store is not None
    manager = runtime.persistent_session_manager
    marked: set[str] = set()

    def check() -> None:
        if stopping is not None and stopping.is_set():
            raise InterruptedError("attachment reconciliation stopped")

    try:
        summaries = [
            *manager.list(archived=False, include_scheduled=True, navigation=True),
            *manager.list(archived=True, include_scheduled=True, navigation=True),
        ]
        for summary in summaries:
            check()
            session = load_business_session(
                runtime.persistent_store, summary.session_id, cancel_check=check
            )
            if session.load_diagnostics.parse_error_count:
                raise ValueError("incomplete session history during attachment mark")
            for event in session.events:
                attachments = [
                    *(event.payload.get("attachments") or []),
                    *(event.payload.get("images") or []),
                ]
                if not isinstance(attachments, list):
                    continue
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    asset_id = attachment.get("asset_id")
                    if isinstance(asset_id, str):
                        marked.add(asset_id)
        if runtime.scheduled_task_store is not None:
            marked.update(runtime.scheduled_task_store.referenced_assets())
        if runtime.session_request_service is not None:
            marked.update(runtime.session_request_service.store.referenced_assets())
        check()
        result = store.reconcile(marked)
        logger.info("attachment reconciliation complete marked=%d result=%s", len(marked), result)
        return result
    except InterruptedError:
        return None
    except Exception:
        logger.exception("attachment reconciliation failed")
        return None
