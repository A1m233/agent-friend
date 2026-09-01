"""Bounded, session-owned recovery evidence for interrupted tool executions."""

from __future__ import annotations

import json
import locale
import logging
import os
import re
import shutil
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

TOOL_EXECUTION_EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_STREAM_BYTES = 256 * 1024
MAX_EVIDENCE_FILE_BYTES = 8 * 1024 * 1024
MAX_SESSION_EVIDENCE_BYTES = 100 * 1024 * 1024
MAX_INLINE_EVIDENCE_BYTES = 64 * 1024
EVIDENCE_WRITE_INTERVAL_SECONDS = 1.0
EVIDENCE_WRITE_BYTES = 64 * 1024

_EVIDENCE_REF_PATTERN = re.compile(r"^tev_[0-9a-f]{32}$")


class ToolExecutionEvidenceError(RuntimeError):
    """Evidence persistence or validation failed without affecting tool execution."""


def _canonical_json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_text(value: str, limit: int) -> tuple[str, int]:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value, 0
    omitted = len(raw) - limit
    return raw[-limit:].decode("utf-8", errors="ignore"), omitted


@dataclass(frozen=True)
class ToolExecutionEvidence:
    """One atomically committed, incomplete command-output snapshot."""

    evidence_ref: str
    session_id: str
    run_id: str
    tool_call_id: str
    operation_id: str
    tool_name: str
    process_status: str
    output_seq: int
    dropped_bytes: int
    stdout: str = ""
    stderr: str = ""
    pty: str = ""
    source: str = "host_command_runtime"
    complete: bool = False
    captured_at: str = ""

    @property
    def has_output(self) -> bool:
        return any((self.stdout, self.stderr, self.pty))

    @property
    def output_bytes(self) -> int:
        return sum(len(value.encode("utf-8")) for value in (self.stdout, self.stderr, self.pty))

    def _content_record(self) -> dict[str, object]:
        return {
            "schema_version": TOOL_EXECUTION_EVIDENCE_SCHEMA_VERSION,
            "kind": "command_output",
            "evidence_ref": self.evidence_ref,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "operation_id": self.operation_id,
            "tool_name": self.tool_name,
            "source": self.source,
            "complete": self.complete,
            "process_status": self.process_status,
            "output_seq": self.output_seq,
            "dropped_bytes": self.dropped_bytes,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "pty": self.pty,
            "captured_at": self.captured_at,
        }

    def to_record(self) -> dict[str, object]:
        content = self._content_record()
        return {
            **content,
            "content_sha256": sha256(_canonical_json_bytes(content)).hexdigest(),
            "output_bytes": self.output_bytes,
        }

    @classmethod
    def from_record(cls, value: object) -> ToolExecutionEvidence:
        if not isinstance(value, dict):
            raise ToolExecutionEvidenceError("evidence record must be an object")
        if value.get("schema_version") != TOOL_EXECUTION_EVIDENCE_SCHEMA_VERSION:
            raise ToolExecutionEvidenceError("unsupported evidence schema version")
        if value.get("kind") != "command_output":
            raise ToolExecutionEvidenceError("unsupported evidence kind")
        required_strings = (
            "evidence_ref",
            "session_id",
            "run_id",
            "tool_call_id",
            "operation_id",
            "tool_name",
            "source",
            "process_status",
            "stdout",
            "stderr",
            "pty",
            "captured_at",
        )
        strings: dict[str, str] = {}
        for key in required_strings:
            item = value.get(key)
            if not isinstance(item, str):
                raise ToolExecutionEvidenceError(f"invalid evidence field: {key}")
            strings[key] = item
        if not _EVIDENCE_REF_PATTERN.fullmatch(strings["evidence_ref"]):
            raise ToolExecutionEvidenceError("invalid evidence reference")
        output_seq = value.get("output_seq")
        dropped_bytes = value.get("dropped_bytes")
        if (
            not isinstance(output_seq, int)
            or isinstance(output_seq, bool)
            or output_seq < 0
            or not isinstance(dropped_bytes, int)
            or isinstance(dropped_bytes, bool)
            or dropped_bytes < 0
        ):
            raise ToolExecutionEvidenceError("invalid evidence counters")
        if value.get("complete") is not False:
            raise ToolExecutionEvidenceError("process evidence must remain incomplete")
        content = {
            key: value[key]
            for key in (
                "schema_version",
                "kind",
                *required_strings,
                "complete",
                "output_seq",
                "dropped_bytes",
            )
        }
        expected_hash = value.get("content_sha256")
        if (
            not isinstance(expected_hash, str)
            or sha256(_canonical_json_bytes(content)).hexdigest() != expected_hash
        ):
            raise ToolExecutionEvidenceError("evidence content hash mismatch")
        evidence = cls(
            evidence_ref=strings["evidence_ref"],
            session_id=strings["session_id"],
            run_id=strings["run_id"],
            tool_call_id=strings["tool_call_id"],
            operation_id=strings["operation_id"],
            tool_name=strings["tool_name"],
            source=strings["source"],
            complete=False,
            process_status=strings["process_status"],
            output_seq=output_seq,
            dropped_bytes=dropped_bytes,
            stdout=strings["stdout"],
            stderr=strings["stderr"],
            pty=strings["pty"],
            captured_at=strings["captured_at"],
        )
        if evidence.output_bytes > MAX_EVIDENCE_STREAM_BYTES * 3:
            raise ToolExecutionEvidenceError("evidence output exceeds the bounded limit")
        recorded_output_bytes = value.get("output_bytes")
        if recorded_output_bytes != evidence.output_bytes:
            raise ToolExecutionEvidenceError("evidence output length mismatch")
        return evidence


