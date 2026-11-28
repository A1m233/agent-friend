"""Durable, bounded ownership journal. Never persist a command, environment or credential."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import portalocker

from .windows import protect_directory, uptime_ms

Phase = Literal["queued", "preparing", "starting", "running", "reconciling", "stopping", "finished"]
ResourceState = Literal["not_created", "active", "closed", "unverified"]
ExecutionResult = Literal["not_started", "unknown", "exited"]
MAX_RECORDS = 512
MAX_BYTES = 2 * 1024 * 1024


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class LifecycleUnavailable(RuntimeError):
    pass


@dataclass
class LifecycleRecord:
    operation_id: str
    runtime_instance_id: str
    kind: Literal["command", "prepare", "setup"] = "command"
    scope: Literal["sandbox", "host"] = "sandbox"
    parent_operation_id: str | None = None
    executor_instance_id: str | None = None
    session_id: str = ""
    run_id: str = ""
    tool_call_id: str = ""
    phase: Phase = "queued"
    execution_result: ExecutionResult = "not_started"
    resource_state: ResourceState = "not_created"
    resource_evidence: str = "not_submitted"
    reconciliation_required: bool = False
    stop_reason: str | None = None
    exit_code: int | None = None
    first_failure: str | None = None
    failures: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    uptime_ms: int | None = field(default_factory=uptime_ms)
    version: int = 1

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        # Boot observations are host evidence, not a user action token.
        value.pop("uptime_ms", None)
        return value


class LifecycleStore:
    def __init__(self, runtime_instance_id: str, directory: Path | None = None) -> None:
        self.runtime_instance_id = runtime_instance_id
        self.directory = directory
        self._lock = threading.RLock()
        self._closed = False
        self._records: dict[str, LifecycleRecord] = {}
        self._recoveries: list[dict[str, Any]] = []
        self._owner: portalocker.Lock | None = None
        self.error: str | None = None
        if directory is None:
            return
        try:
            protect_directory(directory)
            self._owner = portalocker.Lock(
                directory / "owner.lock",
                mode="a+b",
                timeout=0,
                flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            self._owner.acquire()
            self._load()
        except portalocker.exceptions.LockException:
            self.error = "lifecycle_owner_active"
            self._owner = None
        except (OSError, ValueError, TypeError, KeyError):
            self.error = "lifecycle_state_unavailable"

    def require_available(self) -> None:
        if self._closed:
            raise LifecycleUnavailable("lifecycle_owner_closed")
        if self.error:
            raise LifecycleUnavailable(self.error)

    def recoveries(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._recoveries)

    def save_recoveries(self, recoveries: list[dict[str, Any]]) -> None:
        with self._lock:
            self.require_available()
            self._recoveries = copy.deepcopy(recoveries[-64:])
            self._save()

    def begin(self, operation_id: str, **fields: Any) -> LifecycleRecord:
        with self._lock:
            self.require_available()
            if operation_id in self._records:
                raise LifecycleUnavailable("operation_already_registered")
            self._prune()
            if len(self._records) >= MAX_RECORDS:
                raise LifecycleUnavailable("lifecycle_capacity_exhausted")
            record = LifecycleRecord(operation_id, self.runtime_instance_id, **fields)
            self._validate(record)
            self._records[operation_id] = record
            self._save()
            return copy.deepcopy(record)

    def get(self, operation_id: str) -> LifecycleRecord | None:
        with self._lock:
            return copy.deepcopy(self._records.get(operation_id))

    def records(self, *, scope: str | None = None) -> tuple[LifecycleRecord, ...]:
        with self._lock:
            return tuple(
                copy.deepcopy(r)
                for r in self._records.values()
                if scope is None or r.scope == scope
            )

    def change(
        self, operation_id: str, *, failure: str | None = None, **changes: Any
    ) -> LifecycleRecord:
        with self._lock:
            self.require_available()
            record = copy.deepcopy(self._records[operation_id])
            for key, value in changes.items():
                if key not in {
                    "phase",
                    "execution_result",
                    "resource_state",
                    "resource_evidence",
                    "reconciliation_required",
                    "stop_reason",
                    "exit_code",
                    "executor_instance_id",
                }:
                    raise ValueError(f"immutable lifecycle field: {key}")
                setattr(record, key, value)
            if failure is not None:
                if (
                    not failure
                    or len(failure) > 100
                    or not all(c.isalnum() or c == "_" for c in failure)
                ):
                    raise ValueError("failure must be a bounded diagnostic code")
                record.first_failure = record.first_failure or failure
                if failure not in record.failures:
                    record.failures = [*record.failures[-7:], failure]
            record.version += 1
            record.updated_at = time.time()
            self._validate(record)
            self._records[operation_id] = record
            self._save()
            return copy.deepcopy(record)

    def blockers(self, *, maintenance: bool = False) -> tuple[LifecycleRecord, ...]:
        return tuple(
            r
            for r in self.records(scope="sandbox")
            if r.reconciliation_required
            or (maintenance and r.resource_state in {"active", "unverified"})
        )

    def _prune(self) -> None:
        completed = [
            r
            for r in self._records.values()
            if r.phase == "finished"
            and r.resource_state in {"closed", "not_created"}
            and not r.reconciliation_required
        ]
        for record in sorted(completed, key=lambda r: r.updated_at)[:-128]:
            del self._records[record.operation_id]

    def _load(self) -> None:
        assert self.directory is not None
        path = self.directory / "state.json"
        if not path.exists():
            return
        if path.is_symlink() or path.is_junction() or path.stat().st_size > MAX_BYTES:
            raise ValueError("invalid lifecycle storage")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("payloadSha256") != _digest(
            {"records": data.get("records"), "recoveries": data.get("recoveries", [])}
        ):
            raise ValueError("lifecycle checksum mismatch")
        if (
            data.get("schemaVersion") != 1
            or not isinstance(data.get("records"), list)
            or len(data["records"]) > MAX_RECORDS
        ):
            raise ValueError("invalid lifecycle schema")
        current_uptime = uptime_ms()
        recoveries = data.get("recoveries", [])
        if not isinstance(recoveries, list) or len(recoveries) > 64:
            raise ValueError("invalid recovery history")
        for recovery in recoveries:
            if (
                not isinstance(recovery, dict)
                or not isinstance(recovery.get("recovery_id"), str)
                or type(recovery.get("version")) is not int
                or recovery["version"] < 1
                or not isinstance(recovery.get("request_keys"), list)
                or len(recovery["request_keys"]) > 128
                or any(
                    not isinstance(key, str) or not key or len(key) > 128
                    for key in recovery["request_keys"]
                )
                or not isinstance(recovery.get("phase"), str)
                or recovery["phase"]
                not in {"reconciling", "stopping", "repairing", "verifying", "finished"}
                or (
                    recovery.get("result") is not None
                    and recovery.get("result")
                    not in {"succeeded", "blocked", "failed", "cancelled", "interrupted"}
                )
            ):
                raise ValueError("invalid recovery record")
        self._recoveries = recoveries
        for raw in data["records"]:
            record = LifecycleRecord(**raw)
            self._validate(record)
            if not isinstance(record.operation_id, str) or record.operation_id in self._records:
                raise ValueError("invalid operation identity")
            if record.phase not in {
                "queued",
                "preparing",
                "starting",
                "running",
                "reconciling",
                "stopping",
                "finished",
            } or record.resource_state not in {"not_created", "active", "closed", "unverified"}:
                raise ValueError("invalid lifecycle facts")
            if record.runtime_instance_id != self.runtime_instance_id and record.resource_state in {
                "active",
                "unverified",
            }:
                # A decreasing 64-bit OS uptime proves a reboot. A larger value
                # proves nothing; never infer a reboot from wall-clock changes.
                rebooted = (
                    current_uptime is not None
                    and type(record.uptime_ms) is int
                    and current_uptime < record.uptime_ms
                )
                record.resource_state = "closed" if rebooted else "unverified"
                record.resource_evidence = (
                    "system_restarted" if rebooted else "previous_runtime_unconfirmed"
                )
                record.reconciliation_required = not rebooted
                record.phase = "finished"
                record.version += 1
            elif record.phase == "queued" and record.resource_state == "not_created":
                record.phase = "finished"
            self._records[record.operation_id] = record
        self._save()

    @staticmethod
    def _validate(record: LifecycleRecord) -> None:
        if (
            any(
                not isinstance(value, str) or len(value) > 256
                for value in (
                    record.operation_id,
                    record.runtime_instance_id,
                    record.session_id,
                    record.run_id,
                    record.tool_call_id,
                    record.resource_evidence,
                )
            )
            or not record.operation_id
            or not record.runtime_instance_id
        ):
            raise ValueError("invalid lifecycle identity")
        if (
            record.kind not in {"command", "prepare", "setup"}
            or record.scope not in {"sandbox", "host"}
            or record.phase
            not in {
                "queued",
                "preparing",
                "starting",
                "running",
                "reconciling",
                "stopping",
                "finished",
            }
            or record.execution_result not in {"not_started", "unknown", "exited"}
            or record.resource_state not in {"not_created", "active", "closed", "unverified"}
            or type(record.reconciliation_required) is not bool
            or type(record.version) is not int
            or record.version < 1
            or not record.resource_evidence
            or not all(c.isascii() and (c.isalnum() or c == "_") for c in record.resource_evidence)
            or not isinstance(record.failures, list)
            or len(record.failures) > 8
            or any(
                not isinstance(f, str)
                or not f
                or len(f) > 100
                or not all(c.isascii() and (c.isalnum() or c == "_") for c in f)
                for f in [
                    *record.failures,
                    *([record.first_failure] if record.first_failure is not None else []),
                ]
            )
            or (
                record.uptime_ms is not None
                and (type(record.uptime_ms) is not int or record.uptime_ms < 0)
            )
            or (record.exit_code is not None and type(record.exit_code) is not int)
        ):
            raise ValueError("invalid lifecycle facts")

    def _save(self) -> None:
        if self.directory is None:
            return
        try:
            payload = {
                "records": [asdict(r) for r in self._records.values()],
                "recoveries": self._recoveries,
            }
            content = json.dumps(
                {"schemaVersion": 1, "payloadSha256": _digest(payload), **payload},
                ensure_ascii=False,
            )
            if len(content.encode("utf-8")) > MAX_BYTES:
                raise ValueError("lifecycle storage capacity exceeded")
            temporary = self.directory / "state.tmp"
            if temporary.exists() and (temporary.is_symlink() or temporary.is_junction()):
                raise ValueError("invalid lifecycle temporary file")
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / "state.json")
        except (OSError, ValueError):
            self.error = "lifecycle_write_failed"
            raise LifecycleUnavailable(self.error) from None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._owner is not None:
                self._owner.release()
                self._owner = None
