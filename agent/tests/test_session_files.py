from __future__ import annotations

import asyncio
import hashlib
import json
import stat
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from agent.attachments import AttachmentStore
from agent.command_runtime.workspace import check_workspace_budget, session_workspace_path
from agent.context.summary_prompt import render_transcript
from agent.session_files import SessionFileService
from agent.session_files.service import safe_filename
from agent.sessions import Event, JsonlSessionStore, Session, SessionManager
from agent.tools import ToolRegistry, make_default_registry
from agent.tools.builtin.session_files import SessionFilesTool

Environment = tuple[JsonlSessionStore, AttachmentStore, SessionFileService, ToolRegistry]

SESSION_ARG = "__agent_friend_current_session_id"


async def chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def upload(
    store: JsonlSessionStore, assets: AttachmentStore, sid: str, data: bytes, name: str = "same.pdf"
) -> Event:
    _, ref = asyncio.run(assets.import_stream(chunks(data), display_name=name))
    event = Event(
        uuid=str(uuid4()),
        type="user_message",
        ts=datetime.now(UTC),
        payload={"content": "read upload", "attachments": [ref.to_dict()]},
    )
    store.append_event(sid, event)
    return event


@pytest.fixture
def environment(
    tmp_path: Path,
) -> tuple[JsonlSessionStore, AttachmentStore, SessionFileService, ToolRegistry]:
    sessions = JsonlSessionStore(tmp_path / "sessions")
    for sid in ("one", "two"):
        sessions.create(Session.new(title="test", persona="default", model="fake", session_id=sid))
    assets = AttachmentStore(tmp_path / "assets")
    service = SessionFileService(sessions, assets, tmp_path / "workspaces")
    return sessions, assets, service, ToolRegistry([SessionFilesTool(service)])


def test_cross_turn_restart_compaction_same_name_and_identity(
    environment: Environment, tmp_path: Path
) -> None:
    sessions, assets, service, registry = environment
    first = upload(sessions, assets, "one", b"%PDF-1.7\noriginal")
    upload(sessions, assets, "one", b"%PDF-1.7\ndifferent")
    sessions.append_event(
        "one",
        Event(
            uuid=str(uuid4()),
            type="compaction",
            ts=datetime.now(UTC),
            payload={"summary": "same.pdf", "covered_through_uuid": first.uuid},
        ),
    )
    # Recreate both stores: no process-local inventory or model history survives.
    restarted = SessionFileService(
        JsonlSessionStore(tmp_path / "sessions"), AttachmentStore(tmp_path / "assets"), service.root
    )
    registry = ToolRegistry([SessionFilesTool(restarted)])
    page = json.loads(
        registry.invoke("session_files", {"action": "list", "limit": 1, SESSION_ARG: "one"}).text
    )
    assert page["next_offset"] == 1
    assert not service.root.exists()
    entry = page["files"][0]
    assert entry["source_message_ref"] == first.uuid
    assert entry["sha256"] == hashlib.sha256(b"%PDF-1.7\noriginal").hexdigest()
    result = registry.invoke(
        "session_files", {"action": "prepare", "file_ref": entry["file_ref"], SESSION_ARG: "one"}
    )
    assert not result.is_error
    prepared = json.loads(result.text)
    path = Path(prepared["workspace"]) / prepared["relative_path"]
    assert path.read_bytes() == b"%PDF-1.7\noriginal"
    assert "original" not in result.text
    second = restarted.prepare("one", restarted.files("one")[1].file_ref)
    assert second["relative_path"] != prepared["relative_path"]
    assert not path.samefile(assets.verify(entry["asset_id"]))
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.write_bytes(b"changed copy")
    assert assets.verify(entry["asset_id"]).read_bytes() == b"%PDF-1.7\noriginal"
    restarted.prepare("one", entry["file_ref"])
    assert path.read_bytes() == b"%PDF-1.7\noriginal"
    transcript = render_transcript(sessions.load("one").messages)
    assert first.uuid in transcript and entry["asset_id"] in transcript


def test_foreign_reference_and_inactive_upload_are_rejected(environment: Environment) -> None:
    sessions, assets, service, registry = environment
    event = upload(sessions, assets, "one", b"%PDF-1.7\nsecret")
    file_ref = service.files("one")[0].file_ref
    denied = registry.invoke(
        "session_files", {"action": "prepare", "file_ref": file_ref, SESSION_ARG: "two"}
    )
    assert denied.is_error and "session_file_not_found" in denied.text
    assert not service.root.exists()
    sessions.append_event(
        "one",
        Event(
            uuid=str(uuid4()),
            type="turn_rewrite",
            ts=datetime.now(UTC),
            payload={"inactive_event_uuids": [event.uuid]},
        ),
    )
    assert service.files("one") == []
    assert registry.invoke(
        "session_files", {"action": "prepare", "file_ref": file_ref, SESSION_ARG: "one"}
    ).is_error


@pytest.mark.parametrize("missing", [True, False])
def test_original_failure_never_uses_cached_or_same_name_file(
    environment: Environment, missing: bool, tmp_path: Path
) -> None:
    sessions, assets, service, registry = environment
    upload(sessions, assets, "one", b"%PDF-1.7\noriginal")
    entry = service.files("one")[0]
    prepared = service.prepare("one", entry.file_ref)
    (tmp_path / "same.pdf").write_bytes(b"local unrelated")
    original = assets.verify(entry.attachment.asset_id)
    if missing:
        original.unlink()
    else:
        original.write_bytes(b"%PDF-1.7\nreplaced")  # same byte count
    result = registry.invoke(
        "session_files", {"action": "prepare", "file_ref": entry.file_ref, SESSION_ARG: "one"}
    )
    assert result.is_error and "attachment_integrity_failed" in result.text
    assert (
        Path(prepared["workspace"]) / prepared["relative_path"]
    ).read_bytes() == b"%PDF-1.7\noriginal"


