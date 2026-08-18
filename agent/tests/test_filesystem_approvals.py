from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from agent.filesystem import (
    ApprovalDecision,
    ApprovalItem,
    FilesystemApprovalBroker,
    FilesystemError,
    FilesystemService,
    ProtectedPathPolicy,
)
from agent.filesystem.models import FilesystemAuthorizationLedger, PendingFilesystemApproval
from agent.filesystem.tools import (
    FILESYSTEM_LEDGER_ARG,
    FILESYSTEM_RUN_ID_ARG,
    FILESYSTEM_SESSION_ID_ARG,
    FILESYSTEM_SURFACE_ARG,
    FILESYSTEM_TOOL_CALL_ID_ARG,
    FilesystemOperationTool,
    RequestFilesystemAccessTool,
)
from agent.runtime import ListenerRegistry, Subscriber
from agent.tools import ToolResult


def _policy(tmp_path: Path) -> ProtectedPathPolicy:
    return ProtectedPathPolicy(
        product_roots=((tmp_path / "product").resolve(),),
        personal_skill_roots=((tmp_path / "product" / "skills").resolve(),),
    )


def _args(tmp_path: Path, ledger: FilesystemAuthorizationLedger) -> dict[str, object]:
    return {
        "reason": "读取用户指定的说明文件",
        "items": [
            {
                "item_id": "docs",
                "locator": {"kind": "path", "path": str(tmp_path)},
                "root_kind": "directory",
                "permissions": ["search_metadata", "read_text"],
            }
        ],
        FILESYSTEM_LEDGER_ARG: ledger,
        FILESYSTEM_SESSION_ID_ARG: "session-1",
        FILESYSTEM_RUN_ID_ARG: "run-1",
        FILESYSTEM_TOOL_CALL_ID_ARG: "tool-1",
        FILESYSTEM_SURFACE_ARG: "desktop_chat",
    }


def test_non_desktop_surface_fails_closed(tmp_path: Path) -> None:
    result = RequestFilesystemAccessTool(FilesystemApprovalBroker(), _policy(tmp_path)).invoke(
        {**_args(tmp_path, FilesystemAuthorizationLedger()), FILESYSTEM_SURFACE_ARG: "im:qq"}
    )
    assert result.is_error is True
    assert json.loads(result.text)["status"] == "unavailable"
    assert result.meta == {
        "error_kind": "approval_surface_unavailable",
        "no_progress_family": "approval_surface_unavailable",
    }


def test_instruction_directory_requires_exact_file_for_content_or_write(tmp_path: Path) -> None:
    config_root = tmp_path / ".codex"
    config_root.mkdir()
    config_file = config_root / "config.toml"
    config_file.write_text("model = 'fixture'\n", encoding="utf-8")
    tool = RequestFilesystemAccessTool(FilesystemApprovalBroker(), _policy(tmp_path))

    with pytest.raises(FilesystemError) as broad:
        tool._parse_items(
            [
                {
                    "item_id": "codex-dir",
                    "locator": {"kind": "path", "path": str(config_root)},
                    "root_kind": "directory",
                    "permissions": ["search_metadata", "read_text", "edit"],
                }
            ]
        )
    assert getattr(broad.value, "code", None) == "protected_path"
    assert "准确文件" in str(broad.value)

    metadata = tool._parse_items(
        [
            {
                "item_id": "codex-dir",
                "locator": {"kind": "path", "path": str(config_root)},
                "root_kind": "directory",
                "permissions": ["search_metadata"],
            }
        ]
    )
    assert metadata[0].permissions == frozenset({"search_metadata"})

    exact = tool._parse_items(
        [
            {
                "item_id": "codex-config",
                "locator": {"kind": "path", "path": str(config_file)},
                "root_kind": "file",
                "permissions": ["read_text", "edit"],
            }
        ]
    )
    assert exact[0].allow_protected is True


def test_filesystem_tool_contract_explains_create_and_exact_patch_syntax(tmp_path: Path) -> None:
    access = RequestFilesystemAccessTool(FilesystemApprovalBroker(), _policy(tmp_path))
    copy_tool = FilesystemOperationTool("filesystem_copy", FilesystemService(_policy(tmp_path)))
    patch_tool = FilesystemOperationTool(
        "filesystem_apply_patch", FilesystemService(_policy(tmp_path))
    )

    assert "edit 只修改已有目标" in access.description
    assert "同时申请 search_metadata 和 read_text" in access.description
    permissions_description = access.parameters_schema["properties"]["items"]["items"][
        "properties"
    ]["permissions"]["description"]
    assert "filesystem_list_directory" in permissions_description
    assert "备份" in copy_tool.description and "create" in copy_tool.description
    assert "*** Begin Patch" in patch_tool.description
    assert "*** End Patch" in patch_tool.description


