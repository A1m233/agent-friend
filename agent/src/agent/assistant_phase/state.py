"""Pure per-run state; UI expansion is deliberately not a model fact."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from llm_providers.assistant_content import AssistantContentPart

from llm_providers import (
    LLMAssistantItemStart,
    LLMTextDelta,
    LLMTurnDone,
    ProviderAssistantItem,
)

Phase = Literal["commentary", "final_answer", "unknown"]
Validation = Literal["pending", "valid", "missing_final", "conflict"]


def normalize_phase(value: object) -> Phase:
    if value == "commentary":
        return "commentary"
    if value == "final_answer":
        return "final_answer"
    return "unknown"


@dataclass
class TextItem:
    source_id: str | None
    index: int
    phase: Phase
    message_id: str = field(default_factory=lambda: str(uuid4()))
    text: str = ""
    done: bool = False
    persisted: bool = False
    content_parts: tuple[AssistantContentPart, ...] = ()
    is_image: bool = False


@dataclass
class PhaseRun:
    mode: Literal["native", "legacy"]
    run_id: str
    items: dict[str | None, TextItem] = field(default_factory=dict)
    validation: Validation = "pending"
    published: bool = False
    saw_final: bool = False
    conflict: bool = False
    tool_seen: bool = False
    arrival_text: str = ""
    response_offset: int = 0
    visible: bool = True

    def begin_response(self) -> None:
        self.response_offset += max((item.index + 1 for item in self.items.values()), default=0)
        self.items.clear()
        self.arrival_text = ""
        self.tool_seen = False

    def start(self, event: LLMAssistantItemStart) -> None:
        self.items[event.item_id] = TextItem(
            event.item_id, event.output_index, normalize_phase(event.phase)
        )

    def text(self, event: LLMTextDelta) -> tuple[TextItem, bool]:
        key = event.item_id or None
        if key not in self.items:
            self.items[key] = TextItem(
                key,
                event.output_index or 0,
                normalize_phase(event.phase) if event.item_id else "unknown",
            )
        item = self.items[key]
        first = not item.text
        item.text += event.text
        self.arrival_text += event.text
        return item, first

    def ordered_text(self) -> list[TextItem]:
        return sorted((item for item in self.items.values() if item.text), key=lambda i: i.index)

    def provider_items(self) -> tuple[ProviderAssistantItem, ...]:
        return tuple(
            ProviderAssistantItem(
                item.source_id,
                item.index,
                item.text,
                None if item.phase == "unknown" else item.phase,
                item.content_parts,
            )
            for item in self.ordered_text()
            if item.source_id is not None
        )

    def event_ids(self) -> dict[str, str]:
        return {
            item.source_id: item.message_id
            for item in self.ordered_text()
            if item.source_id is not None
        }

    def reconcile_phases(self, event: LLMTurnDone) -> list[TextItem]:
        """The completed response owns final metadata, not the start snapshot."""
        changed: list[TextItem] = []
        if event.assistant_turn is None:
            return changed
        for complete in event.assistant_turn.text_items:
            item = self.items.get(complete.item_id)
            if item is None or item.index != complete.output_index or item.text != complete.content:
                raise ValueError("completed assistant item does not match streamed identity/text")
            phase = normalize_phase(complete.phase)
            item.content_parts = complete.content_parts
            if item.phase != phase:
                item.phase = phase
                changed.append(item)
        return changed

    def finish(self, event: LLMTurnDone) -> None:
        """Validate source order, never delta arrival order or last text position."""
        sequence: list[tuple[int, Phase | None]] = [
            (item.index, item.phase)
            for item in self.ordered_text()
            if item.text.strip() and not item.is_image
        ]
        assistant = event.assistant_turn
        calls = assistant.tool_calls if assistant else ()
        if (calls or self.tool_seen) and any(phase == "final_answer" for _, phase in sequence):
            # Tools execute after the complete response, regardless of item order.
            self.conflict = True
        if calls:
            sequence.extend(
                (
                    call.output_index
                    if call.output_index is not None
                    else len(sequence) + call.index,
                    None,
                )
                for call in calls
            )
        elif self.tool_seen:
            sequence.append((max((i for i, _ in sequence), default=0) + 1, None))
        for _, phase in sorted(sequence, key=lambda item: item[0]):
            if phase == "final_answer":
                self.saw_final = True
            elif self.saw_final:
                self.conflict = True
        # Explicit boundaries must complete; legacy has a response-level boundary.
        if any(not item.done and item.source_id is not None for item in self.ordered_text()):
            self.conflict = True
        image_only = bool(assistant and assistant.image_count and not sequence and not calls)
        self.validation = (
            "conflict"
            if self.conflict
            else "valid"
            if self.saw_final or image_only
            else "missing_final"
        )
