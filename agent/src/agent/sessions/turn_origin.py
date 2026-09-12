"""Select a verified model prefix before replacing a business user turn."""

from collections.abc import Callable

from ..messages import Message
from .events import Event
from .model_history import model_relevant_event_uuids
from .rollout import restore_rollout
from .session import Session


def history_before_rewrite(
    session: Session,
    target_user_uuid: str,
    prefix_events: list[Event],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> list[Message]:
    """Select a committed prefix using business identities, then verify its rollout.

    active_events intentionally omits the model journal. Read raw events for the
    journal, but never select a commit that contains an invalidated business turn.
    Rewrite markers do not themselves add model content: their effect is already
    represented in prefix_events. Other changes still pass through reconcile.
    """
    if not prefix_events:
        return []
    prefix = Session.from_events([event for event in prefix_events if event.type != "turn_rewrite"])
    allowed = model_relevant_event_uuids(prefix)
    marker_ids = {event.uuid for event in session.events if event.type == "turn_rewrite"}
    cutoff: int | None = None
    for index, event in enumerate(session.events):
        if cancel_check is not None:
            cancel_check()
        if event.uuid == target_user_uuid:
            break
        if event.type != "rollout_commit":
            continue
        identities = event.payload.get("active_event_uuids")
        if not isinstance(identities, list) or any(
            not isinstance(item, str) for item in identities
        ):
            continue
        relevant = tuple(item for item in identities if item not in marker_ids)
        if relevant == allowed[: len(relevant)]:
            cutoff = index
    if cutoff is None:
        return prefix.llm_messages
    restored = restore_rollout(
        Session.from_events(session.events[: cutoff + 1]), cancel_check=cancel_check
    )
    if restored.runtime_blocked:
        return prefix.llm_messages
    restored.history.active_event_uuids = tuple(
        item for item in restored.history.active_event_uuids if item not in marker_ids
    )
    restored.history.reconcile(prefix)
    return restored.history.snapshot()
