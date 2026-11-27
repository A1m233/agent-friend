"""Full reads are owned maintenance operations with an explicit, logged purpose."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import suppress
from typing import Literal, cast

from ..events import Event
from ..query import CancelCheck, load_business_session, load_session
from ..session import Session
from ..store import SessionStore
from ..store_types import SessionReadState

logger = logging.getLogger(__name__)
MaintenancePurpose = Literal[
    "attachment_reachability", "tool_transaction_recovery", "persona_editor_snapshot"
]


def maintenance_session(
    store: SessionStore,
    session_id: str,
    *,
    purpose: MaintenancePurpose,
    raw: bool = False,
    cancel_check: CancelCheck = None,
) -> Session:
    session = (load_session if raw else load_business_session)(
        store, session_id, cancel_check=cancel_check
    )
    with suppress(Exception):
        logger.debug(
            "session_maintenance purpose=%s version=%s events=%d",
            purpose,
            session.loaded_source_version,
            len(session.events),
        )
    return session


def maintenance_events(
    store: SessionStore,
    session_id: str,
    state: SessionReadState,
    *,
    purpose: MaintenancePurpose,
    cancel_check: CancelCheck = None,
) -> Iterator[Event]:
    method = getattr(store, "iter_events", None)
    if not callable(method):
        raise NotImplementedError("store does not support streaming maintenance")
    try:
        yield from cast(
            Iterator[Event],
            method(session_id, state, materialize_rollout=False, cancel_check=cancel_check),
        )
    finally:
        with suppress(Exception):
            logger.debug(
                "session_maintenance purpose=%s version=%s events=%d",
                purpose,
                state.source_version,
                state.event_count,
            )
