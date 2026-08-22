"""受管本地执行环境的稳定内部模型。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class EnvironmentStatus(StrEnum):
    """Provider、session environment 和 run snapshot 的统一状态。"""

    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ManagedEnvironmentError(RuntimeError):
    """带稳定错误码的受管环境失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProviderIdentity:
    """一个可并存、可失效的 provider 内容身份。"""

    provider: str
    runtime_version: str
    identity: str
    platform: str
    architecture: str

    def public_summary(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "runtime_version": self.runtime_version,
            "identity": self.identity[:16],
            "platform": self.platform,
            "architecture": self.architecture,
        }


@dataclass(frozen=True)
class ProviderHealth:
    """不含路径、命令或用户内容的 provider health。"""

    identity: ProviderIdentity
    status: EnvironmentStatus
    error_code: str | None = None
    message: str = ""
    prepare_duration_seconds: float = 0.0

    def public_summary(self) -> dict[str, Any]:
        return {
            **self.identity.public_summary(),
            "status": self.status.value,
            "error_code": self.error_code,
            "prepare_duration_bucket": duration_bucket(self.prepare_duration_seconds),
        }


@dataclass(frozen=True)
class PreparedProviderEnvironment:
    """一个 session 下某 provider 的不可变运行快照。"""

    identity: ProviderIdentity
    status: EnvironmentStatus
    session_key: str
    revision: int = 0
    executable_aliases: dict[str, Path] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    read_only_roots: tuple[Path, ...] = ()
    writable_roots: tuple[Path, ...] = ()
    shared_cache_root: Path | None = None
    error_code: str | None = None
    message: str = ""
    prepare_duration_seconds: float = 0.0

    def public_summary(self) -> dict[str, Any]:
        return {
            **self.identity.public_summary(),
            "status": self.status.value,
            "environment_revision": self.revision,
            "error_code": self.error_code,
            "prepare_duration_bucket": duration_bucket(self.prepare_duration_seconds),
        }


@dataclass(frozen=True)
class PreparedExecutionEnvironmentSnapshot:
    """在模型采样前固定、供同一 run 全程消费的环境集合。"""

    session_key: str
    providers: dict[str, PreparedProviderEnvironment]

    def provider(self, name: str) -> PreparedProviderEnvironment | None:
        return self.providers.get(name)

    def public_summary(self) -> dict[str, Any]:
        return {
            "status": aggregate_status(self.providers.values()).value,
            "providers": {
                name: provider.public_summary() for name, provider in sorted(self.providers.items())
            },
        }


@dataclass(frozen=True)
class ResolvedManagedCommand:
    """Provider 对一次 model-facing 命令的受管解析结果。"""

    provider: PreparedProviderEnvironment
    argv: tuple[str, ...]
    display_argv: tuple[str, ...]
    environment: dict[str, str]
    read_only_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    package_operation: bool = False
    requires_network: bool = False
    requires_write: bool = False
    cache_hit: bool | None = None

    def public_summary(self) -> dict[str, Any]:
        result = self.provider.public_summary()
        result["package_operation"] = self.package_operation
        if self.cache_hit is not None:
            result["cache_hit"] = self.cache_hit
        return result


def aggregate_status(
    providers: Iterable[PreparedProviderEnvironment],
) -> EnvironmentStatus:
    values = [item.status for item in providers]
    if not values or all(status is EnvironmentStatus.UNAVAILABLE for status in values):
        return EnvironmentStatus.UNAVAILABLE
    if any(status is not EnvironmentStatus.READY for status in values):
        return EnvironmentStatus.DEGRADED
    return EnvironmentStatus.READY


def duration_bucket(seconds: float) -> str:
    if seconds < 0.1:
        return "lt_100ms"
    if seconds < 1:
        return "100ms_to_1s"
    if seconds < 5:
        return "1s_to_5s"
    if seconds < 30:
        return "5s_to_30s"
    return "gte_30s"


__all__ = [
    "EnvironmentStatus",
    "ManagedEnvironmentError",
    "PreparedExecutionEnvironmentSnapshot",
    "PreparedProviderEnvironment",
    "ProviderHealth",
    "ProviderIdentity",
    "ResolvedManagedCommand",
    "aggregate_status",
    "duration_bucket",
]
