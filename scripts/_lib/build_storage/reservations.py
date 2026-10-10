"""Conservative concurrent volume admission; stale owners remain protected."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .files import read_json, write_json
from .locks import lock
from .policy import space_plan
from .store import Storage


@contextmanager
def reserve(storage: Storage, needs: list[tuple[str, Path, int]]) -> Iterator[None]:
    operation = uuid.uuid4().hex
    destination = storage.control / "reservations" / f"{operation}.json"
    guard = storage.lock_path("admission", "volumes")
    with lock(guard):
        planned = space_plan(needs)
        pending: dict[str, int] = {}
        for path in destination.parent.glob("*.json"):
            other = read_json(path)
            for item in other["volumes"]:
                pending[item["volumeId"]] = pending.get(item["volumeId"], 0) + item["requiredBytes"]
        for item in planned["volumes"]:
            if item["availableBytes"] < item["requiredBytes"] + pending.get(item["volumeId"], 0):
                raise RuntimeError(
                    "insufficient unreserved volume space; inspect active or interrupted operations"
                )
        write_json(
            destination,
            {
                "schemaVersion": 1,
                "operationId": operation,
                "repoId": storage.repo_id,
                "ownerPid": os.getpid(),
                "startedAt": time.time(),
                "volumes": planned["volumes"],
            },
        )
    storage.operation_id = operation
    finished = False
    try:
        yield
        finished = True
    except Exception:
        finished = True
        raise
    finally:
        # Abrupt interruption may leave child writers; do not release their reserved capacity.
        storage.operation_id = None
        if finished:
            with lock(guard):
                destination.unlink()
