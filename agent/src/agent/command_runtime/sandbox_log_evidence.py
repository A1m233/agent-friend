"""Bounded, content-free observations of logs appended during a sandbox operation.

Signal vocabulary follows openai/codex rust-v0.147.0 windows-sandbox-rs
identity.rs and setup.rs. Windows account deletion uses debug_log upstream;
absence of that signal under normal logging does not rule out deletion.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024
MAX_EVENTS = 64
MAX_FILES = 2
SIGNALS = {
    "sandbox setup required: sandbox users missing or incompatible with marker version": "setup_users_missing_or_incompatible",
    "sandbox setup required: sandbox setup marker missing or incompatible": "setup_marker_missing_or_incompatible",
    "sandbox setup required: offline firewall settings changed": "setup_firewall_settings_changed",
    "DEBUG: sandbox user login failed; deleting ": "users_delete_after_login_failure",
    "setup refresh: spawning ": "refresh_spawn_requested",
    "setup refresh: exited with status ": "refresh_exit_failed",
    "setup refresh: setup refresh failed to launch helper:": "refresh_launch_failed",
    "setup refresh: failed to clear setup_error.json before launch": "report_clear_before_failed",
    "setup refresh: failed to clear setup_error.json after success": "report_clear_after_failed",
    "setup orchestrator: failed to clear setup_error.json before launch": "report_clear_before_failed",
    "setup orchestrator: failed to clear setup_error.json after success": "report_clear_after_failed",
    "sandbox setup marker parse failed": "marker_parse_failed",
}


def _paths(home: Path) -> list[Path]:
    return sorted((home / ".sandbox").glob("sandbox*.log"), reverse=True)[:MAX_FILES]


def log_cursor(home: Path) -> dict[str, Any]:
    """Private in-memory offsets; never persist filenames or file identity tuples."""
    files: dict[str, Any] = {}
    try:
        for path in _paths(home):
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                # A prefix fingerprint also detects replacement/truncation where
                # the filesystem reuses an inode. Only the hash stays in memory.
                length = min(info.st_size, 1024)
                prefix = hashlib.sha256(stream.read(length)).digest()
            files[path.name] = (info.st_dev, info.st_ino, info.st_size, length, prefix)
        return {"files": files, "status": "captured"}
    except OSError:
        return {"files": files, "status": "read_failed"}


def log_window(home: Path, cursor: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    gaps: set[str] = {"shared_log_not_operation_bound", "debug_only_deletion_may_be_absent"}
    if cursor.get("status") != "captured":
        gaps.add("baseline_unavailable")
    seen: set[str] = set()
    try:
        for path in _paths(home):
            seen.add(path.name)
            if path.is_symlink():
                gaps.add("symlink_rejected")
                continue
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                before = cursor.get("files", {}).get(path.name)
                same = bool(
                    before
                    and (info.st_dev, info.st_ino) == before[:2]
                    and info.st_size >= before[2]
                    and hashlib.sha256(stream.read(before[3])).digest() == before[4]
                )
                start = before[2] if same else 0
                scope = "appended" if same else "unattributed_tail"
                if not same:
                    gaps.add("new_or_replaced_log")
                bounded_start = max(start, info.st_size - MAX_BYTES)
                if bounded_start > start:
                    gaps.add("byte_limit")
                stream.seek(max(0, bounded_start - 1))
                previous = stream.read(1) if bounded_start else b"\n"
                stream.seek(bounded_start)
                expected_bytes = min(MAX_BYTES, info.st_size - bounded_start)
                raw = stream.read(expected_bytes)
                if len(raw) != expected_bytes:
                    gaps.add("changed_during_read")
            offset = bounded_start
            events: list[dict[str, Any]] = []
            lines = raw.splitlines(keepends=True)
            for index, line in enumerate(lines):
                line_offset = offset
                offset += len(line)
                if (index == 0 and previous != b"\n") or not line.endswith(b"\n"):
                    gaps.add("partial_line")
                    continue
                text = line.decode("utf-8", errors="replace")
                text = re.sub(r"^\[[^\]\r\n]*\] ", "", text)
                # Never retain arbitrary text, paths, arguments, usernames or ports.
                for phrase, signal in SIGNALS.items():
                    if not text.startswith(phrase):
                        continue
                    native = re.search(r"(?:os error |WinError )(\d{1,10})", text)
                    events.append(
                        {
                            "signal": signal,
                            "offset": line_offset,
                            "nativeCode": int(native[1]) if native else None,
                        }
                    )
            if len(events) > MAX_EVENTS:
                gaps.add("event_limit")
            records.append(
                {
                    "source": hashlib.sha256(path.name.encode()).hexdigest()[:16],
                    "scope": scope,
                    "startOffset": bounded_start,
                    "endOffset": offset,
                    "events": events[-MAX_EVENTS:],
                }
            )
        if set(cursor.get("files", {})) - seen:
            gaps.add("baseline_log_missing")
        if not records:
            gaps.add("logs_unavailable")
    except OSError:
        gaps.add("read_failed")
    return {"records": records, "gaps": sorted(gaps)}
