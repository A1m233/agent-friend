"""Append-only reconciliation for tool transactions orphaned by runtime loss."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol
from uuid import uuid4

from .events import Event
from .query import CancelCheck, check_cancel, load_session
from .session import Session
from .store import SessionStore
from .tool_execution_evidence import (
    ToolExecutionEvidenceError,
    ToolExecutionEvidenceStore,
    render_interrupted_evidence,
)

logger = logging.getLogger(__name__)


class RecoveryProbeOutcome(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    LOST = "lost"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RecoveredToolResult:
    content: str
    is_error: bool
    process_status: str = "unknown"
    semantic_status: str = "unknown"
    executed: bool | None = None
    reason_code: str = "recovered_result"
    evidence_ref: str = ""


@dataclass(frozen=True)
class RecoveryProbe:
    outcome: RecoveryProbeOutcome
    result: RecoveredToolResult | None = None
    reason_code: str = ""


class ToolRecoveryAdapter(Protocol):
    def probe(
        self,
        *,
        session_id: str,
        run_id: str,
        request: Event,
        checkpoint: Event | None,
    ) -> RecoveryProbe: ...


@dataclass(frozen=True)
class ReconciliationReport:
    session: Session
    interrupted_tool_calls: int = 0
    recovered_tool_calls: int = 0
    reattachable_tool_calls: int = 0
    unknown_tool_calls: int = 0
    appended_run_terminals: int = 0


class ToolTransactionReconciler:
    """Close only transactions that have decisive loss or recovered results."""

    def __init__(
        self,
        store: SessionStore,
        *,
        runtime_instance_id: str,
        adapters: dict[str, ToolRecoveryAdapter] | None = None,
        tool_execution_evidence_store: ToolExecutionEvidenceStore | None = None,
    ) -> None:
        self._store = store
        self._runtime_instance_id = runtime_instance_id
        self._adapters = dict(adapters or {})
        self._tool_execution_evidence_store = tool_execution_evidence_store
        self._locks_guard = Lock()
        self._session_locks: dict[str, Lock] = {}
        self._terminal_observers: list[Callable[[Session, Event], None]] = []

    def add_terminal_observer(self, observer: Callable[[Session, Event], None]) -> None:
        self._terminal_observers.append(observer)

    def reconcile_session(
        self,
        session_id: str,
        *,
        cancel_check: CancelCheck = None,
        loaded_session: Session | None = None,
    ) -> ReconciliationReport:
        lock = self._session_lock(session_id)
        while True:
            check_cancel(cancel_check)
            if lock.acquire(timeout=0.05):
                break
        try:
            return self._reconcile_session_once(
                session_id, cancel_check=cancel_check, loaded_session=loaded_session
            )
        finally:
            lock.release()

    def reconcile_known_sessions(self) -> tuple[ReconciliationReport, ...]:
        """Best-effort startup scan sharing the same per-session idempotency guard."""

        reports: list[ReconciliationReport] = []
        for summary in self._store.list():
            try:
                reports.append(self.reconcile_session(summary.session_id))
            except Exception:
                logger.exception(
                    "tool transaction background reconciliation failed session_id=%s",
                    summary.session_id,
                )
        return tuple(reports)

    def _session_lock(self, session_id: str) -> Lock:
        with self._locks_guard:
            return self._session_locks.setdefault(session_id, Lock())

    def _reconcile_session_once(
        self,
        session_id: str,
        *,
        cancel_check: CancelCheck = None,
        loaded_session: Session | None = None,
    ) -> ReconciliationReport:
        can_reuse = getattr(self._store, "can_reuse_loaded", None)
        # Check inside the reconciliation guard, after the manager's writer lease.
        # Unknown stores and changed files retain the full-load fallback.
        if (
            loaded_session is not None
            and loaded_session.session_id == session_id
            and callable(can_reuse)
            and can_reuse(loaded_session)
        ):
            session = loaded_session
        else:
            session = load_session(self._store, session_id, cancel_check=cancel_check)
        requests: dict[tuple[str, str], Event] = {}
        results: set[tuple[str, str]] = set()
        checkpoints: dict[tuple[str, str], Event] = {}
        terminal_runs: set[str] = set()
        request_aliases: dict[tuple[str, str], tuple[str, str]] = {}

        for event in session.active_events:
            check_cancel(cancel_check)
            run_id = self._run_id(event)
            if event.type == "run_terminal":
                payload_run_id = event.payload.get("run_id")
                if isinstance(payload_run_id, str) and payload_run_id:
                    terminal_runs.add(payload_run_id)
                continue
            if event.type != "tool_call_request" or not run_id:
                continue
            provider_call_id = event.payload.get("tool_call_id")
            event_call_id = event.payload.get("event_tool_call_id")
            transaction_id = event_call_id or provider_call_id
            if not isinstance(transaction_id, str) or not transaction_id:
                continue
            key = (run_id, transaction_id)
            requests[key] = event
            if isinstance(provider_call_id, str) and provider_call_id:
                request_aliases[(run_id, provider_call_id)] = key

        for event in session.active_events:
            check_cancel(cancel_check)
            if event.type not in {"tool_call_result", "tool_execution_checkpoint"}:
                continue
            run_id = self._run_id(event)
            transaction_id = (
                event.payload.get("event_tool_call_id")
                if event.type == "tool_call_result"
                else None
            ) or event.payload.get("tool_call_id")
            if not run_id or not isinstance(transaction_id, str) or not transaction_id:
                continue
            raw_key = (run_id, transaction_id)
            key = raw_key if raw_key in requests else request_aliases.get(raw_key, raw_key)
            if event.type == "tool_call_result":
                results.add(key)
            else:
                checkpoints[key] = event

        interrupted = 0
        recovered = 0
        reattachable = 0
        unknown = 0
        touched_runs: set[str] = set()
        prior_owner_runs = {
            run_id
            for (run_id, _tool_call_id), request in requests.items()
            if (owner := request.meta.get("owner_runtime_instance_id"))
            and owner != self._runtime_instance_id
        }
        # A model-only run can lose its owner before issuing any tool request.
        prior_owner_runs.update(
            started_run_id
            for event in session.active_events
            if event.type == "run_started"
            and isinstance((started_run_id := event.payload.get("run_id")), str)
            and started_run_id
            and (owner := event.meta.get("owner_runtime_instance_id"))
            and owner != self._runtime_instance_id
        )

        for key, request in requests.items():
            check_cancel(cancel_check)
            if key in results:
                continue
            run_id, _tool_call_id = key
            checkpoint = checkpoints.get(key)
            adapter = self._adapters.get(str(request.payload.get("tool_name") or ""))
            probe = (
                adapter.probe(
                    session_id=session_id,
                    run_id=run_id,
                    request=request,
                    checkpoint=checkpoint,
                )
                if adapter is not None
                else self._default_probe(request, checkpoint)
            )
            check_cancel(cancel_check)
            if probe.outcome is RecoveryProbeOutcome.RUNNING:
                reattachable += 1
                continue
            if probe.outcome is RecoveryProbeOutcome.UNKNOWN:
                unknown += 1
                continue
            if probe.outcome is RecoveryProbeOutcome.COMPLETED and probe.result is not None:
                event = self._result_event(
                    request,
                    probe.result,
                    result_origin="host_recovered_result",
                )
                self._append(session, event)
                results.add(key)
                touched_runs.add(run_id)
                recovered += 1
                continue
            if probe.outcome is RecoveryProbeOutcome.LOST:
                result = self._interrupted_result_from_evidence(
                    session_id,
                    request,
                    checkpoint,
                )
                if result is None:
                    result = RecoveredToolResult(
                        content=(
                            "原宿主运行实例已丢失，无法继续该工具事务；"
                            "工具是否产生副作用及其领域结果均未知。"
                        ),
                        is_error=True,
                        process_status=(
                            "runtime_lost"
                            if checkpoint is not None
                            and checkpoint.payload.get("recovery_mode") == "resident_only"
                            else "unknown"
                        ),
                        semantic_status="unknown",
                        executed=None,
                        reason_code=probe.reason_code or "computer_or_owner_runtime_lost",
                    )
                event = self._result_event(
                    request,
                    result,
                    result_origin="host_reconciliation",
                    transaction_status="interrupted",
                )
                self._append(session, event)
                results.add(key)
                touched_runs.add(run_id)
                interrupted += 1

        appended_terminals = 0
        for run_id in sorted(touched_runs | prior_owner_runs):
            run_keys = [key for key in requests if key[0] == run_id]
            if run_id in terminal_runs or not all(key in results for key in run_keys):
                continue
            terminal = Event(
                type="run_terminal",
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={
                    "run_id": run_id,
                    "status": "interrupted",
                    "reason_code": "computer_or_owner_runtime_lost",
                    "source": "startup_reconciliation",
                    "recovered": True,
                },
                meta={
                    "run_id": run_id,
                    "owner_runtime_instance_id": self._runtime_instance_id,
                },
            )
            self._append(session, terminal)
            for observer in tuple(self._terminal_observers):
                try:
                    observer(session, terminal)
                except Exception:
                    logger.exception("recovered terminal observer failed: %s", run_id)
            terminal_runs.add(run_id)
            appended_terminals += 1

        report = ReconciliationReport(
            session=session,
            interrupted_tool_calls=interrupted,
            recovered_tool_calls=recovered,
            reattachable_tool_calls=reattachable,
            unknown_tool_calls=unknown,
            appended_run_terminals=appended_terminals,
        )
        if any(
            (
                report.interrupted_tool_calls,
                report.recovered_tool_calls,
                report.reattachable_tool_calls,
                report.unknown_tool_calls,
                report.appended_run_terminals,
            )
        ):
            logger.info(
                "[RuntimeContinuity] orphan_reconciliation session_id=%s "
                "interrupted=%d recovered=%d reattachable=%d unknown=%d run_terminals=%d",
                session_id,
                report.interrupted_tool_calls,
                report.recovered_tool_calls,
                report.reattachable_tool_calls,
                report.unknown_tool_calls,
                report.appended_run_terminals,
            )
        return report

    def _default_probe(self, request: Event, checkpoint: Event | None) -> RecoveryProbe:
        owner = (
            str(checkpoint.meta.get("owner_runtime_instance_id") or "")
            if checkpoint is not None
            else str(request.meta.get("owner_runtime_instance_id") or "")
        )
        if owner == self._runtime_instance_id:
            return RecoveryProbe(RecoveryProbeOutcome.UNKNOWN, reason_code="current_owner_unknown")
        if checkpoint is not None and checkpoint.payload.get("recovery_mode") in {
            "queryable",
            "reattachable",
        }:
            return RecoveryProbe(
                RecoveryProbeOutcome.UNKNOWN,
                reason_code="recovery_adapter_unavailable",
            )
        return RecoveryProbe(
            RecoveryProbeOutcome.LOST,
            reason_code="computer_or_owner_runtime_lost",
        )

    def _interrupted_result_from_evidence(
        self,
        session_id: str,
        request: Event,
        checkpoint: Event | None,
    ) -> RecoveredToolResult | None:
        evidence_store = self._tool_execution_evidence_store
        if evidence_store is None or checkpoint is None:
            return None
        evidence_ref = checkpoint.payload.get("evidence_ref")
        operation_id = checkpoint.payload.get("operation_id")
        tool_name = request.payload.get("tool_name")
        run_id = self._run_id(request)
        tool_call_ids = {
            value
            for value in (
                request.payload.get("event_tool_call_id"),
                request.payload.get("tool_call_id"),
            )
            if isinstance(value, str) and value
        }
        if (
            not isinstance(evidence_ref, str)
            or not isinstance(operation_id, str)
            or not isinstance(tool_name, str)
            or tool_name not in {"exec_command", "host_process_start"}
            or not run_id
        ):
            return None
        try:
            evidence = evidence_store.load_for_transaction(
                session_id=session_id,
                evidence_ref=evidence_ref,
                run_id=run_id,
                tool_call_ids=tool_call_ids,
                operation_id=operation_id,
                tool_name=tool_name,
            )
        except ToolExecutionEvidenceError:
            logger.warning(
                "startup tool evidence rejected run_id=%s tool_call_id=%s evidence_ref=%s",
                run_id,
                next(iter(tool_call_ids), ""),
                evidence_ref,
                exc_info=True,
            )
            return None
        rendered = render_interrupted_evidence(evidence) if evidence is not None else None
        if rendered is None:
            return None
        return RecoveredToolResult(
            content=rendered,
            is_error=True,
            process_status="runtime_lost",
            semantic_status="unknown",
            executed=None,
            reason_code="computer_or_owner_runtime_lost",
            evidence_ref=evidence_ref,
        )

    def _result_event(
        self,
        request: Event,
        result: RecoveredToolResult,
        *,
        result_origin: str,
        transaction_status: str = "completed",
    ) -> Event:
        executed: bool | str = result.executed if result.executed is not None else "unknown"
        payload: dict[str, object] = {
            "tool_call_id": request.payload.get("tool_call_id", ""),
            "event_tool_call_id": request.payload.get("event_tool_call_id", ""),
            "tool_name": request.payload.get("tool_name", ""),
            "content": result.content,
            "is_error": result.is_error,
            "executed": executed,
            "authorization_requested": False,
            "error_code": result.reason_code if result.is_error else "",
            "transaction_status": transaction_status,
            "process_status": result.process_status,
            "semantic_status": result.semantic_status,
            "execution_state": executed,
            "result_origin": result_origin,
            "reason_code": result.reason_code,
        }
        if result.evidence_ref:
            payload["evidence_ref"] = result.evidence_ref
        return Event(
            type="tool_call_result",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload=payload,
            meta={
                "run_id": self._run_id(request),
                "result_origin": result_origin,
                "owner_runtime_instance_id": self._runtime_instance_id,
            },
        )

    def _append(self, session: Session, event: Event) -> None:
        self._store.append_event(session.session_id, event)
        session.append(event)

    @staticmethod
    def _run_id(event: Event) -> str:
        run_id = event.meta.get("run_id")
        return run_id if isinstance(run_id, str) else ""


__all__ = [
    "ReconciliationReport",
    "RecoveredToolResult",
    "RecoveryProbe",
    "RecoveryProbeOutcome",
    "ToolRecoveryAdapter",
    "ToolTransactionReconciler",
]
