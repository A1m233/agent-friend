"""Capability-checked local filesystem operations."""

from __future__ import annotations

import codecs
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    DestructiveAction,
    FilesystemAuthorizationLedger,
    FilesystemCapability,
    FilesystemPermission,
)
from .policy import ProtectedPathPolicy, canonical_path, is_within

MAX_READ_CHARS = 10_000
MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024
MAX_SEARCH_ENTRIES = 50_000
MAX_CONTENT_SEARCH_FILES = 2_000
MAX_CONTENT_SEARCH_BYTES = 32 * 1024 * 1024
MAX_CONTENT_MATCHES_PER_FILE = 5
MAX_CONTENT_SNIPPET_CHARS = 240
MAX_CONTENT_SEARCH_SNIPPET_CHARS = 10_000
MAX_CONTENT_QUERY_CHARS = 4_096
MAX_STREAM_FRAGMENT_CHARS = 64 * 1024
SKIPPED_DIRECTORY_NAMES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__"})


class FilesystemError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class ResolvedPath:
    capability: FilesystemCapability
    path: Path


class FilesystemService:
    def __init__(self, policy: ProtectedPathPolicy | None = None) -> None:
        self.policy = policy or ProtectedPathPolicy.default()

    def resolve(
        self,
        ledger: FilesystemAuthorizationLedger,
        scope_id: str,
        relative_path: str,
        permission: FilesystemPermission,
        *,
        write: bool = False,
        allow_missing: bool = False,
    ) -> ResolvedPath:
        capability = ledger.scope(scope_id)
        if capability is None:
            raise FilesystemError(
                "permission_denied",
                "文件授权不存在或已失效。",
                permission_reason="scope_unavailable",
            )
        if permission not in capability.permissions:
            raise FilesystemError(
                "permission_denied",
                f"当前授权不包含 {permission}。",
                permission_reason="insufficient_permission",
                required_permission=permission,
            )
        relative = Path(relative_path or ".")
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise FilesystemError("scope_escape", "只能使用授权范围内的相对路径。")
        if capability.root_kind == "file" and relative not in {Path("."), Path("")}:
            raise FilesystemError("scope_escape", "文件授权不能访问其它相对路径。")
        raw_target = (
            capability.root if capability.root_kind == "file" else capability.root / relative
        )
        try:
            target = canonical_path(raw_target, allow_missing=allow_missing)
        except FileNotFoundError as exc:
            raise FilesystemError("not_found", "目标不存在。", path=str(raw_target)) from exc
        root = canonical_path(capability.root, allow_missing=True)
        if target != root and not is_within(target, root):
            raise FilesystemError("scope_escape", "路径解析后超出授权范围。")
        if self._is_link_component(raw_target, stop=root):
            raise FilesystemError("link_not_allowed", "授权路径中包含符号链接或目录链接。")
        if self.policy.is_protected(target) and not capability.allow_protected:
            raise FilesystemError("protected_path", "该产品路径未被当前父目录授权覆盖。")
        if write and not self.policy.generic_write_allowed(
            target,
            allow_instruction_metadata=capability.allow_protected,
        ):
            raise FilesystemError("protected_path", "产品内部状态不能通过通用文件工具写入。")
        return ResolvedPath(capability, target)

    @staticmethod
    def _is_link_component(path: Path, *, stop: Path) -> bool:
        current = path
        while current != stop and current.parent != current:
            try:
                if current.exists() and FilesystemService._is_link_like(current):
                    return True
                mode = current.lstat().st_mode if current.exists() else 0
                if mode and stat.S_ISLNK(mode):
                    return True
            except OSError:
                return True
            current = current.parent
        return False

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        try:
            return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
        except OSError:
            return True

    def list_directory(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str = ".",
        limit: int = 100,
        continuation: str | None = None,
    ) -> dict[str, object]:
        resolved = self.resolve(ledger, scope_id, relative_path, "search_metadata")
        if not resolved.path.is_dir():
            raise FilesystemError("not_directory", "目标不是目录。")
        offset = self._parse_continuation(continuation, scope_id)
        bounded_limit = max(1, min(limit, 500))
        entries = sorted(os.scandir(resolved.path), key=lambda item: item.name.casefold())
        selected = entries[offset : offset + bounded_limit]
        result = [
            self._entry_payload(item, resolved.capability.root, resolved.capability)
            for item in selected
        ]
        next_offset = offset + len(selected)
        return {
            "entries": result,
            "truncated": next_offset < len(entries),
            "continuation": (
                self._continuation(scope_id, next_offset) if next_offset < len(entries) else None
            ),
        }

    def stat(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str = ".",
    ) -> dict[str, object]:
        resolved = self.resolve(ledger, scope_id, relative_path, "search_metadata")
        try:
            info = resolved.path.lstat()
        except FileNotFoundError as exc:
            raise FilesystemError("not_found", "目标不存在。") from exc
        if self._is_link_like(resolved.path):
            raise FilesystemError("link_not_allowed", "不读取符号链接或目录链接。")
        return {
            "path": self._relative_text(resolved.path, resolved.capability.root),
            "type": "directory" if resolved.path.is_dir() else "file",
            "size": info.st_size,
            "mtime": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
            "identity": f"{info.st_dev}:{info.st_ino}",
            "text_candidate": resolved.path.is_file() and self._text_candidate(resolved.path),
            "protected": self.policy.is_protected(resolved.path),
        }

    def search(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        mode: str,
        query: str,
        glob: str | None = None,
        limit: int = 50,
        continuation: str | None = None,
    ) -> dict[str, object]:
        permission: FilesystemPermission = "read_text" if mode == "content" else "search_metadata"
        resolved = self.resolve(ledger, scope_id, ".", permission)
        if not resolved.path.is_dir():
            raise FilesystemError("not_directory", "搜索范围必须是目录。")
        if (
            mode not in {"name", "content"}
            or not query
            or (mode == "content" and len(query) > MAX_CONTENT_QUERY_CHARS)
        ):
            raise FilesystemError("invalid_arguments", "mode 必须是 name/content，query 不能为空。")
        offset = self._parse_continuation(continuation, scope_id)
        bounded_limit = max(1, min(limit, 100))
        matches: list[dict[str, object]] = []
        scanned_entries = 0
        content_files = 0
        content_bytes = 0
        content_snippet_chars = 0
        skipped_matches = 0
        stopped_early = False
        stack = [resolved.path]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(
                    os.scandir(directory), key=lambda item: item.name.casefold(), reverse=True
                )
            except OSError:
                continue
            for entry in entries:
                scanned_entries += 1
                if scanned_entries > MAX_SEARCH_ENTRIES:
                    stopped_early = True
                    stack.clear()
                    break
                try:
                    if self._is_link_like(Path(entry.path)):
                        continue
                    entry_path = Path(entry.path)
                    relative = self._relative_text(entry_path, resolved.capability.root)
                    protected = self.policy.is_protected(entry_path)
                    if protected and not resolved.capability.allow_protected:
                        continue
                    if (
                        entry.is_dir(follow_symlinks=False)
                        and entry.name not in SKIPPED_DIRECTORY_NAMES
                    ):
                        stack.append(entry_path)
                    if glob and not fnmatch.fnmatch(relative, glob):
                        continue
                    matched = (
                        query.casefold() in entry.name.casefold()
                        or query.casefold() in relative.casefold()
                    )
                    content_match = False
                    content_matches: list[dict[str, object]] = []
                    content_matches_truncated = False
                    content_scan_exhausted = False
                    if mode == "content" and entry.is_file(follow_symlinks=False):
                        if (
                            content_files >= MAX_CONTENT_SEARCH_FILES
                            or content_bytes >= MAX_CONTENT_SEARCH_BYTES
                        ):
                            stopped_early = True
                            stack.clear()
                            break
                        content_files += 1
                        (
                            content_matches,
                            match_limit_reached,
                            content_scan_truncated,
                            scanned_bytes,
                        ) = self._file_content_matches(
                            entry_path,
                            query,
                            max_bytes=MAX_CONTENT_SEARCH_BYTES - content_bytes,
                        )
                        content_bytes += scanned_bytes
                        content_matches_truncated = match_limit_reached or content_scan_truncated
                        content_scan_exhausted = (
                            content_scan_truncated and content_bytes >= MAX_CONTENT_SEARCH_BYTES
                        )
                        if content_scan_exhausted:
                            stopped_early = True
                            stack.clear()
                        content_match = bool(content_matches)
                        matched = content_match
                    if not matched:
                        if content_scan_exhausted:
                            break
                        continue
                    if skipped_matches < offset:
                        skipped_matches += 1
                        continue
                    match_snippet_chars = sum(
                        len(str(item.get("snippet", ""))) for item in content_matches
                    )
                    if (
                        mode == "content"
                        and matches
                        and content_snippet_chars + match_snippet_chars
                        > MAX_CONTENT_SEARCH_SNIPPET_CHARS
                    ):
                        stopped_early = True
                        stack.clear()
                        break
                    content_snippet_chars += match_snippet_chars
                    matches.append(
                        {
                            **self._entry_payload(
                                entry, resolved.capability.root, resolved.capability
                            ),
                            **(
                                {
                                    "content_match": True,
                                    "content_matches": content_matches,
                                    "content_matches_truncated": content_matches_truncated,
                                }
                                if content_match
                                else {}
                            ),
                        }
                    )
                    if len(matches) >= bounded_limit:
                        stopped_early = True
                        stack.clear()
                        break
                    if content_scan_exhausted:
                        break
                except OSError:
                    continue
        next_offset = offset + len(matches)
        return {
            "matches": matches,
            "truncated": stopped_early,
            "continuation": self._continuation(scope_id, next_offset) if stopped_early else None,
            "scanned_entries": min(scanned_entries, MAX_SEARCH_ENTRIES),
            **({"content_bytes_scanned": content_bytes} if mode == "content" else {}),
            **({"content_snippet_chars": content_snippet_chars} if mode == "content" else {}),
        }

    def read_text(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str = ".",
        start_line: int = 1,
        start_column: int = 0,
        max_lines: int = 200,
        encoding: str = "auto",
    ) -> dict[str, object]:
        resolved = self.resolve(ledger, scope_id, relative_path, "read_text")
        if not resolved.path.is_file():
            raise FilesystemError("not_file", "目标不是文件。")
        size = resolved.path.stat().st_size
        if size > MAX_TEXT_FILE_BYTES:
            return self._read_large_text(
                resolved,
                start_line=start_line,
                start_column=start_column,
                max_lines=max_lines,
                encoding=encoding,
                size=size,
            )
        raw = resolved.path.read_bytes()
        text, detected = self._decode_text(raw, encoding)
        lines = text.splitlines(keepends=True)
        first = max(1, start_line)
        if start_column < 0:
            raise FilesystemError("invalid_arguments", "start_column 不能小于 0。")
        if start_column and (first > len(lines) or start_column >= len(lines[first - 1])):
            raise FilesystemError(
                "invalid_arguments",
                "start_column 超出 start_line 对应行；只能复用上一页返回的 next_start_column。",
            )
        count = max(1, min(max_lines, 2_000))
        line_index = first - 1
        line_limit = min(len(lines), line_index + count)
        column = start_column
        returned_chars = 0
        chunk_parts: list[str] = []
        end_line = first - 1
        end_column = 0
        next_line: int | None = None
        next_column: int | None = None
        while line_index < line_limit and returned_chars < MAX_READ_CHARS:
            line = lines[line_index]
            fragment = line[column:]
            remaining = MAX_READ_CHARS - returned_chars
            take = min(len(fragment), remaining)
            if take:
                chunk_parts.append(fragment[:take])
                returned_chars += take
                end_line = line_index + 1
                end_column = column + take
            if take < len(fragment):
                next_line = line_index + 1
                next_column = column + take
                break
            line_index += 1
            column = 0

        if next_line is None and line_index < len(lines):
            next_line = line_index + 1
            next_column = 0
        chunk = "".join(chunk_parts)
        truncated = next_line is not None
        truncation_reason = (
            "max_output_chars"
            if truncated and returned_chars >= MAX_READ_CHARS
            else "max_lines"
            if truncated
            else None
        )
        return {
            "path": self._relative_text(resolved.path, resolved.capability.root),
            "content": chunk,
            "encoding": detected,
            "start_line": first,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "truncated": truncated,
            "truncation_reason": truncation_reason,
            "next_start_line": next_line,
            "next_start_column": next_column,
            "returned_chars": returned_chars,
            "output_char_limit": MAX_READ_CHARS,
            "total_lines": len(lines),
            "size": size,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def read_text_tail(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str = ".",
        max_lines: int = 200,
        encoding: str = "auto",
    ) -> dict[str, object]:
        resolved = self.resolve(ledger, scope_id, relative_path, "read_text")
        if not resolved.path.is_file():
            raise FilesystemError("not_file", "目标不是文件。")
        count = max(1, min(max_lines, 2_000))
        detected = self._stream_text_encoding(resolved.path, encoding)
        fragments: deque[tuple[int, int, str]] = deque()
        retained_chars = 0
        line_number = 1
        column = 0
        total_lines = 0
        saw_content = False
        dropped_for_lines = False
        dropped_for_chars = False
        try:
            with resolved.path.open("r", encoding=detected, errors="strict", newline="") as handle:
                while fragment := handle.readline(MAX_STREAM_FRAGMENT_CHARS):
                    saw_content = True
                    start_column = column
                    fragments.append((line_number, start_column, fragment))
                    retained_chars += len(fragment)
                    if fragment.endswith(("\n", "\r")):
                        total_lines = line_number
                        line_number += 1
                        column = 0
                    else:
                        column += len(fragment)
                        total_lines = line_number
                    minimum_line = max(1, total_lines - count + 1)
                    while fragments and fragments[0][0] < minimum_line:
                        retained_chars -= len(fragments.popleft()[2])
                        dropped_for_lines = True
                    while retained_chars > MAX_READ_CHARS and fragments:
                        old_line, old_column, old_fragment = fragments[0]
                        excess = retained_chars - MAX_READ_CHARS
                        if excess >= len(old_fragment):
                            retained_chars -= len(fragments.popleft()[2])
                        else:
                            fragments[0] = (
                                old_line,
                                old_column + excess,
                                old_fragment[excess:],
                            )
                            retained_chars -= excess
                        dropped_for_chars = True
        except UnicodeDecodeError as exc:
            raise FilesystemError("unsupported_content", "文本编码无法严格解码。") from exc
        content = "".join(fragment for _line, _column, fragment in fragments)
        start_line_value = fragments[0][0] if fragments else (1 if not saw_content else total_lines)
        start_column_value = fragments[0][1] if fragments else 0
        return {
            "path": self._relative_text(resolved.path, resolved.capability.root),
            "content": content,
            "encoding": detected,
            "start_line": start_line_value,
            "start_column": start_column_value,
            "end_line": total_lines,
            "truncated": dropped_for_lines or dropped_for_chars,
            "truncation_reason": (
                "max_output_chars"
                if dropped_for_chars
                else "max_lines"
                if dropped_for_lines
                else None
            ),
            "returned_chars": len(content),
            "output_char_limit": MAX_READ_CHARS,
            "total_lines": total_lines,
            "size": resolved.path.stat().st_size,
            "sha256": self._sha256_file(resolved.path),
        }

    def create_directory(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str,
    ) -> dict[str, object]:
        resolved = self.resolve(
            ledger, scope_id, relative_path, "create", write=True, allow_missing=True
        )
        existed = resolved.path.exists()
        if existed and not resolved.path.is_dir():
            raise FilesystemError("target_conflict", "目标已存在且不是目录。")
        resolved.path.mkdir(parents=True, exist_ok=True)
        self._revalidate(resolved)
        return {"status": "ready" if existed else "created", "path": relative_path}

    def write_text(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str,
        content: str,
        expected_sha256: str | None = None,
        overwrite_action_id: str | None = None,
    ) -> dict[str, object]:
        if not isinstance(content, str):
            raise FilesystemError("invalid_arguments", "content 必须是文本。")
        try:
            preliminary = self.resolve(
                ledger, scope_id, relative_path, "create", write=True, allow_missing=True
            )
        except FilesystemError as exc:
            if exc.code != "permission_denied":
                raise
            capability = ledger.scope(scope_id)
            raw_target = (
                capability.root
                if capability is not None and capability.root_kind == "file"
                else capability.root / Path(relative_path or ".")
                if capability is not None
                else None
            )
            if raw_target is not None and not raw_target.exists():
                raise FilesystemError(
                    "permission_denied",
                    "目标不存在；创建新文件或备份需要 create 权限，edit 不能创建目标。",
                    permission_reason="insufficient_permission",
                    required_permission="create",
                ) from exc
            preliminary = self.resolve(ledger, scope_id, relative_path, "edit", write=True)
        existed = preliminary.path.exists()
        if existed:
            preliminary = self.resolve(ledger, scope_id, relative_path, "edit", write=True)
            if not preliminary.path.is_file():
                raise FilesystemError("target_conflict", "覆盖目标不是文件。")
            self._require_hash(preliminary.path, expected_sha256)
            self._require_action(
                ledger,
                overwrite_action_id,
                action="overwrite",
                target=preliminary.path,
                message="覆盖动作未获准确授权。",
            )
        elif expected_sha256 is not None:
            raise FilesystemError("target_changed", "目标已不存在，不能按旧版本覆盖。")
        raw = content.encode("utf-8")
        self._atomic_write(
            preliminary,
            raw,
            expected_sha256=expected_sha256 if existed else None,
            expect_missing=not existed,
        )
        return {
            "status": "replaced" if existed else "created",
            "path": relative_path,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def apply_patch(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str,
        patch: str,
        expected_sha256: str,
    ) -> dict[str, object]:
        resolved = self.resolve(ledger, scope_id, relative_path, "edit", write=True)
        raw = resolved.path.read_bytes()
        self._require_hash(resolved.path, expected_sha256, raw=raw)
        text, encoding = self._decode_text(raw, "auto")
        candidate, additions, deletions = self._apply_strict_patch(text, patch)
        encoded = candidate.encode(encoding)
        self._atomic_write(resolved, encoded, expected_sha256=expected_sha256)
        return {
            "status": "patched",
            "path": relative_path,
            "added_lines": additions,
            "deleted_lines": deletions,
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def copy(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        source_scope_id: str,
        source_path: str,
        destination_scope_id: str,
        destination_path: str,
        overwrite_action_id: str | None = None,
    ) -> dict[str, object]:
        source = self.resolve(ledger, source_scope_id, source_path, "read_text")
        destination = self.resolve(
            ledger,
            destination_scope_id,
            destination_path,
            "create",
            write=True,
            allow_missing=True,
        )
        if self._is_link_like(source.path):
            raise FilesystemError("link_not_allowed", "不能复制链接。")
        if destination.path.exists():
            self._require_action(
                ledger,
                overwrite_action_id,
                action="overwrite",
                target=destination.path,
                message="目标已存在且未批准覆盖。",
            )
        if source.path.is_dir():
            self._assert_tree_allowed(source.path, source.capability, write=False)
            self._assert_destination_tree_allowed(
                source.path, destination.path, destination.capability
            )
            self._copy_directory_atomic(source.path, destination)
            return {"status": "copied", "type": "directory", "destination": destination_path}
        raw_hash = self._copy_file_atomic(source.path, destination)
        return {
            "status": "copied",
            "type": "file",
            "destination": destination_path,
            "sha256": raw_hash,
        }

    def move(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        source_scope_id: str,
        source_path: str,
        destination_scope_id: str,
        destination_path: str,
        action_id: str,
    ) -> dict[str, object]:
        source = self.resolve(ledger, source_scope_id, source_path, "edit", write=True)
        destination = self.resolve(
            ledger,
            destination_scope_id,
            destination_path,
            "create",
            write=True,
            allow_missing=True,
        )
        if destination.path.exists():
            raise FilesystemError("target_conflict", "移动目标已存在。")
        self._require_action(
            ledger,
            action_id,
            action="move",
            target=source.path,
            message="移动动作未获准确授权。",
        )
        if source.path.is_dir():
            self._assert_tree_allowed(source.path, source.capability, write=True)
            self._assert_destination_tree_allowed(
                source.path, destination.path, destination.capability
            )
        destination.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source.path, destination.path)
        except OSError:
            self.copy(
                ledger,
                source_scope_id=source_scope_id,
                source_path=source_path,
                destination_scope_id=destination_scope_id,
                destination_path=destination_path,
            )
            try:
                self._delete_path(source.path, recursive=source.path.is_dir())
            except OSError as exc:
                raise FilesystemError(
                    "partial_failure",
                    "目标已复制，但源目标删除失败。",
                    source=str(source.path),
                    destination=str(destination.path),
                ) from exc
        return {"status": "moved", "source": source_path, "destination": destination_path}

    def delete(
        self,
        ledger: FilesystemAuthorizationLedger,
        *,
        scope_id: str,
        relative_path: str,
        action_id: str,
        recursive: bool = False,
    ) -> dict[str, object]:
        resolved = self.resolve(ledger, scope_id, relative_path, "edit", write=True)
        self._require_action(
            ledger,
            action_id,
            action="delete",
            target=resolved.path,
            recursive=recursive,
            message="删除动作未获准确授权。",
        )
        if resolved.path.is_dir() and recursive:
            self._assert_tree_allowed(resolved.path, resolved.capability, write=True)
        self._delete_path(resolved.path, recursive=recursive)
        return {"status": "deleted", "path": relative_path, "recursive": recursive}

    @staticmethod
    def _delete_path(path: Path, *, recursive: bool) -> None:
        if path.is_dir():
            if not recursive:
                path.rmdir()
            else:
                shutil.rmtree(path)
        else:
            path.unlink()

    @staticmethod
    def _require_action(
        ledger: FilesystemAuthorizationLedger,
        action_id: str | None,
        *,
        action: DestructiveAction,
        target: Path,
        message: str,
        recursive: bool = False,
    ) -> None:
        if action_id is None:
            raise FilesystemError("action_not_authorized", message)
        status = ledger.consume_action_status(
            action_id,
            action=action,
            target=target,
            recursive=recursive,
        )
        if status == "target_changed":
            raise FilesystemError("target_changed", "批准后目标身份已经变化。")
        if status != "ok":
            raise FilesystemError("action_not_authorized", message)

    def _copy_directory(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        for entry in os.scandir(source):
            if self._is_link_like(Path(entry.path)):
                raise FilesystemError("link_not_allowed", "目录中包含链接，复制已停止。")
            child_source = Path(entry.path)
            child_destination = destination / entry.name
            if entry.is_dir(follow_symlinks=False):
                self._copy_directory(child_source, child_destination)
            else:
                resolved = ResolvedPath(
                    FilesystemCapability("internal", child_destination, "file", frozenset()),
                    child_destination,
                )
                self._copy_file_atomic(child_source, resolved)

    def _copy_directory_atomic(self, source: Path, destination: ResolvedPath) -> None:
        destination.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(
            tempfile.mkdtemp(prefix=".agent-friend-copy-dir-", dir=destination.path.parent)
        )
        temp_path.rmdir()
        backup = destination.path.parent / f".agent-friend-backup-{os.urandom(8).hex()}"
        moved_old = False
        try:
            self._copy_directory(source, temp_path)
            self._revalidate(destination)
            if destination.path.exists():
                os.replace(destination.path, backup)
                moved_old = True
            os.replace(temp_path, destination.path)
            if moved_old:
                self._delete_path(backup, recursive=backup.is_dir())
        except BaseException:
            with suppress(OSError):
                if temp_path.exists():
                    self._delete_path(temp_path, recursive=temp_path.is_dir())
            if moved_old and backup.exists() and not destination.path.exists():
                with suppress(OSError):
                    os.replace(backup, destination.path)
            raise

    def _assert_tree_allowed(
        self,
        root: Path,
        capability: FilesystemCapability,
        *,
        write: bool,
    ) -> None:
        stack = [root]
        while stack:
            current = stack.pop()
            if self._is_link_like(current):
                raise FilesystemError("link_not_allowed", "目录树中包含链接。")
            if self.policy.is_protected(current) and not capability.allow_protected:
                raise FilesystemError("protected_path", "目录树包含未被父 scope 覆盖的产品路径。")
            if write and not self.policy.generic_write_allowed(
                current,
                allow_instruction_metadata=capability.allow_protected,
            ):
                raise FilesystemError("protected_path", "目录树包含不可由通用工具修改的产品路径。")
            if current.is_dir():
                stack.extend(Path(entry.path) for entry in os.scandir(current))

    def _assert_destination_tree_allowed(
        self,
        source: Path,
        destination: Path,
        capability: FilesystemCapability,
    ) -> None:
        stack = [source]
        while stack:
            current = stack.pop()
            relative = current.relative_to(source)
            planned = destination / relative
            if not self.policy.generic_write_allowed(
                planned,
                allow_instruction_metadata=capability.allow_protected,
            ):
                raise FilesystemError(
                    "protected_path", "复制或移动结果会覆盖不可由通用工具修改的产品路径。"
                )
            if current.is_dir():
                stack.extend(Path(entry.path) for entry in os.scandir(current))

    def _copy_file_atomic(self, source: Path, destination: ResolvedPath) -> str:
        digest = hashlib.sha256()
        destination.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".agent-friend-copy-", dir=destination.path.parent)
        backup = destination.path.parent / f".agent-friend-backup-{os.urandom(8).hex()}"
        moved_old = False
        try:
            with os.fdopen(fd, "wb") as target, source.open("rb") as origin:
                while chunk := origin.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            self._revalidate(destination)
            if hashlib.sha256(Path(temp_name).read_bytes()).hexdigest() != digest.hexdigest():
                raise FilesystemError("verification_failed", "复制临时文件字节校验失败。")
            if destination.path.exists():
                os.replace(destination.path, backup)
                moved_old = True
            os.replace(temp_name, destination.path)
            if hashlib.sha256(destination.path.read_bytes()).hexdigest() != digest.hexdigest():
                raise FilesystemError("verification_failed", "复制后字节校验失败。")
            if moved_old:
                backup.unlink()
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            if moved_old and backup.exists():
                with suppress(OSError):
                    destination.path.unlink()
                with suppress(OSError):
                    os.replace(backup, destination.path)
            raise
        return digest.hexdigest()

    def _atomic_write(
        self,
        resolved: ResolvedPath,
        raw: bytes,
        *,
        expected_sha256: str | None = None,
        expect_missing: bool = False,
    ) -> None:
        resolved.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".agent-friend-write-", dir=resolved.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self._revalidate(resolved)
            if expected_sha256 is not None:
                self._require_hash(resolved.path, expected_sha256)
            if expect_missing and resolved.path.exists():
                raise FilesystemError("target_conflict", "创建目标在落盘前已经出现。")
            os.replace(temp_name, resolved.path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise

    def _revalidate(self, resolved: ResolvedPath) -> None:
        parent = canonical_path(resolved.path.parent, allow_missing=False)
        root = canonical_path(resolved.capability.root, allow_missing=True)
        exact_missing_root = resolved.path == root and parent == canonical_path(root.parent)
        if not exact_missing_root and parent != root and not is_within(parent, root):
            raise FilesystemError("target_changed", "落盘前父目录身份已变化。")
        if self._is_link_component(resolved.path.parent, stop=root):
            raise FilesystemError("target_changed", "落盘前路径被替换为链接。")

    @staticmethod
    def _require_hash(path: Path, expected: str | None, *, raw: bytes | None = None) -> None:
        if not expected:
            raise FilesystemError("expected_version_required", "修改既有文件必须提供 SHA-256。")
        actual = hashlib.sha256(raw if raw is not None else path.read_bytes()).hexdigest()
        if actual != expected:
            raise FilesystemError(
                "target_changed", "文件内容已变化，拒绝覆盖。", actual_sha256=actual
            )

    @staticmethod
    def _decode_text(raw: bytes, encoding: str) -> tuple[str, str]:
        if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise FilesystemError("unsupported_content", "目标看起来是二进制文件。")
        allowed = {"auto", "utf-8", "utf-8-sig", "utf-16", "gb18030"}
        if encoding not in allowed:
            raise FilesystemError("unsupported_encoding", "不支持该文本编码。")
        candidates = (
            ["utf-8-sig"]
            if encoding == "auto" and raw.startswith(b"\xef\xbb\xbf")
            else ["utf-16"]
            if encoding == "auto" and raw.startswith((b"\xff\xfe", b"\xfe\xff"))
            else ["utf-8"]
            if encoding == "auto"
            else [encoding]
        )
        try:
            selected = candidates[0]
            return raw.decode(selected), selected
        except UnicodeDecodeError as exc:
            raise FilesystemError("unsupported_content", "文本编码无法严格解码。") from exc

    @staticmethod
    def _stream_text_encoding(path: Path, encoding: str) -> str:
        with path.open("rb") as handle:
            sample = handle.read(4096)
        if b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise FilesystemError("unsupported_content", "目标看起来是二进制文件。")
        allowed = {"auto", "utf-8", "utf-8-sig", "utf-16", "gb18030"}
        if encoding not in allowed:
            raise FilesystemError("unsupported_encoding", "不支持该文本编码。")
        if encoding != "auto":
            return encoding
        if sample.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "utf-16"
        return "utf-8"

    def _read_large_text(
        self,
        resolved: ResolvedPath,
        *,
        start_line: int,
        start_column: int,
        max_lines: int,
        encoding: str,
        size: int,
    ) -> dict[str, object]:
        if start_column < 0:
            raise FilesystemError("invalid_arguments", "start_column 不能小于 0。")
        first = max(1, start_line)
        count = max(1, min(max_lines, 2_000))
        last_requested_line = first + count - 1
        detected = self._stream_text_encoding(resolved.path, encoding)
        line_number = 1
        column = 0
        total_lines = 0
        target_line_length = 0
        saw_content = False
        returned_chars = 0
        chunk_parts: list[str] = []
        end_line = first - 1
        end_column = 0
        next_line: int | None = None
        next_column: int | None = None
        truncation_reason: str | None = None
        try:
            with resolved.path.open("r", encoding=detected, errors="strict", newline="") as handle:
                while fragment := handle.readline(MAX_STREAM_FRAGMENT_CHARS):
                    saw_content = True
                    fragment_line = line_number
                    fragment_column = column
                    fragment_end_column = fragment_column + len(fragment)
                    if fragment_line == first:
                        target_line_length = fragment_end_column
                    if fragment_line > last_requested_line and next_line is None:
                        next_line = fragment_line
                        next_column = 0
                        truncation_reason = "max_lines"
                    if (
                        first <= fragment_line <= last_requested_line
                        and next_line is None
                        and returned_chars >= MAX_READ_CHARS
                    ):
                        next_line = fragment_line
                        next_column = fragment_column
                        truncation_reason = "max_output_chars"
                    if (
                        first <= fragment_line <= last_requested_line
                        and next_line is None
                        and returned_chars < MAX_READ_CHARS
                    ):
                        requested_column = start_column if fragment_line == first else 0
                        if fragment_end_column > requested_column:
                            offset = max(0, requested_column - fragment_column)
                            available = fragment[offset:]
                            remaining = MAX_READ_CHARS - returned_chars
                            take = min(len(available), remaining)
                            if take:
                                chunk_parts.append(available[:take])
                                returned_chars += take
                                end_line = fragment_line
                                end_column = fragment_column + offset + take
                            if take < len(available):
                                next_line = fragment_line
                                next_column = fragment_column + offset + take
                                truncation_reason = "max_output_chars"
                    if fragment.endswith(("\n", "\r")):
                        total_lines = fragment_line
                        line_number += 1
                        column = 0
                    else:
                        total_lines = fragment_line
                        column = fragment_end_column
        except UnicodeDecodeError as exc:
            raise FilesystemError("unsupported_content", "文本编码无法严格解码。") from exc
        if not saw_content:
            total_lines = 0
        if first <= total_lines and start_column >= target_line_length:
            raise FilesystemError(
                "invalid_arguments",
                "start_column 超出 start_line 对应行；只能复用上一页返回的 next_start_column。",
            )
        if next_line is not None and next_line > total_lines:
            next_line = None
            next_column = None
            truncation_reason = None
        chunk = "".join(chunk_parts)
        return {
            "path": self._relative_text(resolved.path, resolved.capability.root),
            "content": chunk,
            "encoding": detected,
            "start_line": first,
            "start_column": start_column,
            "end_line": end_line,
            "end_column": end_column,
            "truncated": next_line is not None,
            "truncation_reason": truncation_reason,
            "next_start_line": next_line,
            "next_start_column": next_column,
            "returned_chars": returned_chars,
            "output_char_limit": MAX_READ_CHARS,
            "total_lines": total_lines,
            "size": size,
            "sha256": self._sha256_file(resolved.path),
        }

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _text_candidate(path: Path) -> bool:
        try:
            sample = path.read_bytes()[:4096]
            if b"\x00" in sample:
                return False
            sample.decode("utf-8")
            return True
        except (OSError, UnicodeDecodeError):
            return False

    @classmethod
    def _file_content_matches(
        cls, path: Path, query: str, *, max_bytes: int
    ) -> tuple[list[dict[str, object]], bool, bool, int]:
        if max_bytes <= 0:
            return [], False, True, 0
        try:
            size = path.stat().st_size
            detected = cls._stream_text_encoding(path, "auto")
            pattern = re.compile(re.escape(query), re.IGNORECASE)
            matches: list[dict[str, object]] = []
            match_limit_reached = False
            line_number = 1
            line_column = 0
            carry = ""
            scanned = 0
            decoder = codecs.getincrementaldecoder(detected)(errors="strict")
            decoded = ""

            def process_fragment(fragment: str) -> None:
                nonlocal carry, line_column, line_number, match_limit_reached
                combined = carry + fragment
                combined_column = line_column - len(carry)
                for match in pattern.finditer(combined):
                    if match.end() <= len(carry):
                        continue
                    if len(matches) >= MAX_CONTENT_MATCHES_PER_FILE:
                        match_limit_reached = True
                        return
                    snippet_start = max(0, match.start() - 80)
                    snippet_end = min(len(combined), snippet_start + MAX_CONTENT_SNIPPET_CHARS)
                    if match.start() >= snippet_end:
                        snippet_start = match.start()
                        snippet_end = min(len(combined), snippet_start + MAX_CONTENT_SNIPPET_CHARS)
                    snippet = combined[snippet_start:snippet_end].rstrip("\r\n")
                    if combined_column + snippet_start > 0:
                        snippet = "…" + snippet
                    if not combined.endswith(("\n", "\r")) or snippet_end < len(
                        combined.rstrip("\r\n")
                    ):
                        snippet += "…"
                    matches.append(
                        {
                            "line": line_number,
                            "column": combined_column + match.start() + 1,
                            "snippet": snippet,
                        }
                    )
                if fragment.endswith(("\n", "\r")):
                    line_number += 1
                    line_column = 0
                    carry = ""
                else:
                    line_column += len(fragment)
                    carry_length = min(len(fragment), max(len(query) - 1, 80))
                    carry = fragment[-carry_length:] if carry_length else ""

            with path.open("rb") as raw:
                while scanned < max_bytes and not match_limit_reached:
                    chunk = raw.read(min(64 * 1024, max_bytes - scanned))
                    if not chunk:
                        break
                    scanned += len(chunk)
                    decoded += decoder.decode(chunk, final=False)
                    while not match_limit_reached:
                        fragment, decoded = cls._take_stream_fragment(decoded, final=False)
                        if fragment is None:
                            break
                        process_fragment(fragment)
                if scanned >= size:
                    decoded += decoder.decode(b"", final=True)
                while decoded and not match_limit_reached:
                    fragment, decoded = cls._take_stream_fragment(decoded, final=True)
                    if fragment is None:
                        break
                    process_fragment(fragment)
            return matches, match_limit_reached, scanned < size, scanned
        except (OSError, UnicodeDecodeError, FilesystemError, re.error):
            return [], False, False, 0

    @staticmethod
    def _take_stream_fragment(text: str, *, final: bool) -> tuple[str | None, str]:
        newline_indexes = [index for index in (text.find("\n"), text.find("\r")) if index >= 0]
        newline_end: int | None = None
        if newline_indexes:
            index = min(newline_indexes)
            if text[index] == "\r" and index + 1 == len(text) and not final:
                newline_end = None
            else:
                newline_end = index + (2 if text[index : index + 2] == "\r\n" else 1)
        if newline_end is not None:
            take = min(newline_end, MAX_STREAM_FRAGMENT_CHARS)
        elif len(text) >= MAX_STREAM_FRAGMENT_CHARS:
            take = MAX_STREAM_FRAGMENT_CHARS
            if not final and take == len(text) and text[take - 1] == "\r":
                take -= 1
        elif final and text:
            take = len(text)
        else:
            return None, text
        return text[:take], text[take:]

    def _entry_payload(
        self,
        entry: os.DirEntry[str],
        root: Path,
        capability: FilesystemCapability,
    ) -> dict[str, object]:
        info = entry.stat(follow_symlinks=False)
        path = Path(entry.path)
        return {
            "name": entry.name,
            "path": FilesystemService._relative_text(path, root),
            "type": "link"
            if self._is_link_like(path)
            else "directory"
            if entry.is_dir(follow_symlinks=False)
            else "file",
            "size": info.st_size,
            "mtime": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
            "protected": self.policy.is_protected(path) and not capability.allow_protected,
        }

    @staticmethod
    def _relative_text(path: Path, root: Path) -> str:
        if path == root:
            return "."
        return path.relative_to(root).as_posix()

    @staticmethod
    def _continuation(scope_id: str, offset: int) -> str:
        return json.dumps({"scope_id": scope_id, "offset": offset}, separators=(",", ":"))

    @staticmethod
    def _parse_continuation(value: str | None, scope_id: str) -> int:
        if value is None:
            return 0
        try:
            payload = json.loads(value)
            if payload.get("scope_id") != scope_id:
                raise ValueError
            offset = int(payload["offset"])
            if offset < 0 or offset > MAX_SEARCH_ENTRIES:
                raise ValueError
            return offset
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise FilesystemError(
                "invalid_continuation", "continuation 无效或不属于当前授权。"
            ) from exc

    @staticmethod
    def _apply_strict_patch(original: str, patch: str) -> tuple[str, int, int]:
        lines = patch.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "*** Begin Patch":
            raise FilesystemError("invalid_patch", "补丁缺少 Begin Patch。")
        if lines[-1].rstrip("\r\n") != "*** End Patch":
            raise FilesystemError("invalid_patch", "补丁缺少 End Patch。")
        body = lines[1:-1]
        if body and body[0].startswith("*** Update File:"):
            body = body[1:]
        if any(
            line.startswith(("*** Add File:", "*** Delete File:", "*** Move to:")) for line in body
        ):
            raise FilesystemError("invalid_patch", "本工具只接受单文件 Update Patch。")
        hunks: list[list[str]] = []
        current: list[str] = []
        for line in body:
            if line.startswith("@@"):
                if current:
                    hunks.append(current)
                current = []
            else:
                current.append(line)
        if current:
            hunks.append(current)
        if not hunks:
            raise FilesystemError("invalid_patch", "补丁没有可应用的 hunk。")
        candidate = original
        cursor = 0
        additions = 0
        deletions = 0
        for hunk in hunks:
            if any(not line.startswith((" ", "+", "-")) for line in hunk):
                raise FilesystemError("invalid_patch", "hunk 行必须以空格、+ 或 - 开头。")
            old = "".join(line[1:] for line in hunk if not line.startswith("+"))
            new = "".join(line[1:] for line in hunk if not line.startswith("-"))
            first = candidate.find(old, cursor)
            if first < 0:
                raise FilesystemError("patch_mismatch", "hunk 与当前文件内容不匹配。")
            if candidate.find(old, first + 1) >= 0:
                raise FilesystemError("patch_ambiguous", "hunk 在文件中匹配多次。")
            candidate = candidate[:first] + new + candidate[first + len(old) :]
            cursor = first + len(new)
            additions += sum(line.startswith("+") for line in hunk)
            deletions += sum(line.startswith("-") for line in hunk)
        return candidate, additions, deletions


def result_or_error(operation: Any) -> dict[str, object]:
    try:
        return {"ok": True, **operation()}
    except FilesystemError as exc:
        return {"ok": False, "error": exc.code, "message": str(exc), **exc.details}
