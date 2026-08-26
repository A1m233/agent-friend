from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import cast

import agent.filesystem.service as filesystem_service_module
import pytest
from agent.filesystem import (
    ApprovalAction,
    FilesystemAuthorizationLedger,
    FilesystemError,
    FilesystemService,
    ProtectedPathPolicy,
)
from agent.filesystem.service import MAX_TEXT_FILE_BYTES


def _policy(tmp_path: Path) -> ProtectedPathPolicy:
    return ProtectedPathPolicy(
        product_roots=((tmp_path / "product").resolve(),),
        personal_skill_roots=((tmp_path / "product" / "skills").resolve(),),
        builtin_skill_roots=(
            (tmp_path / "product" / "skills" / ".builtin").resolve(),
            (tmp_path / "product" / "skills" / ".system").resolve(),
        ),
    )


def test_managed_skill_system_root_is_never_generically_writable(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    managed = (
        tmp_path
        / "product"
        / "skills"
        / ".system"
        / "first-party"
        / "agent-friend-guide"
        / "state.json"
    ).resolve()

    assert policy.is_protected(managed)
    assert not policy.generic_write_allowed(managed)


def _grant(
    ledger: FilesystemAuthorizationLedger,
    root: Path,
    *permissions: str,
    allow_protected: bool = False,
) -> str:
    return ledger.grant_scope(
        root=root.resolve(),
        root_kind="directory" if root.is_dir() else "file",
        permissions=frozenset(permissions),  # type: ignore[arg-type]
        allow_protected=allow_protected,
    ).scope_id


def test_no_scope_and_parent_traversal_are_denied(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_text("hello", encoding="utf-8")
    service = FilesystemService(_policy(tmp_path))
    ledger = FilesystemAuthorizationLedger()

    with pytest.raises(FilesystemError, match="授权不存在") as missing:
        service.read_text(ledger, scope_id="missing", relative_path="note.txt")
    assert missing.value.code == "permission_denied"

    scope_id = _grant(ledger, root, "read_text")
    with pytest.raises(FilesystemError) as escape:
        service.read_text(ledger, scope_id=scope_id, relative_path="../outside.txt")
    assert escape.value.code == "scope_escape"


def test_search_metadata_does_not_return_file_content(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "needle.txt").write_text("private body", encoding="utf-8")
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "search_metadata")

    payload = FilesystemService(_policy(tmp_path)).search(
        ledger,
        scope_id=scope_id,
        mode="name",
        query="needle",
    )

    assert payload["matches"] == [
        {
            "name": "needle.txt",
            "path": "needle.txt",
            "type": "file",
            "size": 12,
            "mtime": payload["matches"][0]["mtime"],  # type: ignore[index]
            "protected": False,
        }
    ]
    assert "private body" not in str(payload)
    with pytest.raises(FilesystemError) as content_denied:
        FilesystemService(_policy(tmp_path)).search(
            ledger,
            scope_id=scope_id,
            mode="content",
            query="private",
        )
    assert content_denied.value.code == "permission_denied"


def test_content_search_returns_bounded_line_snippets(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.txt").write_text(
        "header\n"
        "first Needle result\n"
        "second needle result\n"
        "third needle result\n"
        "fourth needle result\n"
        "fifth needle result\n"
        "sixth needle result\n",
        encoding="utf-8",
    )
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")

    payload = FilesystemService(_policy(tmp_path)).search(
        ledger,
        scope_id=scope_id,
        mode="content",
        query="needle",
    )

    matches = cast(list[dict[str, object]], payload["matches"])
    assert len(matches) == 1
    match = matches[0]
    assert match["path"] == "notes.txt"
    assert match["content_match"] is True
    assert match["content_matches_truncated"] is True
    content_matches = cast(list[dict[str, object]], match["content_matches"])
    assert content_matches == [
        {"line": 2, "column": 7, "snippet": "first Needle result"},
        {"line": 3, "column": 8, "snippet": "second needle result"},
        {"line": 4, "column": 7, "snippet": "third needle result"},
        {"line": 5, "column": 8, "snippet": "fourth needle result"},
        {"line": 6, "column": 7, "snippet": "fifth needle result"},
    ]
    assert payload["content_snippet_chars"] == sum(
        len(cast(str, item["snippet"])) for item in content_matches
    )


def test_read_text_is_chunked_and_binary_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "text.txt").write_bytes(b"one\ntwo\nthree\n")
    (root / "blob.bin").write_bytes(b"a\x00b")
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")
    service = FilesystemService(_policy(tmp_path))

    payload = service.read_text(
        ledger,
        scope_id=scope_id,
        relative_path="text.txt",
        start_line=2,
        max_lines=1,
    )
    assert payload["content"] == "two\n"
    assert payload["truncated"] is True
    assert payload["truncation_reason"] == "max_lines"
    assert payload["next_start_line"] == 3
    assert payload["next_start_column"] == 0
    assert payload["returned_chars"] == 4
    assert payload["output_char_limit"] == 10_000
    assert payload["total_lines"] == 3

    with pytest.raises(FilesystemError) as binary:
        service.read_text(ledger, scope_id=scope_id, relative_path="blob.bin")
    assert binary.value.code == "unsupported_content"


