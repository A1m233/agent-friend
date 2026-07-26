from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, ClassVar, cast

import pytest
from agent.tool_runtime import ToolBatchExecutor
from agent.tools import Tool, ToolRegistry, ToolResult


@dataclass
class _SleepTool:
    name: str
    delay: float
    execution_kind: str = "parallel_read"
    timeout_seconds: float = 1.0
    start_log: list[str] | None = None
    description: ClassVar[str] = "test"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        if self.start_log is not None:
            self.start_log.append(f"body:{self.name}")
        time.sleep(self.delay)
        return ToolResult(str(args["value"]))


def _call(name: str, value: str) -> dict[str, Any]:
    return {"id": value, "event_id": f"event-{value}", "name": name, "args": {"value": value}}


def test_parallel_read_bodies_finish_faster_but_results_keep_call_order() -> None:
    registry = ToolRegistry(cast(list[Tool], [_SleepTool("a", 0.12), _SleepTool("b", 0.12)]))
    executor = ToolBatchExecutor(max_workers=2)
    started = time.monotonic()
    results = executor.execute(
        [_call("a", "first"), _call("b", "second")],
        registry=registry,
        default_timeout_seconds=1,
        invocation_args=lambda _name, args: args,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 0.22
    assert [item.result.text for item in results] == ["first", "second"]


def test_serial_tool_is_a_barrier() -> None:
    registry = ToolRegistry(
        cast(
            list[Tool],
            [
                _SleepTool("a", 0.06),
                _SleepTool("serial", 0.06, execution_kind="serial"),
                _SleepTool("b", 0.06),
            ],
        )
    )
    executor = ToolBatchExecutor(max_workers=3)
    started = time.monotonic()
    executor.execute(
        [_call("a", "a"), _call("serial", "s"), _call("b", "b")],
        registry=registry,
        default_timeout_seconds=1,
        invocation_args=lambda _name, args: args,
    )
    assert time.monotonic() - started >= 0.16


def test_hooks_are_ordered_around_parallel_bodies() -> None:
    seen: list[str] = []
    registry = ToolRegistry(
        cast(
            list[Tool],
            [_SleepTool("a", 0.03, start_log=seen), _SleepTool("b", 0.01, start_log=seen)],
        )
    )
    executor = ToolBatchExecutor(max_workers=2)
    executor.execute(
        [_call("a", "a"), _call("b", "b")],
        registry=registry,
        default_timeout_seconds=1,
        invocation_args=lambda _name, args: args,
        pre_hook=lambda name, _args: seen.append(f"pre:{name}"),
        post_hook=lambda name, _args, _result: seen.append(f"post:{name}"),
    )
    assert seen[:2] == ["pre:a", "pre:b"]
    assert set(seen[2:4]) == {"body:a", "body:b"}
    assert seen[4:] == ["post:a", "post:b"]


def test_timeout_returns_error_and_discards_late_result() -> None:
    registry = ToolRegistry(cast(list[Tool], [_SleepTool("slow", 0.15, timeout_seconds=0.02)]))
    result = ToolBatchExecutor(max_workers=1).execute(
        [_call("slow", "late")],
        registry=registry,
        default_timeout_seconds=1,
        invocation_args=lambda _name, args: args,
    )[0]
    assert result.result.is_error
    assert result.result.meta["error_kind"] == "timeout"


def test_cancel_check_interrupts_waiting_for_a_tool() -> None:
    registry = ToolRegistry(cast(list[Tool], [_SleepTool("slow", 0.15)]))
    checks = 0

    def cancel_check() -> None:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise RuntimeError("cancelled-by-user")

    with pytest.raises(RuntimeError, match="cancelled-by-user"):
        ToolBatchExecutor(max_workers=1).execute(
            [_call("slow", "waiting")],
            registry=registry,
            default_timeout_seconds=1,
            invocation_args=lambda _name, args: args,
            cancel_check=cancel_check,
        )


def test_parallel_batch_diagnostic_counts_only_real_parallel_groups() -> None:
    registry = ToolRegistry(
        cast(
            list[Tool],
            [
                _SleepTool("a", 0),
                _SleepTool("serial", 0, execution_kind="serial"),
                _SleepTool("b", 0),
                _SleepTool("c", 0),
            ],
        )
    )
    calls = [_call("a", "a"), _call("serial", "s"), _call("b", "b"), _call("c", "c")]
    assert ToolBatchExecutor(max_workers=2).parallel_batch_count(calls, registry=registry) == 1
    assert ToolBatchExecutor(max_workers=1).parallel_batch_count(calls, registry=registry) == 0
