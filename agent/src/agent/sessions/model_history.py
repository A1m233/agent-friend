"""Process-local model conversation history for one active session.

The durable :class:`Session` event stream remains authoritative for product data.
This object owns only the exact, process-local model view needed to keep dynamic
context append-only across separately constructed ``Conversation`` instances.
It stores normalized ``Message`` items, never a prepared provider request body.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from llm_providers import ProviderAssistantTurn

from ..attachments import AttachmentRef
from ..messages import Message
from .session import Session

HistoryRelation = Literal["baseline", "append", "rewrite", "unchanged"]

_MODEL_RELEVANT_EVENT_TYPES = frozenset(
    {
        "session_meta",
        "user_message",
        "assistant_message",
        "persona_change",
        "model_change",
        "model_config_change",
        "channel_change",
        "tool_call_request",
        "tool_call_result",
        "model_output_rejection",
        "compaction",
        "tool_context_edit",
        "system_trigger",
        "turn_rewrite",
        "run_error",
        "todo_reminder",
        "provider_reasoning_state",
        "skill_catalog_state",
        "skill_context",
    }
)


def model_relevant_event_uuids(session: Session) -> tuple[str, ...]:
    """Return business/model facts while excluding rollout bookkeeping itself."""

    return tuple(
        event.uuid for event in session.active_events if event.type in _MODEL_RELEVANT_EVENT_TYPES
    )


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
        return cls.from_business_session(session, reason=reason)

    @classmethod
    def from_business_session(
        cls, session: Session, *, reason: str = "session_start"
    ) -> SessionModelHistory:
        """Build a new-process baseline without persisted model-only items."""

        return cls(
            items=copy.deepcopy(session.business_llm_messages),
            active_event_uuids=model_relevant_event_uuids(session),
            last_relation="baseline",
            last_reason=reason,
        )

    @classmethod
    def from_persisted(
        cls,
        items: Sequence[Message],
        *,
        active_event_uuids: Sequence[str],
        generation: int,
        provider_request_count: int,
        reason: str,
    ) -> SessionModelHistory:
        return cls(
            items=copy.deepcopy(list(items)),
            active_event_uuids=tuple(active_event_uuids),
            generation=generation,
            provider_request_count=provider_request_count,
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

        current_events = [
            event for event in session.active_events if event.type in _MODEL_RELEVANT_EVENT_TYPES
        ]
        current = tuple(event.uuid for event in current_events)
        if current == self.active_event_uuids:
            return HistoryUpdate("unchanged", len(self.items), len(self.items))
        if current[: len(self.active_event_uuids)] == self.active_event_uuids:
            previous = len(self.items)
            suffix_events = current_events[len(self.active_event_uuids) :]
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
                    for message in session.business_llm_messages
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
        self.items = copy.deepcopy(session.business_llm_messages)
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
        item_event_uuids: Mapping[str, str] | None = None,
    ) -> Message:
        if assistant.text_items:
            group_id = str(uuid4())
            ordered: list[tuple[int, ProviderAssistantTurn, str | None, str | None]] = [
                (
                    item.output_index,
                    ProviderAssistantTurn(item.content),
                    (item_event_uuids or {}).get(item.item_id),
                    item.phase,
                )
                for item in assistant.text_items
                if item.content
            ]
            ordered.extend(
                (
                    call.output_index
                    if call.output_index is not None
                    else max((item.output_index for item in assistant.text_items), default=0) + 1,
                    ProviderAssistantTurn(None, (call,)),
                    None,
                    None,
                )
                for call in assistant.tool_calls
            )
            first: Message | None = None
            for _, part, identity, phase in sorted(ordered, key=lambda entry: entry[0]):
                message = self.append_provider_assistant(
                    part,
                    timestamp=timestamp,
                    event_uuid=identity,
                    run_id=run_id,
                )
                # append() owns a copy: set the same metadata on both returned
                # and stored objects before any serialization/checkpoint.
                extra: dict[str, object] = {
                    "assistant_response_id": group_id,
                    "phase": phase or "unknown",
                }
                if first is None and assistant.reasoning_state is not None:
                    extra["provider_reasoning_state"] = assistant.reasoning_state.to_event_payload()
                    if run_id:
                        extra["provider_reasoning_run_id"] = run_id
                message.meta.update(extra)
                self.items[-1].meta.update(copy.deepcopy(extra))
                if first is None:
                    first = message
            if first is not None:
                return first
        meta: dict[str, object] = {}
        if run_id:
            meta["provider_run_id"] = run_id
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
        images: tuple[AttachmentRef, ...] = (),
        run_id: str | None = None,
    ) -> Message:
        meta: dict[str, object] = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "is_error": is_error,
        }
        if run_id:
            meta["provider_run_id"] = run_id
        message = Message(
            role="tool",
            content=content,
            images=images,
            timestamp=timestamp,
            uuid=event_uuid,
            meta=meta,
        )
        self.append([message])
        return message

    def insert_tool_result_after_call(
        self,
        *,
        content: str,
        tool_call_id: str,
        tool_name: str,
        timestamp: datetime,
        event_uuid: str,
        is_error: bool,
        images: tuple[AttachmentRef, ...] = (),
        run_id: str | None = None,
    ) -> Message:
        """Insert a repaired result next to its call instead of at the history tail."""

        meta: dict[str, object] = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "is_error": is_error,
        }
        if run_id:
            meta["provider_run_id"] = run_id
        message = Message(
            role="tool",
            content=content,
            images=images,
            timestamp=timestamp,
            uuid=event_uuid,
            meta=meta,
        )
        original_items = self.items
        existing = [item for item in original_items if item.uuid == event_uuid]
        self.items = [item for item in original_items if item.uuid != event_uuid]
        insertion_index = self._tool_result_insertion_index(tool_call_id, run_id=run_id)
        if insertion_index is None:
            insertion_index = len(self.items)
        self.items.insert(insertion_index, message)
        if self.items == original_items:
            return existing[0] if existing else message
        if not existing and insertion_index == len(original_items):
            self.last_relation = "append"
            self.last_reason = None
            return message
        self.generation += 1
        self.last_relation = "rewrite"
        self.last_reason = "tool_result_history_repair"
        return message

    def unmatched_tool_calls(self) -> tuple[tuple[str, str, str | None], ...]:
        """Return calls that would reach a non-tool boundary without a result."""

        missing: list[tuple[str, str, str | None]] = []
        pending: dict[str, tuple[str, str | None]] = {}
        pending_group: object = None
        for message in self.items:
            if message.role == "tool":
                tool_call_id = message.meta.get("tool_call_id")
                if isinstance(tool_call_id, str):
                    pending.pop(tool_call_id, None)
                continue
            group = message.meta.get("assistant_response_id")
            if pending and not (message.role == "assistant" and group and group == pending_group):
                missing.extend(
                    (tool_call_id, tool_name, run_id)
                    for tool_call_id, (tool_name, run_id) in pending.items()
                )
                pending = {}
            pending_group = group
            if message.role != "assistant":
                continue
            raw_calls = message.meta.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict):
                    continue
                tool_call_id = raw_call.get("id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    continue
                tool_name = raw_call.get("name")
                raw_run_id = (
                    message.meta.get("provider_run_id")
                    or message.meta.get("provider_reasoning_run_id")
                    or message.meta.get("run_id")
                )
                run_id = raw_run_id if isinstance(raw_run_id, str) and raw_run_id else None
                pending[tool_call_id] = (
                    tool_name if isinstance(tool_name, str) else "",
                    run_id,
                )
        missing.extend(
            (tool_call_id, tool_name, run_id)
            for tool_call_id, (tool_name, run_id) in pending.items()
        )
        return tuple(missing)

    @staticmethod
    def validate_provider_tool_history(messages: Sequence[Mapping[str, Any]]) -> None:
        """Fail locally when a provider message list violates tool pairing rules."""

        pending: set[str] = set()
        pending_group: object = None
        for index, message in enumerate(messages):
            role = message.get("role")
            if role == "tool":
                tool_call_id = message.get("tool_call_id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    raise ValueError(
                        "model_history_tool_transaction_invalid: "
                        f"tool message at index {index} has no tool_call_id"
                    )
                if tool_call_id not in pending:
                    raise ValueError(
                        "model_history_tool_transaction_invalid: "
                        f"orphan tool result {tool_call_id!r} at index {index}"
                    )
                pending.remove(tool_call_id)
                continue
            group = message.get("_assistant_response_id")
            if pending and not (role == "assistant" and group and group == pending_group):
                raise ValueError(
                    "model_history_tool_transaction_invalid: missing tool results "
                    f"{sorted(pending)!r} before message index {index}"
                )
            pending_group = group
            if role != "assistant":
                continue
            raw_calls = message.get("tool_calls")
            if not isinstance(raw_calls, list) or not raw_calls:
                continue
            call_ids: list[str] = []
            for raw_call in raw_calls:
                call_id = raw_call.get("id") if isinstance(raw_call, Mapping) else None
                if not isinstance(call_id, str) or not call_id:
                    # Unbindable model output is represented by a separate
                    # model_output_rejection fact and never starts a tool transaction.
                    continue
                call_ids.append(call_id)
            if not call_ids:
                continue
            if len(call_ids) != len(set(call_ids)) or pending.intersection(call_ids):
                raise ValueError(
                    "model_history_tool_transaction_invalid: duplicate tool call ids "
                    f"at message index {index}"
                )
            pending.update(call_ids)
        if pending:
            raise ValueError(
                "model_history_tool_transaction_invalid: missing tool results "
                f"{sorted(pending)!r} at end of history"
            )

    def _tool_result_insertion_index(self, tool_call_id: str, *, run_id: str | None) -> int | None:
        fallback_index: int | None = None
        for index in range(len(self.items) - 1, -1, -1):
            message = self.items[index]
            if message.role != "assistant":
                continue
            raw_calls = message.meta.get("tool_calls")
            if not isinstance(raw_calls, list) or not any(
                isinstance(raw_call, dict) and raw_call.get("id") == tool_call_id
                for raw_call in raw_calls
            ):
                continue
            message_run_id = (
                message.meta.get("provider_run_id")
                or message.meta.get("provider_reasoning_run_id")
                or message.meta.get("run_id")
            )
            if fallback_index is None:
                fallback_index = index
            if run_id and message_run_id != run_id:
                continue
            insertion_index = index + 1
            group = message.meta.get("assistant_response_id")
            while insertion_index < len(self.items) and (
                self.items[insertion_index].role == "tool"
                or (
                    group and self.items[insertion_index].meta.get("assistant_response_id") == group
                )
            ):
                insertion_index += 1
            return insertion_index
        if fallback_index is None:
            return None
        insertion_index = fallback_index + 1
        group = self.items[fallback_index].meta.get("assistant_response_id")
        while insertion_index < len(self.items) and (
            self.items[insertion_index].role == "tool"
            or (group and self.items[insertion_index].meta.get("assistant_response_id") == group)
        ):
            insertion_index += 1
        return insertion_index

    def note_active_events(self, session: Session) -> None:
        self.active_event_uuids = model_relevant_event_uuids(session)

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
    "model_relevant_event_uuids",
]
