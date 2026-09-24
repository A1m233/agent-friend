"""Bounded execution of synchronous tools with deterministic result ordering."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ..tools.protocol import ToolResult
from ..tools.registry import ToolRegistry
from .control import TOOL_INVOCATION_CONTROL_ARG, ToolInvocationControl

ToolHookInvoker = Callable[[str, dict[str, Any], Callable[[], ToolResult]], ToolResult]
PreToolHook = Callable[[str, dict[str, Any]], ToolResult | None]
PostToolHook = Callable[[str, dict[str, Any], ToolResult], None]


class ToolExecutionKind(StrEnum):
    SERIAL = "serial"
    PARALLEL_READ = "parallel_read"


@dataclass(frozen=True)
class ExecutedToolCall:
    call: dict[str, Any]
    result: ToolResult
    duration_seconds: float


@dataclass(frozen=True)
class _Completed:
    result: ToolResult
    completed_at: float


@dataclass
class _Pending:
    call: dict[str, Any]
    public_args: dict[str, Any]
    future: Future[_Completed] | None
    started_at: float
    timeout_seconds: float
    immediate_result: ToolResult | None = None
    invoke: Callable[[], ToolResult] | None = None
    control: ToolInvocationControl | None = None


class ToolBatchExecutor:
    def __init__(self, max_workers: int) -> None:
        self._max_workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-tool")

    def parallel_batch_count(
        self,
        calls: list[dict[str, Any]],
        *,
        registry: ToolRegistry | None,
        force_serial: bool = False,
    ) -> int:
        """Return the number of batches that will actually have parallel capacity."""

        if self._max_workers < 2:
            return 0
        return sum(
            len(batch) > 1 for batch in self._batches(calls, registry, force_serial=force_serial)
        )

    def execute(
        self,
        calls: list[dict[str, Any]],
        *,
        registry: ToolRegistry | None,
        default_timeout_seconds: float,
        invocation_args: Callable[[str, dict[str, Any], str], dict[str, Any]],
        cancel_check: Callable[[], None] | None = None,
        legacy_hook_invoker: ToolHookInvoker | None = None,
        pre_hook: PreToolHook | None = None,
        post_hook: PostToolHook | None = None,
        result_transform: Callable[[ToolResult], ToolResult] | None = None,
    ) -> list[ExecutedToolCall]:
        results: list[ExecutedToolCall] = []
        for batch in self._batches(calls, registry, force_serial=legacy_hook_invoker is not None):
            pending: list[_Pending] = []
            try:
                for call in batch:
                    if cancel_check is not None:
                        cancel_check()
                    pending.append(
                        self._prepare(
                            call,
                            registry=registry,
                            default_timeout_seconds=default_timeout_seconds,
                            invocation_args=invocation_args,
                            legacy_hook_invoker=legacy_hook_invoker,
                            pre_hook=pre_hook,
                        )
                    )
                # Hooks can outlive a cancel request. Recheck before submitting
                # each body, not only later while waiting for its result.
                for item in pending:
                    if cancel_check is not None:
                        cancel_check()
                    if item.immediate_result is None:
                        assert item.invoke is not None
                        item.started_at = time.monotonic()
                        invoke = item.invoke
                        item.future = self._pool.submit(self._invoke_completed, invoke)
                        item.invoke = None
                for item in pending:
                    result = self._finish(item, cancel_check)
                    if result_transform is not None:
                        result = replace(result, result=result_transform(result.result))
                    if post_hook is not None and legacy_hook_invoker is None:
                        post_hook(item.call["name"], item.public_args, result.result)
                    results.append(result)
            except BaseException:
                # Cancel every in-flight sibling, including reads whose result
                # has not yet reached the ordered result collector.
                for item in pending:
                    if item.control is not None:
                        item.control.cancel("run_cancelled")
                    if item.future is not None:
                        item.future.cancel()
                raise
        return results

    def _prepare(
        self,
        call: dict[str, Any],
        *,
        registry: ToolRegistry | None,
        default_timeout_seconds: float,
        invocation_args: Callable[[str, dict[str, Any], str], dict[str, Any]],
        legacy_hook_invoker: ToolHookInvoker | None,
        pre_hook: PreToolHook | None,
    ) -> _Pending:
        name = call["name"]
        public_args = call["args"]
        started_at = time.monotonic()
        timeout = default_timeout_seconds
        tool: object | None = None
        if registry is not None:
            try:
                tool = registry.get(name)
                configured = getattr(tool, "timeout_seconds", timeout)
                if isinstance(configured, (int, float)) and configured > 0:
                    timeout = float(configured)
            except Exception:
                pass
        if pre_hook is not None and legacy_hook_invoker is None:
            blocked = pre_hook(name, public_args)
            if blocked is not None:
                return _Pending(call, public_args, None, started_at, timeout, blocked)

        control = (
            ToolInvocationControl()
            if tool is not None and getattr(tool, "supports_invocation_control", False) is True
            else None
        )

        def invoke() -> ToolResult:
            def raw() -> ToolResult:
                if registry is None:
                    return ToolResult(
                        text=f"工具 {name!r} 不可用：当前会话未配置工具集",
                        is_error=True,
                        meta={"error_kind": "unavailable"},
                    )
                try:
                    tool_call_id = str(call.get("event_id") or call.get("id") or "")
                    args = invocation_args(name, public_args, tool_call_id)
                    if control is not None:
                        args[TOOL_INVOCATION_CONTROL_ARG] = control
                    return registry.invoke(name, args)
                except Exception as exc:
                    return ToolResult(
                        text=f"工具 {name!r} 执行出错: {exc}",
                        is_error=True,
                        meta={"error_kind": "internal_error", "exception_type": type(exc).__name__},
                    )

            return legacy_hook_invoker(name, public_args, raw) if legacy_hook_invoker else raw()

        return _Pending(
            call, public_args, None, started_at, timeout, invoke=invoke, control=control
        )

    def _finish(
        self, pending: _Pending, cancel_check: Callable[[], None] | None
    ) -> ExecutedToolCall:
        if pending.immediate_result is not None:
            return ExecutedToolCall(
                pending.call, pending.immediate_result, time.monotonic() - pending.started_at
            )
        assert pending.future is not None
        while True:
            deadline = (
                pending.started_at
                + pending.timeout_seconds
                + (pending.control.recovery_wait_seconds if pending.control is not None else 0.0)
            )
            if cancel_check is not None:
                try:
                    cancel_check()
                except BaseException:
                    if pending.control is not None:
                        pending.control.cancel("run_cancelled")
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if pending.future.done():
                    completed = pending.future.result()
                    if completed.completed_at <= deadline:
                        return ExecutedToolCall(
                            pending.call,
                            completed.result,
                            completed.completed_at - pending.started_at,
                        )
                return self._timeout_result(pending)
            try:
                completed = pending.future.result(timeout=min(remaining, 0.05))
                # Recovery may reserve its allowance while result() is waiting.
                deadline = (
                    pending.started_at
                    + pending.timeout_seconds
                    + (
                        pending.control.recovery_wait_seconds
                        if pending.control is not None
                        else 0.0
                    )
                )
                if completed.completed_at > deadline:
                    return self._timeout_result(pending)
                return ExecutedToolCall(
                    pending.call,
                    completed.result,
                    completed.completed_at - pending.started_at,
                )
            except TimeoutError:
                continue

    @staticmethod
    def _timeout_result(pending: _Pending) -> ExecutedToolCall:
        assert pending.future is not None
        if pending.control is not None:
            pending.control.cancel("tool_timeout")
        pending.future.cancel()
        result = ToolResult(
            text="工具在限定时间内没有返回，未取得可用证据。",
            is_error=True,
            meta={
                "error_kind": "timeout",
                "underlying_cancelled": pending.future.cancelled(),
            },
        )
        return ExecutedToolCall(pending.call, result, pending.timeout_seconds)

    @staticmethod
    def _invoke_completed(invoke: Callable[[], ToolResult]) -> _Completed:
        return _Completed(invoke(), time.monotonic())

    @staticmethod
    def _batches(
        calls: list[dict[str, Any]], registry: ToolRegistry | None, *, force_serial: bool
    ) -> list[list[dict[str, Any]]]:
        if force_serial:
            return [[call] for call in calls]
        batches: list[list[dict[str, Any]]] = []
        parallel: list[dict[str, Any]] = []
        for call in calls:
            kind = ToolExecutionKind.SERIAL
            if registry is not None:
                try:
                    kind = ToolExecutionKind(
                        getattr(registry.get(call["name"]), "execution_kind", "serial")
                    )
                except Exception:
                    kind = ToolExecutionKind.SERIAL
            if kind is ToolExecutionKind.PARALLEL_READ:
                parallel.append(call)
                continue
            if parallel:
                batches.append(parallel)
                parallel = []
            batches.append([call])
        if parallel:
            batches.append(parallel)
        return batches