def test_read_text_bounds_large_single_line_without_losing_continuation(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "long.txt").write_bytes(("x" * 15_000 + "\ntail\n").encode())
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")
    service = FilesystemService(_policy(tmp_path))

    first = service.read_text(ledger, scope_id=scope_id, relative_path="long.txt")

    assert first["content"] == "x" * 10_000
    assert first["truncated"] is True
    assert first["truncation_reason"] == "max_output_chars"
    assert first["next_start_line"] == 1
    assert first["next_start_column"] == 10_000
    assert first["end_line"] == 1
    assert first["end_column"] == 10_000

    remainder = service.read_text(
        ledger,
        scope_id=scope_id,
        relative_path="long.txt",
        start_line=first["next_start_line"],
        start_column=first["next_start_column"],
    )

    assert remainder["content"] == "x" * 5_000 + "\ntail\n"
    assert remainder["start_line"] == 1
    assert remainder["start_column"] == 10_000
    assert remainder["truncated"] is False
    assert remainder["next_start_line"] is None
    assert remainder["next_start_column"] is None


def test_large_text_search_streams_past_whole_file_read_limit(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "large.log"
    target.write_bytes(b"x" * (MAX_TEXT_FILE_BYTES + 1024) + b"\nlate needle\n")
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")

    payload = FilesystemService(_policy(tmp_path)).search(
        ledger,
        scope_id=scope_id,
        mode="content",
        query="late needle",
    )

    matches = cast(list[dict[str, object]], payload["matches"])
    assert [item["path"] for item in matches] == ["large.log"]
    assert cast(list[dict[str, object]], matches[0]["content_matches"])[0]["line"] == 2
    assert cast(int, payload["content_bytes_scanned"]) > MAX_TEXT_FILE_BYTES


def test_content_search_reports_truncation_when_stream_budget_is_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "large.log").write_bytes(b"x" * 8192 + b"needle\n")
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")
    monkeypatch.setattr(filesystem_service_module, "MAX_CONTENT_SEARCH_BYTES", 1024)

    payload = FilesystemService(_policy(tmp_path)).search(
        ledger,
        scope_id=scope_id,
        mode="content",
        query="needle",
    )

    assert payload["matches"] == []
    assert payload["truncated"] is True
    assert payload["content_bytes_scanned"] == 1024