def _snapshot_text(snapshot: object, name: str) -> str:
    value = getattr(snapshot, f"{name}_tail", b"")
    if isinstance(value, bytes):
        if value.startswith((b"\xff\xfe", b"\xfe\xff")) or value.count(b"\x00") > len(value) // 4:
            return value.decode("utf-16", errors="replace")
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            encoding = locale.getencoding()
            if encoding.lower().replace("-", "") in {"utf8", "utf_8"}:
                return value.decode("utf-8", errors="replace")
            return value.decode(encoding, errors="replace")
    return value if isinstance(value, str) else ""


def evidence_from_process_snapshot(snapshot: object) -> ToolExecutionEvidence:
    evidence_ref = getattr(snapshot, "evidence_ref", None)
    if not isinstance(evidence_ref, str) or not _EVIDENCE_REF_PATTERN.fullmatch(evidence_ref):
        raise ToolExecutionEvidenceError("process snapshot has no valid evidence reference")
    dropped_bytes = getattr(snapshot, "dropped_bytes", 0)
    bounded: dict[str, str] = {}
    extra_dropped = 0
    for name in ("stdout", "stderr", "pty"):
        bounded[name], omitted = _bounded_text(
            _snapshot_text(snapshot, name), MAX_EVIDENCE_STREAM_BYTES
        )
        extra_dropped += omitted
    return ToolExecutionEvidence(
        evidence_ref=evidence_ref,
        session_id=str(getattr(snapshot, "session_id", "")),
        run_id=str(getattr(snapshot, "run_id", "")),
        tool_call_id=str(getattr(snapshot, "tool_call_id", "")),
        operation_id=str(getattr(snapshot, "process_id", "")),
        tool_name=str(getattr(snapshot, "tool_name", "exec_command")),
        process_status=str(getattr(snapshot, "status", "unknown")),
        output_seq=max(0, int(getattr(snapshot, "output_seq", 0))),
        dropped_bytes=max(0, int(dropped_bytes)) + extra_dropped,
        stdout=bounded["stdout"],
        stderr=bounded["stderr"],
        pty=bounded["pty"],
        captured_at=datetime.now(UTC).isoformat(),
    )


