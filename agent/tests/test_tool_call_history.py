"""Original-call recovery never replays operations or infers missing execution."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from agent.sessions import Event, JsonlSessionStore, Session
from agent.sessions.events import EventType
from agent.tool_runtime.control import (
    TOOL_INVOCATION_CONTROL_ARG,
    ToolInvocationCancelled,
    ToolInvocationControl,
)
from agent.tools.builtin.tool_call_history import InspectToolCallsTool
from jsonschema import ValidationError, validate

SESSION_ARG = "__agent_friend_current_session_id"


def setup_history(tmp_path: Path) -> tuple[JsonlSessionStore, InspectToolCallsTool]:
    store = JsonlSessionStore(tmp_path)
    for sid in ("current", "other"):
        store.create(Session.new(title="t", persona="default", model="m", session_id=sid))
    return store, InspectToolCallsTool(store)


def append(
    store: JsonlSessionStore,
    ref: str,
    *,
    kind: EventType = "tool_call_request",
    sid: str = "current",
    run: str = "r1",
    **payload: object,
) -> None:
    store.append_event(
        sid,
        Event(
            type=kind,
            uuid=ref,
            ts=datetime.now(UTC),
            payload={"tool_name": "exec_command", "tool_call_id": ref, **payload},
            meta={"run_id": run},
        ),
    )


def invoke(tool: InspectToolCallsTool, **args: object) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(tool.invoke({SESSION_ARG: "current", **args}).text))


def test_exact_parameters_are_paginated_and_private_identity_is_not_returned(
    tmp_path: Path,
) -> None:
    store, tool = setup_history(tmp_path)
    args = {"cmd": "x" * 1100, "__agent_friend_current_session_id": "private"}
    append(store, "call", args=args)
    append(store, "foreign", sid="other", args={"cmd": "secret"})
    hit = invoke(tool, mode="search")["results"][0]
    assert hit["call_ref"] == "call"
    assert hit["state"] == "unknown" and hit["executed"] is None
    pages = []
    offset = 0
    while True:
        page = invoke(tool, mode="read", call_ref="call", offset=offset, max_chars=500)
        assert page["arguments_complete"] is False
        pages.append(page["arguments_text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert json.loads("".join(pages)) == {"cmd": args["cmd"]}
    assert invoke(tool, mode="read", call_ref="foreign")["error"] == "call_ref_unavailable"


def test_pairing_prefers_event_identity_and_rejects_legacy_ambiguity(tmp_path: Path) -> None:
    store, tool = setup_history(tmp_path)
    append(store, "a", tool_call_id="same", event_tool_call_id="a-id", args={"cmd": "a"})
    append(store, "b", tool_call_id="same", event_tool_call_id="b-id", args={"cmd": "b"})
    append(
        store,
        "result-a",
        kind="tool_call_result",
        tool_call_id="same",
        event_tool_call_id="a-id",
        content="needle",
        executed=True,
        is_error=True,
    )
    a = invoke(tool, mode="read", call_ref="a")
    assert a["result_ref"] == "result-a" and a["executed"] is True and a["is_error"] is True
    assert invoke(tool, mode="read", call_ref="b")["state"] == "unknown"
    append(store, "c", tool_call_id="legacy")
    append(store, "d", tool_call_id="legacy")
    append(store, "legacy-result", kind="tool_call_result", tool_call_id="legacy")
    assert invoke(tool, mode="read", call_ref="c")["state"] == "association_conflict"
    append(store, "other-run", run="r2", tool_call_id="same")
    assert invoke(tool, mode="read", call_ref="other-run")["state"] == "unknown"
    assert [x["call_ref"] for x in invoke(tool, mode="search", query="needle")["results"]] == ["a"]


def test_cursor_is_stable_on_append_but_invalid_after_rewrite(tmp_path: Path) -> None:
    store, tool = setup_history(tmp_path)
    for ref in ("a", "b", "c"):
        append(store, ref, args={"cmd": ref})
    first = invoke(tool, mode="search", limit=1)
    assert first["results"][0]["call_ref"] == "c"
    append(store, "new", args={"cmd": "new"})
    second = invoke(tool, mode="search", limit=1, cursor=first["next_cursor"])
    assert second["results"][0]["call_ref"] == "b"
    assert (
        invoke(tool, mode="search", cursor=first["next_cursor"], query="changed")["error"]
        == "cursor_invalid"
    )
    append(store, "rewrite", kind="turn_rewrite", inactive_event_uuids=["a"])
    assert invoke(tool, mode="search", cursor=second["next_cursor"])["error"] == "cursor_invalid"
    assert invoke(tool, mode="read", call_ref="a")["error"] == "call_ref_unavailable"


def test_invalid_original_arguments_and_result_identity_remain_evidence(tmp_path: Path) -> None:
    store, tool = setup_history(tmp_path)
    append(store, "broken", args={}, arguments_json="{unfinished", arguments_state="invalid_json")
    page = invoke(tool, mode="read", call_ref="broken")
    assert page["arguments_text"] == "{unfinished"
    assert page["arguments_state"] == "invalid_json"
    assert invoke(tool, mode="read", call_ref="broken", offset=999)["error"] == "offset_invalid"
    append(store, "request", event_tool_call_id="stable", args={"cmd": "x"})
    append(
        store,
        "result",
        kind="tool_call_result",
        event_tool_call_id="stable",
        run="",
        executed=False,
        process_status="not_started",
        semantic_status="unknown",
        content="denied",
    )
    page = invoke(tool, mode="read", call_ref="request")
    assert page["result_ref"] == "result" and page["executed"] is False
    assert page["process_status"] == "not_started"
    assert page["semantic_status"] == "unknown"
    from agent.tools.builtin.tool_result_recovery import ToolResultRecoveryTool

    recovered = ToolResultRecoveryTool(store).invoke(
        {SESSION_ARG: "current", "result_ref": page["result_ref"]}
    )
    assert not recovered.is_error and "denied" in recovered.text


def test_scan_bound_reports_partial_search_and_cancellation_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, tool = setup_history(tmp_path)
    monkeypatch.setattr("agent.tools.builtin.tool_call_history.tool._SCAN_LIMIT", 2)
    for ref in ("a", "b", "c"):
        append(store, ref, args={"cmd": "absent"})
    result = invoke(tool, mode="search", query="needle")
    assert result["scanned_calls"] == 2 and result["complete"] is False
    assert result["results"] == [] and result["next_cursor"]
    control = ToolInvocationControl()
    control.cancel("test")
    with pytest.raises(ToolInvocationCancelled):
        tool.invoke(
            {SESSION_ARG: "current", "mode": "search", TOOL_INVOCATION_CONTROL_ARG: control}
        )


@pytest.mark.parametrize("status", ["running", "exited", "not_started", "unknown"])
def test_process_status_is_reported_without_inventing_business_success(
    tmp_path: Path, status: str
) -> None:
    store, tool = setup_history(tmp_path)
    append(store, "request", args={"cmd": "x"})
    append(
        store,
        "result",
        kind="tool_call_result",
        tool_call_id="request",
        process_status=status,
        semantic_status="unknown",
        content="historical output",
    )
    result = invoke(tool, mode="read", call_ref="request")
    assert result["process_status"] == status
    assert result["semantic_status"] == "unknown"
    assert result["executed"] is None


def test_damaged_history_never_returns_complete_no_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from agent.sessions.query import query_session
    from agent.sessions.store_types import SessionLoadDiagnostics

    store, tool = setup_history(tmp_path)
    snapshot = replace(
        query_session(store, "current"),
        load_diagnostics=SessionLoadDiagnostics(parse_error_count=1),
    )
    monkeypatch.setattr(
        "agent.tools.builtin.tool_call_history.tool.query_session", lambda *args, **kwargs: snapshot
    )
    result = tool.invoke({SESSION_ARG: "current", "mode": "search"})
    assert result.is_error and json.loads(result.text)["error"] == "history_incomplete"


@pytest.mark.parametrize(
    "args",
    [
        {"mode": "search", "session_id": "other"},
        {"mode": "search", "limit": 21},
        {"mode": "search", "call_ref": "a"},
        {"mode": "read"},
        {"mode": "read", "call_ref": "a", "query": "x"},
    ],
)
def test_schema_rejects_cross_scope_and_incompatible_arguments(args: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate(args, InspectToolCallsTool.parameters_schema)
