"""Process-local model conversation history for one active session.

The durable :class:`Session` event stream remains authoritative for product data.
This object owns only the exact, process-local model view needed to keep dynamic
context append-only across separately constructed ``Conversation`` instances.
It stores normalized ``Message`` items, never a prepared provider request body.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from llm_providers import ProviderAssistantTurn

from ..messages import Message
from .session import Session

HistoryRelation = Literal["baseline", "append", "rewrite", "unchanged"]


@dataclass(frozen=True)
class HistoryUpdate:
    relation: HistoryRelation
    previous_items: int
    current_items: int
    reason: str | None = None


@dataclass(frozen=True)
class PreparedModelHistory:
    items: tuple[Message, ...]
    update: HistoryUpdate


@dataclass
class SessionModelHistory:
    """Canonical model-visible items for the current process epoch."""

    items: list[Message] = field(default_factory=list)
    active_event_uuids: tuple[str, ...] = ()
    generation: int = 0
    provider_request_count: int = 0
    last_relation: HistoryRelation = "baseline"
    last_reason: str | None = "session_start"

    @classmethod
    def from_session(
        cls, session: Session, *, reason: str = "session_start"
    ) -> SessionModelHistory:
        return cls(
            items=copy.deepcopy(session.llm_messages),
            active_event_uuids=tuple(event.uuid for event in session.active_events),
            last_relation="baseline",
            last_reason=reason,
        )

    def snapshot(self) -> list[Message]:
        return copy.deepcopy(self.items)

    def reconcile(self, session: Session) -> HistoryUpdate:
        """Verify that a freshly loaded business Session still matches this runtime view.

        Normal conversation code updates ``active_event_uuids`` after committing model
        items. Any mismatch here therefore means an external append, edit/resend,
        rollback, or an interrupted partial update. Rebuild conservatively and start a
        new cache generation instead of guessing item placement around model-only data.
        """

        current = tuple(event.uuid for event in session.active_events)
        if current == self.active_event_uuids:
            return HistoryUpdate("unchanged", len(self.items), len(self.items))
        if current[: len(self.active_event_uuids)] == self.active_event_uuids:
            previous = len(self.items)
            suffix_events = session.active_events[len(self.active_event_uuids) :]
            unsafe_append_types = {
                "provider_reasoning_state",
                "tool_call_request",
                "turn_rewrite",
                "compaction",
                "tool_context_edit",
            }
            if not any(event.type in unsafe_append_types for event in suffix_events):
                suffix_uuids = {event.uuid for event in suffix_events}
                existing_uuids = {message.uuid for message in self.items}
                additions = [
                    message
                    for message in session.llm_messages
                    if message.uuid in suffix_uuids and message.uuid not in existing_uuids
                ]
                if additions:
                    self.append(additions)
                self.active_event_uuids = current
                relation: HistoryRelation = "append" if additions else "unchanged"
                self.last_relation = relation
                self.last_reason = None
                return HistoryUpdate(relation, previous, len(self.items))
        previous = len(self.items)
        self.items = copy.deepcopy(session.llm_messages)
        self.active_event_uuids = current
        self.generation += 1
        self.last_relation = "rewrite"
        self.last_reason = "business_history_changed"
        return HistoryUpdate("rewrite", previous, len(self.items), self.last_reason)

    def adopt_prepared(
        self,
        messages: Sequence[Message],
        *,
        stable_system_prompt: str,
        reason: str | None = None,
    ) -> HistoryUpdate:
        """Adopt one canonical model build, excluding the stable system envelope."""

        prepared = self.prepare(
            messages,
            stable_system_prompt=stable_system_prompt,
            reason=reason,
        )
        self.commit(prepared)
        return prepared.update

    def prepare(
        self,
        messages: Sequence[Message],
        *,
        stable_system_prompt: str,
        reason: str | None = None,
    ) -> PreparedModelHistory:
        candidate = list(messages)
        if (
            candidate
            and candidate[0].role == "system"
            and candidate[0].content == stable_system_prompt
        ):
            candidate = candidate[1:]
        candidate = copy.deepcopy(candidate)
        previous = len(self.items)
        if self._is_prefix(self.items, candidate):
            relation: HistoryRelation = "unchanged" if previous == len(candidate) else "append"
        else:
            relation = "rewrite"
        update = HistoryUpdate(
            relation,
            previous,
            len(candidate),
            reason if relation == "rewrite" else None,
        )
        return PreparedModelHistory(tuple(candidate), update)

    def commit(self, prepared: PreparedModelHistory) -> None:
        self.items = copy.deepcopy(list(prepared.items))
        if prepared.update.relation == "rewrite":
            self.generation += 1
        self.last_relation = prepared.update.relation
        self.last_reason = prepared.update.reason

    def append(self, messages: Sequence[Message]) -> None:
        self.items.extend(copy.deepcopy(list(messages)))
        self.last_relation = "append"
        self.last_reason = None

    def append_provider_assistant(
        self,
        assistant: ProviderAssistantTurn,
        *,
        timestamp: datetime,
        event_uuid: str | None = None,
        run_id: str | None = None,
    ) -> Message:
        meta: dict[str, object] = {}
        if assistant.tool_calls:
            meta["tool_calls"] = [
                {
                    "id": item.tool_call_id,
                    "name": item.tool_name,
                    "args": {},
                    "arguments_json": item.arguments_json,
                }
                for item in assistant.tool_calls
            ]
        if assistant.reasoning_state is not None:
            meta["provider_reasoning_state"] = assistant.reasoning_state.to_event_payload()
            if run_id:
                meta["provider_reasoning_run_id"] = run_id
        message = (
            Message(
                role="assistant",
                content=assistant.content or "",
                timestamp=timestamp,
                meta=meta,
                uuid=event_uuid,
            )
            if event_uuid is not None
            else Message(
                role="assistant",
                content=assistant.content or "",
                timestamp=timestamp,
                meta=meta,
            )
        )
        self.append([message])
        return message

    def append_tool_result(
        self,
        *,
        content: str,
        tool_call_id: str,
        tool_name: str,
        timestamp: datetime,
        event_uuid: str,
        is_error: bool,
    ) -> Message:
        message = Message(
            role="tool",
            content=content,
            timestamp=timestamp,
            uuid=event_uuid,
            meta={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "is_error": is_error,
            },
        )
        self.append([message])
        return message

    def note_active_events(self, session: Session) -> None:
        self.active_event_uuids = tuple(event.uuid for event in session.active_events)

    def note_provider_request(self) -> None:
        self.provider_request_count += 1

    @staticmethod
    def _is_prefix(previous: Sequence[Message], current: Sequence[Message]) -> bool:
        if len(previous) > len(current):
            return False
        return all(
            old.uuid == new.uuid and old.to_openai() == new.to_openai()
            for old, new in zip(previous, current, strict=False)
        )


__all__ = [
    "HistoryRelation",
    "HistoryUpdate",
    "PreparedModelHistory",
    "SessionModelHistory",
]
