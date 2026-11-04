"""Explicit recovery of the known cold-delivery journal fork, never automatic."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..context.checkpoints import resolve_prior_summary
from ..context.turns import safe_compaction_boundaries
from .errors import SessionCorruptError, SessionNotFoundError, SessionPersistError
from .events import ROLLOUT_EVENT_TYPES, Event
from .model_history import model_relevant_event_uuids
from .rollout import SessionRolloutRecorder, serialize_message
from .session import Session
from .store import JsonlSessionStore
from .store_types import SessionReadState


def _business_digest(events: list[Event]) -> str:
    digest = hashlib.sha256()
    for event in events:
        digest.update(event.to_jsonl().encode("utf-8") + b"\n")
    return digest.hexdigest()


def recover_delivery_history(
    store: JsonlSessionStore, session_id: str, *, apply: bool = False
) -> dict[str, object]:
    """Preserve a verified terminal prefix and a safe business suffix, with backup.

    Refuses unknown corruption, rewritten prefixes and new tool transactions.
    Projection-only edits remain in business history; they can be applied again
    by context construction. They must not erase the verified model snapshot.
    """
    lease = store.acquire_writer_lease(session_id)
    try:
        with store.maintenance_guard(session_id):
            source = store._existing_path(session_id)
            if source is None:
                raise SessionNotFoundError(session_id)
            version = store._source_version(source)
            session = store.load_execution(session_id)
            restored = session.restored_rollout
            if (
                restored is None
                or restored.disposition != "corrupt_committed_history"
                or restored.runtime_blocked
                or restored.commit is None
                or not restored.commit.turn_terminal
            ):
                raise SessionCorruptError("不是可恢复的主动投递历史断链")
            history = restored.history
            current = model_relevant_event_uuids(session)
            prefix_ids = history.active_event_uuids
            if not prefix_ids or current[: len(prefix_ids)] != prefix_ids:
                raise SessionCorruptError("已验证历史的业务前缀发生变化")
            suffix_ids = set(current[len(prefix_ids) :])
            suffix = [event for event in session.active_events if event.uuid in suffix_ids]
            safe_types = {
                "system_trigger",
                "assistant_message",
                "user_message",
                "skill_catalog_state",
                "run_error",
                "tool_context_edit",
            }
            if not suffix or any(event.type not in safe_types for event in suffix):
                raise SessionCorruptError("断链后包含不能安全重建的业务变化")
            if not any(
                event.type == "assistant_message"
                and event.meta.get("message_source", {}).get("kind") == "agent_initiated"
                for event in suffix
            ):
                raise SessionCorruptError("缺少主动投递来源")

            # Check the first rejected commit, not an arbitrary later lookalike.
            business: list[Event] = []
            state = SessionReadState()
            found_prefix = False
            rejected: Event | None = None
            for event in store.iter_events(session_id, state):
                if event.type not in ROLLOUT_EVENT_TYPES:
                    business.append(event)
                if event.type == "rollout_commit":
                    if found_prefix and rejected is None:
                        rejected = event
                    if event.payload.get("commit_id") == restored.commit.commit_id:
                        found_prefix = True
            if state.diagnostics.parse_error_count:
                raise SessionCorruptError("存在损坏行，拒绝恢复")
            boundary_index = next(
                index for index, event in enumerate(business) if event.uuid == prefix_ids[-1]
            )
            harmless = {
                "run_started",
                "run_terminal",
                "memory_observation",
                "assistant_phase",
                "image_delivery",
                "model_context",
            }
            if any(
                event.type not in safe_types | harmless for event in business[boundary_index + 1 :]
            ):
                raise SessionCorruptError("断链后包含不能安全重建的原始业务变化")
            if (
                rejected is None
                or rejected.payload.get("base_commit_id") is not None
                or rejected.payload.get("boundary") != "assistant_completed"
                or rejected.payload.get("exact_ineligibility_reason") != "external_message"
                or rejected.payload.get("turn_terminal") is not True
            ):
                raise SessionCorruptError("断链不符合旧版主动投递故障特征")
            expected_business = _business_digest(business)
            before_items = len(history.items)
            tail = Session.from_events([business[0], *suffix])
            history.append(tail.business_llm_messages)
            history.active_event_uuids = current
            history.generation += 1
            try:
                safe_compaction_boundaries(history.items)
            except ValueError as exc:
                raise SessionCorruptError("恢复后的工具事务不完整") from exc
            summary = resolve_prior_summary(session.active_events, history.items, scope="model")
            if summary.status == "invalid":
                raise SessionCorruptError(f"恢复后的摘要无效: {summary.invalid_reason}")
            expected_messages = [serialize_message(item)[0] for item in history.items]
            restored.cache_epoch.advance("validated_delivery_recovery")

            with TemporaryDirectory(prefix=".delivery-recovery-", dir=store.base_dir) as work:
                staged_store = JsonlSessionStore(work)
                staged = Path(work) / source.name
                with staged.open("xb") as output:
                    for event in business:
                        output.write(event.to_jsonl().encode("utf-8") + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                staged_session = staged_store.load_business(session_id)
                recorder = SessionRolloutRecorder(staged_store, staged_session)
                recorder.commit(
                    history=history,
                    model_context=restored.model_context,
                    cache_epoch=restored.cache_epoch,
                    boundary="manual_checkpoint",
                    turn_terminal=True,
                    exact_ineligibility_reason="validated_delivery_recovery",
                )
                after_session = staged_store.load_execution(session_id)
                after = after_session.restored_rollout
                if (
                    after is None
                    or after.runtime_blocked
                    or after.disposition
                    in {"corrupt_committed_history", "incompatible_rollout_schema"}
                    or [serialize_message(item)[0] for item in after.history.items]
                    != expected_messages
                    or after.history.active_event_uuids != current
                    or _business_digest(after_session.events) != expected_business
                ):
                    raise SessionCorruptError("恢复副本校验失败，原文件未修改")
                if store._source_version(source) != version:
                    raise SessionPersistError("恢复输入已变化，拒绝发布")
                report: dict[str, object] = {
                    "session_id": session_id,
                    "business_events": len(business),
                    "business_sha256": expected_business,
                    "verified_prefix_items": before_items,
                    "recovered_items": len(history.items),
                    "summary_status": summary.status,
                    "before_bytes": version[2],
                    "after_bytes": staged.stat().st_size,
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
                    with staged.open("rb+") as output:
                        os.fsync(output.fileno())
                    os.replace(staged, source)
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
