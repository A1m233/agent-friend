"""Public ordinary-memory maintenance boundary and index wakeup."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .models import MaintenanceAuthorization

if TYPE_CHECKING:
    from ..store import SqliteMemoryStore


class SemanticMaintenance:
    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        owner_user_id: str,
        request_backfill: Callable[[], None] | None = None,
    ) -> None:
        self.repository = store.maintenance_repository()
        self.owner = owner_user_id
        self._backfill = request_backfill

    def commit(self, authorization: MaintenanceAuthorization) -> dict[str, Any]:
        receipt = self.repository.commit(self.owner, authorization)
        index_status = "disabled"
        if self._backfill is not None:
            try:
                self._backfill()
                index_status = "requested"
            except Exception:
                # The transaction is already durable. Report index lag honestly.
                index_status = "request_failed"
        return self.repository.record_index_refresh(self.owner, receipt["batch_id"], index_status)
