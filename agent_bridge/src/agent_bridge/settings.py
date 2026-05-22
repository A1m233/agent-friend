"""bridge 进程配置项。

使用 :mod:`pydantic_settings` 从环境变量 / ``.env`` 加载，优先级：
环境变量 > ``.env`` > 默认值（详见 docs/decisions/0002-incubation-tech-stack §3.17）。

本期只暴露最少几个字段：监听地址 / 端口 / 日志级别 / 会话目录。

详见 docs/requirements/006-agent-bridge/design.md §4.2。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    """bridge 启动配置。

    所有字段都有合理默认值，开发期无需配置 ``.env`` 即可启动。

    Attributes:
        host: 监听地址；默认仅 bind 本机回环，符合
            [0002 §3.12](../../decisions/0002-incubation-tech-stack/README.md) 安全约束。
        port: 监听端口；与 ``0002 §3.18`` 预留的 18765 不同，本期默认 18800
            避免与既有占用冲突；通过 ``AGENT_BRIDGE_PORT`` 覆盖。
        log_level: uvicorn / FastAPI 日志级别。
        sessions_dir: AG-UI 持久化出口指向的 sessions 目录；与 CLI
            ``data/sessions/`` 共享（参见 006 R-4.3.2）。
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_BRIDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 18800
    log_level: str = "INFO"
    sessions_dir: Path = Path("data/sessions")