def test_large_text_supports_bounded_line_range_and_tail(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "large.log"
    target.write_bytes(
        b"header\r\n" + b"x" * (MAX_TEXT_FILE_BYTES + 1024) + b"\r\npenultimate\r\nlast\r\n"
    )
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")
    service = FilesystemService(_policy(tmp_path))

    ranged = service.read_text(
        ledger,
        scope_id=scope_id,
        relative_path="large.log",
        start_line=3,
        max_lines=1,
    )
    tail = service.read_text_tail(
        ledger,
        scope_id=scope_id,
        relative_path="large.log",
        max_lines=2,
    )

    assert ranged["content"] == "penultimate\r\n"
    assert ranged["next_start_line"] == 4
    assert tail["content"] == "penultimate\r\nlast\r\n"
    assert tail["start_line"] == 3
    assert tail["total_lines"] == 4
    assert tail["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_atomic_create_overwrite_and_patch_require_current_version(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "create", "edit", "read_text")
    service = FilesystemService(_policy(tmp_path))

    created = service.write_text(
        ledger,
        scope_id=scope_id,
        relative_path="note.txt",
        content="alpha\nbeta\n",
    )
    old_hash = str(created["sha256"])
    target = (root / "note.txt").resolve()
    action = ApprovalAction("overwrite-1", "overwrite", target)
    ledger.grant_action(action)
    replaced = service.write_text(
        ledger,
        scope_id=scope_id,
        relative_path="note.txt",
        content="alpha\ngamma\n",
        expected_sha256=old_hash,
        overwrite_action_id=action.action_id,
    )
    assert replaced["status"] == "replaced"

    with pytest.raises(FilesystemError) as stale:
        service.apply_patch(
            ledger,
            scope_id=scope_id,
            relative_path="note.txt",
            expected_sha256=old_hash,
            patch="*** Begin Patch\n*** Update File: note.txt\n@@\n-alpha\n+omega\n*** End Patch\n",
        )
    assert stale.value.code == "target_changed"

    current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    patched = service.apply_patch(
        ledger,
        scope_id=scope_id,
        relative_path="note.txt",
        expected_sha256=current_hash,
        patch=(
            "*** Begin Patch\n*** Update File: note.txt\n@@\n alpha\n-gamma\n+delta\n"
            "*** End Patch\n"
        ),
    )
    assert patched["status"] == "patched"
    assert target.read_text(encoding="utf-8") == "alpha\ndelta\n"


def test_edit_only_scope_explains_that_missing_backup_needs_create(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "edit")

    with pytest.raises(FilesystemError) as missing:
        FilesystemService(_policy(tmp_path)).write_text(
            ledger,
            scope_id=scope_id,
            relative_path="config.toml.backup",
            content="backup",
        )

    assert missing.value.code == "permission_denied"
    assert "create" in str(missing.value)
    assert "edit" in str(missing.value)


def test_binary_copy_is_byte_transparent(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source_root.mkdir()
    destination_root.mkdir()
    raw = bytes(range(256)) * 4
    (source_root / "blob.bin").write_bytes(raw)
    ledger = FilesystemAuthorizationLedger()
    source_scope = _grant(ledger, source_root, "read_text")
    destination_scope = _grant(ledger, destination_root, "create")

    FilesystemService(_policy(tmp_path)).copy(
        ledger,
        source_scope_id=source_scope,
        source_path="blob.bin",
        destination_scope_id=destination_scope,
        destination_path="copy.bin",
    )

    assert (destination_root / "copy.bin").read_bytes() == raw


def test_copy_overwrite_uses_exact_action_and_replaces_bytes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.bin").write_bytes(b"new\x00bytes")
    target = root / "target.bin"
    target.write_bytes(b"old")
    ledger = FilesystemAuthorizationLedger()
    scope = _grant(ledger, root, "read_text", "create")
    action = ApprovalAction("overwrite-copy", "overwrite", target.resolve())
    ledger.grant_action(action)

    FilesystemService(_policy(tmp_path)).copy(
        ledger,
        source_scope_id=scope,
        source_path="source.bin",
        destination_scope_id=scope,
        destination_path="target.bin",
        overwrite_action_id=action.action_id,
    )

    assert target.read_bytes() == b"new\x00bytes"


def test_protected_parent_scope_is_cut_out_but_explicit_read_and_skill_write_work(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    skills = product / "skills"
    builtin = skills / ".builtin" / "creator"
    builtin.mkdir(parents=True)
    (product / "session.jsonl").write_text("history", encoding="utf-8")
    (builtin / "SKILL.md").write_text("builtin", encoding="utf-8")
    policy = _policy(tmp_path)
    service = FilesystemService(policy)
    ledger = FilesystemAuthorizationLedger()
    parent_scope = _grant(ledger, tmp_path, "read_text")

    with pytest.raises(FilesystemError) as protected:
        service.read_text(
            ledger,
            scope_id=parent_scope,
            relative_path="product/session.jsonl",
        )
    assert protected.value.code == "protected_path"

    explicit_scope = _grant(
        ledger,
        product / "session.jsonl",
        "read_text",
        allow_protected=True,
    )
    assert service.read_text(ledger, scope_id=explicit_scope)["content"] == "history"

    skill_scope = ledger.grant_scope(
        root=skills.resolve(),
        root_kind="directory",
        permissions=frozenset({"create", "edit"}),
        allow_protected=True,
    ).scope_id
    service.write_text(
        ledger,
        scope_id=skill_scope,
        relative_path="mine/SKILL.md",
        content="personal",
    )
    assert (skills / "mine" / "SKILL.md").read_text(encoding="utf-8") == "personal"
    with pytest.raises(FilesystemError) as builtin_write:
        service.write_text(
            ledger,
            scope_id=skill_scope,
            relative_path=".builtin/creator/new.txt",
            content="bad",
        )
    assert builtin_write.value.code == "protected_path"


def test_parent_search_and_recursive_delete_do_not_cross_protected_subtree(
    tmp_path: Path,
) -> None:
    product = tmp_path / "product"
    product.mkdir()
    (product / "private.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "public.txt").write_text("public", encoding="utf-8")
    ledger = FilesystemAuthorizationLedger()
    scope = _grant(ledger, tmp_path, "search_metadata", "edit")
    service = FilesystemService(_policy(tmp_path))

    searched = service.search(
        ledger,
        scope_id=scope,
        mode="name",
        query="private",
    )
    assert searched["matches"] == []

    action = ApprovalAction("delete-parent", "delete", tmp_path.resolve(), recursive=True)
    ledger.grant_action(action)
    with pytest.raises(FilesystemError) as delete:
        service.delete(
            ledger,
            scope_id=scope,
            relative_path=".",
            action_id=action.action_id,
            recursive=True,
        )
    assert delete.value.code == "protected_path"
    assert (product / "private.txt").is_file()


def test_destructive_action_is_bound_to_target_identity(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("before", encoding="utf-8")
    info = target.lstat()
    identity = f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
    ledger = FilesystemAuthorizationLedger()
    scope = _grant(ledger, root, "edit")
    action = ApprovalAction(
        "delete-note",
        "delete",
        target.resolve(),
        expected_identity=identity,
    )
    ledger.grant_action(action)
    target.write_text("changed after approval", encoding="utf-8")

    with pytest.raises(FilesystemError) as changed:
        FilesystemService(_policy(tmp_path)).delete(
            ledger,
            scope_id=scope,
            relative_path="note.txt",
            action_id=action.action_id,
        )
    assert changed.value.code == "target_changed"
    assert target.is_file()


def test_agent_instruction_metadata_requires_exact_scope(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "AGENTS.md"
    target.write_bytes(b"old\n")
    policy = _policy(tmp_path)
    service = FilesystemService(policy)
    ledger = FilesystemAuthorizationLedger()
    parent_scope = _grant(ledger, root, "create", "edit")
    old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    parent_action = ApprovalAction("parent-overwrite", "overwrite", target.resolve())
    ledger.grant_action(parent_action)

    with pytest.raises(FilesystemError) as protected:
        service.write_text(
            ledger,
            scope_id=parent_scope,
            relative_path="AGENTS.md",
            content="bad\n",
            expected_sha256=old_hash,
            overwrite_action_id=parent_action.action_id,
        )
    assert protected.value.code == "protected_path"

    exact_scope = ledger.grant_scope(
        root=target.resolve(),
        root_kind="file",
        permissions=frozenset({"edit"}),
        allow_protected=True,
    ).scope_id
    exact_action = ApprovalAction("exact-overwrite", "overwrite", target.resolve())
    ledger.grant_action(exact_action)
    service.write_text(
        ledger,
        scope_id=exact_scope,
        relative_path=".",
        content="new\n",
        expected_sha256=old_hash,
        overwrite_action_id=exact_action.action_id,
    )
    assert target.read_bytes() == b"new\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs optional privileges")
def test_symlink_escape_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")

    with pytest.raises(FilesystemError) as escaped:
        FilesystemService(_policy(tmp_path)).read_text(
            ledger,
            scope_id=scope_id,
            relative_path="link/secret.txt",
        )
    assert escaped.value.code in {"scope_escape", "link_not_allowed"}


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
def test_windows_junction_escape_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    junction = root / "junction"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    ledger = FilesystemAuthorizationLedger()
    scope_id = _grant(ledger, root, "read_text")

    with pytest.raises(FilesystemError) as escaped:
        FilesystemService(_policy(tmp_path)).read_text(
            ledger,
            scope_id=scope_id,
            relative_path="junction/secret.txt",
        )
    assert escaped.value.code in {"scope_escape", "link_not_allowed"}
