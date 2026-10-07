"""Rebuild canonical attachment reachability from append-only session events."""

from __future__ import annotations

import logging

from .assembly import BridgeRuntime

logger = logging.getLogger(__name__)


def reconcile_attachments(runtime: BridgeRuntime) -> dict[str, int] | None:
    store = runtime.attachment_store
    if store is None:
        return None
    try:
        with store.reference_guard:
            return _reconcile_locked(runtime)
    except Exception:
        logger.exception("attachment reconciliation failed")
        return None


def _reconcile_locked(runtime: BridgeRuntime) -> dict[str, int] | None:
    store = runtime.attachment_store
    assert store is not None
    manager = runtime.persistent_session_manager
    marked: set[str] = set()
    try:
        summaries = [
            *manager.list(archived=False, include_scheduled=True),
            *manager.list(archived=True, include_scheduled=True),
        ]
        for summary in summaries:
            session = manager.query(summary.session_id)
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
        result = store.reconcile(marked)
        logger.info("attachment reconciliation complete marked=%d result=%s", len(marked), result)
        return result
    except Exception:
        logger.exception("attachment reconciliation failed")
        return None
