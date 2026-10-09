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
TOOL_COUNT = 10
LONG_TURNS = 65


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"agent-friend/complete-turn-fixture/{name}"))


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
    from agent.message_references import QuotedTextReference
    from agent.sessions.events import Event, EventType
    from agent.sessions.session import Session

    sid = identity(kind)
    count = 2 if kind == "short" else LONG_TURNS
    session = Session.new(
        f"[分页夹具] {'普通短会话' if kind == 'short' else '65轮与10个重复工具'}",
        "default",
        model,
        session_id=sid,
        created_at=STAMP,
    )
    events = list(session.events)
    turns: list[dict[str, Any]] = []
    for turn in range(count):
        prefix = f"{kind}/{turn}"
        rid = identity(f"{prefix}/run")
        started = STAMP + timedelta(seconds=len(events))

        def append(
            event_type: EventType,
            name: str,
            *,
            _prefix: str = prefix,
            _rid: str = rid,
            **payload: Any,
        ) -> Event:
            event = Event(
                event_type,
                identity(f"{_prefix}/{name}"),
                STAMP + timedelta(seconds=len(events)),
                payload,
                {"run_id": _rid, "owner_runtime_instance_id": "offline-history-fixture"},
            )
            events.append(event)
            return event

        append(
            "run_started",
            "started",
            run_id=rid,
            started_at=started.isoformat(),
            source_kind="user_message",
            presentation="conversation",
            output_visibility="user",
        )
        marker = f"PAGE_{kind.upper()}_{turn:03d}"
        references: list[dict[str, Any]] = []
        expected_references: list[dict[str, str]] = []
        if kind == "long" and turn == count - 1:
            oldest = turns[0]
            for role, source_uuid, quote in (
                ("user", oldest["user_uuid"], oldest["user_marker"]),
                ("assistant", oldest["answer_uuid"], oldest["answer_marker"] + "_BEGIN"),
            ):
                reference = QuotedTextReference.from_dict(
                    {
                        "reference_id": identity(f"{prefix}/reference/{role}"),
                        "quoted_text": quote,
                        "source_role": role,
                        "availability": "available",
                        "source": {
                            "kind": "session_message",
                            "message_event_uuid": source_uuid,
                            "text_block_id": source_uuid,
                            "external_source_key": None,
                        },
                        "locator": {
                            "kind": "text_range",
                            "coordinate_space": "rendered_text_utf16_v1",
                            "start": 0,
                            "end": len(quote),
                        },
                    }
                )
                references.append(reference.to_dict())
                expected_references.append(
                    {
                        "reference_id": reference.reference_id,
                        "source_role": role,
                        "source_uuid": source_uuid,
                        "text_block_id": source_uuid,
                        "marker": quote,
                        "quoted_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                    }
                )
        user = append(
            "user_message",
            "user",
            content=f"{marker}_USER：这是离线展示夹具。",
            **({"references": references} if references else {}),
        )
        calls: list[dict[str, str]] = []
        if kind == "long" and turn == count - 1:
            for number in range(TOOL_COUNT):
                tool_marker = f"PAGE_TOOL_{number:03d}"
                call_id = identity(f"{prefix}/tool/{number}")
                request = append(
                    "tool_call_request",
                    f"request/{number}",
                    event_tool_call_id=call_id,
                    tool_call_id=call_id,
                    tool_name="exec_command",
                    arguments_json=json.dumps({"cmd": f"echo {tool_marker}", "workdir": "."}),
                )
                content = json.dumps(
                    {
                        "exit_code": 0,
                        "status": "exited",
                        "stdout": f"{tool_marker}_BEGIN\n"
                        + "离线工具结果 αβ😀\n" * 400
                        + f"{tool_marker}_END",
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
                    content=content,
                    is_error=False,
                )
                calls.append(
                    {
                        "request_uuid": request.uuid,
                        "result_uuid": result.uuid,
                        "tool_call_id": call_id,
                        "marker": tool_marker,
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    }
                )
        answer_marker = marker + "_ANSWER"
        answer = answer_marker + "_BEGIN\n"
        answer += (
            "本轮十个工具仅为历史记录，未执行任何命令。\n" * 200
            if calls
            else "这一轮的完整回复。\n"
        )
        answer += answer_marker + "_END"
        reply = append("assistant_message", "answer", content=answer)
        append(
            "run_terminal",
            "terminal",
            run_id=rid,
            status="completed",
            reason_code="completed",
            source="runtime",
            recovered=False,
            started_at=started.isoformat(),
            finished_at=(STAMP + timedelta(seconds=len(events))).isoformat(),
        )
        turns.append(
            {
                "number": turn,
                "run_id": rid,
                "user_marker": marker + "_USER",
                "answer_marker": answer_marker,
                "user_uuid": user.uuid,
                "answer_uuid": reply.uuid,
                "message_uuid": calls[0]["request_uuid"] if calls else reply.uuid,
                "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                "answer_bytes": len(answer.encode()),
                "tool_count": len(calls),
                "tools": calls,
                "references": expected_references,
            }
        )
    return events, {
        "kind": kind,
        "session_id": sid,
        "title": session.initial_title,
        "expected": {
            "turn_count": count,
            "latest_turn_count": min(count, 30),
            "message_count": count * 2,
            "turns": turns,
            "oldest": turns[0],
            "latest": turns[-1],
        },
    }


def verify_synthetic(directory: Path, row: dict[str, Any]) -> dict[str, Any]:
    from agent.message_references import parse_quoted_text_references
    from agent.sessions.store import JsonlSessionStore

    store = JsonlSessionStore(directory)
    seen: dict[str, Any] = {}
    pages: list[int] = []
    before = epoch = None
    largest = 0
    while True:
        page = store.query_page(
            row["session_id"], turn_limit=30, before=before, history_epoch=epoch
        )
        current = [json.loads(event.to_jsonl()) for event in page.active_events]
        turns = sum(item["type"] in {"user_message", "system_trigger"} for item in current)
        pages.append(turns)
        if turns > 30 or any("body" in item or "result_event" in item for item in current):
            raise ValueError("synthetic_history_contract_changed")
        for item in current:
            if item["uuid"] in seen and item["type"] != "session_meta":
                raise ValueError("synthetic_history_duplicate_event")
            seen[item["uuid"]] = item
        page_ids = {item["uuid"] for item in current}
        for turn in row["expected"]["turns"]:
            if turn["user_uuid"] not in page_ids:
                continue
            if turn["answer_uuid"] not in page_ids:
                raise ValueError("synthetic_history_split_turn")
            for tool in turn["tools"]:
                if not {tool["request_uuid"], tool["result_uuid"]} <= page_ids:
                    raise ValueError("synthetic_history_split_tool_pair")
                content = seen[tool["result_uuid"]]["payload"]["content"]
                if hashlib.sha256(content.encode()).hexdigest() != tool["content_sha256"]:
                    raise ValueError("synthetic_tool_content_changed")
            for reference in turn["references"]:
                if reference["source_uuid"] in page_ids:
                    raise ValueError("synthetic_reference_source_must_require_older_pages")
        largest = max(largest, len(json.dumps(current, ensure_ascii=False).encode()))
        epoch = page.history_epoch
        if page.history_cursor is None:
            break
        if before == page.history_cursor:
            raise ValueError("synthetic_history_cursor_did_not_advance")
        before = page.history_cursor
    expected_pages = [30, 30, 5] if row["kind"] == "long" else [2]
    if pages != expected_pages:
        raise ValueError("synthetic_history_turn_count_mismatch")
    for turn in row["expected"]["turns"]:
        answer = seen[turn["answer_uuid"]]["payload"]["content"]
        if hashlib.sha256(answer.encode()).hexdigest() != turn["answer_sha256"]:
            raise ValueError("synthetic_answer_changed")
        references = parse_quoted_text_references(
            seen[turn["user_uuid"]]["payload"].get("references")
        )
        if len(references) != len(turn["references"]):
            raise ValueError("synthetic_reference_count_changed")
        for reference, expected in zip(references, turn["references"], strict=True):
            source = seen[expected["source_uuid"]]
            quote = reference.quoted_text
            if (
                quote is None
                or reference.reference_id != expected["reference_id"]
                or reference.source.message_event_uuid != source["uuid"]
                or reference.source.text_block_id != source["uuid"]
                or source["type"] != reference.source_role + "_message"
                or not source["payload"]["content"].startswith(quote)
                or hashlib.sha256(quote.encode()).hexdigest() != expected["quoted_sha256"]
            ):
                raise ValueError("synthetic_reference_source_changed")
    store.discard_business_index(row["session_id"])
    return {
        "page_turn_counts": pages,
        "largest_event_bytes": largest,
        "full_tool_pairs": sum(turn["tool_count"] for turn in row["expected"]["turns"]),
        "unloaded_reference_sources": sum(
            len(turn["references"]) for turn in row["expected"]["turns"]
        ),
    }


def prepare(
    source: Path | None, output: Path, *, model: str = "deepseek/deepseek-v4-flash"
) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    output.mkdir(parents=True, exist_ok=False)
    directory = output / "data" / "sessions"
    directory.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    original = None
    if source is not None:
        source = source.resolve(strict=True)
        before = source.stat()
        with source.open("rb") as handle:
            meta = json.loads(handle.readline())
        if source.suffix.lower() != ".jsonl" or meta.get("type") != "session_meta":
            raise ValueError("fixture requires an explicit session_meta JSONL")
        sid = str(UUID(meta["uuid"]))
        source_hash = digest_file(source)
        copied = directory / f"{sid}.jsonl"
        with source.open("rb") as origin, copied.open("xb") as target:
            shutil.copyfileobj(origin, target)
        after = source.stat()
        if (
            (before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_ino, after.st_size, after.st_mtime_ns)
            or digest_file(copied) != source_hash
            or digest_file(source) != source_hash
        ):
            raise ValueError("source_changed_during_copy")
        original = {"path": str(source), "sha256": source_hash, "unchanged": True}
        rows.append(
            {
                "kind": "fixed_copy",
                "session_id": sid,
                "title": meta["payload"]["initial_title"],
                "sha256": source_hash,
                "bytes": before.st_size,
                "expected": {"copied_verbatim": True},
            }
        )
    storage_imports(output / "data")
    for kind in ("short", "long"):
        events, row = synthetic_events(kind, model)
        path = directory / f"{row['session_id']}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(event.to_jsonl() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        row.update(sha256=digest_file(path), bytes=path.stat().st_size)
        row["verification"] = verify_synthetic(directory, row)
        rows.append(row)
    backup = output / "settings-backup"
    backup.mkdir()
    report = {
        "schema_version": 2,
        "kind": "isolated_desktop_turn_history_fixture",
        "synthetic_only": source is None,
        "created_at": datetime.now(UTC).isoformat(),
        "script_sha256": digest_file(Path(__file__)),
        "original": original,
        "data_dir": str(output / "data"),
        "log_dir": str(output / "logs"),
        "sessions": rows,
        "settings_backup": {"directory": str(backup), "prepared_only": True, "files_copied": 0},
        "limits": [
            "No runtime, tools, LLM, network, personas, credentials, or settings read or started.",
            "Synthetic histories are completed business histories, not exact execution snapshots.",
            "AGENT_FRIEND_DATA_DIR does not redirect Tauri settings; runtime owner must preserve profile settings separately.",
            "Derived fixture indices are discarded after the complete-turn self-check.",
        ],
    }
    with (output / "identity.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path)
    source.add_argument("--synthetic-only", action="store_true")
    parser.add_argument("--model", default="deepseek/deepseek-v4-flash")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.fixture, args.output, model=args.model)
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
