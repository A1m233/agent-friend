"""Disposable execution snapshots tied to an exact, fully hashed original source."""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import perf_counter, thread_time
from typing import TYPE_CHECKING

from ..errors import SessionCorruptError
from ..query import CancelCheck, check_cancel
from ..session import Session
from ..store_types import SourceVersion
from .codec import RECOVERY_SEMANTICS_VERSION, SNAPSHOT_SCHEMA_VERSION, decode_state, encode_state
from .digests import SourceDigestTracker, SourceHasher

if TYPE_CHECKING:
    from ..rollout import RolloutCommitState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionCacheObservation:
    status: str
    verification_ms: float = 0.0
    source_bytes: int = 0
    snapshot_bytes: int = 0
    maintenance_ms: float = 0.0
    lease_verification_ms: float = 0.0
    lease_source_bytes: int = 0
    lease_verified: bool | None = None
    maintenance_cpu_ms: float = 0.0
    state_capture_ms: float = 0.0
    state_capture_cpu_ms: float = 0.0
    serialization_ms: float = 0.0
    serialization_cpu_ms: float = 0.0
    guard_wait_ms: float = 0.0
    guard_wait_cpu_ms: float = 0.0
    write_flush_ms: float = 0.0
    write_flush_cpu_ms: float = 0.0
    atomic_publish_ms: float = 0.0
    atomic_publish_cpu_ms: float = 0.0


