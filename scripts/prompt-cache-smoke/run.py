"""Real prompt-cache A/B for Kimi layout and DeepSeek prefix persistence."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from agent.tools.builtin.datetime_calculate import DateTimeCalculateTool
from agent_bridge.runtime_config import resolve_bridge_runtime_config

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PersonaNotFoundError,
    Session,
    SessionManager,
    ToolRegistry,
    personas_dir,
    sessions_dir,
)
from llm_providers import (
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
    ModelCallControl,
    ModelCallTraceContext,
    ModelRequest,
    ProviderRequestWitness,
    default_model_runtime_config,
)
from runtime_config import credential_source_for_mode


@dataclass(frozen=True)
class _Observation:
    scenario: str
    phase: str
    prompt_tokens: int
    cached_prompt_tokens: int | None
    cache_miss_prompt_tokens: int | None
    cached_prompt_tokens_source: str | None
    cache_miss_prompt_tokens_source: str | None
    request_fingerprint: str | None
    first_text_ms: float | None
    total_ms: float
    request_relation: str = "unavailable"
    common_prefix_items: int | None = None
    cache_write_prompt_tokens: int | None = None
    input_other_prompt_tokens: int | None = None
    cache_write_prompt_tokens_source: str | None = None
    input_other_prompt_tokens_source: str | None = None
    input_token_accounting: str = "unavailable"
    prompt_cache_observation: dict[str, Any] | None = None


@dataclass(frozen=True)
class _ToolTurnResult:
    observation: _Observation
    assistant_messages: list[dict[str, Any]]
    tool_message: dict[str, Any]
    request_witness: ProviderRequestWitness | None


@dataclass(frozen=True)
class _LongSessionStats:
    session_ref: str
    history_chars: int
    history_messages: int
    complete_turns: int
    tool_events: int
    attachments: int


@dataclass
class _MutableTimeProvider:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _client_for_model(runtime_config: Any, model: str) -> Any:
    return runtime_config.llm_client_factory(default_model_runtime_config(model))


def _history(marker: str, target_chars: int) -> list[dict[str, str]]:
    unit = f"[{marker}] 稳定的长会话背景资料，用于验证共同前缀缓存。"
    content = (unit * (target_chars // len(unit) + 1))[:target_chars]
    chunk_size = max(1, target_chars // 6)
    chunks = [content[index : index + chunk_size] for index in range(0, len(content), chunk_size)]
    messages: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks):
        messages.extend(
            [
                {"role": "user", "content": f"历史资料 {index}: {chunk}"},
                {"role": "assistant", "content": f"已记录历史资料 {index}。"},
            ]
        )
    return messages


def _messages(
    *,
    marker: str,
    history_chars: int,
    memory_variant: str,
    runtime_variant: str,
    dynamic_memory_before_history: bool,
) -> list[dict[str, str]]:
    stable_memory = f"[{marker}] 稳定记忆：用户偏好简洁回答。"
    memory = (
        f"[{marker}] 动态记忆快照：{memory_variant}"
        if dynamic_memory_before_history
        else stable_memory
    )
    return [
        {
            "role": "system",
            "content": f"[{marker}] 缓存实验。忽略资料内容，只回复 OK。",
        },
        {"role": "system", "content": memory},
        *_history(marker, history_chars),
        {
            "role": "system",
            "content": f'<runtime_now at="{runtime_variant}" tz="Asia/Shanghai" />',
        },
        {"role": "user", "content": "只回复 OK。"},
    ]


def _request(
    client: Any,
    *,
    scenario: str,
    phase: str,
    messages: list[dict[str, str]],
    prompt_cache_key: str | None,
    max_tokens: int = 128,
    tools: list[dict[str, Any]] | None = None,
) -> _Observation:
    overrides: dict[str, Any] = {"max_tokens": max_tokens}
    if prompt_cache_key is not None:
        overrides["prompt_cache_key"] = prompt_cache_key
    started = time.perf_counter()
    first_text_ms: float | None = None
    usage: LLMUsage | None = None
    request_fingerprint: str | None = None
    request_relation = "unavailable"
    common_prefix_items: int | None = None
    prompt_cache_observation: dict[str, Any] | None = None
    request = ModelRequest(
        messages=messages,
        tools=tools,
        settings=overrides,
        cache_key=prompt_cache_key,
        control=ModelCallControl(
            trace_context=ModelCallTraceContext(
                operation="prompt_cache_smoke",
                call_kind=f"{scenario}:{phase}",
            )
        ),
    )
    for event in client.stream(request):
        if isinstance(event, LLMTextDelta) and first_text_ms is None:
            first_text_ms = (time.perf_counter() - started) * 1000
        elif isinstance(event, LLMTurnDone):
            usage = event.usage
            request_fingerprint = (
                event.diagnostics.request_fingerprint if event.diagnostics is not None else None
            )
            request_relation = (
                event.diagnostics.request_relation
                if event.diagnostics is not None
                else "unavailable"
            )
            common_prefix_items = (
                event.diagnostics.common_prefix_items if event.diagnostics is not None else None
            )
            prompt_cache_observation = (
                dict(event.diagnostics.cache_observation)
                if event.diagnostics is not None and event.diagnostics.cache_observation is not None
                else None
            )
    total_ms = (time.perf_counter() - started) * 1000
    if usage is None:
        raise AssertionError(f"{scenario}/{phase}: provider did not return stream usage")
    return _Observation(
        scenario=scenario,
        phase=phase,
        prompt_tokens=usage.prompt_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        cache_write_prompt_tokens=usage.cache_write_prompt_tokens,
        input_other_prompt_tokens=usage.input_other_prompt_tokens,
        cache_miss_prompt_tokens=usage.cache_miss_prompt_tokens,
        cached_prompt_tokens_source=usage.cached_prompt_tokens_source,
        cache_write_prompt_tokens_source=usage.cache_write_prompt_tokens_source,
        input_other_prompt_tokens_source=usage.input_other_prompt_tokens_source,
        cache_miss_prompt_tokens_source=usage.cache_miss_prompt_tokens_source,
        input_token_accounting=usage.input_token_accounting,
        request_fingerprint=request_fingerprint,
        first_text_ms=round(first_text_ms, 1) if first_text_ms is not None else None,
        total_ms=round(total_ms, 1),
        request_relation=request_relation,
        common_prefix_items=common_prefix_items,
        prompt_cache_observation=prompt_cache_observation,
    )


def _tool_turn_request(
    client: Any,
    *,
    scenario: str,
    phase: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    previous_request_witness: ProviderRequestWitness | None = None,
) -> _ToolTurnResult:
    """Run one forced real tool turn and retain its replayable provider transcript."""

    started = time.perf_counter()
    first_text_ms: float | None = None
    text_parts: list[str] = []
    tool_calls: dict[int, dict[str, str]] = {}
    usage: LLMUsage | None = None
    turn_done: LLMTurnDone | None = None
    tool_name = "cache_probe_tool_00"
    request = ModelRequest(
        messages=messages,
        tools=tools,
        settings={
            "max_tokens": 256,
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        },
        control=ModelCallControl(
            previous_request_witness=previous_request_witness,
            trace_context=ModelCallTraceContext(
                operation="prompt_cache_smoke",
                call_kind=f"{scenario}:{phase}",
            ),
        ),
    )
    for event in client.stream(request):
        if isinstance(event, LLMTextDelta):
            if first_text_ms is None:
                first_text_ms = (time.perf_counter() - started) * 1000
            text_parts.append(event.text)
        elif isinstance(event, LLMToolCallDelta):
            slot = tool_calls.setdefault(
                event.index,
                {"id": "", "name": "", "arguments": ""},
            )
            if event.tool_call_id:
                slot["id"] = event.tool_call_id
            if event.tool_name:
                slot["name"] = event.tool_name
            slot["arguments"] += event.args_json_delta
        elif isinstance(event, LLMTurnDone):
            usage = event.usage
            turn_done = event

    total_ms = (time.perf_counter() - started) * 1000
    if usage is None or turn_done is None:
        raise AssertionError(f"{scenario}/{phase}: provider did not return stream usage")
    if turn_done.stop_reason != "tool_use" or set(tool_calls) != {0}:
        raise AssertionError(
            f"{scenario}/{phase}: expected exactly one forced tool call, "
            f"got stop_reason={turn_done.stop_reason!r} indexes={sorted(tool_calls)}"
        )
    call = tool_calls[0]
    if not call["id"] or call["name"] != tool_name:
        raise AssertionError(f"{scenario}/{phase}: provider returned an invalid forced tool call")
    decoded_args = json.loads(call["arguments"])
    if not isinstance(decoded_args, dict):
        raise AssertionError(f"{scenario}/{phase}: tool arguments were not a JSON object")

    assistant_turn = turn_done.assistant_turn
    if assistant_turn is None:
        raise AssertionError(
            f"{scenario}/{phase}: provider did not freeze completed assistant turn"
        )
    assistant_messages: list[dict[str, Any]] = [
        dict(item) for item in assistant_turn.to_openai_messages()
    ]
    tool_message = {
        "role": "tool",
        "tool_call_id": call["id"],
        "content": json.dumps(
            {"ok": True, "query": decoded_args.get("query", "")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    request_fingerprint = (
        turn_done.diagnostics.request_fingerprint if turn_done.diagnostics is not None else None
    )
    request_witness = (
        turn_done.diagnostics.request_witness if turn_done.diagnostics is not None else None
    )
    return _ToolTurnResult(
        observation=_Observation(
            scenario=scenario,
            phase=phase,
            prompt_tokens=usage.prompt_tokens,
            cached_prompt_tokens=usage.cached_prompt_tokens,
            cache_write_prompt_tokens=usage.cache_write_prompt_tokens,
            input_other_prompt_tokens=usage.input_other_prompt_tokens,
            cache_miss_prompt_tokens=usage.cache_miss_prompt_tokens,
            cached_prompt_tokens_source=usage.cached_prompt_tokens_source,
            cache_write_prompt_tokens_source=usage.cache_write_prompt_tokens_source,
            input_other_prompt_tokens_source=usage.input_other_prompt_tokens_source,
            cache_miss_prompt_tokens_source=usage.cache_miss_prompt_tokens_source,
            input_token_accounting=usage.input_token_accounting,
            request_fingerprint=request_fingerprint,
            first_text_ms=round(first_text_ms, 1) if first_text_ms is not None else None,
            total_ms=round(total_ms, 1),
            request_relation=(
                turn_done.diagnostics.request_relation
                if turn_done.diagnostics is not None
                else "unavailable"
            ),
            common_prefix_items=(
                turn_done.diagnostics.common_prefix_items
                if turn_done.diagnostics is not None
                else None
            ),
            prompt_cache_observation=(
                dict(turn_done.diagnostics.cache_observation)
                if turn_done.diagnostics is not None
                and turn_done.diagnostics.cache_observation is not None
                else None
            ),
        ),
        assistant_messages=assistant_messages,
        tool_message=tool_message,
        request_witness=request_witness,
    )


def _run_kimi(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
    exact_repeat_only: bool = False,
) -> dict[str, Any]:
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = _client_for_model(runtime_config, model)
    run_marker = uuid4().hex
    observations: list[_Observation] = []
    scenarios = (
        (("exact_repeat_with_key", False, True, False),)
        if exact_repeat_only
        else (
            ("tail_dynamic_without_key", False, False, True),
            ("early_memory_dynamic_without_key", True, False, True),
            ("tail_dynamic_with_key", False, True, True),
        )
    )
    for name, dynamic_memory, use_key, dynamic_runtime in scenarios:
        marker = f"{run_marker}-{name}"
        cache_key = f"prompt-cache-smoke-{marker}" if use_key else None
        for phase, variant in (("warm", "A"), ("probe", "B")):
            observations.append(
                _request(
                    client,
                    scenario=name,
                    phase=phase,
                    messages=_messages(
                        marker=marker,
                        history_chars=history_chars,
                        memory_variant=variant,
                        runtime_variant=(
                            f"2026-07-28T22:04:0{1 if phase == 'warm' else 2}+08:00"
                            if dynamic_runtime
                            else "2026-07-28T22:04:01+08:00"
                        ),
                        dynamic_memory_before_history=dynamic_memory,
                    ),
                    prompt_cache_key=cache_key,
                )
            )
            if phase == "warm" and settle_seconds > 0:
                time.sleep(settle_seconds)
    return {
        "model": model,
        "real_llm_calls": len(observations),
        "history_chars_per_request": history_chars,
        "observations": [asdict(item) for item in observations],
    }


def _deepseek_base(
    marker: str,
    history_chars: int,
    *,
    production_shape: bool,
) -> list[dict[str, str]]:
    unit = f"[{marker}] stable DeepSeek prefix unit for cache persistence. "
    stable = (unit * (history_chars // len(unit) + 1))[:history_chars]
    if production_shape:
        chunk_size = max(1, len(stable) // 91)
        chunks = [stable[index : index + chunk_size] for index in range(0, len(stable), chunk_size)]
        chunks = chunks[:91]
        history: list[dict[str, str]] = []
        for index, chunk in enumerate(chunks):
            history.extend(
                [
                    {"role": "user", "content": f"history-{index}: {chunk}"},
                    {"role": "assistant", "content": f"ack-{index}"},
                ]
            )
        return [
            {"role": "system", "content": "Cache experiment; reply only OK."},
            *history,
            {"role": "user", "content": f"[{marker}] anchor; reply only OK."},
        ]
    return [
        {
            "role": "system",
            "content": "Cache experiment. Treat the long block as data and reply only OK.\n"
            + stable,
        },
        {"role": "user", "content": f"[{marker}] anchor; reply only OK."},
    ]


def _deepseek_tools(marker: str) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for index in range(26):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"cache_probe_tool_{index:02d}",
                    "description": (
                        f"[{marker}] Stable synthetic tool {index}. "
                        "Use only when explicitly asked; preserve evidence and return structured data. "
                        * 3
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                            "mode": {"type": "string", "enum": ["fast", "balanced", "deep"]},
                            "include_metadata": {"type": "boolean"},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _run_deepseek(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
    production_shape: bool,
) -> dict[str, Any]:
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = _client_for_model(runtime_config, model)
    run_marker = uuid4().hex
    observations: list[_Observation] = []

    for scenario in ("common_prefix_learning", "synthetic_message_extension"):
        marker = f"{run_marker}-{scenario}"
        base = _deepseek_base(marker, history_chars, production_shape=production_shape)
        tools = _deepseek_tools(marker) if production_shape else None
        first = [
            *base,
            {"role": "system", "content": f"[{marker}] transient-runtime-1"},
        ]
        if scenario == "common_prefix_learning":
            requests = [
                first,
                [
                    *base,
                    {"role": "assistant", "content": "fixed-step-1"},
                    {"role": "system", "content": f"[{marker}] transient-runtime-2"},
                ],
                [
                    *base,
                    {"role": "assistant", "content": "fixed-step-1"},
                    {"role": "system", "content": f"[{marker}] transient-runtime-3"},
                ],
            ]
        else:
            second = [
                *first,
                {"role": "assistant", "content": "fixed-step-1"},
                {"role": "system", "content": f"[{marker}] transient-runtime-2"},
            ]
            requests = [
                first,
                second,
                [
                    *second,
                    {"role": "assistant", "content": "fixed-step-2"},
                    {"role": "system", "content": f"[{marker}] transient-runtime-3"},
                ],
            ]
        for index, messages in enumerate(requests, start=1):
            observations.append(
                _request(
                    client,
                    scenario=scenario,
                    phase=f"call_{index}",
                    messages=messages,
                    prompt_cache_key=None,
                    max_tokens=32,
                    tools=tools,
                )
            )
            if index < len(requests) and settle_seconds > 0:
                time.sleep(settle_seconds)

    return {
        "model": model,
        "real_llm_calls": len(observations),
        "history_chars_per_request": history_chars,
        "production_shape": production_shape,
        "messages_in_stable_prefix": len(
            _deepseek_base("shape", history_chars, production_shape=production_shape)
        ),
        "tools_per_request": 26 if production_shape else 0,
        "request_shapes": {
            "common_prefix_learning": "stable long prefix with a replaced transient tail",
            "synthetic_message_extension": (
                "JSON message-array extension with synthetic assistant messages; "
                "not a provider transcript ledger"
            ),
        },
        "observations": [asdict(item) for item in observations],
    }


def _run_deepseek_shape_matrix(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
) -> dict[str, Any]:
    """Isolate message cardinality from tool-schema presence with exact repeats."""

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = _client_for_model(runtime_config, model)
    run_marker = uuid4().hex
    observations: list[_Observation] = []
    shapes = (
        ("compact_messages_without_tools", False, False),
        ("compact_messages_with_tools", False, True),
        ("many_messages_without_tools", True, False),
        ("many_messages_with_tools", True, True),
    )

    for scenario, many_messages, with_tools in shapes:
        marker = f"{run_marker}-{scenario}"
        messages = _deepseek_base(marker, history_chars, production_shape=many_messages)
        tools = _deepseek_tools(marker) if with_tools else None
        for phase in ("warm", "probe"):
            observations.append(
                _request(
                    client,
                    scenario=scenario,
                    phase=phase,
                    messages=messages,
                    prompt_cache_key=None,
                    max_tokens=32,
                    tools=tools,
                )
            )
            if phase == "warm" and settle_seconds > 0:
                time.sleep(settle_seconds)

    return {
        "model": model,
        "real_llm_calls": len(observations),
        "history_chars_per_request": history_chars,
        "experiment": "deepseek_exact_repeat_shape_matrix",
        "request_shapes": {
            scenario: {
                "messages": 184 if many_messages else 2,
                "tools": 26 if with_tools else 0,
            }
            for scenario, many_messages, with_tools in shapes
        },
        "observations": [asdict(item) for item in observations],
    }


def _deepseek_tool_loop_base(marker: str, history_chars: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        dict(message) for message in _deepseek_base(marker, history_chars, production_shape=True)
    ]
    messages[0]["content"] = (
        "Cache transcript experiment. Follow the latest user request and call the forced tool."
    )
    messages[-1]["content"] = (
        f"[{marker}] Call cache_probe_tool_00 exactly once with query='cache-check'."
    )
    return messages


def _run_deepseek_tool_loop_ab(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
) -> dict[str, Any]:
    """Compare current tail rebuild with a real provider-transcript append."""

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = _client_for_model(runtime_config, model)
    run_marker = uuid4().hex
    observations: list[_Observation] = []
    prefix_checks: dict[str, bool] = {}

    for scenario in ("current_tail_rebuild", "provider_transcript_continuation"):
        marker = f"{run_marker}-{scenario}"
        base = _deepseek_tool_loop_base(marker, history_chars)
        tools = _deepseek_tools(marker)
        first_messages = [
            *base,
            {
                "role": "user",
                "content": (
                    '<agent_friend_runtime_context source="host" sequence="0" '
                    f'supersedes="-1"><runtime_now at="2026-08-24T01:00:00+08:00" '
                    f'marker="{marker}" /></agent_friend_runtime_context>'
                ),
            },
        ]
        first = _tool_turn_request(
            client,
            scenario=scenario,
            phase="warm_tool_call",
            messages=first_messages,
            tools=tools,
        )
        observations.append(first.observation)
        if settle_seconds > 0:
            time.sleep(settle_seconds)

        if scenario == "current_tail_rebuild":
            second_messages = [
                *base,
                *first.assistant_messages,
                first.tool_message,
                {
                    "role": "user",
                    "content": (
                        '<agent_friend_runtime_context source="host" sequence="1" '
                        f'supersedes="0"><runtime_now at="2026-08-24T01:00:05+08:00" '
                        f'marker="{marker}" /></agent_friend_runtime_context>'
                    ),
                },
            ]
        else:
            second_messages = [
                *first_messages,
                *first.assistant_messages,
                first.tool_message,
                {
                    "role": "user",
                    "content": (
                        '<agent_friend_runtime_context source="host" sequence="1" '
                        f'supersedes="0"><runtime_now at="2026-08-24T01:00:05+08:00" '
                        f'marker="{marker}" /></agent_friend_runtime_context>'
                    ),
                },
            ]
        prefix_checks[scenario] = second_messages[: len(first_messages)] == first_messages
        second = _tool_turn_request(
            client,
            scenario=scenario,
            phase="probe_after_tool",
            messages=second_messages,
            tools=tools,
            previous_request_witness=first.request_witness,
        )
        observations.append(second.observation)

    return {
        "model": model,
        "real_llm_calls": len(observations),
        "history_chars_per_request": history_chars,
        "experiment": "deepseek_real_tool_loop_transcript_ab",
        "messages_in_stable_base": 184,
        "tools_per_request": 26,
        "first_request_prefix_preserved_in_probe": prefix_checks,
        "request_shapes": {
            "current_tail_rebuild": (
                "rebuild base, append actual assistant/tool, then render a fresh transient tail"
            ),
            "provider_transcript_continuation": (
                "preserve the exact first input, append the completed provider assistant, "
                "tool result, and a superseding host runtime snapshot"
            ),
        },
        "observations": [asdict(item) for item in observations],
        "acceptance": {
            "scenario": "provider_transcript_continuation",
            "phase": "probe_after_tool",
            "threshold": 0.95,
            "cached_input_ratio": (
                observations[-1].cached_prompt_tokens / observations[-1].prompt_tokens
                if observations[-1].cached_prompt_tokens is not None
                and observations[-1].prompt_tokens > 0
                else None
            ),
        },
    }


def _safe_session_ref(session_id: str) -> str:
    return hashlib.sha256(f"prompt-cache-smoke\0{session_id}".encode()).hexdigest()[:12]


def _long_session_stats(session: Session) -> _LongSessionStats:
    messages = session.llm_messages
    counts = Counter(event.type for event in session.active_events)
    return _LongSessionStats(
        session_ref=_safe_session_ref(session.session_id),
        history_chars=sum(
            len(message.content) for message in messages if isinstance(message.content, str)
        ),
        history_messages=len(messages),
        complete_turns=min(
            counts.get("user_message", 0),
            counts.get("assistant_message", 0),
        ),
        tool_events=(counts.get("tool_call_request", 0) + counts.get("tool_call_result", 0)),
        attachments=sum(len(message.attachments) for message in messages),
    )


def _select_long_session(
    *,
    store: JsonlSessionStore,
    model: str,
    source_session_ref: str,
    min_history_chars: int,
) -> tuple[Session, _LongSessionStats]:
    for summary in store.list():
        if _safe_session_ref(summary.session_id) != source_session_ref:
            continue
        session = store.load(summary.session_id)
        stats = _long_session_stats(session)
        if session.current_model != model:
            raise ValueError("selected long session does not use the requested model")
        if stats.history_chars < min_history_chars:
            raise ValueError("selected long session is below the requested history size")
        if stats.complete_turns < 5:
            raise ValueError("selected long session has fewer than five complete turns")
        if stats.attachments:
            raise ValueError("selected long session contains attachments")
        return session, stats
    raise ValueError(f"no local session matches safe ref {source_session_ref!r}")


def _active_persona_id(session: Session, catalog: PersonaCatalog) -> str:
    persona_id = session.current_persona_id
    if persona_id is not None:
        try:
            catalog.get(persona_id)
            return persona_id
        except PersonaNotFoundError:
            pass
    try:
        return catalog.find_by_name(session.current_persona_name).id
    except PersonaNotFoundError:
        return BUILTIN_DEFAULT_PERSONA_ID


def _latest_prompt_cache_observation(
    conversation: Conversation, *, call_index: int
) -> dict[str, Any]:
    assistant = next(
        event
        for event in reversed(conversation.session.events)
        if event.type == "assistant_message"
    )
    usage = assistant.meta.get("context_usage")
    cache_diagnostics = usage.get("cache_diagnostics") if isinstance(usage, dict) else None
    observation = (
        cache_diagnostics.get("observation") if isinstance(cache_diagnostics, dict) else None
    )
    if not isinstance(observation, dict):
        raise AssertionError(f"cross_user_turn/call_{call_index}: missing prompt-cache observation")
    return dict(observation)


def _new_prompt_cache_observations(session: Session, *, start_index: int) -> list[dict[str, Any]]:
    """Collect content-free provider observations attached to newly appended events."""

    observations: list[dict[str, Any]] = []
    for event in session.events[start_index:]:
        usage = event.meta.get("context_usage")
        cache_diagnostics = usage.get("cache_diagnostics") if isinstance(usage, dict) else None
        observation = (
            cache_diagnostics.get("observation") if isinstance(cache_diagnostics, dict) else None
        )
        if isinstance(observation, dict):
            observations.append(dict(observation))
    return observations


def _run_deepseek_long_session_dialogue(
    *,
    model: str,
    history_chars: int,
    source_session_ref: str,
    dialogue_turns: int,
) -> dict[str, Any]:
    """Clone one selected long session and issue two or three ordinary questions."""

    if dialogue_turns not in {2, 3}:
        raise ValueError("long-session dialogue supports exactly two or three turns")

    source_store = JsonlSessionStore(sessions_dir())
    source_session, stats = _select_long_session(
        store=source_store,
        model=model,
        source_session_ref=source_session_ref,
        min_history_chars=history_chars,
    )
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = runtime_config.llm_client_factory(source_session.current_model_config)
    observations: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="agent-friend-prompt-cache-long-session-") as temp_dir:
        clone_store = JsonlSessionStore(base_dir=Path(temp_dir) / "sessions")
        clone_store.create(source_session)
        session = clone_store.load(source_session.session_id)
        catalog = PersonaCatalog(external_dir=personas_dir())
        persona_id = _active_persona_id(session, catalog)
        builder = MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)
        conversation = Conversation(
            session=session,
            store=clone_store,
            llm_client=client,
            context_manager=NaiveContextManager(),
            prompt_builder=builder,
            prompt_builder_factory=lambda selected_persona_id: MarkdownPromptBuilder(
                persona_id=selected_persona_id,
                catalog=catalog,
            ),
            catalog=catalog,
        )
        prompts = (
            "请用两三句话解释，为什么刚烧开的水放在室温下会逐渐变凉？不需要使用工具。",
            "沿着刚才的解释，再举一个日常生活中的类似例子，也用两三句话回答，不需要使用工具。",
            "我们为啥之前的缓存情况好像很差来着？用两三句话回答，不需要使用工具。",
        )
        for index, prompt in enumerate(prompts[:dialogue_turns], start=1):
            list(conversation.stream(prompt, run_id=f"long-session-dialogue-{index}"))
            observations.append(_latest_prompt_cache_observation(conversation, call_index=index))

    for call_index, observation in enumerate(observations[1:], start=2):
        structure = observation.get("structure")
        if not isinstance(structure, dict):
            raise AssertionError(f"cross_user_turn/call_{call_index}: missing structure")
        if structure.get("comparison_scope") != "cross_user_turn":
            raise AssertionError(f"cross_user_turn/call_{call_index}: new user run was not linked")

    cache_read_ratios: list[float | None] = []
    for observation in observations:
        usage = observation.get("usage")
        input_total = usage.get("input_total") if isinstance(usage, dict) else None
        cache_read = usage.get("cache_read") if isinstance(usage, dict) else None
        cache_read_ratios.append(
            cache_read / input_total
            if isinstance(cache_read, int) and isinstance(input_total, int) and input_total > 0
            else None
        )
    return {
        "model": model,
        "real_llm_calls": dialogue_turns,
        "experiment": "deepseek_long_session_ordinary_dialogue",
        "source_session": asdict(stats),
        "request_cache_read_ratios": cache_read_ratios,
        "observations": observations,
    }


def _run_deepseek_rollout_recovery_dialogue(
    *,
    model: str,
    switch_model: str,
    history_chars: int,
    source_session_ref: str,
) -> dict[str, Any]:
    """Run the requirement-113 no-restart/restart/model-switch smoke matrix."""

    source_store = JsonlSessionStore(sessions_dir())
    source_session, stats = _select_long_session(
        store=source_store,
        model=model,
        source_session_ref=source_session_ref,
        min_history_chars=history_chars,
    )
    product_events = [
        event
        for event in source_session.events
        if event.type
        not in {
            "rollout_segment",
            "model_transcript_item",
            "rollout_request_prepared",
            "rollout_commit",
        }
    ]
    cloned_session = Session.from_events(product_events)
    catalog = PersonaCatalog(external_dir=personas_dir())
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    scenarios: list[dict[str, Any]] = []

    with TemporaryDirectory(prefix="agent-friend-prompt-cache-rollout-") as temp_dir:
        session_root = Path(temp_dir) / "sessions"
        JsonlSessionStore(session_root).create(cloned_session)

        def build_manager() -> SessionManager:
            return SessionManager(
                store=JsonlSessionStore(session_root),
                llm_client_factory=runtime_config.llm_client_factory,
                prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
                    persona_id=persona_id,
                    catalog=catalog,
                ),
                context_manager_factory=NaiveContextManager,
                runtime_instance_id=f"rollout-smoke-{uuid4().hex[:8]}",
                runtime_start_reason="prompt_cache_smoke",
            )

        def run_turn(
            manager: SessionManager,
            *,
            scenario: str,
            prompt: str,
            switch_to: str | None = None,
        ) -> None:
            conversation = manager.start_conversation(manager.open(cloned_session.session_id))
            if switch_to is not None:
                conversation.switch_model(switch_to)
            list(conversation.stream(prompt, run_id=f"rollout-smoke-{scenario}"))
            observation = _latest_prompt_cache_observation(
                conversation,
                call_index=len(scenarios) + 1,
            )
            structure = observation.get("structure")
            usage = observation.get("usage")
            structure = structure if isinstance(structure, dict) else {}
            usage = usage if isinstance(usage, dict) else {}
            diagnostics = conversation.cache_epoch_diagnostics
            cache_read = usage.get("cache_read")
            input_total = usage.get("input_total")
            scenarios.append(
                {
                    "scenario": scenario,
                    "client": {
                        "strict_extension": (
                            structure.get("final_wire_relation") == "strict_extension"
                        ),
                        "final_wire_relation": structure.get("final_wire_relation"),
                        "first_divergence_domain": structure.get("first_divergence_domain"),
                        "first_divergence_index": structure.get("first_divergence_index"),
                        "recovery_disposition": diagnostics.get("recovery_disposition"),
                        "cache_epoch_id": diagnostics.get("epoch_id"),
                        "cache_epoch_reason": diagnostics.get("reason"),
                    },
                    "provider": {
                        "cache_hit": isinstance(cache_read, int) and cache_read > 0,
                        "cache_read": cache_read,
                        "input_total": input_total,
                    },
                    "observation": observation,
                }
            )

        manager = build_manager()
        run_turn(
            manager,
            scenario="baseline",
            prompt="请用两三句话解释刚烧开的水为什么会逐渐变凉，不使用工具。",
        )
        run_turn(
            manager,
            scenario="no_restart_continuation",
            prompt="沿着刚才的解释，再举一个日常例子，不使用工具。",
        )
        manager.close()

        manager = build_manager()
        run_turn(
            manager,
            scenario="bridge_restart_recovery",
            prompt="继续刚才的话题，说说散热速度受什么影响，不使用工具。",
        )
        manager.close()

        manager = build_manager()
        run_turn(
            manager,
            scenario="app_restart_recovery",
            prompt="再补充一个保持热饮温度的方法，不使用工具。",
        )
        run_turn(
            manager,
            scenario="explicit_model_switch",
            prompt="切换模型后，用一句话总结前面的讨论，不使用工具。",
            switch_to=switch_model,
        )
        manager.close()

    total_input_tokens = sum(
        item["provider"]["input_total"]
        for item in scenarios
        if isinstance(item["provider"]["input_total"], int)
    )
    return {
        "model": model,
        "switch_model": switch_model,
        "real_llm_calls": len(scenarios),
        "experiment": "deepseek_rollout_recovery_dialogue",
        "source_session": asdict(stats),
        "scenarios": scenarios,
        "total_input_tokens": total_input_tokens,
        "interpretation": {
            "client_prefix_restoration_and_provider_cache_hit_are_independent": True,
            "cache_read_zero_does_not_invalidate_client_strict_extension": True,
        },
    }


def _run_deepseek_dynamic_context_dialogue(
    *,
    model: str,
    history_chars: int,
    source_session_ref: str,
) -> dict[str, Any]:
    """Exercise current-time, cross-day and timezone deltas on a long-session clone."""

    source_store = JsonlSessionStore(sessions_dir())
    source_session, stats = _select_long_session(
        store=source_store,
        model=model,
        source_session_ref=source_session_ref,
        min_history_chars=history_chars,
    )
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = runtime_config.llm_client_factory(source_session.current_model_config)
    clock = _MutableTimeProvider(
        datetime(2026, 8, 30, 23, 59, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    phases: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="agent-friend-prompt-cache-dynamic-context-") as temp_dir:
        clone_store = JsonlSessionStore(base_dir=Path(temp_dir) / "sessions")
        clone_store.create(source_session)
        session = clone_store.load(source_session.session_id)
        catalog = PersonaCatalog(external_dir=personas_dir())
        persona_id = _active_persona_id(session, catalog)
        conversation = Conversation(
            session=session,
            store=clone_store,
            llm_client=client,
            context_manager=NaiveContextManager(),
            prompt_builder=MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog),
            prompt_builder_factory=lambda selected_persona_id: MarkdownPromptBuilder(
                persona_id=selected_persona_id,
                catalog=catalog,
            ),
            catalog=catalog,
            tool_registry=ToolRegistry([DateTimeCalculateTool()]),
            time_provider=clock,
        )
        scenarios = (
            (
                "current_time_tool",
                "这是工具能力验收。必须先调用 datetime_calculate，operation=current；"
                "拿到结果后只用一句话回答当前日期、时间和时区，不得猜测。",
            ),
            (
                "cross_day",
                "宿主日历已经跨日。请只根据当前可见的粗粒度 calendar context，"
                "用一句话确认本地日期与 timezone；不需要调用时间工具。",
            ),
            (
                "timezone_change",
                "宿主 timezone 已改变。请只根据当前可见的粗粒度 calendar context，"
                "用一句话确认本地日期与 timezone；不需要调用时间工具。",
            ),
        )
        for index, (phase, prompt) in enumerate(scenarios, start=1):
            if phase == "cross_day":
                clock.value = datetime(
                    2026,
                    8,
                    31,
                    0,
                    1,
                    tzinfo=ZoneInfo("Asia/Shanghai"),
                )
            elif phase == "timezone_change":
                clock.value = datetime(
                    2026,
                    8,
                    30,
                    9,
                    5,
                    tzinfo=ZoneInfo("America/Los_Angeles"),
                )
            start_index = len(session.events)
            list(conversation.stream(prompt, run_id=f"dynamic-context-{index}"))
            phase_observations = _new_prompt_cache_observations(
                session,
                start_index=start_index,
            )
            observations.extend(phase_observations)
            model_history = conversation._session_model_history.snapshot()
            calendar_items = [
                message.content
                for message in model_history
                if 'contributor="calendar"' in message.content
            ]
            phases.append(
                {
                    "phase": phase,
                    "provider_calls": len(phase_observations),
                    "calendar_items": len(calendar_items),
                    "latest_calendar_matches_host": (
                        f'date="{clock.value.date().isoformat()}"' in calendar_items[-1]
                        and f'timezone="{getattr(clock.value.tzinfo, "key", "")}"'
                        in calendar_items[-1]
                    ),
                }
            )

        current_requests = [
            event
            for event in session.events
            if event.type == "tool_call_request"
            and event.payload.get("tool_name") == "datetime_calculate"
            and event.payload.get("args", {}).get("operation") == "current"
        ]
        current_results = [
            event
            for event in session.events
            if event.type == "tool_call_result"
            and event.payload.get("tool_name") == "datetime_calculate"
        ]
        action_time_matches = False
        if len(current_requests) == 1 and len(current_results) == 1:
            result_payload = json.loads(str(current_results[0].payload.get("content") or "{}"))
            action_time_matches = (
                result_payload.get("current_datetime") == "2026-08-30T23:59:40+08:00"
            )
        precise_runtime_markers = sum(
            marker in message.content
            for message in conversation._session_model_history.snapshot()
            for marker in ("<runtime_now", "<agent_friend_runtime_context")
        )

    return {
        "model": model,
        "real_llm_calls": len(observations),
        "experiment": "deepseek_long_session_dynamic_context_dialogue",
        "source_session": asdict(stats),
        "phases": phases,
        "current_tool_calls": len(current_requests),
        "current_tool_action_time_matches": action_time_matches,
        "precise_runtime_context_markers": precise_runtime_markers,
        "observations": observations,
    }


def _run_deepseek_cross_user_turn(
    *,
    model: str,
    history_chars: int,
) -> dict[str, Any]:
    """Run two real no-tool user turns through Conversation and inspect lineage."""

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = _client_for_model(runtime_config, model)
    marker = uuid4().hex
    stable = (f"[{marker}] stable cross-user-turn cache material. " * (history_chars // 48 + 1))[
        :history_chars
    ]
    observations: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="agent-friend-prompt-cache-") as temp_dir:
        root = Path(temp_dir)
        catalog = PersonaCatalog(external_dir=root / "personas")
        store = JsonlSessionStore(base_dir=root / "sessions")
        session = Session.new(
            title="prompt-cache-cross-user-turn",
            persona="default",
            model=model,
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        )
        store.create(session)
        builder = MarkdownPromptBuilder(
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            catalog=catalog,
        )
        conversation = Conversation(
            session=session,
            store=store,
            llm_client=client,
            context_manager=NaiveContextManager(),
            prompt_builder=builder,
            prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(
                persona_id=persona_id,
                catalog=catalog,
            ),
            catalog=catalog,
        )
        prompts = (
            f"Treat this block as stable data and reply only OK.\n{stable}",
            "Continue the same task and reply only OK.",
        )
        for index, prompt in enumerate(prompts, start=1):
            list(conversation.stream(prompt, run_id=f"cross-user-turn-{index}"))
            assistant = next(
                event
                for event in reversed(conversation.session.events)
                if event.type == "assistant_message"
            )
            usage = assistant.meta.get("context_usage")
            cache_diagnostics = usage.get("cache_diagnostics") if isinstance(usage, dict) else None
            observation = (
                cache_diagnostics.get("observation")
                if isinstance(cache_diagnostics, dict)
                else None
            )
            if not isinstance(observation, dict):
                raise AssertionError(
                    f"cross_user_turn/call_{index}: missing prompt-cache observation"
                )
            observations.append(dict(observation))

    second_structure = observations[1].get("structure")
    if not isinstance(second_structure, dict):
        raise AssertionError("cross_user_turn/call_2: missing structure")
    if second_structure.get("comparison_scope") != "cross_user_turn":
        raise AssertionError(
            "cross_user_turn/call_2: first request of the new user run was not linked"
        )
    if second_structure.get("cross_turn_relation") in {None, "baseline"}:
        raise AssertionError(
            "cross_user_turn/call_2: comparison was incorrectly reported as baseline"
        )
    return {
        "model": model,
        "real_llm_calls": 2,
        "history_chars_in_first_user_turn": history_chars,
        "experiment": "deepseek_conversation_cross_user_turn",
        "observations": observations,
    }


def run(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
    exact_repeat_only: bool = False,
    deepseek_production_shape: bool = False,
    deepseek_shape_matrix: bool = False,
    deepseek_tool_loop_ab: bool = False,
    deepseek_cross_user_turn: bool = False,
    deepseek_long_session_dialogue: bool = False,
    deepseek_dynamic_context_dialogue: bool = False,
    deepseek_rollout_recovery_dialogue: bool = False,
    source_session_ref: str | None = None,
    dialogue_turns: int = 2,
    switch_model: str | None = None,
) -> dict[str, Any]:
    if model.startswith("deepseek/"):
        if exact_repeat_only:
            raise ValueError("--exact-repeat-only is only available for Kimi")
        if deepseek_tool_loop_ab:
            return _run_deepseek_tool_loop_ab(
                model=model,
                history_chars=history_chars,
                settle_seconds=settle_seconds,
            )
        if deepseek_cross_user_turn:
            return _run_deepseek_cross_user_turn(
                model=model,
                history_chars=history_chars,
            )
        if deepseek_long_session_dialogue:
            if source_session_ref is None:
                raise ValueError("long-session dialogue requires a safe source session ref")
            return _run_deepseek_long_session_dialogue(
                model=model,
                history_chars=history_chars,
                source_session_ref=source_session_ref,
                dialogue_turns=dialogue_turns,
            )
        if deepseek_dynamic_context_dialogue:
            if source_session_ref is None:
                raise ValueError("dynamic-context dialogue requires a safe source session ref")
            return _run_deepseek_dynamic_context_dialogue(
                model=model,
                history_chars=history_chars,
                source_session_ref=source_session_ref,
            )
        if deepseek_rollout_recovery_dialogue:
            if source_session_ref is None or switch_model is None:
                raise ValueError(
                    "rollout-recovery dialogue requires source session ref and switch model"
                )
            return _run_deepseek_rollout_recovery_dialogue(
                model=model,
                switch_model=switch_model,
                history_chars=history_chars,
                source_session_ref=source_session_ref,
            )
        if deepseek_shape_matrix:
            return _run_deepseek_shape_matrix(
                model=model,
                history_chars=history_chars,
                settle_seconds=settle_seconds,
            )
        return _run_deepseek(
            model=model,
            history_chars=history_chars,
            settle_seconds=settle_seconds,
            production_shape=deepseek_production_shape,
        )
    return _run_kimi(
        model=model,
        history_chars=history_chars,
        settle_seconds=settle_seconds,
        exact_repeat_only=exact_repeat_only,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="required acknowledgement that this smoke sends up to eight real LLM requests",
    )
    parser.add_argument("--model", default="kimi/kimi-k2.6")
    parser.add_argument("--history-chars", type=int, default=24_000)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument(
        "--exact-repeat-only",
        action="store_true",
        help="send only one identical warm/probe pair with a stable cache key",
    )
    parser.add_argument(
        "--deepseek-production-shape",
        action="store_true",
        help="use 184 stable-prefix messages and 26 stable tool schemas",
    )
    parser.add_argument(
        "--deepseek-shape-matrix",
        action="store_true",
        help="send eight exact-repeat requests isolating 2/184 messages and 0/26 tools",
    )
    parser.add_argument(
        "--deepseek-tool-loop-ab",
        action="store_true",
        help="send four forced-tool requests comparing tail rebuild with transcript append",
    )
    parser.add_argument(
        "--deepseek-cross-user-turn",
        action="store_true",
        help="send two no-tool user turns through Conversation and inspect cross-turn lineage",
    )
    parser.add_argument(
        "--deepseek-long-session-dialogue",
        action="store_true",
        help="clone one approved local long session and send two ordinary no-tool questions",
    )
    parser.add_argument(
        "--deepseek-dynamic-context-dialogue",
        action="store_true",
        help="clone one approved long session and exercise current-time/day/timezone deltas",
    )
    parser.add_argument(
        "--deepseek-rollout-recovery-dialogue",
        action="store_true",
        help=(
            "clone one approved long session and run no-restart, Bridge restart, "
            "app restart, and explicit model-switch turns"
        ),
    )
    parser.add_argument(
        "--source-session-ref",
        help="content-free safe ref of the approved local source session",
    )
    parser.add_argument(
        "--dialogue-turns",
        type=int,
        choices=(2, 3),
        default=2,
        help="number of ordinary questions for long-session dialogue",
    )
    parser.add_argument(
        "--switch-model",
        help="canonical target model id for the rollout recovery model-switch turn",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional UTF-8 JSON report path",
    )
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real LLM calls require explicit --allow-llm")
    if not args.model.startswith(("kimi/", "deepseek/")):
        parser.error("--model must be a canonical Kimi or DeepSeek model id")
    if args.history_chars < 8_000:
        parser.error("--history-chars must be at least 8000")
    if not 0 <= args.settle_seconds <= 30:
        parser.error("--settle-seconds must be between 0 and 30")
    deepseek_modes = sum(
        int(enabled)
        for enabled in (
            args.deepseek_production_shape,
            args.deepseek_shape_matrix,
            args.deepseek_tool_loop_ab,
            args.deepseek_cross_user_turn,
            args.deepseek_long_session_dialogue,
            args.deepseek_dynamic_context_dialogue,
            args.deepseek_rollout_recovery_dialogue,
        )
    )
    if deepseek_modes > 1:
        parser.error("choose only one DeepSeek experiment mode")
    source_session_mode = (
        args.deepseek_long_session_dialogue
        or args.deepseek_dynamic_context_dialogue
        or args.deepseek_rollout_recovery_dialogue
    )
    if source_session_mode and not args.source_session_ref:
        parser.error("selected long-session mode requires --source-session-ref")
    if args.source_session_ref and not source_session_mode:
        parser.error("--source-session-ref is only valid for long-session modes")
    if args.dialogue_turns != 2 and not args.deepseek_long_session_dialogue:
        parser.error("--dialogue-turns is only configurable for long-session dialogue")
    if args.deepseek_rollout_recovery_dialogue and not args.switch_model:
        parser.error("rollout-recovery dialogue requires --switch-model")
    if args.switch_model and not args.deepseek_rollout_recovery_dialogue:
        parser.error("--switch-model is only valid for rollout-recovery dialogue")
    rendered = json.dumps(
        run(
            model=args.model,
            history_chars=args.history_chars,
            settle_seconds=args.settle_seconds,
            exact_repeat_only=args.exact_repeat_only,
            deepseek_production_shape=args.deepseek_production_shape,
            deepseek_shape_matrix=args.deepseek_shape_matrix,
            deepseek_tool_loop_ab=args.deepseek_tool_loop_ab,
            deepseek_cross_user_turn=args.deepseek_cross_user_turn,
            deepseek_long_session_dialogue=args.deepseek_long_session_dialogue,
            deepseek_dynamic_context_dialogue=(args.deepseek_dynamic_context_dialogue),
            deepseek_rollout_recovery_dialogue=(args.deepseek_rollout_recovery_dialogue),
            source_session_ref=args.source_session_ref,
            dialogue_turns=args.dialogue_turns,
            switch_model=args.switch_model,
        ),
        ensure_ascii=False,
        indent=2,
    )
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
