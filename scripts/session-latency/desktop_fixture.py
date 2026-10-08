"""Prepare isolated desktop history fixtures without starting services or using a network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

if TYPE_CHECKING:
    from agent.sessions.events import Event

ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime(2026, 10, 1, tzinfo=UTC)
TOOL_COUNT = 96


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"agent-friend/157/desktop-fixture/{name}"))


def deny_network(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("desktop_fixture_forbids_network")


def storage_imports(data: Path) -> None:
    socket.socket.connect = deny_network  # type: ignore[method-assign]
    socket.socket.connect_ex = deny_network  # type: ignore[method-assign]
    socket.create_connection = deny_network
    os.environ.update(
        AGENT_FRIEND_DATA_DIR=str(data),
        AGENT_FRIEND_LOG_DIR=str(data.parent / "logs"),
        LITELLM_LOCAL_MODEL_COST_MAP="True",
    )
    for package in ("agent", "llm_providers", "memory", "runtime_config"):
        sys.path.insert(0, str(ROOT / package / "src"))
    # Import storage protocols without constructing application/provider services.
    for name, relative in (
        ("agent", "agent/src/agent"),
        ("agent.sessions", "agent/src/agent/sessions"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(ROOT / relative)]
        sys.modules[name] = module


def synthetic_events(kind: str, model: str) -> tuple[list[Event], dict[str, Any]]:
    from agent.sessions.events import Event, EventType
    from agent.sessions.session import Session

    sid, rid = identity(kind), identity(f"{kind}/run")
    session = Session.new(
        f"[157 夹具] {'普通短会话' if kind == 'short' else '单回合 96 工具与大正文'}",
        "default",
        model,
        session_id=sid,
        created_at=STAMP,
    )
    events = list(session.events)

    def append(event_type: EventType, name: str, **payload: Any) -> Event:
        event = Event(
            event_type,
            identity(f"{kind}/{name}"),
            STAMP + timedelta(seconds=len(events)),
            payload,
            {"run_id": rid, "owner_runtime_instance_id": "157-offline-fixture"},
        )
        events.append(event)
        return event

    append(
        "run_started",
        "started",
        run_id=rid,
        started_at=STAMP.isoformat(),
        source_kind="user_message",
        presentation="conversation",
        output_visibility="user",
    )
    user_marker = f"157_{kind.upper()}_USER"
    append("user_message", "user", content=f"{user_marker}：这是离线展示夹具。")
    calls: list[dict[str, str]] = []
    if kind == "long":
        for number in range(TOOL_COUNT):
            marker = f"157_TOOL_{number:03d}"
            call_id = identity(f"{kind}/tool/{number}")
            request = append(
                "tool_call_request",
                f"request/{number}",
                event_tool_call_id=call_id,
                tool_call_id=call_id,
                tool_name="exec_command",
                arguments_json=json.dumps({"cmd": f"echo {marker}", "workdir": "."}),
            )
            body = json.dumps(
                {
                    "exit_code": 0,
                    "status": "exited",
                    "stdout": f"{marker}_BEGIN\n" + "离线工具结果 αβ😀\n" * 600 + f"{marker}_END",
                    "stderr": "",
                    "pty": "",
                },
                ensure_ascii=False,
            )
            result = append(
                "tool_call_result",
                f"result/{number}",
                event_tool_call_id=call_id,
                tool_call_id=call_id,
                tool_name="exec_command",
                content=body,
                is_error=False,
            )
            calls.append(
                {
                    "request_uuid": request.uuid,
                    "result_uuid": result.uuid,
                    "marker": marker,
                    "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                }
            )
    answer_marker = f"157_{kind.upper()}_ANSWER"
    answer = f"{answer_marker}_BEGIN\n"
    answer += (
        "全部 96 个工具只作为历史记录展示；本夹具没有执行命令。\n" * 400
        if calls
        else "普通短会话的完整回复。\n"
    )
    answer += f"{answer_marker}_END"
    append("assistant_message", "answer", content=answer)
    append(
        "run_terminal",
        "terminal",
        run_id=rid,
        status="completed",
        reason_code="completed",
        source="runtime",
        recovered=False,
        started_at=STAMP.isoformat(),
        finished_at=(STAMP + timedelta(seconds=len(events))).isoformat(),
    )
    return events, {
        "kind": kind,
        "session_id": sid,
        "title": session.initial_title,
        "run_id": rid,
        "expected": {
            "user_marker": user_marker,
            "answer_marker": answer_marker,
            "user_uuid": identity(f"{kind}/user"),
            "answer_uuid": identity(f"{kind}/answer"),
            "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "tool_count": len(calls),
            "display_items": len(calls) + 2,
            "tools": calls,
        },
    }


def verify_synthetic(directory: Path, row: dict[str, Any]) -> dict[str, Any]:
    from agent.sessions.store import JsonlSessionStore

    store = JsonlSessionStore(directory)
    sid = row["session_id"]
    expected = row["expected"]
    visible: dict[str, Any] = {}
    before = epoch = None
    pages = 0
    largest = 0
    while True:
        page = store.history_page(sid, before=before, epoch=epoch)
        pages += 1
        size = len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        if size > 262144 or len(page["events"]) > 80:
            raise ValueError("synthetic_history_exceeds_bound")
        largest = max(largest, size)
        epoch = page["history_epoch"]
        for event in page["events"]:
            if event["uuid"] in visible:
                raise ValueError("synthetic_history_duplicate_cursor")
            visible[event["uuid"]] = event
        if not page["has_before"]:
            break
        before = page["before_cursor"]
    if len(visible) != expected["display_items"]:
        raise ValueError("synthetic_history_missing_items")
    for tool in expected["tools"]:
        item = visible[tool["request_uuid"]]
        result = item.get("result_event", {})
        if result.get("uuid") != tool["result_uuid"] or result["body"]["state"] != "deferred":
            raise ValueError("synthetic_tool_association_or_deferral_failed")
    # Check the command card's actual result contract at both ends of the run.
    detail_samples = expected["tools"][:: max(1, len(expected["tools"]) - 1)]
    for tool in detail_samples:
        text, offset = "", 0
        while True:
            chunk = store.history_content(sid, tool["result_uuid"], version=epoch, offset=offset)
            text += chunk["text"]
            offset = chunk["next_offset"]
            if chunk["complete"]:
                break
        content = json.loads(text)["payload"]["content"]
        decoded = json.loads(content)
        if (
            hashlib.sha256(content.encode("utf-8")).hexdigest() != tool["content_sha256"]
            or decoded.get("status") != "exited"
            or decoded.get("exit_code") != 0
            or not decoded.get("stdout", "").startswith(tool["marker"] + "_BEGIN")
            or not decoded.get("stdout", "").endswith(tool["marker"] + "_END")
            or decoded.get("stderr") != ""
        ):
            raise ValueError("synthetic_command_display_contract_failed")
    store.discard_business_index(sid)
    return {
        "pages": pages,
        "largest_response_bytes": largest,
        "linked_tools": len(expected["tools"]),
        "command_detail_samples": len(detail_samples),
    }


def prepare(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    if source.suffix.lower() != ".jsonl" or not source.is_file():
        raise ValueError("fixture must be an explicit existing JSONL file")
    before = source.stat()
    with source.open("rb") as handle:
        meta = json.loads(handle.readline())
    if meta.get("type") != "session_meta":
        raise ValueError("fixture requires session_meta first")
    sid = str(UUID(meta["uuid"]))
    source_hash = digest_file(source)
    output.mkdir(parents=True, exist_ok=False)
    directory = output / "data" / "sessions"
    directory.mkdir(parents=True)
    copied = directory / f"{sid}.jsonl"
    with source.open("rb") as original, copied.open("xb") as target:
        shutil.copyfileobj(original, target)
        target.flush()
        os.fsync(target.fileno())
    copied_hash = digest_file(copied)
    after = source.stat()
    if (
        (before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_ino, after.st_size, after.st_mtime_ns)
        or source_hash != copied_hash
        or digest_file(source) != source_hash
    ):
        raise ValueError("source_changed_during_copy; incomplete output retained without identity")
    storage_imports(output / "data")
    rows = [
        {
            "kind": "fixed_gb",
            "session_id": sid,
            "title": meta["payload"]["initial_title"],
            "sha256": copied_hash,
            "bytes": before.st_size,
            "expected": {"copied_verbatim": True},
        }
    ]
    for kind in ("short", "long"):
        events, row = synthetic_events(kind, meta["payload"]["initial_model"])
        path = directory / f"{row['session_id']}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(event.to_jsonl() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        row.update(sha256=digest_file(path), bytes=path.stat().st_size)
        row["verification"] = verify_synthetic(directory, row)
        rows.append(row)
    report = {
        "schema_version": 1,
        "kind": "isolated_desktop_history_fixture",
        "created_at": datetime.now(UTC).isoformat(),
        "script_sha256": digest_file(Path(__file__)),
        "original": {"path": str(source), "sha256": source_hash, "unchanged": True},
        "data_dir": str(output / "data"),
        "log_dir": str(output / "logs"),
        "sessions": rows,
        "limits": [
            "No runtime, tools, LLM, network, personas, credentials, or settings copied or started.",
            "Synthetic histories are completed legacy business histories, not exact execution snapshots.",
            "GB source is copied byte-for-byte; desktop recovery may later append to this isolated copy.",
            "Synthetic presentation self-checks discard derived indices before desktop use.",
        ],
    }
    with (output / "identity.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.fixture, args.output)
    print(
        json.dumps(
            {
                "identity": str(args.output.resolve() / "identity.json"),
                "sessions": [
                    {key: row[key] for key in ("kind", "session_id", "bytes", "sha256")}
                    for row in report["sessions"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