def test_broker_grants_only_approved_subset_for_current_ledger(tmp_path: Path) -> None:
    broker = FilesystemApprovalBroker()
    lifecycle: list[dict[str, object]] = []
    broker.add_listener(lifecycle.append)
    ledger = FilesystemAuthorizationLedger()
    tool = RequestFilesystemAccessTool(broker, _policy(tmp_path))
    holder: list[ToolResult] = []

    worker = threading.Thread(target=lambda: holder.append(tool.invoke(_args(tmp_path, ledger))))
    worker.start()
    deadline = time.monotonic() + 2
    pending: tuple[PendingFilesystemApproval, ...] = ()
    while time.monotonic() < deadline and not pending:
        pending = broker.pending_for_session("session-1")
        time.sleep(0.01)
    assert len(pending) == 1
    assert [(event["status"], event["version"]) for event in lifecycle] == [("pending", 1)]
    assert broker.pending() == pending
    assert pending[0].tool_call_id == "tool-1"
    assert not broker.decide(
        pending[0].request_id,
        ApprovalDecision(),
        session_id="another-session",
        run_id="run-1",
    )
    assert broker.decide(
        pending[0].request_id,
        ApprovalDecision(approved_permissions={"docs": frozenset({"search_metadata"})}),
        session_id="session-1",
        run_id="run-1",
    )
    worker.join(timeout=2)
    assert [(event["status"], event["version"]) for event in lifecycle] == [
        ("pending", 1),
        ("approved", 2),
    ]

    result = holder[0]
    payload = json.loads(result.text)
    assert payload["status"] == "partially_granted"
    scope = ledger.scope(payload["scopes"][0]["filesystem_scope_id"])
    assert scope is not None
    assert scope.permissions == frozenset({"search_metadata"})
    assert FilesystemAuthorizationLedger().scope(scope.scope_id) is None


def test_batch_file_operation_reports_partial_result(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "exists.bin").write_bytes(b"bytes")
    ledger = FilesystemAuthorizationLedger()
    source_scope = ledger.grant_scope(
        root=source.resolve(),
        root_kind="directory",
        permissions=frozenset({"read_text"}),
    )
    destination_scope = ledger.grant_scope(
        root=destination.resolve(),
        root_kind="directory",
        permissions=frozenset({"create"}),
    )
    tool = FilesystemOperationTool("filesystem_copy", FilesystemService(_policy(tmp_path)))

    result = tool.invoke(
        {
            FILESYSTEM_LEDGER_ARG: ledger,
            "items": [
                {
                    "item_id": "ok",
                    "source_filesystem_scope_id": source_scope.scope_id,
                    "source_path": "exists.bin",
                    "destination_filesystem_scope_id": destination_scope.scope_id,
                    "destination_path": "copy.bin",
                },
                {
                    "item_id": "missing",
                    "source_filesystem_scope_id": source_scope.scope_id,
                    "source_path": "missing.bin",
                    "destination_filesystem_scope_id": destination_scope.scope_id,
                    "destination_path": "missing-copy.bin",
                },
            ],
        }
    )

    payload = json.loads(result.text)
    assert result.is_error is True
    assert payload["status"] == "partial"
    assert [item["ok"] for item in payload["items"]] == [True, False]
    assert (destination / "copy.bin").read_bytes() == b"bytes"


def test_run_cancellation_returns_cancelled_instead_of_user_denied(tmp_path: Path) -> None:
    broker = FilesystemApprovalBroker()
    tool = RequestFilesystemAccessTool(broker, _policy(tmp_path))
    holder: list[ToolResult] = []
    worker = threading.Thread(
        target=lambda: holder.append(tool.invoke(_args(tmp_path, FilesystemAuthorizationLedger())))
    )
    worker.start()
    deadline = time.monotonic() + 2
    while not broker.pending_for_session("session-1") and time.monotonic() < deadline:
        time.sleep(0.01)

    broker.deny_session("session-1")
    worker.join(timeout=2)

    assert json.loads(holder[0].text)["status"] == "cancelled"


async def test_broker_lifecycle_is_pushed_immediately_with_monotonic_version(
    tmp_path: Path,
) -> None:
    registry = ListenerRegistry()
    subscriber = Subscriber(
        loop=asyncio.get_running_loop(),
        accept_kinds=frozenset({"filesystem_approval_changed"}),
    )
    registry.register(subscriber)
    broker = FilesystemApprovalBroker()
    broker.add_listener(registry.publish_filesystem_approval_change)
    request = PendingFilesystemApproval.create(
        session_id="session-1",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="读取文件",
        items=(
            ApprovalItem(
                item_id="file",
                path=tmp_path / "README.md",
                root_kind="file",
                permissions=frozenset({"read_text"}),
            ),
        ),
        actions=(),
    )
    worker = threading.Thread(target=lambda: broker.request(request, timeout_seconds=2))
    worker.start()

    requested = await asyncio.wait_for(subscriber.queue.get(), timeout=1)
    assert requested.kind == "filesystem_approval_changed"
    assert requested.filesystem_approval is not None
    assert requested.filesystem_approval["status"] == "pending"
    assert requested.filesystem_approval["version"] == 1

    broker.deny_session("session-1")
    cancelled = await asyncio.wait_for(subscriber.queue.get(), timeout=1)
    assert cancelled.filesystem_approval is not None
    assert cancelled.filesystem_approval["status"] == "cancelled"
    assert cancelled.filesystem_approval["version"] == 2
    worker.join(timeout=2)
