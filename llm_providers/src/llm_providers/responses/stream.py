"""Per-response item state, with native phases and project-owned events."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ..error_normalization import map_provider_error
from ..errors import (
    ErrorEvidence,
    LLMContentPolicyError,
    LLMEmptyResponseError,
    LLMError,
    LLMIncompleteResponseError,
    LLMOutputBudgetExhaustedError,
    LLMProviderError,
)
from ..reasoning import build_provider_reasoning_state
from ..request_witness import ProviderRequestWitness
from ..spec import ProviderSpec
from ..stream_events import (
    LLMActivity,
    LLMAssistantItemDone,
    LLMAssistantItemStart,
    LLMResponseDiagnostics,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTransportDiagnostic,
    LLMTurnDone,
    ProviderAssistantItem,
    ProviderAssistantTurn,
    ProviderToolCall,
)
from ..usage import extract_responses_usage
from .evidence import (
    REASONING_EVENTS,
    REFUSAL_EVENTS,
    UNSUPPORTED_EVENTS,
    event_type,
    part_type,
    reference,
    safe_index,
    summarize,
)
from .request import prepare_request
from .transport import open_responses_stream

Phase = Literal["commentary", "final_answer"] | None


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        # SDK union mismatch warnings can include raw response content.
        result = dump(exclude_none=True, warnings=False)
        if isinstance(result, dict):
            return result
    raise _protocol("expected an object")


def _phase(value: Any) -> Phase:
    if value == "commentary":
        return "commentary"
    if value == "final_answer":
        return "final_answer"
    return None


def _protocol(message: str) -> LLMProviderError:
    error = LLMProviderError(f"responses_protocol_error: {message}")
    error.error_evidence = ErrorEvidence(
        source="protocol", category="protocol", code=message.replace(" ", "_")
    )
    return error


class _ResponseFailure(Exception):
    def __init__(self, payload: dict[str, Any], response_id: str | None) -> None:
        super().__init__("Responses provider reported a failure")
        self.error_source = "stream_error" if payload.get("type") == "error" else "failed_terminal"
        nested = payload.get("error")
        error = dict(nested) if isinstance(nested, dict) else dict(payload)
        if error.get("type") == "error":
            error.pop("type")
        self.body = {"error": error}
        self.request_id: str | None = None
        self.response_id = response_id
        self.status_code = error.get("status_code")


@dataclass
class _Item:
    identity: str
    kind: str
    output_index: int
    phase: Phase = None
    tool_index: int = -1
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    texts: dict[int, str] = field(default_factory=dict)
    completed_texts: dict[int, str] = field(default_factory=dict)
    metadata_indices: set[int] = field(default_factory=set)
    snapshot: dict[str, Any] | None = None
    done: bool = False


class ResponsesState:
    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec
        self.items: dict[str, _Item] = {}
        self.indices: dict[int, str] = {}
        self.tool_count = 0
        self.response_id: str | None = None
        self.response_model: str | None = None
        self.terminal: dict[str, Any] | None = None
        self.last_activity = 0.0
        self.event_number = 0
        self.recent_event_types: deque[str] = deque(maxlen=8)
        self.expected_item_type: str | None = None

    def _add(self, raw: dict[str, Any], index: Any) -> tuple[_Item, list[LLMStreamEvent]]:
        identity = raw.get("id")
        kind = raw.get("type")
        if (
            not isinstance(identity, str)
            or not identity
            or not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
        ):
            raise _protocol("invalid item identity")
        if identity in self.items or index in self.indices:
            raise _protocol("duplicate item identity")
        if not isinstance(kind, str) or kind not in {"message", "function_call", "reasoning"}:
            raise _protocol("unsupported output item type")
        item = _Item(identity=identity, kind=kind, output_index=index)
        events: list[LLMStreamEvent] = []
        if kind == "message":
            if raw.get("role") != "assistant":
                raise _protocol("output message must be assistant")
            item.phase = _phase(raw.get("phase"))
            events.append(LLMAssistantItemStart(identity, index, item.phase))
        elif kind == "function_call":
            if (
                not isinstance(raw.get("call_id"), str)
                or not raw["call_id"]
                or not isinstance(raw.get("name"), str)
                or not raw["name"]
            ):
                raise _protocol("invalid function identity")
            if any(other.call_id == raw["call_id"] for other in self.items.values()):
                raise _protocol("duplicate call_id")
            item.call_id, item.name = raw["call_id"], raw["name"]
            item.tool_index = self.tool_count
            self.tool_count += 1
            events.append(
                LLMToolCallDelta(
                    index=item.tool_index, tool_call_id=item.call_id, tool_name=item.name
                )
            )
        self.items[identity] = item
        self.indices[index] = identity
        return item, events

    def _item(self, event: dict[str, Any], kind: str | None = None) -> _Item:
        self.expected_item_type = kind
        identity = event.get("item_id")
        index = event.get("output_index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise _protocol("invalid output index")
        if (
            not isinstance(identity, str)
            or self.indices.get(index) != identity
            or identity not in self.items
        ):
            raise _protocol("delta references an unknown or mismatched item")
        item = self.items[identity]
        if kind is not None and item.kind != kind:
            raise _protocol("delta type does not match its item")
        if item.done:
            raise _protocol("delta after item completion")
        return item

    def _arguments(self, item: _Item, complete: Any) -> list[LLMStreamEvent]:
        if not isinstance(complete, str) or not complete.startswith(item.arguments):
            raise _protocol("function arguments snapshot conflicts with deltas")
        suffix = complete[len(item.arguments) :]
        item.arguments = complete
        return [LLMToolCallDelta(index=item.tool_index, args_json_delta=suffix)] if suffix else []

    def _finish(
        self, raw: dict[str, Any], index: Any, *, response_complete: bool = False
    ) -> list[LLMStreamEvent]:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise _protocol("invalid output index")
        identity = raw.get("id")
        if not isinstance(identity, str) or not identity:
            raise _protocol("invalid item identity")
        events: list[LLMStreamEvent] = []
        if identity not in self.items:
            item, events = self._add(raw, index)
        else:
            item = self.items[identity]
            if self.indices.get(index) != identity or raw.get("type") != item.kind:
                raise _protocol("item completion identity mismatch")
        if raw.get("status") not in (None, "completed"):
            raise _protocol("item did not complete")
        if item.done:
            if response_complete and item.kind == "message" and item.snapshot is not None:
                # Phase is lifecycle metadata. Only the complete response may
                # supersede the item-done phase; other snapshot fields stay strict.
                previous_snapshot = {
                    key: value for key, value in item.snapshot.items() if key != "phase"
                }
                current_snapshot = {key: value for key, value in raw.items() if key != "phase"}
                if current_snapshot != previous_snapshot:
                    raise _protocol("conflicting item completion")
                item.phase = _phase(raw.get("phase"))
                item.snapshot = raw
                return []
            if raw != item.snapshot:
                raise _protocol("conflicting item completion")
            return []
        if item.kind == "message":
            if raw.get("role") != "assistant":
                raise _protocol("output message must be assistant")
            item.phase = _phase(raw.get("phase"))
            content = raw.get("content")
            if not isinstance(content, list):
                raise _protocol("message completion lacks content")
            seen: set[int] = set()
            for content_index, block in enumerate(content):
                if not isinstance(block, dict):
                    raise _protocol("completed content must be an object")
                if block.get("type") == "refusal":
                    raise LLMContentPolicyError("模型拒绝生成此响应。")
                if block.get("type") != "output_text" or not isinstance(block.get("text"), str):
                    raise _protocol("unsupported completed message content")
                text = block["text"]
                if (
                    content_index in item.completed_texts
                    and item.completed_texts[content_index] != text
                ):
                    raise _protocol("text completion conflicts with metadata")
                previous = item.texts.get(content_index, "")
                if not text.startswith(previous):
                    raise _protocol("text snapshot conflicts with deltas")
                if suffix := text[len(previous) :]:
                    events.append(
                        LLMTextDelta(
                            text=suffix,
                            phase=item.phase,
                            item_id=item.identity,
                            output_index=item.output_index,
                        )
                    )
                item.texts[content_index] = text
                seen.add(content_index)
            if (set(item.texts) | set(item.completed_texts) | item.metadata_indices) - seen:
                raise _protocol("text content missing from completion")
            events.append(LLMAssistantItemDone(item.identity, item.output_index, item.phase))
        elif item.kind == "function_call":
            if raw.get("call_id") != item.call_id or raw.get("name") != item.name:
                raise _protocol("function identity changed")
            events.extend(self._arguments(item, raw.get("arguments")))
            try:
                arguments = json.loads(item.arguments)
            except (ValueError, TypeError) as exc:
                raise _protocol("invalid completed function JSON") from exc
            if not isinstance(arguments, dict):
                raise _protocol("function arguments must be an object")
        item.done = True
        item.snapshot = raw
        return events

    def feed(self, raw: Any) -> list[LLMStreamEvent]:
        self.event_number += 1
        self.expected_item_type = None
        event = None
        try:
            event = _object(raw)
            self.recent_event_types.append(event_type(event.get("type")))
            return self._feed(event)
        except LLMProviderError as error:
            if error.error_evidence is not None and error.error_evidence.source == "protocol":
                identity = event.get("item_id") if event else None
                item = self.items.get(identity) if isinstance(identity, str) else None
                index = safe_index(event.get("output_index")) if event else None
                error.error_evidence = replace(
                    error.error_evidence,
                    response_ref=reference(self.response_id),
                    protocol=replace(
                        summarize(
                            event,
                            stage="feed",
                            number=self.event_number,
                            items=len(self.items),
                            terminal=self.terminal is not None,
                        ),
                        schema_revision=2,
                        recent_event_types=tuple(self.recent_event_types),
                        expected_item_type=self.expected_item_type,
                        actual_item_type=item.kind if item else None,
                        part_type=part_type(event),
                        output_index=index,
                        content_index=safe_index(event.get("content_index")) if event else None,
                        item_index_matches=(
                            item is not None and self.indices.get(index) == identity
                            if index is not None and isinstance(identity, str)
                            else None
                        ),
                    ),
                )
            raise

    def _feed(self, event: dict[str, Any]) -> list[LLMStreamEvent]:
        kind = event.get("type", "")
        if not isinstance(kind, str) or not kind:
            raise _protocol("invalid event type")
        if self.terminal is not None:
            raise _protocol("event after terminal response")
        if kind in {"response.created", "response.in_progress", "response.queued"}:
            response = _object(event.get("response"))
            self._identity(response)
        elif kind == "response.output_item.added":
            item, events = self._add(_object(event.get("item")), event.get("output_index"))
            if item.kind == "function_call" and event["item"].get("arguments"):
                events.extend(self._arguments(item, event["item"]["arguments"]))
            return events
        elif kind == "response.output_text.delta":
            item = self._item(event, "message")
            delta, index = event.get("delta"), event.get("content_index")
            if (
                not isinstance(delta, str)
                or not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
            ):
                raise _protocol("invalid text delta")
            if index in item.completed_texts:
                raise _protocol("text delta after content completion")
            item.texts[index] = item.texts.get(index, "") + delta
            return (
                [
                    LLMTextDelta(
                        text=delta,
                        phase=item.phase,
                        item_id=item.identity,
                        output_index=item.output_index,
                    )
                ]
                if delta
                else []
            )
        elif kind == "response.function_call_arguments.delta":
            item = self._item(event, "function_call")
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise _protocol("invalid function delta")
            item.arguments += delta
            return [LLMToolCallDelta(index=item.tool_index, args_json_delta=delta)] if delta else []
        elif kind == "response.function_call_arguments.done":
            return self._arguments(self._item(event, "function_call"), event.get("arguments"))
        elif kind == "response.output_item.done":
            snapshot = _object(event.get("item"))
            if snapshot.get("status") == "incomplete":
                # Preserve the terminal response's usage/reason instead of
                # failing early or executing a partially generated function.
                pending = self._item(
                    {"item_id": snapshot.get("id"), "output_index": event.get("output_index")}
                )
                pending.snapshot, pending.done = snapshot, True
                return []
            return self._finish(snapshot, event.get("output_index"))
        elif kind in {
            "response.completed",
            "response.failed",
            "response.incomplete",
            "response.cancelled",
        }:
            response = _object(event.get("response"))
            self._identity(response)
            if response.get("status") != kind.removeprefix("response."):
                raise _protocol("terminal status mismatch")
            events = []
            if kind == "response.completed":
                output = response.get("output")
                if not isinstance(output, list):
                    raise _protocol("completed response lacks output")
                for index, item in enumerate(output):
                    events.extend(self._finish(_object(item), index, response_complete=True))
                if len(output) != len(self.items):
                    raise _protocol("completed response omits observed items")
            self.terminal = response
            return events
        elif kind == "error":
            raise _ResponseFailure(event, self.response_id)
        elif kind in REASONING_EVENTS:
            self._item(event, "reasoning")
            if time.monotonic() - self.last_activity >= 2:
                self.last_activity = time.monotonic()
                return [LLMActivity(kind="reasoning")]
        elif kind in REFUSAL_EVENTS:
            raise LLMContentPolicyError("模型拒绝生成此响应。")
        elif kind in UNSUPPORTED_EVENTS:
            raise _protocol("unsupported response capability")
        elif kind in {"response.content_part.added", "response.content_part.done"}:
            self._content_part(event)
        elif kind in {
            "response.output_text.done",
            "response.output_text.annotation.added",
        }:
            self._text_metadata(event)
        else:
            raise _protocol("unsupported stream event")
        return []

    def _content_part(self, event: dict[str, Any]) -> None:
        item = self._item(event)
        if item.kind != "reasoning":
            self._text_metadata(event)
            return
        index = event.get("content_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise _protocol("invalid content index")
        part = _object(event.get("part"))
        if part.get("type") != "reasoning_text" or not isinstance(part.get("text"), str):
            raise _protocol("unsupported reasoning content part")
        # Reasoning boundaries are metadata, not assistant text. The completed
        # reasoning item remains the authority for private continuation state,
        # as it is for response.reasoning_text.delta/done.

    def _text_metadata(self, event: dict[str, Any]) -> None:
        item = self._item(event, "message")
        index = event.get("content_index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise _protocol("invalid content index")
        item.metadata_indices.add(index)
        kind = event["type"]
        if kind == "response.output_text.annotation.added":
            annotation_index = event.get("annotation_index")
            if (
                not isinstance(annotation_index, int)
                or isinstance(annotation_index, bool)
                or annotation_index < 0
                or (
                    event.get("annotation") is not None
                    and not isinstance(event["annotation"], dict)
                )
            ):
                raise _protocol("invalid text annotation")
            return
        if kind == "response.output_text.done":
            text = event.get("text")
        else:
            part = _object(event.get("part"))
            if part.get("type") == "refusal":
                raise LLMContentPolicyError("模型拒绝生成此响应。")
            if part.get("type") != "output_text":
                raise _protocol("unsupported content part")
            text = part.get("text")
        if not isinstance(text, str) or not text.startswith(item.texts.get(index, "")):
            raise _protocol("text metadata conflicts with deltas")
        if kind != "response.content_part.added":
            if index in item.completed_texts and item.completed_texts[index] != text:
                raise _protocol("conflicting text metadata completion")
            item.completed_texts[index] = text

    def _identity(self, response: dict[str, Any]) -> None:
        identity = response.get("id")
        if (
            not isinstance(identity, str)
            or not identity
            or self.response_id not in {None, identity}
        ):
            raise _protocol("response identity changed or missing")
        self.response_id = identity
        model = response.get("model")
        if isinstance(model, str):
            self.response_model = model

    def finish(self, diagnostics: LLMResponseDiagnostics) -> LLMTurnDone:
        try:
            return self._finish_response(diagnostics)
        except LLMProviderError as error:
            if error.error_evidence is not None and error.error_evidence.source == "protocol":
                error.error_evidence = replace(
                    error.error_evidence,
                    response_ref=reference(self.response_id),
                    protocol=summarize(
                        {
                            "type": "response." + str(self.terminal.get("status")),
                            "response": self.terminal,
                        }
                        if self.terminal
                        else None,
                        stage="finish",
                        number=self.event_number,
                        items=len(self.items),
                        terminal=self.terminal is not None,
                    ),
                )
            raise

    def _finish_response(self, diagnostics: LLMResponseDiagnostics) -> LLMTurnDone:
        if self.terminal is None:
            incomplete = LLMIncompleteResponseError("missing_terminal", diagnostics=diagnostics)
            incomplete.error_evidence = ErrorEvidence(
                source="protocol",
                category="incomplete",
                code="missing_terminal",
                response_ref=reference(self.response_id),
                protocol=summarize(
                    None,
                    stage="finish",
                    number=self.event_number,
                    items=len(self.items),
                    terminal=False,
                ),
            )
            raise incomplete
        response = self.terminal
        usage = extract_responses_usage(response)
        status = response["status"]
        if status == "failed":
            raise _ResponseFailure(_object(response.get("error")), self.response_id)
        if status != "completed":
            details = response.get("incomplete_details")
            if details is not None and not isinstance(details, dict):
                raise _protocol("invalid incomplete details")
            reason = (details or {}).get("reason", status)
            if reason == "max_output_tokens" and not any(
                item.texts for item in self.items.values()
            ):
                error = LLMOutputBudgetExhaustedError("模型输出预算耗尽，响应未完整完成。")
                error.usage = usage  # type: ignore[attr-defined]
                error.diagnostics = diagnostics  # type: ignore[attr-defined]
                raise error
            raise LLMIncompleteResponseError(str(reason), usage=usage, diagnostics=diagnostics)
        ordered = [self.items[self.indices[index]] for index in sorted(self.indices)]
        text = "".join(item.texts[index] for item in ordered for index in sorted(item.texts))
        calls = tuple(
            ProviderToolCall(
                item.tool_index, item.call_id, item.name, item.arguments, item.output_index
            )
            for item in ordered
            if item.kind == "function_call"
        )
        if not text and not calls:
            raise LLMEmptyResponseError("模型未返回可见正文或工具调用。")
        reasoning = [
            item.snapshot
            for item in ordered
            if item.kind == "reasoning" and item.snapshot is not None
        ]
        state = build_provider_reasoning_state(
            self.spec, reasoning_content="", reasoning_details=reasoning
        )
        return LLMTurnDone(
            stop_reason="tool_use" if calls else "end_turn",
            usage=usage,
            diagnostics=diagnostics,
            reasoning_state=state,
            assistant_turn=ProviderAssistantTurn(
                text or None,
                calls,
                state,
                tuple(
                    ProviderAssistantItem(
                        item.identity,
                        item.output_index,
                        "".join(item.texts[index] for index in sorted(item.texts)),
                        item.phase,
                    )
                    for item in ordered
                    if item.kind == "message"
                ),
            ),
        )


def stream_responses(
    spec: ProviderSpec,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    overrides: dict[str, Any],
    cancel_check: Callable[[], None] | None,
    diagnostic_callback: Callable[[LLMTransportDiagnostic], None] | None,
    diagnostic_attempt: int,
    timeout_seconds: float,
    previous_request_witness: ProviderRequestWitness | None,
    stream_response: bool = True,
) -> Iterator[LLMStreamEvent]:
    request = prepare_request(spec, messages, tools, overrides)
    request["stream"] = stream_response
    request["timeout"] = min(float(request.get("timeout", timeout_seconds)), timeout_seconds)
    started = time.monotonic()
    state = ResponsesState(spec)
    diagnostics = LLMResponseDiagnostics(transport="responses")

    def notify(phase: str) -> None:
        if diagnostic_callback:
            diagnostic_callback(
                LLMTransportDiagnostic(
                    phase=phase,
                    attempt=diagnostic_attempt,
                    attempt_elapsed_ms=int((time.monotonic() - started) * 1000),
                    transport="responses",
                    request_bytes=diagnostics.request_bytes,
                    request_fingerprint=diagnostics.request_fingerprint,
                    request_witness=diagnostics.request_witness,
                    request_relation=diagnostics.request_relation,
                    provider_request_id=diagnostics.provider_request_id,
                    response_id=state.response_id,
                    response_model=state.response_model,
                )
            )

    notify("request_started")
    stream = None
    try:
        if cancel_check:
            cancel_check()
        stream = open_responses_stream(spec, request, cancel_check, timeout_seconds)
        witness = stream.request_witness
        diagnostics = replace(
            diagnostics,
            stream_open_ms=int((time.monotonic() - started) * 1000),
            provider_request_id=stream.request_id,
        )
        if isinstance(witness, ProviderRequestWitness):
            diagnostics = replace(
                diagnostics,
                request_witness=witness,
                request_bytes=witness.request_bytes,
                request_fingerprint=witness.request_fingerprint,
                request_relation=witness.relation_to(previous_request_witness),
                common_prefix_items=witness.common_prefix_items(previous_request_witness),
                first_divergence_index=witness.first_divergence_index(previous_request_witness),
                first_divergence_role=witness.first_divergence_role(previous_request_witness),
                first_divergence_domain=witness.first_divergence_domain(previous_request_witness),
                first_divergence_previous_domain=witness.first_divergence_previous_domain(
                    previous_request_witness
                ),
                first_divergence_current_domain=witness.first_divergence_current_domain(
                    previous_request_witness
                ),
                lost_prefix_items=witness.lost_prefix_items(previous_request_witness),
                contributor_mapping_precision=witness.contributor_mapping_precision,
            )
        notify("stream_opened")
        for event in stream:
            if diagnostics.first_chunk_ms is None:
                diagnostics = replace(
                    diagnostics, first_chunk_ms=int((time.monotonic() - started) * 1000)
                )
                notify("first_chunk")
            normalized = state.feed(event)
            if diagnostics.first_reasoning_delta_ms is None and any(
                isinstance(item, LLMActivity) and item.kind == "reasoning" for item in normalized
            ):
                diagnostics = replace(
                    diagnostics, first_reasoning_delta_ms=int((time.monotonic() - started) * 1000)
                )
                notify("first_reasoning_delta")
            yield from normalized
        if cancel_check:
            cancel_check()
        diagnostics = replace(
            diagnostics, response_id=state.response_id, response_model=state.response_model
        )
        yield state.finish(diagnostics)
    except BaseException as error:
        if cancel_check:
            cancel_check()
        if not isinstance(error, Exception):
            raise
        failed_witness = getattr(error, "request_witness", None)
        if isinstance(failed_witness, ProviderRequestWitness):
            diagnostics = replace(
                diagnostics,
                request_witness=failed_witness,
                request_bytes=failed_witness.request_bytes,
                request_fingerprint=failed_witness.request_fingerprint,
            )
        diagnostics = replace(
            diagnostics, response_id=state.response_id, response_model=state.response_model
        )
        notify("request_failed")
        if isinstance(error, LLMError):
            if error.error_evidence is not None and error.error_evidence.protocol is not None:
                error.error_evidence = replace(
                    error.error_evidence,
                    request_ref=reference(diagnostics.provider_request_id),
                    response_ref=reference(state.response_id),
                )
            error.diagnostics = diagnostics  # type: ignore[attr-defined]
            if not hasattr(error, "usage"):
                error.usage = extract_responses_usage(state.terminal) if state.terminal else None  # type: ignore[attr-defined]
            raise
        if isinstance(error, _ResponseFailure) and stream is not None:
            error.request_id = stream.request_id
        mapped = map_provider_error(error, provider_id=spec.provider_id)
        mapped.diagnostics = diagnostics  # type: ignore[attr-defined]
        mapped.usage = extract_responses_usage(state.terminal) if state.terminal else None  # type: ignore[attr-defined]
        raise mapped from None
    finally:
        if stream is not None:
            stream.close()
