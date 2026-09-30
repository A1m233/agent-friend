"""Explicit, validated physical rewrite; never invoked by ordinary session loading."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from .errors import SessionCorruptError, SessionNotFoundError, SessionPersistError
from .rollout_encoding import RolloutEncoder, fingerprint
from .store import JsonlSessionStore
from .store_types import SessionReadState


def _logical_digest(store: JsonlSessionStore, session_id: str) -> str:
    state = SessionReadState()
    digest = hashlib.sha256()
    for event in store.iter_events(session_id, state):
        if event.type != "rollout_value":
            digest.update(fingerprint(json.loads(event.to_jsonl())).encode())
    if state.diagnostics.parse_error_count:
        raise SessionCorruptError("维护拒绝重写含损坏记录的历史")
    return digest.hexdigest()


def compact_session(
    store: JsonlSessionStore, session_id: str, *, apply: bool = False
) -> dict[str, object]:
    """Check and optionally publish a replacement; keep an fsynced original backup."""
    lease = store.acquire_writer_lease(session_id)
    try:
        with store.maintenance_guard(session_id):
            source = store._existing_path(session_id)
            if source is None:
                raise SessionNotFoundError(session_id)
            version = store._source_version(source)
            expected = _logical_digest(store, session_id)
            before = store.load_execution(session_id).restored_rollout
            if (
                before is None
                or before.runtime_blocked
                or before.disposition
                in {"corrupt_committed_history", "incompatible_rollout_schema"}
            ):
                raise SessionCorruptError("维护拒绝重写未通过恢复校验的历史")
            # Same filesystem makes publication atomic. Interrupted staging is
            # never discovered by the store's top-level JSONL enumeration.
            with TemporaryDirectory(prefix=".session-maintenance-", dir=store.base_dir) as work:
                staged_store = JsonlSessionStore(work)
                staged = Path(work) / source.name
                encoder = RolloutEncoder()
                state = SessionReadState()
                with staged.open("xb") as output:
                    for event in store.iter_events(session_id, state):
                        if event.type == "rollout_value":
                            continue
                        for record in encoder.encode(event):
                            output.write(record.to_jsonl().encode("utf-8") + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                after = staged_store.load_execution(session_id).restored_rollout
                if (
                    after is None
                    or after.disposition != before.disposition
                    or after.commit != before.commit
                    or _logical_digest(staged_store, session_id) != expected
                ):
                    raise SessionCorruptError("维护结果与原始历史不等价")
                if store._source_version(source) != version:
                    raise SessionPersistError("维护输入已变化，拒绝发布")
                report: dict[str, object] = {
                    "before_bytes": version[2],
                    "after_bytes": staged.stat().st_size,
                    "equivalent": True,
                    "applied": False,
                    "backup": None,
                }
                if apply:
                    backup = source.with_name(f"{source.name}.{uuid4().hex}.backup")
                    with source.open("rb") as input_file, backup.open("xb") as backup_file:
                        shutil.copyfileobj(input_file, backup_file)
                        backup_file.flush()
                        os.fsync(backup_file.fileno())
                    if store._source_version(source) != version:
                        raise SessionPersistError("备份期间输入已变化，原文件保留")
                    os.replace(staged, source)
                    # The old backup is retained even if directory syncing fails.
                    if os.name != "nt":
                        descriptor = os.open(source.parent, os.O_RDONLY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                    report.update(applied=True, backup=str(backup))
                return report
    finally:
        lease.release()
