"""Rebuild canonical attachment reachability from append-only session events."""

from __future__ import annotations

import logging

from .assembly import BridgeRuntime

logger = logging.getLogger(__name__)


def reconcile_attachments(runtime: BridgeRuntime) -> dict[str, int] | None:
    store = runtime.attachment_store
    if store is None:
        return None
    marked: set[str] = set()
    manager = runtime.persistent_session_manager
    try:
        summaries = [*manager.list(archived=False), *manager.list(archived=True)]
        for summary in summaries:
            session = manager.open(summary.session_id)
            for event in session.events:
                attachments = event.payload.get("attachments", [])
                if not isinstance(attachments, list):
                    continue
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    asset_id = attachment.get("asset_id")
                    if isinstance(asset_id, str):
                        marked.add(asset_id)
        result = store.reconcile(marked)
        logger.info("attachment reconciliation complete marked=%d result=%s", len(marked), result)
        return result
    except Exception:
        logger.exception("attachment reconciliation failed")
        return None
