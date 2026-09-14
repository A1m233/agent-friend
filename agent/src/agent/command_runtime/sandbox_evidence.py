"""Content-free, independently retained Windows sandbox incident evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .windows_state import SETUP_VERSION, compatible_state

MAX_INCIDENTS = 32
MAX_INCIDENT_BYTES = 128 * 1024
MAX_AGE_SECONDS = 30 * 86400
MAX_SOURCE_BYTES = 64 * 1024
ERROR_CODES = frozenset(
    [
        "orchestrator_sandbox_dir_create_failed",
        "orchestrator_elevation_check_failed",
        "orchestrator_elevation_required",
        "orchestrator_payload_serialize_failed",
        "orchestrator_helper_launch_failed",
        "orchestrator_helper_launch_canceled",
        "orchestrator_helper_exit_nonzero",
        "orchestrator_helper_report_read_failed",
        "orchestrator_helper_incomplete",
        "helper_request_args_failed",
        "helper_sandbox_dir_create_failed",
        "helper_log_failed",
        "helper_user_provision_failed",
        "helper_users_group_create_failed",
        "helper_user_create_or_update_failed",
        "helper_dpapi_protect_failed",
        "helper_users_file_write_failed",
        "helper_setup_marker_write_failed",
        "helper_sid_resolve_failed",
        "helper_capability_sid_failed",
        "helper_firewall_com_init_failed",
        "helper_firewall_policy_access_failed",
        "helper_firewall_policy_ineffective",
        "helper_firewall_rule_create_or_add_failed",
        "helper_firewall_rule_verify_failed",
        "helper_read_acl_helper_spawn_failed",
        "helper_sandbox_lock_failed",
        "helper_unknown_error",
    ]
)


def error_facts(detail: str) -> dict[str, Any]:
    codes = sorted(set(re.findall(r"\b(?:orchestrator|helper)_[a-z_]+\b", detail)) & ERROR_CODES)
    native = re.search(r"(?:os error |WinError |launch setup helper: )(\d{1,10})", detail)
    exit_code = re.search(
        r"(?:exit(?:ed)?(?: with)? (?:code|status)[: ]+(?:Some\()?)(-?\d{1,12})", detail
    )
    return {
        "codes": codes,
        "nativeCode": int(native[1]) if native else None,
        "exitCode": int(exit_code[1])
        if exit_code
        else (0 if "orchestrator_helper_incomplete" in codes else None),
        "timedOut": "timeout" in detail.lower() or "timed out" in detail.lower(),
        "disconnected": "disconnected" in detail.lower(),
        "unclassified": not codes,
    }


def _read_state(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    facts: dict[str, Any] = {"status": "unknown"}
    try:
        if path.is_symlink():
            return {"status": "symlink_rejected"}, None
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            facts.update(bytes=info.st_size, modifiedNs=info.st_mtime_ns)
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            return facts | {"status": "oversized"}, None
        value = json.loads(raw)
        if not isinstance(value, dict):
            return facts | {"status": "invalid_structure"}, None
        version = value.get("version")
        facts.update(status="readable", version=version if type(version) is int else None)
        if type(version) is not int or version != SETUP_VERSION:
            facts["status"] = "version_mismatch"
        return facts, value
    except FileNotFoundError:
        return facts | {"status": "missing"}, None
    except PermissionError as exc:
        return facts | {
            "status": "access_denied",
            "nativeCode": getattr(exc, "winerror", exc.errno),
        }, None
    except (ValueError, UnicodeError, RecursionError):
        return facts | {"status": "parse_failed"}, None
    except OSError as exc:
        return facts | {
            "status": "read_failed",
            "nativeCode": getattr(exc, "winerror", exc.errno),
        }, None


def state_facts(home: Path) -> dict[str, Any]:
    marker, marker_value = _read_state(home / ".sandbox" / "setup_marker.json")
    users, users_value = _read_state(home / ".sandbox-secrets" / "sandbox_users.json")
    valid = bool(marker_value and users_value and compatible_state(marker_value, users_value))
    return {
        "marker": marker,
        "users": users,
        "compatible": valid,
        "structure": "valid" if valid else "invalid_or_unavailable",
    }


def _report(home: Path) -> dict[str, Any]:
    facts, value = _read_state(home / ".sandbox" / "setup_error.json")
    # Error reports have no setup version. Ignore the state-schema version result.
    if value is not None:
        facts["status"] = "readable"
        facts.pop("version", None)
        code = value.get("code")
        facts["code"] = code if isinstance(code, str) and code in ERROR_CODES else "unknown"
        message = value.get("message")
        facts["error"] = error_facts(message) if isinstance(message, str) else None
    return facts


def _log_facts(home: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        candidates = sorted((home / ".sandbox").glob("sandbox*.log"), reverse=True)[:2]
        for path in candidates:
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                start = max(0, info.st_size - MAX_SOURCE_BYTES)
                stream.seek(start)
                raw = stream.read(MAX_SOURCE_BYTES)
            text = raw.decode("utf-8", errors="replace")
            result.append(
                {
                    "bytes": info.st_size,
                    "modifiedNs": info.st_mtime_ns,
                    "tailStart": start,
                    "facts": error_facts(text),
                    "signals": [
                        signal
                        for phrase, signal in (
                            ("setup refresh: spawning", "refresh_spawn_requested"),
                            ("setup refresh: exited with status", "refresh_exit_failed"),
                            (
                                "failed to clear setup_error.json before launch",
                                "report_clear_before_failed",
                            ),
                            (
                                "failed to clear setup_error.json after success",
                                "report_clear_after_failed",
                            ),
                            ("sandbox setup marker parse failed", "marker_parse_failed"),
                        )
                        if phrase in text
                    ],
                }
            )
    except OSError:
        result.append({"status": "read_failed"})
    return result


def _helpers(executable: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if executable is None:
        return {"status": "unavailable"}
    for name in ("codex-windows-sandbox-setup.exe", "codex-command-runner.exe"):
        path = executable.parent / name
        try:
            if path.is_symlink():
                result[name] = {"status": "symlink_rejected"}
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                if info.st_size > 64 * 1024 * 1024:
                    result[name] = {"status": "oversized"}
                    continue
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
            result[name] = {
                "status": "present",
                "sha256": digest.hexdigest(),
                "bytes": info.st_size,
            }
        except OSError:
            result[name] = {"status": "unavailable"}
    return result


class SandboxEvidence:
    def __init__(
        self,
        diagnostics_path: Path | None,
        home: Path,
        executable: Path | None,
        runtime: str,
        build: str,
    ) -> None:
        self.directory = (
            diagnostics_path.parent / "command-runtime-incidents" if diagnostics_path else None
        )
        self._home = home
        self._executable = executable
        self._runtime = hashlib.sha256(runtime.encode()).hexdigest()[:16]
        self._build = hashlib.sha256(build.encode()).hexdigest()[:16]
        self._lock = threading.RLock()
        self._active: set[str] = set()
        self._gaps: set[str] = set()
        if self.directory is not None:
            try:
                with (self.directory / "coverage").open("rb") as stream:
                    value = json.loads(stream.read(4096))
                known = {
                    "write_failed",
                    "retention_evicted",
                    "incident_oversized",
                    "invalid_incident",
                    "history_unavailable",
                }
                self._gaps.update(
                    g for g in value.get("gaps", []) if isinstance(g, str) and g in known
                )
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError):
                self._gaps.add("history_unavailable")

    def begin(self, stage: str) -> dict[str, Any]:
        operation = uuid4().hex
        with self._lock:
            concurrent = bool(self._active)
            self._active.add(operation)
        return {
            "id": operation,
            "stage": stage,
            "startedAt": datetime.now(UTC).isoformat(),
            "startedNs": time.time_ns(),
            "concurrentAtStart": concurrent,
            "stateBefore": state_facts(self._home),
            "reportBefore": _report(self._home),
        }

    def finish(
        self, operation: dict[str, Any], detail: str | None = None, *, persist_success: bool = False
    ) -> None:
        with self._lock:
            concurrent = operation["concurrentAtStart"] or len(self._active) > 1
            self._active.discard(operation["id"])
        if detail is None and not persist_success:
            return
        report = _report(self._home)
        changed = report != operation["reportBefore"]
        modified = report.get("modifiedNs", 0)
        record = operation | {
            "schemaVersion": 1,
            "runtime": self._runtime,
            "build": self._build,
            "finishedAt": datetime.now(UTC).isoformat(),
            "recordedNs": time.time_ns(),
            "outcome": "failed" if detail is not None else "ok",
            "error": error_facts(detail) if detail is not None else None,
            "stateAfter": state_facts(self._home),
            "reportAfter": report,
            "reportAttribution": "changed_during_operation"
            if (
                changed
                and type(modified) is int
                and modified >= operation["startedNs"]
                and not concurrent
            )
            else "unconfirmed",
            "concurrent": concurrent,
            "logs": _log_facts(self._home),
            "expectedHelpers": _helpers(self._executable),
            "gaps": [
                "actual_helper_image_not_observed",
                "log_records_not_operation_bound",
                "unflushed_crash_tail_unknown",
            ],
        }
        self._write(record)

    def repair_started(self, operation: dict[str, Any]) -> None:
        prior = self.snapshot()["incidents"]
        operation["relatedIncidents"] = [r["id"] for r in prior if r.get("outcome") == "failed"]
        self._write(
            operation
            | {
                "schemaVersion": 1,
                "runtime": self._runtime,
                "build": self._build,
                "outcome": "in_progress",
                "recordedNs": time.time_ns(),
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        if self.directory is None:
            return
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                data = json.dumps(record, ensure_ascii=True).encode()
                if len(data) > MAX_INCIDENT_BYTES:
                    self._gaps.add("incident_oversized")
                    return
                target = self.directory / f"{record['id']}.json"
                temp = target.with_suffix(".tmp")
                with temp.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, target)
                files = sorted(
                    self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True
                )
                now = time.time()
                for index, path in enumerate(files):
                    if index >= MAX_INCIDENTS or now - path.stat().st_mtime > MAX_AGE_SECONDS:
                        path.unlink()
                        self._gaps.add("retention_evicted")
                coverage = self.directory / "coverage"
                temporary = self.directory / "coverage.tmp"
                with temporary.open("wb") as stream:
                    stream.write(json.dumps({"gaps": sorted(self._gaps)}).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, coverage)
            except (OSError, ValueError):
                self._gaps.add("write_failed")

    def snapshot(self) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        with self._lock:
            try:
                files = sorted(self.directory.glob("*.json")) if self.directory else []
                for path in files:
                    if time.time() - path.stat().st_mtime > MAX_AGE_SECONDS:
                        self._gaps.add("retention_evicted")
                        continue
                    if path.is_symlink() or path.stat().st_size > MAX_INCIDENT_BYTES:
                        self._gaps.add("invalid_incident")
                        continue
                    with path.open("rb") as stream:
                        data = stream.read(MAX_INCIDENT_BYTES + 1)
                    try:
                        record = json.loads(data)
                    except (ValueError, RecursionError):
                        self._gaps.add("invalid_incident")
                        continue
                    if (
                        not isinstance(record, dict)
                        or record.get("schemaVersion") != 1
                        or not isinstance(record.get("id"), str)
                        or type(record.get("recordedNs")) is not int
                    ):
                        self._gaps.add("invalid_incident")
                        continue
                    records.append(record)
            except (OSError, ValueError):
                self._gaps.add("history_unavailable")
            records.sort(key=lambda r: r.get("recordedNs", 0))
            return {
                "schemaVersion": 1,
                "incidents": records[-MAX_INCIDENTS:],
                "gaps": sorted(self._gaps),
                "limits": {"count": MAX_INCIDENTS, "itemBytes": MAX_INCIDENT_BYTES, "ageDays": 30},
                "scope": "persisted-only",
            }