def _encode(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class ExecutionSnapshotCache:
    def __init__(
        self,
        base_dir: Path,
        tracker: SourceDigestTracker,
        *,
        maintenance_guard: Callable[[str], AbstractContextManager[None]],
    ) -> None:
        self._root = base_dir / ".execution"
        self._tracker = tracker
        self._maintenance_guard = maintenance_guard
        self._lock = RLock()
        self._observations: OrderedDict[str, ExecutionCacheObservation] = OrderedDict()

    def path(self, session_id: str) -> Path:
        return self._root / f"{sha256(session_id.encode('utf-8')).hexdigest()}.json"

    def observation(self, session_id: str) -> ExecutionCacheObservation | None:
        with self._lock:
            return self._observations.get(session_id)

    def note_lease_verification(
        self, session_id: str, elapsed_ms: float, source_bytes: int, matched: bool
    ) -> None:
        previous = self.observation(session_id) or ExecutionCacheObservation("unknown")
        self._observe(
            session_id,
            replace(
                previous,
                lease_verification_ms=elapsed_ms,
                lease_source_bytes=source_bytes,
                lease_verified=matched,
            ),
        )

    def _observe(self, session_id: str, observation: ExecutionCacheObservation) -> None:
        try:
            with self._lock:
                self._observations[session_id] = observation
                self._observations.move_to_end(session_id)
                while len(self._observations) > 512:
                    self._observations.popitem(last=False)
            logger.debug(
                "execution_snapshot %s",
                observation,
                extra={"session_key": sha256(session_id.encode()).hexdigest()[:16]},
            )
        except Exception:
            # An optional log sink must never alter read/write success semantics.
            pass

    def try_load(
        self,
        session_id: str,
        *,
        source_version: SourceVersion,
        read_source: Callable[[], tuple[SourceVersion, SourceHasher]],
        is_current: Callable[[], bool],
        cancel_check: CancelCheck = None,
    ) -> Session | None:
        check_cancel(cancel_check)
        try:
            encoded = self.path(session_id).read_bytes()
            envelope = json.loads(encoded)
            body = envelope["body"]
            if (
                envelope.get("sha256") != sha256(_encode(body)).hexdigest()
                or body.get("schema") != SNAPSHOT_SCHEMA_VERSION
                or body.get("semantics") != RECOVERY_SEMANTICS_VERSION
                or body.get("session_id") != session_id
                or body.get("source_version") != list(source_version)
            ):
                raise ValueError("snapshot metadata mismatch")
            session = decode_state(body.get("state"), session_id)
        except FileNotFoundError:
            self._observe(session_id, ExecutionCacheObservation("absent"))
            return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError, SessionCorruptError):
            self._observe(session_id, ExecutionCacheObservation("invalid"))
            return None
        check_cancel(cancel_check)
        started = perf_counter()
        # Always read the entire source for a cold restore, even if mtime and size
        # are unchanged. A process-local tracker is only for write maintenance.
        verified_version, hasher = read_source()
        elapsed = (perf_counter() - started) * 1000
        if (
            verified_version != source_version
            or hasher.hexdigest() != body.get("source_sha256")
            or not is_current()
        ):
            self._tracker.discard(session_id)
            self._observe(
                session_id,
                ExecutionCacheObservation(
                    "source_changed", elapsed, source_version[2], len(encoded)
                ),
            )
            return None
        check_cancel(cancel_check)
        self._tracker.accept(session_id, source_version, hasher)
        session.loaded_source_version = source_version
        session.execution_snapshot_digest = hasher.hexdigest()
        self._observe(
            session_id,
            ExecutionCacheObservation("hit", elapsed, source_version[2], len(encoded)),
        )
        return session

    def publish(
        self,
        session: Session,
        commit: RolloutCommitState | None,
        *,
        source_version: SourceVersion,
        is_current: Callable[[], bool],
    ) -> bool:
        """Best effort; never make successful authoritative persistence fail."""
        from ..rollout import is_exact_rollout_candidate

        started = perf_counter()
        cpu_started = thread_time()
        session_id = session.session_id
        status = "ineligible"
        size = 0
        phases: dict[str, float] = {}
        try:
            if (
                commit is None
                or session.load_diagnostics.parse_error_count
                or session.has_unresolved_tool_transactions()
                or not is_exact_rollout_candidate(session, commit)
            ):
                return False
            source_digest = self._tracker.digest(session_id, source_version)
            if source_digest is None or not is_current():
                status = "unverified_source"
                return False
            phase_started, phase_cpu = perf_counter(), thread_time()
            body = {
                "schema": SNAPSHOT_SCHEMA_VERSION,
                "semantics": RECOVERY_SEMANTICS_VERSION,
                "session_id": session_id,
                "source_version": list(source_version),
                "source_sha256": source_digest,
                "state": encode_state(session, commit),
            }
            phases["state_capture_ms"] = (perf_counter() - phase_started) * 1000
            phases["state_capture_cpu_ms"] = (thread_time() - phase_cpu) * 1000
            phase_started, phase_cpu = perf_counter(), thread_time()
            # The canonical body is also the exact byte slice stored inside the
            # unchanged JSON envelope. Do not encode/sort a large tree twice.
            encoded_body = _encode(body)
            encoded = b"".join(
                (
                    b'{"body":',
                    encoded_body,
                    b',"sha256":"',
                    sha256(encoded_body).hexdigest().encode("ascii"),
                    b'"}',
                )
            )
            phases["serialization_ms"] = (perf_counter() - phase_started) * 1000
            phases["serialization_cpu_ms"] = (thread_time() - phase_cpu) * 1000
            size = len(encoded)
            self._root.mkdir(parents=True, exist_ok=True)
            published = self._publish_encoded(session_id, encoded, is_current, phases)
            status = "stored" if published else "source_changed"
            return published
        except Exception:
            status = "write_failed"
            with suppress(Exception):
                logger.warning("execution snapshot maintenance failed", exc_info=True)
            return False
        finally:
            self._observe(
                session_id,
                ExecutionCacheObservation(
                    status,
                    snapshot_bytes=size,
                    maintenance_ms=(perf_counter() - started) * 1000,
                    maintenance_cpu_ms=(thread_time() - cpu_started) * 1000,
                    state_capture_ms=phases.get("state_capture_ms", 0.0),
                    state_capture_cpu_ms=phases.get("state_capture_cpu_ms", 0.0),
                    serialization_ms=phases.get("serialization_ms", 0.0),
                    serialization_cpu_ms=phases.get("serialization_cpu_ms", 0.0),
                    guard_wait_ms=phases.get("guard_wait_ms", 0.0),
                    guard_wait_cpu_ms=phases.get("guard_wait_cpu_ms", 0.0),
                    write_flush_ms=phases.get("write_flush_ms", 0.0),
                    write_flush_cpu_ms=phases.get("write_flush_cpu_ms", 0.0),
                    atomic_publish_ms=phases.get("atomic_publish_ms", 0.0),
                    atomic_publish_cpu_ms=phases.get("atomic_publish_cpu_ms", 0.0),
                ),
            )

    def _publish_encoded(
        self,
        session_id: str,
        encoded: bytes,
        is_current: Callable[[], bool],
        phases: dict[str, float],
    ) -> bool:
        # Reuse the authoritative store's cross-process maintenance guard. One
        # fixed temporary per session bounds crash debris and can be replaced
        # safely only while no other publisher or source writer holds the guard.
        target = self.path(session_id)
        temporary = target.with_suffix(".tmp")
        phase_started, phase_cpu = perf_counter(), thread_time()
        with self._maintenance_guard(session_id), self._lock:
            phases["guard_wait_ms"] = (perf_counter() - phase_started) * 1000
            phases["guard_wait_cpu_ms"] = (thread_time() - phase_cpu) * 1000
            try:
                phase_started, phase_cpu = perf_counter(), thread_time()
                temporary.unlink(missing_ok=True)
                with temporary.open("xb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                phases["write_flush_ms"] = (perf_counter() - phase_started) * 1000
                phases["write_flush_cpu_ms"] = (thread_time() - phase_cpu) * 1000
                phase_started, phase_cpu = perf_counter(), thread_time()
                if not is_current():
                    return False
                os.replace(temporary, target)
                phases["atomic_publish_ms"] = (perf_counter() - phase_started) * 1000
                phases["atomic_publish_cpu_ms"] = (thread_time() - phase_cpu) * 1000
                return True
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    with suppress(Exception):
                        logger.debug("execution snapshot temporary cleanup failed", exc_info=True)

    def delete(self, session_id: str) -> None:
        """Caller holds the same authoritative maintenance guard used by publish."""
        self._tracker.discard(session_id)
        self.path(session_id).unlink(missing_ok=True)
        self.path(session_id).with_suffix(".tmp").unlink(missing_ok=True)
        with self._lock:
            self._observations.pop(session_id, None)


__all__ = [
    "ExecutionCacheObservation",
    "ExecutionSnapshotCache",
    "SourceDigestTracker",
    "SourceHasher",
]