def test_schema_rejects_paths_and_ownership_overrides(environment: Environment) -> None:
    _, _, _, registry = environment
    for args in (
        {"action": "prepare", "file_ref": "../../other"},
        {"action": "list", "session_id": "two"},
        {"action": "prepare", "file_ref": "file-" + "a" * 64, "offset": 1},
        {"action": "list", "limit": 0},
    ):
        result = registry.invoke("session_files", {**args, SESSION_ARG: "one"})
        assert result.is_error and "invalid_arguments" in result.text
    wire = registry.to_openai_tools()[0]["function"]
    assert SESSION_ARG not in json.dumps(wire)
    assert "上下文压缩" in wire["description"]
    assert registry.invoke("session_files", {"action": "list"}).is_error


def test_filename_escape_and_symlink(environment: Environment, tmp_path: Path) -> None:
    sessions, assets, service, _ = environment
    event = upload(sessions, assets, "one", b"%PDF-1.7\ninput")
    # Persist a legacy unsafe display name: paths still derive only from safe host identifiers.
    sessions.append_event(
        "one",
        Event(
            uuid=str(uuid4()),
            type="user_message",
            ts=datetime.now(UTC),
            payload={
                "content": "unsafe name",
                "attachments": [
                    {**event.payload["attachments"][0], "display_name": "../../CON.pdf"}
                ],
            },
        ),
    )
    prepared = service.prepare("one", service.files("one")[1].file_ref)
    path = Path(prepared["workspace"]) / prepared["relative_path"]
    assert path.is_relative_to(session_workspace_path(service.root, "one"))
    assert "../" not in prepared["relative_path"]
    target = tmp_path / "outside"
    target.mkdir()
    foreign = session_workspace_path(service.root, "two")
    try:
        foreign.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    upload(sessions, assets, "two", b"%PDF-1.7\ninput")
    with pytest.raises(ValueError, match="unsafe_path"):
        service.prepare("two", service.files("two")[0].file_ref)
    assert list(target.iterdir()) == []


def test_capacity_preflight_counts_increment_and_does_not_delete(tmp_path: Path) -> None:
    original = tmp_path / "old.txt"
    original.write_bytes(b"12345")
    check_workspace_budget(tmp_path, added_bytes=5, added_files=1, max_bytes=10, max_files=2)
    with pytest.raises(ValueError, match="quota"):
        check_workspace_budget(tmp_path, added_bytes=6, added_files=1, max_bytes=10)
    with pytest.raises(ValueError, match="quota"):
        check_workspace_budget(tmp_path, added_bytes=5, added_files=1, max_file_bytes=4)
    assert original.read_bytes() == b"12345"


@pytest.mark.parametrize("name", ["CON.extra.pdf", "LPT¹.pdf", "NUL.pdf"])
def test_windows_reserved_names_are_portable(name: str) -> None:
    from agent.attachments import AttachmentRef

    ref = AttachmentRef("a" * 64, name, "document", "application/pdf", 1, "a" * 64)
    assert safe_filename(ref).startswith("_")
    assert safe_filename(ref).endswith(".pdf")


def test_factory_exposes_session_file_tool(environment: Environment) -> None:
    sessions, assets, _, _ = environment
    registry = make_default_registry(
        session_store=sessions, attachment_store=assets, stderr_warn=False
    )
    assert "session_files" in registry
    assert "session_files" not in make_default_registry(attachment_store=assets, stderr_warn=False)


def test_delete_cleans_readonly_inputs_but_preserves_other_session_and_asset(
    environment: Environment,
) -> None:
    sessions, assets, service, _ = environment
    for sid in ("one", "two"):
        upload(sessions, assets, sid, b"%PDF-1.7\nshared")
        service.prepare(sid, service.files(sid)[0].file_ref)
    asset = service.files("one")[0].attachment
    manager = SessionManager(store=sessions, command_workspaces_root=service.root)
    manager.delete("one")
    assert not session_workspace_path(service.root, "one").exists()
    assert session_workspace_path(service.root, "two").exists()
    assert assets.verify(asset.asset_id).read_bytes() == b"%PDF-1.7\nshared"


def test_cached_verified_copy_is_readable_even_when_new_writes_exceed_quota(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, assets, service, _ = environment
    upload(sessions, assets, "one", b"%PDF-1.7\nsmall")
    ref = service.files("one")[0].file_ref
    first = service.prepare("one", ref)

    def deny(*args: object, **kwargs: object) -> None:
        raise ValueError("session_workspace_quota_exceeded")

    monkeypatch.setattr("agent.session_files.service.check_workspace_budget", deny)
    assert service.prepare("one", ref) == first


def test_large_pdf_above_old_25_mib_limit_is_materialized_without_model_payload(
    environment: Environment,
) -> None:
    sessions, assets, service, registry = environment
    data = b"%PDF-1.7\n" + b" " * (26 * 1024 * 1024)
    upload(sessions, assets, "one", data)
    ref = service.files("one")[0].file_ref
    result = registry.invoke(
        "session_files", {"action": "prepare", "file_ref": ref, SESSION_ARG: "one"}
    )
    assert not result.is_error and len(result.text) < 2500
    payload = json.loads(result.text)
    path = Path(payload["workspace"]) / payload["relative_path"]
    assert path.stat().st_size == len(data)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
