"""应用级受管执行环境协调器。"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Protocol

from .models import (
    EnvironmentStatus,
    PreparedExecutionEnvironmentSnapshot,
    PreparedProviderEnvironment,
    ProviderHealth,
    ProviderIdentity,
    ResolvedManagedCommand,
)

logger = logging.getLogger(__name__)


class EnvironmentProvider(Protocol):
    """首期静态 provider registry 使用的最小内部接口。"""

    name: str
    identity: ProviderIdentity

    def prepare_global(self) -> ProviderHealth: ...

    def prepare_session(
        self,
        session_key: str,
        health: ProviderHealth,
    ) -> PreparedProviderEnvironment: ...

    def resolve_command(
        self,
        environment: PreparedProviderEnvironment,
        argv: tuple[str, ...],
    ) -> ResolvedManagedCommand | None: ...

    def matches_command(self, argv: tuple[str, ...]) -> bool: ...

    def record_command_success(
        self,
        command: ResolvedManagedCommand,
    ) -> PreparedProviderEnvironment: ...

    def collect_diagnostics(
        self,
        *,
        active_session_keys: frozenset[str],
    ) -> dict[str, object]: ...

    def classify_failure(
        self,
        command: ResolvedManagedCommand,
        output: str,
    ) -> tuple[str, str] | None: ...

    def release_session(self, session_key: str) -> None: ...

    def collect_health(self) -> ProviderHealth: ...

    def gc(self, *, active_session_keys: frozenset[str]) -> dict[str, int]: ...


class ExecutionEnvironmentManager:
    """协调 global prepare、session single-flight 与 run snapshot。"""

    def __init__(
        self,
        providers: Iterable[EnvironmentProvider],
        *,
        prepare_timeout_seconds: float = 30.0,
        max_workers: int = 4,
    ) -> None:
        provider_tuple = tuple(providers)
        names = [provider.name for provider in provider_tuple]
        if len(names) != len(set(names)):
            raise ValueError("execution environment provider 名称必须唯一")
        self._providers = {provider.name: provider for provider in provider_tuple}
        self._prepare_timeout_seconds = prepare_timeout_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(max_workers, len(provider_tuple) + 1)),
            thread_name_prefix="execution-environment",
        )
        self._lock = threading.RLock()
        self._global_futures: dict[str, Future[ProviderHealth]] = {}
        self._session_futures: dict[str, Future[PreparedExecutionEnvironmentSnapshot]] = {}
        self._run_snapshots: dict[tuple[str, str], PreparedExecutionEnvironmentSnapshot] = {}
        self._pending_turn_snapshots: dict[str, PreparedExecutionEnvironmentSnapshot] = {}
        self._closed = False

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def start(self) -> None:
        """在应用启动时异步预热所有共享 provider base。"""
        with self._lock:
            self._ensure_open()
            for name, provider in self._providers.items():
                if name not in self._global_futures:
                    self._global_futures[name] = self._executor.submit(
                        self._prepare_global, provider
                    )

    def schedule_session(
        self,
        session_id: str,
    ) -> Future[PreparedExecutionEnvironmentSnapshot]:
        """幂等启动 session overlay prepare，不保存原始 session id。"""
        session_key = session_key_for(session_id)
        with self._lock:
            self._ensure_open()
            self.start()
            future = self._session_futures.get(session_key)
            if future is None or (future.done() and self._snapshot_retryable(future)):
                future = self._executor.submit(self._prepare_session, session_key)
                self._session_futures[session_key] = future
            return future

    def snapshot_before_run(
        self,
        session_id: str,
        run_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> PreparedExecutionEnvironmentSnapshot:
        """等待既有 future 并把结果固定给本次 run；失败不阻断普通聊天。"""
        session_key = session_key_for(session_id)
        run_key = (session_key, run_id)
        with self._lock:
            existing = self._run_snapshots.get(run_key)
            pending = self._pending_turn_snapshots.pop(session_key, None)
        if existing is not None:
            return existing
        if pending is not None:
            with self._lock:
                return self._run_snapshots.setdefault(run_key, pending)
        future = self.schedule_session(session_id)
        try:
            snapshot = future.result(
                timeout=(
                    self._prepare_timeout_seconds if timeout_seconds is None else timeout_seconds
                )
            )
        except TimeoutError:
            snapshot = self._unavailable_snapshot(
                session_key,
                code="managed_environment_prepare_timeout",
                message="受管执行环境未能在首轮准备时限内完成。",
            )
        except Exception:
            logger.warning("session execution environment prepare failed", exc_info=True)
            snapshot = self._unavailable_snapshot(
                session_key,
                code="managed_environment_prepare_failed",
                message="受管执行环境准备失败。",
            )
        with self._lock:
            return self._run_snapshots.setdefault(run_key, snapshot)

    def prepare_before_turn(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> PreparedExecutionEnvironmentSnapshot:
        """在 pre-turn 阶段完成准备，供随后首次 tool call 绑定 run id。"""
        session_key = session_key_for(session_id)
        future = self.schedule_session(session_id)
        try:
            snapshot = future.result(
                timeout=(
                    self._prepare_timeout_seconds if timeout_seconds is None else timeout_seconds
                )
            )
        except TimeoutError:
            snapshot = self._unavailable_snapshot(
                session_key,
                code="managed_environment_prepare_timeout",
                message="受管执行环境未能在首轮准备时限内完成。",
            )
        except Exception:
            logger.warning("pre-turn execution environment prepare failed", exc_info=True)
            snapshot = self._unavailable_snapshot(
                session_key,
                code="managed_environment_prepare_failed",
                message="受管执行环境准备失败。",
            )
        with self._lock:
            self._pending_turn_snapshots[session_key] = snapshot
        return snapshot

    def snapshot_for_tool(
        self,
        session_id: str,
        run_id: str,
    ) -> PreparedExecutionEnvironmentSnapshot:
        """读取 pre-turn 已固定的 run snapshot；测试/旧入口作有界兜底。"""
        session_key = session_key_for(session_id)
        with self._lock:
            snapshot = self._run_snapshots.get((session_key, run_id))
        if snapshot is not None:
            return snapshot
        return self.snapshot_before_run(session_id, run_id)

    def resolve_command(
        self,
        snapshot: PreparedExecutionEnvironmentSnapshot,
        argv: tuple[str, ...],
    ) -> ResolvedManagedCommand | None:
        for name, provider in self._providers.items():
            environment = snapshot.provider(name)
            if environment is None:
                continue
            resolved = provider.resolve_command(environment, argv)
            if resolved is not None:
                return resolved
        return None

    def matches_command(self, argv: tuple[str, ...]) -> bool:
        return any(provider.matches_command(argv) for provider in self._providers.values())

    def record_command_success(
        self,
        session_id: str,
        run_id: str,
        command: ResolvedManagedCommand,
    ) -> PreparedProviderEnvironment:
        provider = next(
            (
                candidate
                for candidate in self._providers.values()
                if candidate.identity.provider == command.provider.identity.provider
            ),
            None,
        )
        if provider is None:
            raise RuntimeError("managed command provider is no longer registered")
        updated = provider.record_command_success(command)
        session_key = session_key_for(session_id)
        run_key = (session_key, run_id)
        with self._lock:
            snapshot = self._run_snapshots.get(run_key)
            if snapshot is not None:
                providers = dict(snapshot.providers)
                providers[provider.name] = updated
                self._run_snapshots[run_key] = PreparedExecutionEnvironmentSnapshot(
                    session_key=session_key,
                    providers=providers,
                )
        return updated

    def release_run(self, session_id: str, run_id: str) -> None:
        with self._lock:
            self._run_snapshots.pop((session_key_for(session_id), run_id), None)

    def delete_session(self, session_id: str) -> None:
        session_key = session_key_for(session_id)
        with self._lock:
            if any(key == session_key for key, _run_id in self._run_snapshots):
                raise RuntimeError("session 仍有 active execution environment snapshot")
            self._session_futures.pop(session_key, None)
            self._pending_turn_snapshots.pop(session_key, None)
        for provider in self._providers.values():
            provider.release_session(session_key)

    def collect_health(self) -> dict[str, ProviderHealth]:
        result: dict[str, ProviderHealth] = {}
        for name, provider in self._providers.items():
            try:
                result[name] = provider.collect_health()
            except Exception:
                logger.warning("execution environment health collection failed", exc_info=True)
                result[name] = ProviderHealth(
                    identity=provider.identity,
                    status=EnvironmentStatus.UNAVAILABLE,
                    error_code="managed_environment_health_failed",
                )
        return result

    def collect_diagnostics(self) -> dict[str, object]:
        with self._lock:
            active = frozenset(key for key, _run_id in self._run_snapshots)
        providers = [
            provider.collect_diagnostics(active_session_keys=active)
            for provider in self._providers.values()
        ]
        statuses = [str(item.get("status", "unavailable")) for item in providers]
        status = (
            "ready"
            if statuses and all(value == "ready" for value in statuses)
            else "unavailable"
            if not statuses or all(value == "unavailable" for value in statuses)
            else "degraded"
        )
        return {
            "schemaVersion": 1,
            "status": status,
            "providers": providers,
        }

    def classify_failure(
        self,
        command: ResolvedManagedCommand,
        output: str,
    ) -> tuple[str, str] | None:
        provider = next(
            (
                candidate
                for candidate in self._providers.values()
                if candidate.identity.provider == command.provider.identity.provider
            ),
            None,
        )
        return provider.classify_failure(command, output) if provider is not None else None

    def gc(self) -> dict[str, dict[str, int]]:
        with self._lock:
            active = frozenset(key for key, _run_id in self._run_snapshots)
        return {
            name: provider.gc(active_session_keys=active)
            for name, provider in self._providers.items()
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _prepare_global(self, provider: EnvironmentProvider) -> ProviderHealth:
        try:
            return provider.prepare_global()
        except Exception as exc:
            logger.warning("execution environment global prepare failed", exc_info=True)
            return ProviderHealth(
                identity=provider.identity,
                status=EnvironmentStatus.UNAVAILABLE,
                error_code="managed_environment_unavailable",
                message=str(exc),
            )

    def _prepare_session(self, session_key: str) -> PreparedExecutionEnvironmentSnapshot:
        providers: dict[str, PreparedProviderEnvironment] = {}
        for name, provider in self._providers.items():
            future = self._global_futures[name]
            health = future.result(timeout=self._prepare_timeout_seconds)
            if health.status is EnvironmentStatus.UNAVAILABLE:
                providers[name] = PreparedProviderEnvironment(
                    identity=health.identity,
                    status=EnvironmentStatus.UNAVAILABLE,
                    session_key=session_key,
                    error_code=health.error_code or "managed_environment_unavailable",
                    message=health.message,
                    prepare_duration_seconds=health.prepare_duration_seconds,
                )
                continue
            try:
                providers[name] = provider.prepare_session(session_key, health)
            except Exception as exc:
                logger.warning(
                    "execution environment provider session prepare failed", exc_info=True
                )
                providers[name] = PreparedProviderEnvironment(
                    identity=provider.identity,
                    status=EnvironmentStatus.UNAVAILABLE,
                    session_key=session_key,
                    error_code=getattr(exc, "code", "managed_environment_prepare_failed"),
                    message=str(exc),
                )
        return PreparedExecutionEnvironmentSnapshot(session_key=session_key, providers=providers)

    def _unavailable_snapshot(
        self,
        session_key: str,
        *,
        code: str,
        message: str,
    ) -> PreparedExecutionEnvironmentSnapshot:
        return PreparedExecutionEnvironmentSnapshot(
            session_key=session_key,
            providers={
                name: PreparedProviderEnvironment(
                    identity=provider.identity,
                    status=EnvironmentStatus.UNAVAILABLE,
                    session_key=session_key,
                    error_code=code,
                    message=message,
                )
                for name, provider in self._providers.items()
            },
        )

    @staticmethod
    def _snapshot_retryable(
        future: Future[PreparedExecutionEnvironmentSnapshot],
    ) -> bool:
        try:
            snapshot = future.result()
        except Exception:
            return True
        return all(
            provider.status is EnvironmentStatus.UNAVAILABLE
            for provider in snapshot.providers.values()
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("execution environment manager 已关闭")


def session_key_for(session_id: str) -> str:
    """把产品 session id 转换为不可逆、固定长度的内部目录 key。"""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def environment_root_layout(root: Path) -> dict[str, Path]:
    """集中声明用户数据根下的 owner 边界。"""
    return {
        "sessions": root / "sessions",
        "shared_cache": root / "shared-cache",
        "staging": root / "staging",
        "locks": root / "locks",
    }


__all__ = [
    "EnvironmentProvider",
    "ExecutionEnvironmentManager",
    "environment_root_layout",
    "session_key_for",
]
