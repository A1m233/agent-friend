"""Content-free lifecycle export. Operational identifiers are correlation hashes only."""

from __future__ import annotations

import hashlib
from typing import Any

from .store import LifecycleStore


def _identity(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16] if value else None


def snapshot(store: LifecycleStore, recovery: dict[str, Any] | None) -> dict[str, Any]:
    records = store.records()
    return {
        "schemaVersion": 1,
        "storageError": store.error,
        "records": [
            {
                "operation": _identity(record.operation_id),
                "parent": _identity(record.parent_operation_id),
                "runtime": _identity(record.runtime_instance_id),
                "executor": _identity(record.executor_instance_id),
                "kind": record.kind,
                "scope": record.scope,
                "phase": record.phase,
                "executionResult": record.execution_result,
                "resourceState": record.resource_state,
                "resourceEvidence": record.resource_evidence,
                "reconciliationRequired": record.reconciliation_required,
                "firstFailure": record.first_failure,
                "failures": record.failures,
                "version": record.version,
                "exitCode": record.exit_code,
            }
            for record in records
        ],
        "recovery": {
            "operation": _identity(recovery["recovery_id"]),
            "phase": recovery["phase"],
            "result": recovery["result"],
            "version": recovery["version"],
            "cancelRequested": recovery.get("cancel_requested", False),
            "blockerCount": len(recovery.get("blockers", [])),
        }
        if recovery
        else None,
    }
