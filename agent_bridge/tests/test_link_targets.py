from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from agent.command_runtime.workspace import session_workspace_path
from agent.sessions import Event, JsonlSessionStore, Session
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.routes.link_targets import inspect_path, local_path, register_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import AttachmentStore


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    sessions = JsonlSessionStore(tmp_path / "sessions")
    for sid in ("one", "two"):
        sessions.create(Session.new(title="test", persona="default", model="fake", session_id=sid))
    assets = AttachmentStore(tmp_path / "assets")
    workspaces = tmp_path / "workspaces"
    monkeypatch.setattr(
        "agent_bridge.routes.link_targets.command_workspaces_dir", lambda: workspaces
    )
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(
            persistent_store=sessions,
            attachment_store=assets,
            persona_store=SimpleNamespace(user_file=tmp_path / "persona" / "persona.md"),
        ),
    )
    app = FastAPI()
    register_routes(app, runtime)
    return TestClient(app), sessions, assets, workspaces


def test_exact_file_names_and_uri_decoding(tmp_path: Path) -> None:
    path = tmp_path / "报告 (1) %20 #name_.txt"
    path.write_text("test", encoding="utf-8")
    assert inspect_path(local_path(str(path), None))["value"] == str(path)
    assert inspect_path(local_path(path.as_uri(), None))["value"] == str(path)
    assert inspect_path(local_path(path.as_uri() + "#L42", None))["value"] == str(path)
    assert inspect_path(Path(str(path) + ":42:2"))["value"] == str(path)
    assert inspect_path(Path(str(path) + "#L42"))["value"] == str(path)
    if os.name != "nt":
        exact = Path(str(path) + ":42")
        exact.write_text("other", encoding="utf-8")
        assert inspect_path(exact)["value"] == str(exact)


def test_encoded_markdown_paths_do_not_double_decode_uris(tmp_path: Path) -> None:
    spaced = tmp_path / "report one.pdf"
    spaced.write_text("one", encoding="utf-8")
    literal = tmp_path / "report%20one.pdf"
    assert inspect_path(literal, allow_url_encoding=True)["value"] == str(spaced)
    assert inspect_path(Path(str(literal) + ":42:2"), allow_url_encoding=True)["value"] == str(
        spaced
    )
    assert inspect_path(local_path(literal.as_uri(), None))["kind"] == "unavailable"
    literal.write_text("different", encoding="utf-8")
    assert inspect_path(literal, allow_url_encoding=True)["value"] == str(literal)


@pytest.mark.parametrize(
    "value", ["javascript:alert(1)", "C:relative", "//ambiguous/path", "\x00bad", "file:///bad%XX"]
)
def test_rejects_ambiguous_and_non_file_targets(value: str) -> None:
    with pytest.raises(ValueError):
        local_path(value, None)


def test_relative_target_is_bound_to_its_session(environment) -> None:  # type: ignore[no-untyped-def]
    client, _, _, workspaces = environment
    for sid in ("one", "two"):
        path = session_workspace_path(workspaces, sid) / "report.txt"
        path.parent.mkdir(parents=True)
        path.write_text(sid, encoding="utf-8")
        result = client.post(
            "/v1/link-targets/resolve",
            json={"context": "session", "sessionId": sid, "target": "report.txt"},
        )
        assert result.status_code == 200
        assert result.json() == {"kind": "file", "value": str(path)}
    result = client.post(
        "/v1/link-targets/resolve", json={"context": "session", "target": "report.txt"}
    )
    assert result.json()["kind"] == "unavailable"
    assert "目录信息" in result.json()["reason"]


def test_attachment_identity_cannot_be_borrowed_from_another_session(environment) -> None:  # type: ignore[no-untyped-def]
    client, sessions, assets, _ = environment

    async def chunks() -> AsyncIterator[bytes]:
        yield b"%PDF-1.7\noriginal"

    draft, ref = asyncio.run(assets.import_stream(chunks(), display_name="report.pdf"))
    event = Event(
        uuid=str(uuid4()),
        type="user_message",
        ts=datetime.now(UTC),
        payload={"content": "read", "attachments": [ref.to_dict()]},
    )
    sessions.append_event("one", event)
    one = client.post(
        "/v1/link-targets/resolve",
        json={"context": "session", "sessionId": "one", "assetId": ref.asset_id},
    ).json()
    assert one["kind"] == "file"
    assert Path(one["value"]).read_bytes() == b"%PDF-1.7\noriginal"
    two = client.post(
        "/v1/link-targets/resolve",
        json={"context": "session", "sessionId": "two", "assetId": ref.asset_id},
    ).json()
    assert two["kind"] == "unavailable" and two["value"] == ""
    assert (
        client.post(
            "/v1/link-targets/resolve", json={"context": "session", "draftId": draft}
        ).json()["kind"]
        == "file"
    )
    assets.release_draft(draft)
    assert (
        client.post(
            "/v1/link-targets/resolve", json={"context": "session", "draftId": draft}
        ).json()["kind"]
        == "unavailable"
    )


def test_missing_and_wrong_context_have_visible_errors(environment, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client, _, _, _ = environment
    result = client.post(
        "/v1/link-targets/resolve",
        json={"context": "session", "sessionId": "missing", "target": str(tmp_path)},
    ).json()
    assert result["kind"] == "unavailable" and "会话" in result["reason"]
    result = client.post(
        "/v1/link-targets/resolve", json={"context": "persona", "target": "missing.txt"}
    ).json()
    assert result["kind"] == "unavailable" and "不存在" in result["reason"]
    assert (
        client.post(
            "/v1/link-targets/resolve", json={"context": "session", "target": "a", "draftId": "b"}
        ).status_code
        == 422
    )
