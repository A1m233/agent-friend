"""Real sandboxed command approval probes: fixed echo only, no LLM."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.command_runtime import CommandRuntime, CommandRuntimeError, ExecCommandTool
from agent.command_runtime.runtime import sandbox_context
from agent.command_runtime.tools import (
    COMMAND_RUN_CONTEXT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
)
from agent.command_runtime.vendor import verify_codex_executable
from agent.paths import user_data_dir
from agent.tool_runtime import ToolRunContext
from agent_bridge.protocols.im.approvals import IMApprovalService
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.provider import IMProvider


class ProbeRuntime(CommandRuntime):
    def _setup_windows_sandbox(self, **_kwargs: Any) -> dict[str, object]:
        # Live QQ verification must not provision system sandbox identities.
        raise CommandRuntimeError(
            "probe_requires_ready_sandbox",
            "Existing product sandbox needs repair; live probe will not provision it",
        )


async def command_cases(
    root: Path,
    service: IMApprovalService,
    provider: IMProvider,
    session_id: str,
    callback_lock: asyncio.Lock,
    record: Any,
    executable: Path,
    *,
    auto_only: bool = False,
) -> None:
    executable = verify_codex_executable(executable)
    runtime = ProbeRuntime(
        executable,
        codex_home=user_data_dir() / "command-runtime" / "codex-home",
        execution_root=root / "execution",
    )
    tool = ExecCommandTool(runtime, service.broker, session_workspaces_root=root / "workspaces")
    event = InboundEvent(
        event_type="C2C_MESSAGE_CREATE",
        chat_scope="c2c",
        chat_id=provider.bind_id,
        user_id=provider.bind_id,
        message_id="",
        content="",
        message_type=0,
        timestamp=datetime.now(UTC).isoformat(),
    )
    command = (
        [os.environ["COMSPEC"], "/D", "/S", "/C", "echo AF163-COMMAND-OK"]
        if os.name == "nt"
        else ["/bin/echo", "AF163-COMMAND-OK"]
    )
    try:
        if os.name == "nt" and runtime.sandbox_capability()["ready"] is not True:
            record("preflight_failed", capability=runtime.sandbox_capability())
            raise RuntimeError("existing sandbox not ready")
        # Verify the real sandbox first, before asking the user for another click.
        process = await asyncio.to_thread(
            runtime.start_process,
            argv=tuple(command),
            workdir=root,
            env=dict(os.environ),
            mode="buffered",
            session_id=session_id,
            run_id="probe-preflight",
            tool_call_id="probe-preflight",
            sandbox=sandbox_context(
                workdir=root, writable=False, additional_roots=(), network_enabled=False
            ),
        )
        finished = await asyncio.to_thread(
            runtime.wait,
            process.process_id,
            session_id=session_id,
            run_id="probe-preflight",
            timeout_seconds=10,
        )
        if not finished.terminal():
            runtime.terminate(
                process.process_id,
                session_id=session_id,
                run_id="probe-preflight",
                reason="timeout",
            )
        preflight_ok = finished.exit_code == 0 and b"AF163-COMMAND-OK" in finished.stdout_tail
        record(
            "command_preflight",
            passed=preflight_ok,
            exit_code=finished.exit_code,
            stdout=finished.stdout_tail.decode("utf-8", errors="replace"),
            stderr=finished.stderr_tail.decode("utf-8", errors="replace"),
        )
        if not preflight_ok:
            raise RuntimeError("sandbox command preflight failed")
        cases = (
            (("command-auto", True),)
            if auto_only
            else (("command-allow", True), ("command-deny", False))
        )
        for case, expect_allow in cases:
            context = ToolRunContext.create("")
            before = len(runtime._processes)
            record("case_started", case=case, expected_allow=expect_allow, argv=command)
            try:
                with service.run_route(provider, event, session_id, case) as registered:
                    if not registered:
                        raise RuntimeError("fixture QQ route not registered")
                    result = await asyncio.to_thread(
                        tool.invoke,
                        {
                            "argv": command,
                            "workdir": str(root),
                            "timeout_seconds": 10,
                            "reason": "AF163 第3步：请允许，仅输出固定文字"
                            if expect_allow
                            else "AF163 第4步：请拒绝，命令不应启动",
                            COMMAND_SESSION_ID_ARG: session_id,
                            COMMAND_RUN_ID_ARG: case,
                            COMMAND_TOOL_CALL_ID_ARG: case,
                            COMMAND_SURFACE_ARG: "im:qq",
                            COMMAND_RUN_CONTEXT_ARG: context,
                        },
                    )
                    async with callback_lock:
                        pass
                    payload = json.loads(result.text)
                    started = len(runtime._processes) - before
                    passed = (
                        (
                            not result.is_error
                            and payload.get("exit_code") == 0
                            and "AF163-COMMAND-OK" in payload.get("stdout", "")
                            and started == 1
                        )
                        if expect_allow
                        else (
                            result.is_error
                            and payload.get("error") == "approval_denied"
                            and result.meta.get("executed") is False
                            and started == 0
                        )
                    )
                    record(
                        "case_result",
                        case=case,
                        passed=passed,
                        started_processes=started,
                        process_id=payload.get("process_id"),
                        status=payload.get("status"),
                        exit_code=payload.get("exit_code"),
                        stdout=payload.get("stdout"),
                        error=payload.get("error"),
                        message=payload.get("message"),
                        approval=payload.get("approval"),
                    )
                    await provider.send(
                        OutboundContent(
                            chat_id=provider.bind_id,
                            chat_scope="c2c",
                            text=f"AF163 {case}："
                            + (
                                "验证通过，命令输出正确并已退出。"
                                if passed and expect_allow
                                else "验证通过，命令未启动。"
                                if passed
                                else "结果不符合预期，已停止测试。"
                            ),
                        )
                    )
                    if not passed:
                        raise RuntimeError("live command approval expectation failed")
            finally:
                context.close_commands()
    finally:
        runtime.close()
