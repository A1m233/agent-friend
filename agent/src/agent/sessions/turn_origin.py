"""Select a verified model prefix before replacing a business user turn."""

from collections.abc import Callable

from ..messages import Message
from .events import Event
from .model_history import SessionModelHistory, model_relevant_event_uuids
from .rollout import restore_rollout
from .session import Session


def recover_surviving_prefix(session: Session) -> SessionModelHistory | None:
    """Recover legacy failed edits from a verified, still-active rollout prefix.

    Only plain suffix additions are replayed here. Tool/summary mutations require
    their own committed model state; never graft them onto an unrelated prefix.
    """
    current = model_relevant_event_uuids(session)
    cutoff: int | None = None
    prefix_size = 0
    for index, event in enumerate(session.events):
        if event.type != "rollout_commit":
            continue
        identities = event.payload.get("active_event_uuids")
        if (
            isinstance(identities, list)
            and identities
            and tuple(identities) == current[: len(identities)]
        ):
            cutoff = index
            prefix_size = len(identities)
    if cutoff is None:
        return None
    suffix_ids = set(current[prefix_size:])
    safe_types = {
        "user_message",
        "assistant_message",
        "run_error",
        "model_config_change",
        "model_change",
        "persona_change",
        "channel_change",
        "skill_catalog_state",
        "skill_context",
    }
    if any(e.uuid in suffix_ids and e.type not in safe_types for e in session.active_events):
        return None
    restored = restore_rollout(Session.from_events(session.events[: cutoff + 1]))
    if restored.runtime_blocked or restored.commit is None:
        return None
    # This must take the verified unchanged/append branch, not recurse into recovery.
    if restored.history.active_event_uuids != current[:prefix_size]:
        return None
    restored.history.reconcile(session)
    return restored.history


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
