"""Real-Kimi prompt-cache A/B for turn context and memory placement."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from agent_bridge.runtime_config import resolve_bridge_runtime_config

from llm_providers import LLMTextDelta, LLMTurnDone, LLMUsage
from runtime_config import credential_source_for_mode


@dataclass(frozen=True)
class _Observation:
    scenario: str
    phase: str
    prompt_tokens: int
    cached_prompt_tokens: int | None
    cache_miss_prompt_tokens: int | None
    first_text_ms: float | None
    total_ms: float


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
) -> _Observation:
    overrides: dict[str, Any] = {"max_tokens": 128}
    if prompt_cache_key is not None:
        overrides["prompt_cache_key"] = prompt_cache_key
    started = time.perf_counter()
    first_text_ms: float | None = None
    usage: LLMUsage | None = None
    for event in client.stream(messages, tools=None, **overrides):
        if isinstance(event, LLMTextDelta) and first_text_ms is None:
            first_text_ms = (time.perf_counter() - started) * 1000
        elif isinstance(event, LLMTurnDone):
            usage = event.usage
    total_ms = (time.perf_counter() - started) * 1000
    if usage is None:
        raise AssertionError(f"{scenario}/{phase}: provider did not return stream usage")
    return _Observation(
        scenario=scenario,
        phase=phase,
        prompt_tokens=usage.prompt_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        cache_miss_prompt_tokens=usage.cache_miss_prompt_tokens,
        first_text_ms=round(first_text_ms, 1) if first_text_ms is not None else None,
        total_ms=round(total_ms, 1),
    )


def run(
    *,
    model: str,
    history_chars: int,
    settle_seconds: float,
    exact_repeat_only: bool = False,
) -> dict[str, Any]:
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = runtime_config.llm_client_factory(model)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="required acknowledgement that this smoke sends up to six real Kimi requests",
    )
    parser.add_argument("--model", default="kimi/kimi-k2.6")
    parser.add_argument("--history-chars", type=int, default=24_000)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument(
        "--exact-repeat-only",
        action="store_true",
        help="send only one identical warm/probe pair with a stable cache key",
    )
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real LLM calls require explicit --allow-llm")
    if not args.model.startswith("kimi/"):
        parser.error("--model must be a canonical Kimi model id")
    if args.history_chars < 8_000:
        parser.error("--history-chars must be at least 8000")
    if not 0 <= args.settle_seconds <= 30:
        parser.error("--settle-seconds must be between 0 and 30")
    print(
        json.dumps(
            run(
                model=args.model,
                history_chars=args.history_chars,
                settle_seconds=args.settle_seconds,
                exact_repeat_only=args.exact_repeat_only,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
