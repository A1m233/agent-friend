"""Bounded content-free command diagnostics; never persist raw tool or setup output."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_EVENTS = 128
MAX_BYTES = 64 * 1024
STAGES = {
    "start",
    "setup",
    "probe",
    "helper",
    "policy",
    "visibility",
    "connection",
    "automatic_recovery",
    "manual_recovery",
    "recovery_resume",
}
CODES = {
    "requested",
    "ok",
    "failed",
    "cancelled",
    "timed_out",
    "not_authorized",
    "allowed",
    "helper_launch_failed",
    "helper_incomplete",
    "helper_exit_failed",
    "state_invalid",
    "runtime_lost",
    "unknown",
    "proxy_domain_denied",
    "proxy_private_target_denied",
    "proxy_route_denied",
    "payload_rejected",
}
PROXIES = {"none", "executor_managed", "inherited_loopback"}


class CommandDiagnostics:
    def __init__(self, path: Path | None, runtime_id: str, build_id: str = "") -> None:
        self._path = path
        self._runtime = hashlib.sha256(runtime_id.encode()).hexdigest()[:16]
        self._build = hashlib.sha256(build_id.encode()).hexdigest()[:16]
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._gaps: set[str] = set()
        self._repair_required = False
        if path is not None and path.exists():
            try:
                with path.open("rb") as source:
                    raw = source.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("oversized")
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get("events"), list):
                    raise ValueError("invalid history")
                self._repair_required = data.get("repairRequired") is True
                self._gaps.update(
                    g
                    for g in data.get("gaps", [])
                    if isinstance(g, str)
                    and g in {"invalid_record", "history_unavailable", "evicted", "write_failed"}
                )
                for item in data["events"][-MAX_EVENTS:]:
                    if self._valid(item):
                        self._events.append(item)
                    else:
                        self._gaps.add("invalid_record")
            except (OSError, ValueError, KeyError, TypeError):
                self._gaps.add("history_unavailable")

    @staticmethod
    def _valid(item: object) -> bool:
        if not isinstance(item, dict) or set(item) != {
            "at",
            "runtime",
            "build",
            "stage",
            "code",
            "nativeCode",
            "proxy",
            "operation",
        }:
            return False
        try:
            datetime.fromisoformat(item["at"])
            return (
                item["stage"] in STAGES
                and item["code"] in CODES
                and item["proxy"] in PROXIES
                and (item["nativeCode"] is None or type(item["nativeCode"]) is int)
                and all(
                    isinstance(item[k], str)
                    and len(item[k]) == 16
                    and all(c in "0123456789abcdef" for c in item[k])
                    for k in ("runtime", "operation", "build")
                )
            )
        except (ValueError, TypeError):
            return False

    def record(
        self,
        stage: str,
        code: str,
        *,
        operation: str = "",
        native_code: int | None = None,
        proxy: str = "none",
        persist: bool = True,
    ) -> None:
        item = {
            "at": datetime.now(UTC).isoformat(),
            "runtime": self._runtime,
            "build": self._build,
            "operation": hashlib.sha256(operation.encode()).hexdigest()[:16],
            "stage": stage,
            "code": code,
            "nativeCode": native_code,
            "proxy": proxy,
        }
        if not self._valid(item):
            raise ValueError("invalid diagnostic event")
        with self._lock:
            if len(self._events) == MAX_EVENTS:
                self._gaps.add("evicted")
            self._events.append(item)
            if code in {"helper_launch_failed", "helper_incomplete", "helper_exit_failed"} or (
                stage in {"setup", "probe"} and code == "failed"
            ):
                self._repair_required = True
            elif stage in {"setup", "probe"} and code == "ok":
                self._repair_required = False
            if persist:
                self.flush()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schemaVersion": 1,
                "events": copy.deepcopy(list(self._events)),
                "gaps": sorted(self._gaps),
                "limit": MAX_EVENTS,
                "repairRequired": self._repair_required,
            }

    def needs_repair(self) -> bool:
        with self._lock:
            return self._repair_required

    def flush(self) -> None:
        if self._path is None:
            return
        with self._lock:
            target = self._path.with_suffix(".tmp")
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                raw = json.dumps(self.snapshot(), ensure_ascii=True).encode()
                if len(raw) > MAX_BYTES:
                    raise ValueError("diagnostics exceeds limit")
                target.write_bytes(raw)
                os.replace(target, self._path)
            except (OSError, ValueError):
                self._gaps.add("write_failed")


def classify_setup_error(message: str) -> tuple[str, int | None]:
    """Extract allowlisted facts, not arbitrary messages (which can contain user paths)."""
    import re

    match = re.search(r"ShellExecuteExW failed to launch setup helper: (\d{1,10})", message)
    if match:
        return "helper_launch_failed", int(match[1])
    if any(
        code in message
        for code in ("orchestrator_helper_launch_failed", "orchestrator_helper_launch_canceled")
    ):
        native = re.search(r"(?:os error |WinError )(\d{1,10})", message)
        return "helper_launch_failed", int(native[1]) if native else None
    if "orchestrator_helper_incomplete" in message:
        return "helper_incomplete", None
    if "orchestrator_helper_exit_nonzero" in message:
        return "helper_exit_failed", None
    return "unknown", None
