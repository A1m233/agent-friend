"""Live approval cases using real tools against disposable local files, without LLM."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.filesystem import FilesystemService, ProtectedPathPolicy
from agent.filesystem.models import FilesystemAuthorizationLedger
from agent.filesystem.tools import (
    FILESYSTEM_LEDGER_ARG,
    FILESYSTEM_RUN_ID_ARG,
    FILESYSTEM_SESSION_ID_ARG,
    FILESYSTEM_SURFACE_ARG,
    FILESYSTEM_TOOL_CALL_ID_ARG,
    FilesystemOperationTool,
    RequestFilesystemAccessTool,
)
from agent_bridge.protocols.im.approvals import IMApprovalService
from agent_bridge.protocols.im.content import OutboundContent
from agent_bridge.protocols.im.event import InboundEvent
from agent_bridge.protocols.im.provider import IMProvider


async def file_cases(
    root: Path,
    service: IMApprovalService,
    provider: IMProvider,
    session_id: str,
    callback_lock: asyncio.Lock,
    record: Any,
    *,
    pending_auto: bool = False,
) -> None:
    policy = ProtectedPathPolicy(
        product_roots=((root / "product").resolve(),), personal_skill_roots=()
    )
    access = RequestFilesystemAccessTool(service.broker, policy)
    writer = FilesystemOperationTool("filesystem_write_text", FilesystemService(policy))
    # A host-created route starts this fixture directly. This is not evidence of
    # a user message entering IMRouter or of model-selected tool arguments.
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
    cases = (
        (("pending-auto", True),) if pending_auto else (("file-allow", True), ("file-deny", False))
    )
    for case, expect_allow in cases:
        directory = root / case
        directory.mkdir()
        target = directory / "验证文件.txt"
        ledger = FilesystemAuthorizationLedger()
        context = {
            FILESYSTEM_LEDGER_ARG: ledger,
            FILESYSTEM_SESSION_ID_ARG: session_id,
            FILESYSTEM_RUN_ID_ARG: case,
            FILESYSTEM_TOOL_CALL_ID_ARG: f"{case}-access",
            FILESYSTEM_SURFACE_ARG: "im:qq",
        }
        reason = (
            "AF163 第1步：请仅本次允许，创建临时验证文件"
            if expect_allow
            else "AF163 第2步：请拒绝，不应创建临时验证文件"
        )
        if pending_auto:
            reason = "AF163 等待中切换：请点授权设置，再开启免授权，让本请求自动继续"
        record("case_started", case=case, target=str(target), expected_allow=expect_allow)
        with service.run_route(provider, event, session_id, case) as registered:
            if not registered:
                raise RuntimeError("fixture QQ route not registered")
            result = await asyncio.to_thread(
                access.invoke,
                {
                    **context,
                    "reason": reason,
                    "items": [
                        {
                            "item_id": case,
                            "locator": {"kind": "path", "path": str(directory)},
                            "root_kind": "directory",
                            "permissions": ["create"],
                        }
                    ],
                },
            )
            payload = json.loads(result.text)
            granted = payload.get("status") == "granted" and not result.is_error
            operation_error = None
            expected_content = "AF163 real QQ approval write\n"
            if granted:
                operation = await asyncio.to_thread(
                    writer.invoke,
                    {
                        **context,
                        "filesystem_scope_id": payload["scopes"][0]["filesystem_scope_id"],
                        "relative_path": target.name,
                        "content": expected_content,
                    },
                )
                operation_error = operation.is_error
            async with callback_lock:
                pass  # Wait for the matching click's receipt before closing the route.
            exists = target.exists()
            content_matches = exists and target.read_text(encoding="utf-8") == expected_content
            passed = (
                granted == expect_allow
                and exists == expect_allow
                and (not expect_allow or content_matches)
            )
            record(
                "case_result",
                case=case,
                request_id=payload.get("request_id"),
                authorization_status=payload.get("status"),
                file_exists=exists,
                content_matches=content_matches,
                operation_error=operation_error,
                approval=payload.get("approval"),
                passed=passed,
            )
            await provider.send(
                OutboundContent(
                    chat_id=provider.bind_id,
                    chat_scope="c2c",
                    text=f"AF163 {case}："
                    + (
                        "验证通过，文件已写入且内容一致。"
                        if passed and expect_allow
                        else "验证通过，文件未创建。"
                        if passed
                        else "结果不符合本步骤预期，已停止测试。"
                    ),
                )
            )
            if not passed:
                raise RuntimeError("live filesystem approval expectation failed")
