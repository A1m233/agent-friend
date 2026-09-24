"""Discover uploads from durable active history, never from compacted model history."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from ..attachments import AttachmentError, AttachmentRef, AttachmentStore
from ..command_runtime.workspace import (
    MAX_SESSION_WORKSPACE_FILE_BYTES,
    check_workspace_budget,
    reject_linked_path,
    session_workspace_path,
)
from ..sessions.store import SessionStore


class SessionFileError(ValueError):
    """Expected, model-visible failure with a stable code."""


@dataclass(frozen=True)
class SessionFile:
    file_ref: str
    source_message_ref: str
    attachment: AttachmentRef

    def metadata(self) -> dict[str, Any]:
        return {
            "file_ref": self.file_ref,
            "source": "user_upload",
            "source_message_ref": self.source_message_ref,
            **self.attachment.to_dict(),
        }


def safe_filename(ref: AttachmentRef) -> str:
    name = re.sub(r"[^\w.() -]", "_", ref.display_name, flags=re.UNICODE).strip(" .")
    suffix = Path(name).suffix[:12]
    stem = name[: -len(suffix)] if suffix else name
    stem = stem[:40].strip(" .") or "input"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])", stem.split(".")[0]):
        stem = f"_{stem}"
    return stem + suffix


def verified_file(path: Path, ref: AttachmentRef) -> bool:
    if not path.is_file() or path.stat().st_size != ref.size_bytes:
        return False
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        remaining = ref.size_bytes
        while chunk := stream.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                return False
            digest.update(chunk)
    return remaining == 0 and digest.hexdigest() == ref.sha256


class SessionFileService:
    def __init__(self, sessions: SessionStore, assets: AttachmentStore, root: Path) -> None:
        self.sessions = sessions
        self.assets = assets
        self.root = root.absolute()
        self._lock = RLock()

    def files(self, session_id: str) -> list[SessionFile]:
        if not session_id:
            raise SessionFileError("session_context_missing")
        session = self.sessions.load(session_id)
        found = []
        for event in session.active_events:
            if event.type != "user_message":
                continue
            for index, raw in enumerate(event.payload.get("attachments") or []):
                try:
                    ref = AttachmentRef.from_dict(raw)
                except (KeyError, ValueError, TypeError) as exc:
                    raise SessionFileError("session_file_reference_invalid") from exc
                if (
                    not re.fullmatch(r"[0-9a-f]{64}", ref.asset_id)
                    or ref.asset_id != ref.sha256
                    or ref.size_bytes <= 0
                ):
                    raise SessionFileError("session_file_reference_invalid")
                identity = hashlib.sha256(f"{event.uuid}:{index}".encode()).hexdigest()
                found.append(SessionFile(f"file-{identity}", event.uuid, ref))
        return found

    def prepare(self, session_id: str, file_ref: str) -> dict[str, Any]:
        with self._lock:
            selected = next(
                (item for item in self.files(session_id) if item.file_ref == file_ref), None
            )
            if selected is None:
                raise SessionFileError("session_file_not_found")
            ref = selected.attachment
            if ref.size_bytes > min(
                self.assets.max_attachment_bytes, MAX_SESSION_WORKSPACE_FILE_BYTES
            ):
                raise SessionFileError("session_file_too_large")
            # Authorize via the session first; global blob metadata is not an ownership grant.
            try:
                source = self.assets.verify(ref.asset_id)
            except AttachmentError as exc:
                raise SessionFileError(exc.code) from exc
            reject_linked_path(source)
            reject_linked_path(self.root)
            workspace = session_workspace_path(self.root, session_id)
            destination = workspace / "inputs" / file_ref / safe_filename(ref)
            reject_linked_path(destination)
            if not verified_file(source, ref):
                raise SessionFileError("attachment_integrity_failed")
            result = {
                **selected.metadata(),
                "integrity": "sha256_verified",
                "workspace": str(workspace),
                "relative_path": destination.relative_to(workspace).as_posix(),
                "read_only_copy": True,
                "usage": (
                    "普通 exec_command 省略 workdir，在默认会话工作目录使用 relative_path；"
                    "Skill 脚本沿用 skill binding，按既有审批把 workspace 加为额外只读根。"
                    "按需读取页或片段，修改另存新文件。"
                ),
            }
            if destination.exists() and destination.stat().st_nlink != 1:
                raise SessionFileError("session_workspace_unsafe_path")
            if verified_file(destination, ref):
                destination.chmod(stat.S_IREAD)
                return result
            destination.parent.mkdir(parents=True, exist_ok=True)
            check_workspace_budget(
                workspace, added_bytes=ref.size_bytes, added_files=1, replacing=destination
            )
            temporary: Path | None = None
            try:
                with (
                    source.open("rb") as incoming,
                    tempfile.NamedTemporaryFile(
                        dir=destination.parent, prefix=".preparing-", delete=False
                    ) as outgoing,
                ):
                    temporary = Path(outgoing.name)
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := incoming.read(1024 * 1024):
                        size += len(chunk)
                        if size > ref.size_bytes:
                            raise SessionFileError("attachment_integrity_failed")
                        digest.update(chunk)
                        outgoing.write(chunk)
                    if size != ref.size_bytes or digest.hexdigest() != ref.sha256:
                        raise SessionFileError("attachment_integrity_failed")
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                # Recheck before replacement; never follow a user-created destination link.
                reject_linked_path(destination)
                if destination.exists():
                    if destination.stat().st_nlink != 1:
                        raise SessionFileError("session_workspace_unsafe_path")
                    destination.chmod(stat.S_IREAD | stat.S_IWRITE)
                os.replace(temporary, destination)
                temporary = None
                destination.chmod(stat.S_IREAD)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return result