class ToolExecutionEvidenceStore:
    """Atomically replace one bounded evidence file per command transaction."""

    def __init__(
        self,
        root: Path,
        *,
        max_session_bytes: int = MAX_SESSION_EVIDENCE_BYTES,
    ) -> None:
        self._root = root.resolve()
        self._max_session_bytes = max_session_bytes
        self._lock = threading.RLock()
        self._session_usage: dict[str, int] = {}
        self._last_write: dict[tuple[str, str], tuple[float, int, int]] = {}

    @staticmethod
    def new_reference() -> str:
        return f"tev_{uuid4().hex}"

    def observe_process_snapshot(self, snapshot: object) -> None:
        try:
            self.persist_process_snapshot(snapshot)
        except ToolExecutionEvidenceError:
            logger.warning(
                "tool execution evidence checkpoint failed session_id=%s run_id=%s "
                "tool_call_id=%s output_seq=%s",
                getattr(snapshot, "session_id", ""),
                getattr(snapshot, "run_id", ""),
                getattr(snapshot, "tool_call_id", ""),
                getattr(snapshot, "output_seq", 0),
                exc_info=True,
            )

    def persist_process_snapshot(self, snapshot: object, *, force: bool = False) -> bool:
        evidence_ref = getattr(snapshot, "evidence_ref", None)
        session_id = getattr(snapshot, "session_id", None)
        if not isinstance(evidence_ref, str) or not evidence_ref:
            return False
        if not isinstance(session_id, str) or not session_id:
            raise ToolExecutionEvidenceError("process snapshot has no session owner")
        output_seq = getattr(snapshot, "output_seq", 0)
        status = getattr(snapshot, "status", "unknown")
        terminal = status != "running"
        evidence = evidence_from_process_snapshot(snapshot)
        if not evidence.has_output:
            return False
        now = time.monotonic()
        key = (session_id, evidence_ref)
        with self._lock:
            previous = self._last_write.get(key)
            if previous is not None and not force and not terminal:
                previous_at, previous_seq, previous_bytes = previous
                if output_seq <= previous_seq:
                    return False
                if (
                    now - previous_at < EVIDENCE_WRITE_INTERVAL_SECONDS
                    and evidence.output_bytes - previous_bytes < EVIDENCE_WRITE_BYTES
                ):
                    return False
            self._write_locked(evidence)
            self._last_write[key] = (now, evidence.output_seq, evidence.output_bytes)
        return True

    def write(self, evidence: ToolExecutionEvidence) -> None:
        with self._lock:
            self._write_locked(evidence)
            self._last_write[(evidence.session_id, evidence.evidence_ref)] = (
                time.monotonic(),
                evidence.output_seq,
                evidence.output_bytes,
            )

    def load(self, session_id: str, evidence_ref: str) -> ToolExecutionEvidence | None:
        path = self._evidence_path(session_id, evidence_ref)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ToolExecutionEvidenceError(f"failed to read execution evidence: {exc}") from exc
        if len(raw) > MAX_EVIDENCE_FILE_BYTES:
            raise ToolExecutionEvidenceError("evidence file exceeds the bounded limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolExecutionEvidenceError("execution evidence is corrupt") from exc
        evidence = ToolExecutionEvidence.from_record(value)
        if evidence.session_id != session_id or evidence.evidence_ref != evidence_ref:
            raise ToolExecutionEvidenceError("execution evidence owner mismatch")
        return evidence

    def load_for_transaction(
        self,
        *,
        session_id: str,
        evidence_ref: str,
        run_id: str,
        tool_call_ids: set[str],
        operation_id: str,
        tool_name: str,
    ) -> ToolExecutionEvidence | None:
        evidence = self.load(session_id, evidence_ref)
        if evidence is None:
            return None
        if (
            evidence.run_id != run_id
            or evidence.tool_call_id not in tool_call_ids
            or evidence.operation_id != operation_id
            or evidence.tool_name != tool_name
        ):
            raise ToolExecutionEvidenceError("execution evidence transaction mismatch")
        return evidence

    def delete(self, session_id: str, evidence_ref: str) -> None:
        path = self._evidence_path(session_id, evidence_ref)
        with self._lock:
            try:
                old_size = path.stat().st_size
            except FileNotFoundError:
                old_size = 0
            except OSError as exc:
                raise ToolExecutionEvidenceError(
                    f"failed to inspect execution evidence: {exc}"
                ) from exc
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise ToolExecutionEvidenceError(
                    f"failed to delete execution evidence: {exc}"
                ) from exc
            key = self._session_key(session_id)
            if key in self._session_usage:
                self._session_usage[key] = max(0, self._session_usage[key] - old_size)
            self._last_write.pop((session_id, evidence_ref), None)
            with suppress(OSError):
                path.parent.rmdir()

    def delete_session(self, session_id: str) -> None:
        directory = self._session_dir(session_id)
        with self._lock:
            if directory.exists():
                try:
                    shutil.rmtree(directory)
                except OSError as exc:
                    raise ToolExecutionEvidenceError(
                        f"failed to delete session execution evidence: {exc}"
                    ) from exc
            key = self._session_key(session_id)
            self._session_usage.pop(key, None)
            for item in tuple(self._last_write):
                if item[0] == session_id:
                    self._last_write.pop(item, None)

    def _write_locked(self, evidence: ToolExecutionEvidence) -> None:
        record = evidence.to_record()
        raw = _canonical_json_bytes(record)
        if len(raw) > MAX_EVIDENCE_FILE_BYTES:
            raise ToolExecutionEvidenceError("evidence file exceeds the bounded limit")
        directory = self._session_dir(evidence.session_id)
        path = self._evidence_path(evidence.session_id, evidence.evidence_ref)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            old_size = path.stat().st_size
        except FileNotFoundError:
            old_size = 0
        except OSError as exc:
            raise ToolExecutionEvidenceError(
                f"failed to inspect execution evidence: {exc}"
            ) from exc
        session_key = self._session_key(evidence.session_id)
        usage = self._session_usage.get(session_key)
        if usage is None:
            try:
                usage = sum(item.stat().st_size for item in directory.glob("tev_*.json"))
            except OSError as exc:
                raise ToolExecutionEvidenceError(
                    f"failed to inspect session evidence quota: {exc}"
                ) from exc
        new_usage = usage - old_size + len(raw)
        if new_usage > self._max_session_bytes:
            raise ToolExecutionEvidenceError("session execution evidence quota exceeded")
        temporary = directory / f".{evidence.evidence_ref}.{uuid4().hex}.tmp"
        try:
            with temporary.open("wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise ToolExecutionEvidenceError(
                f"failed to persist execution evidence: {exc}"
            ) from exc
        self._session_usage[session_key] = new_usage

    def _evidence_path(self, session_id: str, evidence_ref: str) -> Path:
        if not _EVIDENCE_REF_PATTERN.fullmatch(evidence_ref):
            raise ToolExecutionEvidenceError("invalid evidence reference")
        return self._session_dir(session_id) / f"{evidence_ref}.json"

    def _session_dir(self, session_id: str) -> Path:
        if not session_id:
            raise ToolExecutionEvidenceError("session id is required")
        return self._root / f"session-{self._session_key(session_id)}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        return sha256(session_id.encode("utf-8")).hexdigest()


def render_interrupted_evidence(evidence: ToolExecutionEvidence) -> str | None:
    """Render stable model-visible partial output without implying command success."""

    if not evidence.has_output:
        return None
    streams = (
        ("stdout", evidence.stdout),
        ("stderr", evidence.stderr),
        ("pty", evidence.pty),
    )
    nonempty = [(name, value) for name, value in streams if value]
    quota = MAX_INLINE_EVIDENCE_BYTES // max(1, len(nonempty))
    payload: dict[str, object] = {
        "status": "interrupted",
        "process_status": "cancelled_or_detached",
        "semantic_status": "unknown",
        "partial": True,
        "complete": False,
        "evidence_ref": evidence.evidence_ref,
        "evidence_source": evidence.source,
        "output_seq": evidence.output_seq,
        "dropped_bytes": evidence.dropped_bytes,
        "message": (
            "上一轮命令在正式结果提交前被中断；以下内容只是宿主已持久化的部分输出，"
            "不能证明命令成功、失败或副作用已回滚。"
        ),
    }
    omitted = 0
    for name, value in streams:
        retained, dropped = _bounded_text(value, quota)
        payload[name] = retained
        omitted += dropped
    payload["dropped_bytes"] = evidence.dropped_bytes + omitted
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def render_recoverable_evidence(evidence: ToolExecutionEvidence) -> str:
    """Render the full bounded artifact for query/offset recovery."""

    return json.dumps(
        {
            "status": "interrupted_evidence",
            "partial": True,
            "complete": False,
            "evidence_ref": evidence.evidence_ref,
            "evidence_source": evidence.source,
            "output_seq": evidence.output_seq,
            "dropped_bytes": evidence.dropped_bytes,
            "stdout": evidence.stdout,
            "stderr": evidence.stderr,
            "pty": evidence.pty,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "MAX_INLINE_EVIDENCE_BYTES",
    "ToolExecutionEvidence",
    "ToolExecutionEvidenceError",
    "ToolExecutionEvidenceStore",
    "evidence_from_process_snapshot",
    "render_interrupted_evidence",
    "render_recoverable_evidence",
]
