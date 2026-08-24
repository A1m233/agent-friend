"""Real prompt-cache A/B for Kimi layout and DeepSeek prefix persistence."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from agent_bridge.runtime_config import resolve_bridge_runtime_config

from llm_providers import (
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
    ModelCallControl,
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


@dataclass(frozen=True)
class _ToolTurnResult:
    observation: _Observation
    assistant_message: dict[str, Any]
    tool_message: dict[str, Any]
    request_witness: ProviderRequestWitness | None


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
    for event in client.stream(messages, tools=tools, **overrides):
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
    total_ms = (time.perf_counter() - started) * 1000
    if usage is None:
        raise AssertionError(f"{scenario}/{phase}: provider did not return stream usage")
    return _Observation(
        scenario=scenario,
        phase=phase,
        prompt_tokens=usage.prompt_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        cache_miss_prompt_tokens=usage.cache_miss_prompt_tokens,
        cached_prompt_tokens_source=usage.cached_prompt_tokens_source,
        cache_miss_prompt_tokens_source=usage.cache_miss_prompt_tokens_source,
        request_fingerprint=request_fingerprint,
        first_text_ms=round(first_text_ms, 1) if first_text_ms is not None else None,
        total_ms=round(total_ms, 1),
        request_relation=request_relation,
        common_prefix_items=common_prefix_items,
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
            "max_tokens": 64,
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        },
        control=ModelCallControl(previous_request_witness=previous_request_witness),
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
    assistant_message: dict[str, Any] = dict(assistant_turn.to_openai_message())
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
            cache_miss_prompt_tokens=usage.cache_miss_prompt_tokens,
            cached_prompt_tokens_source=usage.cached_prompt_tokens_source,
            cache_miss_prompt_tokens_source=usage.cache_miss_prompt_tokens_source,
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
        ),
        assistant_message=assistant_message,
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
                first.assistant_message,
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
                first.assistant_message,
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


def run(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
    exact_repeat_only: bool = False,
    deepseek_production_shape: bool = False,
    deepseek_shape_matrix: bool = False,
    deepseek_tool_loop_ab: bool = False,
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
        )
    )
    if deepseek_modes > 1:
        parser.error("choose only one DeepSeek experiment mode")
    print(
        json.dumps(
            run(
                model=args.model,
                history_chars=args.history_chars,
                settle_seconds=args.settle_seconds,
                exact_repeat_only=args.exact_repeat_only,
                deepseek_production_shape=args.deepseek_production_shape,
                deepseek_shape_matrix=args.deepseek_shape_matrix,
                deepseek_tool_loop_ab=args.deepseek_tool_loop_ab,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
